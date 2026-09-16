from __future__ import annotations

import gzip
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .common import ROOT, digest, now, read_json, write_json

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


def _string_local_files(task):
    configured = os.environ.get("PHARM_STRING_DATA_DIR", "").strip()
    if not configured:
        local_config = ROOT / "configs/string_data.local.json"
        if local_config.is_file():
            value = read_json(local_config).get("data_dir")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("configs/string_data.local.json 缺少有效 data_dir")
            configured = value.strip()
    if not configured:
        return None
    root = Path(configured).expanduser()
    if not root.is_absolute():
        root = ROOT / root
    version = str(task.get("string_version", "12.0"))
    taxon_id = int(task["taxon_id"])
    names = {
        "aliases": "%d.protein.aliases.v%s.txt.gz" % (taxon_id, version),
        "info": "%d.protein.info.v%s.txt.gz" % (taxon_id, version),
        "links": "%d.protein.links.detailed.v%s.txt.gz" % (taxon_id, version),
    }
    files = {name: root / filename for name, filename in names.items()}
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise ValueError("STRING 本地数据文件缺失：" + "; ".join(missing))
    return files


def string_local_signature(task):
    """Return stable local dataset evidence for run-resume invalidation."""
    files = _string_local_files(task)
    if files is None:
        return None
    return {name: {"filename": path.name, "sha256": digest(path)} for name, path in files.items()}


