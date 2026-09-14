from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .common import now, write_json

SOURCES = {
    "batman": "http://bionet.ncpsb.org.cn/batman-tcm",
    "genecards": "https://www.genecards.org/",
    "omim": "https://omim.org/",
    "string": "https://string-db.org/",
    "david": "https://davidbioinformatics.nih.gov/",
}


def probe(name, directory):
    """Read-only reachability evidence; a login link is not a login requirement."""
    record = {"source": name, "requested_url": SOURCES[name], "accessed_at": now()}
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(SOURCES[name], timeout=(10, 25), headers={"User-Agent": "PharmacologyResearchDemo/0.1 (manual supervised research)"})
        record.update(status_code=response.status_code, final_url=response.url)
        # Keep a bounded raw response locally, not authentication headers/cookies.
        raw = response.content[:500000]
        (target / (name + ".html")).write_bytes(raw)
        text = response.text[:500000]
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        record["title"] = re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
        record["status"] = "reachable" if response.ok else "http_error"
        record["note"] = "仅验证页面响应；查询、导出权限与账号要求需浏览器进一步检查。"
    except requests.RequestException as exc:
        record.update(status="connection_error", error=type(exc).__name__, note="页面未能连接，不能据此认定需要账号。")
    write_json(target / (name + ".json"), record)
    return record


def probe_all(directory):
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda name: probe(name, directory), SOURCES))
    return {record["source"]: record for record in results}


def string_network(genes, task, directory):
    """Use STRING's public API; preserve ID mapping, unmapped genes and raw data."""
    import math
    from .processing import normalize_symbols
    normalized, rejected = normalize_symbols(genes)
    if rejected or len(normalized) != len(genes) or not genes:
        raise ValueError("STRING requires nonempty unique well-formed input symbols")
    confidence = float(task["string_confidence"])
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("STRING confidence must be finite and between 0 and 1")
    if task["string_additional_nodes"] != 0:
        raise ValueError("Only explicit STRING additional_nodes=0 is currently supported")
    target = Path(directory)
    session = requests.Session()
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry = Retry(total=1, connect=1, read=1, backoff_factor=1, status_forcelist=[502, 503, 504], allowed_methods=["GET", "POST"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "string_input.json", {"genes": genes, "taxon_id": task["taxon_id"],
               "confidence": confidence, "additional_nodes": 0, "requested_version": task.get("string_version", "12.0")})
    version_response = session.get("https://string-db.org/api/json/version", timeout=(10, 30))
    version_response.raise_for_status()
    versions = version_response.json()
    write_json(target / "string_version_raw.json", versions)
    if not isinstance(versions, list) or not versions:
        raise ValueError("无法核验 STRING 版本")
    version = versions[0]
    required_version = task.get("string_version", "12.0")
    if version.get("string_version") != required_version:
        raise ValueError("STRING 当前 API 版本与任务要求不一致，需确认稳定版本入口")
    stable = version.get("stable_address", "")
    from urllib.parse import urlparse
    parsed = urlparse(stable)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".string-db.org"):
        raise ValueError("STRING 未返回可验证的官方稳定入口")
    api = stable.rstrip("/") + "/api/json/"
    common = {"species": int(task["taxon_id"]), "caller_identity": "pharmacology_multiagent_demo"}
    params = dict(common, identifiers="\r".join(genes), limit=1, echo_query=1)
    mapping_response = session.post(api + "get_string_ids", data=params, timeout=(10, 60))
    mapping_response.raise_for_status()
    mappings = mapping_response.json()
    if not isinstance(mappings, list):
        raise ValueError("STRING 映射未返回列表")
    write_json(target / "string_mapping_raw.json", mappings)
    id_to_gene = {}
    mapped = set()
    for row in mappings:
        index = row.get("queryIndex")
        if not isinstance(index, int) or not 0 <= index < len(genes) or not row.get("stringId"):
            raise ValueError("STRING 返回不可核验的 queryIndex / stringId")
        gene = genes[index]
        old = id_to_gene.get(row["stringId"])
        if old and old != gene:
            raise ValueError("多个输入基因映射到同一 STRING ID，需复核")
        id_to_gene[row["stringId"]] = gene
        mapped.add(gene)
    network = []
    if id_to_gene:
        network_response = session.post(api + "network", data=dict(common, identifiers="\r".join(id_to_gene), required_score=round(float(task["string_confidence"]) * 1000), add_nodes=int(task["string_additional_nodes"])), timeout=(10, 60))
        network_response.raise_for_status()
        network = network_response.json()
        if not isinstance(network, list):
            raise ValueError("STRING 网络未返回列表")
    write_json(target / "string_network_raw.json", network)
    edges = []
    seen = set()
    for edge in network:
        a, b = edge.get("stringId_A"), edge.get("stringId_B")
        if a not in id_to_gene or b not in id_to_gene:
            raise ValueError("STRING 返回输入集合之外的节点，需核对额外节点设置")
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        score = float(edge["score"])
        if not math.isfinite(score) or not 0 <= score <= 1 or score < confidence:
            raise ValueError("STRING 返回低于任务阈值的边")
        edges.append({"source": id_to_gene[a], "target": id_to_gene[b], "score": score})
    connected = {e[k] for e in edges for k in ("source", "target")}
    meta = {"source": "STRING public API", "accessed_at": now(), "api": api, "version": required_version, "parameters": {"species": common["species"], "required_score": round(float(task["string_confidence"]) * 1000), "add_nodes": int(task["string_additional_nodes"])}, "unmapped": sorted(set(genes) - mapped), "mapped": sorted(mapped), "isolated": sorted(mapped - connected)}
    write_json(target / "string_provenance.json", meta)
    return {"nodes": sorted(mapped), "edges": edges, "provenance": meta}
