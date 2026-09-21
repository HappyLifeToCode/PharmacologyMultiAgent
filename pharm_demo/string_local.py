"""STRING local download files as the preferred network source, with API fallback.

Selection: task string_source="api" or "local_files" forces one side; otherwise
local files are used when a data directory is configured, and the public API is
called only when no local data is configured. The actual source is recorded in
string_provenance.json. Data directory resolution order: task string_local_dir >
PHARM_STRING_DATA_DIR > configs/string_data.local.json {"data_dir": ...} >
data/string/v<version>. Official .txt.gz downloads are read directly; plain .txt
files are also accepted. Mapping is exact-match on preferred_name (then aliases
for unresolved symbols), which differs from get_string_ids API resolution.
"""
from __future__ import annotations

import gzip
import math
import os
from pathlib import Path

from .common import ROOT, digest, now, read_json, write_json
from .processing import normalize_symbols

CHANNELS = ["neighborhood", "fusion", "cooccurence", "coexpression",
            "experimental", "database", "textmining", "combined_score"]


def _data_root(task):
    """Return (root, explicit): explicit configs fail loudly; the default dir is opportunistic."""
    configured = str(task.get("string_local_dir") or "").strip()
    if not configured:
        configured = os.environ.get("PHARM_STRING_DATA_DIR", "").strip()
    if not configured:
        local_config = ROOT / "configs/string_data.local.json"
        if local_config.is_file():
            value = read_json(local_config).get("data_dir")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("configs/string_data.local.json 缺少有效 data_dir")
            configured = value.strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            root = ROOT / root
        return root, True
    return ROOT / "data" / "string" / ("v" + str(task.get("string_version", "12.0"))), False


