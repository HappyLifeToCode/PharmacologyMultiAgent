"""Full Open Targets disease background; no BATMAN target restriction.

The derived cache contains counts and IDF-weight sums, never source scores.
Only a temporary SQLite table is used to deduplicate full disease/gene pairs.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from ..core.common import ROOT, now, read_json, write_json
from ..diseases import open_targets_local as ot
from .progress import report

VERSION = "ot_idf_cosine_v1"
# These exact ontology entities are broad classes, not every descendant.
GENERAL_IDS = {
    "MONDO_0005070": "neoplasm",
    "MONDO_0004992": "cancer",
    "MONDO_0023370": "neoplastic disease or syndrome",
    "MONDO_0045024": "cancer or benign tumor",
    "Orphanet_91088": "Other metabolic disease",
}


def identifier(value):
    return str(value or "").split(" | ", 1)[0].replace(":", "_")


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _fingerprint(root, grouped):
    files = [root / "manifest.json", Path(__file__), Path(ot.__file__)]
    for entries in grouped.values():
        files.extend(root / item["dataset"] / item["file"] for item in entries)
    hashes = []
    for path in sorted(set(files), key=str):
        before = path.stat()
        checksum = _digest(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("建立疾病背景时源数据发生变化，请重试")
        hashes.append((str(path.resolve()), checksum))
    return hashlib.sha256(json.dumps([VERSION, hashes], sort_keys=True).encode()).hexdigest()


def load(data_dir=None, *, cache_dir=None):
    root = ot.resolve_data_dir(data_dir)
    manifest, grouped = ot._load_manifest(root)
    report("disease_background", "核对完整疾病靶点背景")
    fingerprint = _fingerprint(root, grouped)
    cache = Path(cache_dir) if cache_dir else ROOT / "local/agent/disease-background"
    path = cache / (fingerprint + ".json")
    try:
        value = read_json(path)
        if (isinstance(value, dict) and value.get("version") == VERSION and value.get("fingerprint") == fingerprint
                and isinstance(value.get("diseases"), dict) and isinstance(value.get("gene_weights"), dict)
                and isinstance(value.get("symbol_map"), dict) and value.get("disease_count", 0) > 0
                and value.get("checksum") == _checksum(value)):
            return value
    except (OSError, ValueError, TypeError):
        pass
    cache.mkdir(parents=True, exist_ok=True)
    temporary = cache / (fingerprint + "." + uuid4().hex + ".sqlite.tmp")
    try:
        value = _build(root, manifest, grouped, temporary)
        if _fingerprint(root, grouped) != fingerprint:
            raise RuntimeError("建立疾病背景时数据版本发生变化，请重试")
        value["fingerprint"] = fingerprint
        value["checksum"] = _checksum(value)
        # A concurrent builder may publish the same content. Unique staging
        # paths avoid collisions with the common atomic writer's .tmp file.
        staging = cache / (fingerprint + "." + uuid4().hex + ".json.tmp")
        try:
            write_json(staging, value)
            staging.replace(path)
        finally:
            staging.unlink(missing_ok=True)
        return value
    except sqlite3.Error as exc:
        raise RuntimeError("完整疾病背景索引暂不可用：" + str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _checksum(value):
    payload = {key: item for key, item in value.items() if key != "checksum"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _build(root, manifest, grouped, temporary):
    import pyarrow as pa
    import pyarrow.compute as pc

    targets, aliases, approved = ot._load_targets(root, grouped["target"])
    metadata = ot._load_disease_metadata(root, grouped["disease"])
    types = ot._load_disease_entity_types(root, grouped["disease"])
    areas = set()
    for entry in grouped["disease"]:
        reader = ot._parquet().ParquetFile(root / entry["dataset"] / entry["file"])
        if "therapeuticAreas" in reader.schema_arrow.names:
            for batch in reader.iter_batches(columns=["therapeuticAreas"], batch_size=100_000):
                for row in batch.to_pylist():
                    areas.update(row.get("therapeuticAreas") or [])
    diseases = {key: {"name": row.get("canonical_name"), "parents": row.get("parents", []),
                      "ancestors": row.get("ancestors", []),
                      "is_category": key in areas or key in GENERAL_IDS,
                      "category_reason": "Open Targets therapeutic area" if key in areas else "registered broad ontology entity" if key in GENERAL_IDS else None}
                for key, row in metadata.items() if types.get(key) == "disease"}
    symbol_map = {}
    for name in aliases.keys() | approved.keys():
        matches = approved.get(name) or aliases.get(name, set())
        if len(matches) == 1:
            symbol = targets[next(iter(matches))]["approved_symbol"]
            if symbol:
                symbol_map[name] = symbol
    target_ids = sorted(key for key, row in targets.items() if row["approved_symbol"])
    target_values = pa.array(target_ids)
    symbols = pa.array([targets[key]["approved_symbol"] for key in target_ids])
    disease_values = pa.array(sorted(diseases))
    connection = sqlite3.connect(temporary)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA cache_size=-32768")
        connection.execute("CREATE TABLE pairs(disease TEXT, gene TEXT, PRIMARY KEY(disease,gene)) WITHOUT ROWID")
        for number, entry in enumerate(grouped["association_overall_direct"], 1):
            report("disease_background", "建立完整疾病靶点背景（首次）", current=number - 1,
                   total=len(grouped["association_overall_direct"]), unit="个分片")
            reader = ot._parquet().ParquetFile(root / entry["dataset"] / entry["file"])
            for batch in reader.iter_batches(columns=["targetId", "diseaseId", "associationScore"], batch_size=100_000):
                # Same finite, nonnegative score acceptance as the collector.
                scores = batch.column("associationScore")
                valid = pc.and_kleene(pc.is_finite(scores), pc.greater_equal(scores, 0))
                batch = batch.filter(valid)
                target_indices = pc.index_in(ot._trim_ids(batch.column("targetId")), value_set=target_values)
                gene = pc.take(symbols, target_indices)
                disease = ot._trim_ids(batch.column("diseaseId"))
                pairs = pa.table({"disease": disease, "gene": gene})
                pairs = pairs.filter(pc.and_(pc.is_valid(gene), pc.is_in(disease, value_set=disease_values)))
                connection.executemany("INSERT OR IGNORE INTO pairs VALUES (?,?)", zip(pairs["disease"].to_pylist(), pairs["gene"].to_pylist()))
            connection.commit()
        count = connection.execute("SELECT COUNT(DISTINCT disease) FROM pairs").fetchone()[0]
        if not count:
            raise RuntimeError("Open Targets 完整疾病背景没有有效关联")
        weights = {gene: math.log((count + 1) / (frequency + 1)) + 1
                   for gene, frequency in connection.execute("SELECT gene,COUNT(*) FROM pairs GROUP BY gene")}
        counts, sums = defaultdict(int), defaultdict(float)
        report("disease_background", "汇总疾病背景与通用靶点权重")
        for disease, gene in connection.execute("SELECT disease,gene FROM pairs ORDER BY disease,gene"):
            counts[disease] += 1
            sums[disease] += weights[gene]
        for key, row in diseases.items():
            row.update(target_count=counts[key], weight_sum=sums[key])
        return {"version": VERSION, "created_at": now(), "release": manifest["release"],
                "disease_count": count, "gene_count": len(weights), "gene_weights": weights,
                "symbol_map": symbol_map, "diseases": diseases,
                "scope": "all disease entities and all approved-symbol direct associations; no BATMAN restriction; score >= 0"}
    finally:
        connection.close()


def prepare_inputs(input_genes, background):
    mapping, weights = background["symbol_map"], background["gene_weights"]
    inputs = {mapping[gene.casefold()] for gene in input_genes if gene.casefold() in mapping}
    inputs &= weights.keys()
    return inputs, sum(weights[gene] for gene in sorted(inputs))


def describe(disease, matched_ot_symbols, input_genes, background, *, prepared=None):
    key = identifier(disease)
    entity = (background or {}).get("diseases", {}).get(key, {})
    category = key in GENERAL_IDS or entity.get("is_category", False)
    scope = {"disease_id": key, "disease_scope": "category" if category else "non_category",
             "category_reason": entity.get("category_reason") or ("registered broad ontology entity" if key in GENERAL_IDS else None),
             "parents": entity.get("parents", []), "ancestors": entity.get("ancestors", [])}
    if not background:
        return scope, None
    mapping, weights = background["symbol_map"], background["gene_weights"]
    inputs, input_weight = prepared if prepared is not None else prepare_inputs(input_genes, background)
    matched = {mapping[gene.casefold()] for gene in matched_ot_symbols if gene.casefold() in mapping}
    matched &= inputs
    count = entity.get("target_count", 0)
    disease_weight = entity.get("weight_sum", 0)
    if not count or not input_weight or not disease_weight or not matched:
        return scope, None
    numerator = sum(weights[gene] for gene in sorted(matched))
    score = numerator / math.sqrt(input_weight * disease_weight)
    if score > 1 + 1e-9 or len(matched) > count:
        raise RuntimeError("查询关联与完整疾病背景不一致，拒绝生成校正分值")
    return scope, {"version": VERSION, "score": round(min(1.0, score), 8),
                   "matched_approved_genes": sorted(matched), "matched_count": len(matched),
                   "input_background_target_count": len(inputs), "disease_target_count": count,
                   "disease_coverage": len(matched) / count,
                   "input_weight_sum": input_weight, "disease_weight_sum": disease_weight,
                   "matched_weight_sum": numerator, "source": "open_targets",
                   "note": "IDF 加权余弦匹配度，校正疾病靶点集合大小与常见靶点；不是疗效概率或显著性检验"}
