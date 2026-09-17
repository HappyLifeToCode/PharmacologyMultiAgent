"""STRING v12.0 local files (9606.protein.*) as an explicit offline network source.

Selected only by task config string_source="local_files"; never a silent API fallback.
Mapping is exact-match on preferred_name and aliases, which is NOT identical to the
resolution of STRING's get_string_ids API; differences are recorded in provenance.
"""
from __future__ import annotations

import math
from pathlib import Path

from .common import ROOT, digest, now, write_json
from .processing import normalize_symbols

VERSION = "12.0"
FILES = {
    "info": "9606.protein.info.v12.0.txt",
    "aliases": "9606.protein.aliases.v12.0.txt",
    "links": "9606.protein.links.detailed.v12.0.txt",
}


def _data_files(task):
    required_version = task.get("string_version", VERSION)
    if required_version != VERSION:
        raise ValueError("本地 STRING 文件固定为 v%s，与任务要求的 %s 不一致" % (VERSION, required_version))
    if int(task["taxon_id"]) != 9606:
        raise ValueError("本地 STRING 文件只包含人类（taxon 9606）")
    directory = Path(task.get("string_local_dir") or ROOT / "data" / "string" / ("v" + VERSION))
    if not directory.is_absolute():
        directory = ROOT / directory
    files = {kind: directory / name for kind, name in FILES.items()}
    missing = [str(p) for p in files.values() if not p.is_file()]
    if missing:
        raise ValueError("本地 STRING 文件缺失：" + "; ".join(missing))
    return directory, files


def _map_symbols(genes, files):
    """Exact-match mapping; ambiguous aliases are reported, never silently resolved."""
    wanted = set(genes)
    preferred = {}
    with files["info"].open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or not parts[0] or not parts[1]:
                raise ValueError("STRING info 文件行结构异常：" + line[:120])
            preferred.setdefault(parts[1], set()).add(parts[0])
    alias_hits = {}
    with files["aliases"].open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                raise ValueError("STRING aliases 文件行结构异常：" + line[:120])
            if parts[1] in wanted:
                alias_hits.setdefault(parts[1], set()).add(parts[0])
    id_to_gene, mapping, ambiguous, unmapped = {}, [], [], []
    for gene in genes:
        candidates = set(preferred.get(gene, ())) | set(alias_hits.get(gene, ()))
        if not candidates:
            unmapped.append(gene)
            mapping.append({"gene_symbol": gene, "status": "unmapped", "string_id": None, "candidates": []})
        elif len(candidates) > 1:
            ambiguous.append(gene)
            mapping.append({"gene_symbol": gene, "status": "ambiguous",
                            "string_id": None, "candidates": sorted(candidates)})
        else:
            string_id = candidates.pop()
            old = id_to_gene.get(string_id)
            if old and old != gene:
                raise ValueError("多个输入基因映射到同一 STRING ID，需复核：%s / %s -> %s" % (old, gene, string_id))
            id_to_gene[string_id] = gene
            via = "preferred_name" if gene in preferred else "alias"
            mapping.append({"gene_symbol": gene, "status": "mapped", "string_id": string_id,
                            "candidates": [string_id], "matched_via": via})
    return id_to_gene, mapping, ambiguous, unmapped


def _scan_edges(files, id_to_gene, required_score):
    edges, seen = [], set()
    with files["links"].open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("protein1"):
                continue
            parts = line.split()
            if len(parts) != 10:
                raise ValueError("STRING links 文件行结构异常：" + line[:120])
            a, b = parts[0], parts[1]
            if a not in id_to_gene or b not in id_to_gene or a == b:
                continue
            score = int(parts[9])
            if score < required_score:
                continue
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": id_to_gene[key[0]], "target": id_to_gene[key[1]],
                          "score": score / 1000.0,
                          "stringId_A": key[0], "stringId_B": key[1], "combined_score": score})
    return edges


def string_local_network(genes, task, directory):
    """Build the input-internal network from local STRING files; keep full evidence."""
    normalized, rejected = normalize_symbols(genes)
    if rejected or len(normalized) != len(genes) or not genes:
        raise ValueError("STRING requires nonempty unique well-formed input symbols")
    confidence = float(task["string_confidence"])
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("STRING confidence must be finite and between 0 and 1")
    if task["string_additional_nodes"] != 0:
        raise ValueError("Only explicit STRING additional_nodes=0 is currently supported")
    data_dir, files = _data_files(task)
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    required_score = round(confidence * 1000)
    file_records = {kind: {"file": path.name, "size": path.stat().st_size, "sha256": digest(path)}
                    for kind, path in sorted(files.items())}
    write_json(target / "string_local_files.json",
               {"source": "STRING local files", "string_version": VERSION, "taxon_id": 9606,
                "data_dir": str(data_dir), "files": file_records, "recorded_at": now()})
    write_json(target / "string_input.json", {"genes": genes, "taxon_id": int(task["taxon_id"]),
               "confidence": confidence, "additional_nodes": 0, "requested_version": VERSION,
               "source": "local_files"})
    id_to_gene, mapping, ambiguous, unmapped = _map_symbols(genes, files)
    write_json(target / "string_local_mapping.json", {"mapping_method":
               "exact match on preferred_name and aliases; differs from get_string_ids API resolution",
               "records": mapping})
    import csv
    with (target / "string_local_mapping.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["gene_symbol", "status", "string_id", "matched_via"])
        writer.writeheader()
        writer.writerows({k: r.get(k) for k in ["gene_symbol", "status", "string_id", "matched_via"]}
                         for r in mapping)
    edges = _scan_edges(files, id_to_gene, required_score)
    write_json(target / "string_local_network_raw.json",
               [{"stringId_A": e["stringId_A"], "stringId_B": e["stringId_B"],
                 "combined_score": e["combined_score"]} for e in edges])
    for edge in edges:
        del edge["stringId_A"], edge["stringId_B"], edge["combined_score"]
    mapped = set(id_to_gene.values())
    connected = {e[k] for e in edges for k in ("source", "target")}
    meta = {"source": "STRING local files v%s (taxon 9606)" % VERSION, "recorded_at": now(),
            "data_files": {kind: rec["sha256"] for kind, rec in file_records.items()},
            "parameters": {"species": 9606, "required_score": required_score, "add_nodes": 0},
            "mapping_method": "exact match on preferred_name and aliases",
            "unmapped": sorted(unmapped), "ambiguous": sorted(ambiguous),
            "mapped": sorted(mapped), "isolated": sorted(mapped - connected)}
    write_json(target / "string_provenance.json", meta)
    return {"nodes": sorted(mapped), "edges": edges, "provenance": meta}