def string_local_files(task):
    """Resolve the three local STRING files (.txt.gz preferred, .txt accepted).

    Returns None when only the default directory was tried and files are absent;
    raises when an explicitly configured directory is broken.
    """
    root, explicit = _data_root(task)
    version = str(task.get("string_version", "12.0"))
    try:
        taxon_id = int(task["taxon_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("task.taxon_id 缺失或无效") from exc
    stems = {"aliases": "protein.aliases", "info": "protein.info",
             "links": "protein.links.detailed"}
    files = {}
    for kind, stem in stems.items():
        base = "%d.%s.v%s.txt" % (taxon_id, stem, version)
        for candidate in (root / (base + ".gz"), root / base):
            if candidate.is_file():
                files[kind] = candidate
                break
    if len(files) == len(stems):
        return files
    if not explicit and not files:
        return None
    missing = [str(root / ("%d.%s.v%s.txt[.gz]" % (taxon_id, stem, version)))
               for kind, stem in stems.items() if kind not in files]
    raise ValueError("本地 STRING 文件缺失：" + "; ".join(missing))


def string_local_available(task):
    """Whether the local source would be used for this task in prefer-local mode."""
    try:
        return string_local_files(task) is not None
    except ValueError:
        # Only an explicitly configured directory should fail loudly at run time;
        # a task without local configuration just falls back to the API.
        return _data_root(task)[1]


def string_local_signature(task):
    """Stable local dataset evidence for run-resume invalidation; None when API is used."""
    if task.get("string_source") == "api":
        return None
    try:
        files = string_local_files(task)
    except ValueError as exc:
        return {"unavailable": str(exc)}
    if files is None:
        return None
    return {kind: {"filename": path.name, "sha256": digest(path)}
            for kind, path in sorted(files.items())}


def _open_text(path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="strict")
    return path.open("r", encoding="utf-8", errors="strict")


def _map_symbols(genes, task, files):
    """preferred_name first; aliases only for unresolved symbols. Ambiguity is reported."""
    taxon_id = int(task["taxon_id"])
    version = str(task.get("string_version", "12.0"))
    prefix = str(taxon_id) + ".ENSP"
    requested_upper = {gene.upper(): gene for gene in genes}
    preferred = {gene: set() for gene in genes}
    with _open_text(files["info"]) as stream:
        header = stream.readline().rstrip("\r\n").split("\t")
        if header != ["#string_protein_id", "preferred_name", "protein_size", "annotation"]:
            raise ValueError("STRING protein.info 表头不符合 v%s 格式" % version)
        for line in stream:
            fields = line.rstrip("\r\n").split("\t", 3)
            if len(fields) != 4 or not fields[0].startswith(prefix):
                raise ValueError("STRING protein.info 包含无效行或物种不一致：" + line[:120])
            original = requested_upper.get(fields[1].upper())
            if original:
                preferred[original].add(fields[0])
    unresolved = {gene.upper(): gene for gene in genes if len(preferred[gene]) != 1}
    alias_hits = {gene: set() for gene in unresolved.values()}
    if unresolved:
        with _open_text(files["aliases"]) as stream:
            header = stream.readline().rstrip("\r\n").split("\t")
            if header != ["#string_protein_id", "alias", "source"]:
                raise ValueError("STRING protein.aliases 表头不符合 v%s 格式" % version)
            for line in stream:
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) != 3 or not fields[0].startswith(prefix):
                    raise ValueError("STRING protein.aliases 包含无效行或物种不一致：" + line[:120])
                original = unresolved.get(fields[1].upper())
                if original:
                    alias_hits[original].add(fields[0])
    id_to_gene, records, ambiguous, unmapped = {}, [], [], []
    for gene in genes:
        direct = preferred[gene]
        candidates = direct if len(direct) == 1 else direct | alias_hits.get(gene, set())
        method = "preferred_name" if len(direct) == 1 else "alias"
        if len(candidates) == 1:
            string_id = next(iter(candidates))
            old = id_to_gene.get(string_id)
            if old and old["gene"] != gene:
                # preferred_name 命中优先于别名命中：别名方记为歧义（其别名指向可能误导），
                # 双方都未直接命中时两个基因都记歧义，不静默选择
                if method == "alias" and old["method"] == "preferred_name":
                    ambiguous.append(gene)
                    records.append({"query": gene, "status": "ambiguous",
                                    "candidates": [string_id], "method": method,
                                    "note": "别名命中与 %s 的 preferred_name 冲突，判为歧义" % old["gene"]})
                    continue
                if method == "preferred_name" and old["method"] == "alias":
                    ambiguous.append(old["gene"])
                    for record in records:
                        if record["query"] == old["gene"]:
                            record.update(status="ambiguous", method="alias",
                                          note="别名命中被 %s 的 preferred_name 取代" % gene)
                else:
                    ambiguous.extend([old["gene"], gene])
                    records = [r for r in records if r["query"] != old["gene"]]
                    records.append({"query": old["gene"], "status": "ambiguous",
                                    "candidates": [string_id], "method": old["method"],
                                    "note": "与 %s 命中同一 STRING ID，判为歧义" % gene})
                    records.append({"query": gene, "status": "ambiguous",
                                    "candidates": [string_id], "method": method,
                                    "note": "与 %s 命中同一 STRING ID，判为歧义" % old["gene"]})
                    del id_to_gene[string_id]
                    continue
            id_to_gene[string_id] = {"gene": gene, "method": method}
            records.append({"query": gene, "status": "mapped", "string_id": string_id, "method": method})
        elif candidates:
            ambiguous.append(gene)
            records.append({"query": gene, "status": "ambiguous",
                            "candidates": sorted(candidates), "method": method})
        else:
            unmapped.append(gene)
            records.append({"query": gene, "status": "unmapped"})
    id_to_gene = {k: v["gene"] for k, v in id_to_gene.items()}
    return id_to_gene, records, ambiguous, unmapped


def _scan_edges(files, id_to_gene, required_score, version):
    selected = {}
    if not id_to_gene:
        return []
    with _open_text(files["links"]) as stream:
        header = stream.readline().strip().split()
        if header != ["protein1", "protein2"] + CHANNELS:
            raise ValueError("STRING protein.links.detailed 表头不符合 v%s 格式" % version)
        for line in stream:
            fields = line.strip().split()
            if len(fields) != 10:
                raise ValueError("STRING links 文件行结构异常：" + line[:120])
            a, b = fields[:2]
            if a not in id_to_gene or b not in id_to_gene or a == b:
                continue
            try:
                scores = [int(value) for value in fields[2:]]
            except ValueError as exc:
                raise ValueError("STRING 网络评分不是整数：" + line[:120]) from exc
            if any(value < 0 or value > 1000 for value in scores):
                raise ValueError("STRING 网络评分超出 0 到 1000：" + line[:120])
            if scores[-1] < required_score:
                continue
            key = (a, b) if a < b else (b, a)
            row = {"protein1": a, "protein2": b, **dict(zip(CHANNELS, scores))}
            previous = selected.get(key)
            if previous is None or row["combined_score"] > previous["combined_score"]:
                selected[key] = row
    return [selected[key] for key in sorted(selected)]


def string_local_network(genes, task, directory):
    """Build the input-internal network from local STRING files; keep full evidence."""
    normalized, rejected = normalize_symbols(genes)
    if rejected or len(normalized) != len(genes) or not genes:
        raise ValueError("STRING requires nonempty unique well-formed input symbols")
    confidence = float(task["string_confidence"])
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("STRING confidence must be finite and between 0 and 1")
    if int(task.get("string_additional_nodes", 0)) != 0:
        raise ValueError("Only explicit STRING additional_nodes=0 is currently supported")
    version = str(task.get("string_version", "12.0"))
    taxon_id = int(task["taxon_id"])
    files = string_local_files(task)
    if files is None:
        raise ValueError("任务要求本地 STRING 来源，但未找到数据文件（默认目录 data/string/v%s 为空）" % version)
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    required_score = round(confidence * 1000)
    file_records = {kind: {"filename": path.name, "bytes": path.stat().st_size,
                           "sha256": digest(path)} for kind, path in sorted(files.items())}
    version_record = {"source": "STRING local download", "string_version": version,
                      "taxon_id": taxon_id, "files": file_records, "recorded_at": now()}
    write_json(target / "string_version_raw.json", version_record)
    write_json(target / "string_input.json", {"genes": genes, "taxon_id": taxon_id,
               "confidence": confidence, "additional_nodes": 0, "requested_version": version,
               "source": "local_files"})
    id_to_gene, records, ambiguous, unmapped = _map_symbols(genes, task, files)
    write_json(target / "string_mapping_raw.json", {"mapping_method":
               "exact match on preferred_name, then aliases for unresolved symbols; "
               "differs from get_string_ids API resolution", "records": records})
    import csv
    with (target / "string_local_mapping.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["gene_symbol", "status", "string_id", "method"])
        writer.writeheader()
        writer.writerows({"gene_symbol": r["query"], "status": r["status"],
                          "string_id": r.get("string_id"), "method": r.get("method")}
                         for r in records)
    raw_edges = _scan_edges(files, id_to_gene, required_score, version)
    write_json(target / "string_network_raw.json", raw_edges)
    edges = [{"source": id_to_gene[row["protein1"]], "target": id_to_gene[row["protein2"]],
              "score": row["combined_score"] / 1000.0} for row in raw_edges]
    mapped = sorted(id_to_gene.values())
    connected = {e[k] for e in edges for k in ("source", "target")}
    meta = {**version_record,
            "parameters": {"species": taxon_id, "required_score": required_score, "add_nodes": 0},
            "mapping_method": "exact match on preferred_name, then aliases for unresolved symbols",
            "mapped": mapped, "unmapped": sorted(unmapped), "ambiguous": sorted(ambiguous),
            "isolated": sorted(set(mapped) - connected)}
    write_json(target / "string_provenance.json", meta)
    return {"nodes": mapped, "edges": edges, "provenance": meta}