def _string_network_local(genes, task, directory, files):
    """Read a versioned, species-specific STRING download without network access."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    taxon_id = int(task["taxon_id"])
    version = str(task.get("string_version", "12.0"))
    if int(task.get("string_additional_nodes", 0)) != 0:
        raise ValueError("STRING 本地数据模式暂不支持 additional_nodes；请保持为 0")
    threshold = float(task["string_confidence"])
    if not 0 <= threshold <= 1:
        raise ValueError("STRING 置信度必须在 0 到 1 之间")
    required_score = round(threshold * 1000)
    prefix = str(taxon_id) + ".ENSP"
    requested = list(dict.fromkeys(genes))
    requested_upper = {gene.upper(): gene for gene in requested}

    preferred_candidates = {gene: set() for gene in requested}
    with gzip.open(files["info"], "rt", encoding="utf-8", errors="strict") as stream:
        header = stream.readline().rstrip("\r\n").split("\t")
        if header != ["#string_protein_id", "preferred_name", "protein_size", "annotation"]:
            raise ValueError("STRING protein.info 表头不符合 v%s 格式" % version)
        for line in stream:
            fields = line.rstrip("\r\n").split("\t", 3)
            if len(fields) != 4 or not fields[0].startswith(prefix):
                raise ValueError("STRING protein.info 包含无效行或物种不一致")
            original = requested_upper.get(fields[1].upper())
            if original:
                preferred_candidates[original].add(fields[0])

    unresolved = {gene.upper(): gene for gene in requested if len(preferred_candidates[gene]) != 1}
    alias_candidates = {gene: set() for gene in unresolved.values()}
    if unresolved:
        with gzip.open(files["aliases"], "rt", encoding="utf-8", errors="strict") as stream:
            header = stream.readline().rstrip("\r\n").split("\t")
            if header != ["#string_protein_id", "alias", "source"]:
                raise ValueError("STRING protein.aliases 表头不符合 v%s 格式" % version)
            for line in stream:
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) != 3 or not fields[0].startswith(prefix):
                    raise ValueError("STRING protein.aliases 包含无效行或物种不一致")
                original = unresolved.get(fields[1].upper())
                if original:
                    alias_candidates[original].add(fields[0])

    mappings = []
    id_to_gene = {}
    ambiguous = {}
    for gene in requested:
        preferred = preferred_candidates[gene]
        candidates = preferred if len(preferred) == 1 else preferred | alias_candidates.get(gene, set())
        method = "preferred_name" if len(preferred) == 1 else "alias"
        if len(candidates) == 1:
            string_id = next(iter(candidates))
            old = id_to_gene.get(string_id)
            if old and old != gene:
                raise ValueError("多个输入基因映射到同一 STRING ID，需复核：%s / %s" % (old, gene))
            id_to_gene[string_id] = gene
            mappings.append({"query": gene, "string_id": string_id, "method": method, "status": "mapped"})
        elif candidates:
            ambiguous[gene] = sorted(candidates)
            mappings.append({"query": gene, "candidates": sorted(candidates), "method": method, "status": "ambiguous"})
        else:
            mappings.append({"query": gene, "status": "unmapped"})
    write_json(target / "string_mapping_raw.json", mappings)

    selected = {}
    channel_names = ["neighborhood", "fusion", "cooccurence", "coexpression", "experimental", "database", "textmining", "combined_score"]
    if id_to_gene:
        with gzip.open(files["links"], "rt", encoding="utf-8", errors="strict") as stream:
            header = stream.readline().strip().split()
            expected = ["protein1", "protein2"] + channel_names
            if header != expected:
                raise ValueError("STRING protein.links.detailed 表头不符合 v%s 格式" % version)
            for line in stream:
                fields = line.strip().split()
                if len(fields) != 10:
                    raise ValueError("STRING protein.links.detailed 包含列数错误")
                a, b = fields[:2]
                if a not in id_to_gene or b not in id_to_gene:
                    continue
                try:
                    scores = [int(value) for value in fields[2:]]
                except ValueError as exc:
                    raise ValueError("STRING 网络评分不是整数") from exc
                if any(value < 0 or value > 1000 for value in scores):
                    raise ValueError("STRING 网络评分超出 0 到 1000")
                if scores[-1] < required_score:
                    continue
                key = tuple(sorted((a, b)))
                row = {"protein1": a, "protein2": b, **dict(zip(channel_names, scores))}
                previous = selected.get(key)
                if previous is None or row["combined_score"] > previous["combined_score"]:
                    selected[key] = row
    network_rows = [selected[key] for key in sorted(selected)]
    write_json(target / "string_network_raw.json", network_rows)

    edges = [{
        "source": id_to_gene[key[0]],
        "target": id_to_gene[key[1]],
        "score": selected[key]["combined_score"] / 1000.0,
    } for key in sorted(selected)]
    mapped = sorted(id_to_gene.values())
    unmapped = sorted(set(requested) - set(mapped) - set(ambiguous))
    file_records = {name: {"filename": path.name, "bytes": path.stat().st_size, "sha256": digest(path)} for name, path in files.items()}
    version_record = {"source": "STRING local download", "version": version, "taxon_id": taxon_id, "files": file_records}
    write_json(target / "string_version_raw.json", version_record)
    meta = {
        **version_record,
        "accessed_at": now(),
        "parameters": {"species": taxon_id, "required_score": required_score, "add_nodes": 0},
        "mapped": mapped,
        "unmapped": unmapped,
        "ambiguous": ambiguous,
    }
    write_json(target / "string_provenance.json", meta)
    return {"nodes": mapped, "edges": edges, "provenance": meta}


def _string_network_api(genes, task, directory):
    """Use STRING's public API; preserve ID mapping, unmapped genes and raw data."""
    target = Path(directory)
    session = requests.Session()
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry = Retry(total=1, connect=1, read=1, backoff_factor=1, status_forcelist=[502, 503, 504], allowed_methods=["GET", "POST"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    target.mkdir(parents=True, exist_ok=True)
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
        if score < float(task["string_confidence"]):
            raise ValueError("STRING 返回低于任务阈值的边")
        edges.append({"source": id_to_gene[a], "target": id_to_gene[b], "score": score})
    meta = {"source": "STRING public API", "accessed_at": now(), "api": api, "version": required_version, "parameters": {"species": common["species"], "required_score": round(float(task["string_confidence"]) * 1000), "add_nodes": int(task["string_additional_nodes"])}, "unmapped": sorted(set(genes) - mapped), "mapped": sorted(mapped)}
    write_json(target / "string_provenance.json", meta)
    return {"nodes": sorted(mapped), "edges": edges, "provenance": meta}


def string_network(genes, task, directory):
    """Prefer configured local STRING files; otherwise use the public API."""
    files = _string_local_files(task)
    if files is not None:
        return _string_network_local(genes, task, directory, files)
    return _string_network_api(genes, task, directory)
