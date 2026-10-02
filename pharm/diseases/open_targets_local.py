"""Local Open Targets 26.09 snapshot collector.

The download release is stored as Parquet files.  This module keeps the same
generic ``associations.csv`` contract as the GraphQL collector while making
the snapshot version, file hashes, mapping coverage, and selection rule
explicit in provenance.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from ..core.common import ROOT, digest, now, read_json, write_json


DOWNLOADS_URL = "https://platform.opentargets.org/downloads"
DEFAULT_DATA_DIR = ROOT / "data" / "open_targets" / "26.09"


def _parquet():
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "本地 Open Targets 模式需要 pyarrow；请先安装 requirements.txt 中的依赖"
        ) from exc
    return parquet


def resolve_data_dir(data_dir=None) -> Path:
    value = data_dir or os.environ.get("PHARM_OPEN_TARGETS_DATA_DIR")
    if not value:
        local_config = ROOT / "configs" / "open_targets_data.local.json"
        if local_config.is_file():
            configured = read_json(local_config).get("data_dir")
            if not isinstance(configured, str) or not configured.strip():
                raise ValueError("configs/open_targets_data.local.json 缺少有效 data_dir")
            value = configured.strip()
    path = Path(value) if value else DEFAULT_DATA_DIR
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _label_values(value):
    if not value:
        return []
    values = []
    for item in value:
        if isinstance(item, dict):
            label = item.get("label")
        else:
            label = item
        if label is not None and str(label).strip():
            values.append(str(label).strip())
    return values


def _load_manifest(data_dir: Path) -> tuple[dict, dict[str, list[dict]]]:
    path = data_dir / "manifest.json"
    if not path.is_file():
        raise RuntimeError("Open Targets 本地数据缺少 manifest.json：" + str(path))
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Open Targets 本地 manifest.json 无法读取") from exc
    release = manifest.get("release")
    entries = manifest.get("datasets")
    if not isinstance(release, str) or not release.strip() or not isinstance(entries, list):
        raise RuntimeError("Open Targets 本地 manifest.json 缺少 release/datasets")
    expected_release = os.environ.get("PHARM_OPEN_TARGETS_RELEASE")
    if expected_release and expected_release != release:
        raise RuntimeError(
            "Open Targets 本地数据版本不符合要求：期望 %s，实际 %s" %
            (expected_release, release))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("dataset"), str) or not isinstance(entry.get("file"), str):
            raise RuntimeError("Open Targets 本地 manifest.json 含无效文件条目")
        path_value = data_dir / entry["dataset"] / entry["file"]
        if path_value.suffix != ".parquet" or not path_value.is_file():
            raise RuntimeError("Open Targets 本地数据文件缺失：" + str(path_value))
        expected_bytes = entry.get("bytes")
        if isinstance(expected_bytes, int) and path_value.stat().st_size != expected_bytes:
            raise RuntimeError("Open Targets 本地文件大小与 manifest 不一致：" + str(path_value))
        grouped[entry["dataset"]].append(entry)
    required = ("target", "disease", "association_overall_direct")
    missing = [name for name in required if not grouped.get(name)]
    if missing:
        raise RuntimeError("Open Targets 本地数据缺少数据集：" + ", ".join(missing))
    return manifest, grouped


def _load_targets(data_dir: Path, entries: list[dict]) -> tuple[dict[str, dict], dict[str, set[str]], dict[str, set[str]]]:
    parquet = _parquet()
    targets: dict[str, dict] = {}
    aliases: dict[str, set[str]] = defaultdict(set)
    approved_symbols: dict[str, set[str]] = defaultdict(set)
    columns = ["id", "approvedSymbol", "symbolSynonyms", "obsoleteSymbols", "synonyms"]
    for entry in entries:
        reader = parquet.ParquetFile(data_dir / entry["dataset"] / entry["file"])
        for batch in reader.iter_batches(columns=columns, batch_size=100_000):
            for row in batch.to_pylist():
                target_id = str(row.get("id") or "").strip()
                approved = str(row.get("approvedSymbol") or "").strip()
                if not target_id or not approved:
                    continue
                targets[target_id] = {"id": target_id, "approved_symbol": approved}
                approved_symbols[approved.casefold()].add(target_id)
                labels = [approved]
                for field in ("symbolSynonyms", "obsoleteSymbols", "synonyms"):
                    labels.extend(_label_values(row.get(field)))
                for label in labels:
                    aliases[label.casefold()].add(target_id)
    return targets, aliases, approved_symbols


def _load_diseases(data_dir: Path, entries: list[dict]) -> dict[str, str]:
    parquet = _parquet()
    diseases: dict[str, str] = {}
    for entry in entries:
        reader = parquet.ParquetFile(data_dir / entry["dataset"] / entry["file"])
        for batch in reader.iter_batches(columns=["id", "name"], batch_size=100_000):
            for row in batch.to_pylist():
                disease_id = str(row.get("id") or "").strip()
                disease_name = str(row.get("name") or "").strip()
                if disease_id and disease_name:
                    diseases.setdefault(disease_id, disease_name)
    return diseases


def _entity_type_from_ontology(disease_id: str, parents=None) -> str:
    """Classify Open Targets entities for the workbench display.

    MONDO/Orphanet entries are disease ontology terms. EFO terms are kept as
    diseases only when their ontology parents point into a disease ontology;
    measurements, traits, and other ontology entities remain phenotypes.
    This is a display classification, not a treatment claim.
    """
    value = str(disease_id or "").strip()
    prefix = value.split("_", 1)[0].casefold()
    if prefix in {"mondo", "orphanet", "doid"}:
        return "disease"
    for parent in parents or []:
        parent_prefix = str(parent).split("_", 1)[0].casefold()
        if prefix == "efo" and parent_prefix in {"mondo", "orphanet", "doid"}:
            return "disease"
    return "phenotype"


def _load_disease_entity_types(data_dir: Path, entries: list[dict]) -> dict[str, str]:
    """Read the ontology fields needed to separate diseases from traits."""
    parquet = _parquet()
    entity_types: dict[str, str] = {}
    for entry in entries:
        reader = parquet.ParquetFile(data_dir / entry["dataset"] / entry["file"])
        columns = ["id"]
        if "parents" in reader.schema.names:
            columns.append("parents")
        for batch in reader.iter_batches(columns=columns, batch_size=100_000):
            for row in batch.to_pylist():
                disease_id = str(row.get("id") or "").strip()
                if disease_id:
                    entity_types[disease_id] = _entity_type_from_ontology(
                        disease_id, row.get("parents"))
    return entity_types


def _clean_diseases(diseases):
    clean = []
    seen = set()
    for value in diseases:
        disease = str(value).strip()
        key = disease.casefold()
        if disease and key not in seen:
            clean.append(disease)
            seen.add(key)
    if not clean:
        raise ValueError("diseases 不能全为空")
    return clean


def _normalise_disease_label(value):
    return re.sub(r"[^\w]+", " ", str(value).casefold()).strip()


def _resolve_local_diseases(queries, diseases_by_id):
    """Resolve disease IDs/names without silently picking among ambiguous hits."""
    by_id = {str(key).casefold(): key for key in diseases_by_id}
    by_name = defaultdict(list)
    normalised_names = defaultdict(list)
    for disease_id, name in diseases_by_id.items():
        by_name[str(name).casefold()].append(disease_id)
        normalised_names[_normalise_disease_label(name)].append(disease_id)
    resolved = []
    unresolved = []
    for query in queries:
        key = query.casefold()
        candidates = []
        if key in by_id:
            candidates = [by_id[key]]
        elif len(by_name.get(key, [])) == 1:
            candidates = by_name[key]
        else:
            normalised = _normalise_disease_label(query)
            if len(normalised_names.get(normalised, [])) == 1:
                candidates = normalised_names[normalised]
            else:
                matches = [disease_id for disease_id, name in diseases_by_id.items()
                           if normalised and (normalised in _normalise_disease_label(name)
                                              or _normalise_disease_label(name) in normalised)]
                candidates = sorted(set(matches))
        if len(candidates) == 1:
            disease_id = candidates[0]
            resolved.append({"query": query, "disease_id": disease_id,
                             "name": diseases_by_id[disease_id]})
        elif not candidates:
            unresolved.append({"disease": query, "reason": "local disease name or ID not found"})
        else:
            unresolved.append({"disease": query, "reason": "local disease name is ambiguous",
                               "candidate_disease_ids": candidates})
    return resolved, unresolved


def collect_open_targets_local_by_diseases(diseases, output_dir,
                                           top_k_per_disease=None,
                                           min_score=None, data_dir=None) -> dict:
    """Collect disease -> target rows from a local Open Targets snapshot."""
    started_at = now()
    if not isinstance(diseases, (list, tuple)) or not diseases:
        raise ValueError("diseases 必须是非空疾病关键词列表")
    clean_diseases = _clean_diseases(diseases)
    if top_k_per_disease is not None and (not isinstance(top_k_per_disease, int) or top_k_per_disease < 1):
        raise ValueError("top_k_per_disease 必须是正整数")
    if min_score is not None and (not isinstance(min_score, (int, float)) or min_score < 0 or min_score > 1):
        raise ValueError("min_score 必须在 0—1 之间")

    output_dir = Path(output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    local_dir = resolve_data_dir(data_dir)
    manifest, grouped = _load_manifest(local_dir)
    targets, _aliases, _approved_symbols = _load_targets(local_dir, grouped["target"])
    diseases_by_id = _load_diseases(local_dir, grouped["disease"])
    disease_entity_types = _load_disease_entity_types(local_dir, grouped["disease"])
    resolved, unresolved = _resolve_local_diseases(clean_diseases, diseases_by_id)
    if not resolved:
        raise RuntimeError("Open Targets 本地数据未解析任何疾病关键词")

    wanted_diseases = {item["disease_id"] for item in resolved}
    rows_by_disease: dict[str, list[dict]] = defaultdict(list)
    rows_seen_by_disease: dict[str, int] = defaultdict(int)
    association_rows_seen = 0
    missing_target_ids = set()
    parquet = _parquet()
    association_columns = [
        "targetId", "diseaseId", "aggregationType", "aggregationValue",
        "associationScore", "evidenceCount", "currentNovelty",
    ]
    for entry in grouped["association_overall_direct"]:
        reader = parquet.ParquetFile(local_dir / entry["dataset"] / entry["file"])
        for batch in reader.iter_batches(columns=association_columns, batch_size=100_000):
            for row in batch.to_pylist():
                disease_id = str(row.get("diseaseId") or "").strip()
                if disease_id not in wanted_diseases:
                    continue
                association_rows_seen += 1
                rows_seen_by_disease[disease_id] += 1
                target_id = str(row.get("targetId") or "").strip()
                if target_id not in targets:
                    missing_target_ids.add(target_id)
                    continue
                score = row.get("associationScore")
                if not isinstance(score, (int, float)) or not math.isfinite(float(score)) or float(score) < 0:
                    continue
                if min_score is not None and float(score) < min_score:
                    continue
                row["target_id"] = target_id
                row["source_file"] = entry["file"]
                rows_by_disease[disease_id].append(row)

    associations = []
    disease_keys = []
    selected_records = []
    resolved_by_id = {item["disease_id"]: item for item in resolved}
    for disease_id in sorted(wanted_diseases):
        disease = resolved_by_id[disease_id]
        rows = sorted(rows_by_disease.get(disease_id, []),
                      key=lambda row: (-float(row["associationScore"]), str(row.get("targetId") or "")))
        if top_k_per_disease is not None:
            rows = rows[:top_k_per_disease]
        selected_records.append({"disease_id": disease_id, "query": disease["query"],
                                 "disease_name": disease["name"],
                                 "association_rows_seen": rows_seen_by_disease.get(disease_id, 0),
                                 "selected_rows": len(rows)})
        disease_key = disease_id + " | " + disease["name"]
        disease_keys.append(disease_key)
        for row in rows:
            target_id = row["target_id"]
            target = targets[target_id]
            extra = {
                "data_mode": "local_snapshot",
                "release": manifest["release"],
                "direction": "disease_to_target",
                "query": disease["query"],
                "disease_id": disease_id,
                "disease_name": disease["name"],
                "entity_type": disease_entity_types.get(disease_id, "phenotype"),
                "target_id": target_id,
                "ensembl_id": target_id,
                "approved_symbol": target["approved_symbol"],
                "aggregation_type": row.get("aggregationType"),
                "aggregation_value": row.get("aggregationValue"),
                "association_score": row.get("associationScore"),
                "evidence_count": row.get("evidenceCount"),
                "current_novelty": row.get("currentNovelty"),
                "source_file": row.get("source_file"),
            }
            associations.append({"disease": disease_key,
                                 "gene_symbol": target["approved_symbol"],
                                 "score": "%.12g" % float(row["associationScore"]),
                                 "source": "open_targets",
                                 "extra": json.dumps(extra, ensure_ascii=False, sort_keys=True)})

    if not associations:
        raise RuntimeError("Open Targets 本地数据未产生有效疾病—基因关联")
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "associations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "score", "source", "extra"])
        writer.writeheader()
        writer.writerows(associations)
    with (raw_dir / "open_targets_local_selection.jsonl").open("w", encoding="utf-8") as stream:
        for record in selected_records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (raw_dir / "unresolved_diseases.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "reason", "candidate_disease_ids"])
        writer.writeheader()
        for row in unresolved:
            writer.writerow({"disease": row["disease"], "reason": row["reason"],
                             "candidate_disease_ids": "|".join(row.get("candidate_disease_ids", []))})

    manifest_path = local_dir / "manifest.json"
    provenance = {
        "sources": {"associations": {
            "complete": True,
            "source_url": DOWNLOADS_URL,
            "accessed_at": str(manifest.get("downloaded_at") or date.today().isoformat()).split("T", 1)[0],
            "mode": "local_snapshot",
            "direction": "disease_to_target",
            "release": manifest["release"],
            "data_dir": str(local_dir),
            "manifest_sha256": digest(manifest_path),
            "raw_files": ["raw/open_targets_local_selection.jsonl", "raw/unresolved_diseases.csv"],
            "dataset": "association_overall_direct",
            "dataset_files": [entry for entry in grouped["association_overall_direct"]],
            "queried_diseases": clean_diseases,
            "resolved_diseases": [item["query"] for item in resolved],
            "unresolved_diseases": [item["disease"] for item in unresolved],
            "missing_target_ids": sorted(missing_target_ids),
            "diseases": disease_keys,
            "selection": {"top_k_per_disease": top_k_per_disease,
                           "min_score": min_score,
                           "raw_rows_complete": True,
                           "score_field": "associationScore",
                           "datasource_scores_available": False},
            "coverage": {"target_files": len(grouped["target"]),
                         "disease_files": len(grouped["disease"]),
                         "association_files": len(grouped["association_overall_direct"]),
                         "resolved_disease_count": len(resolved),
                         "association_rows_seen": association_rows_seen},
        }},
        "mapping": {
            "confirmed": True,
            "method": "Open Targets local disease ID/name resolution; approved symbols and Ensembl target IDs retained",
            "version": "Open Targets Platform release " + manifest["release"],
            "raw_files": ["raw/open_targets_local_selection.jsonl", "raw/unresolved_diseases.csv"],
        },
        "collection": {"started_at": started_at, "finished_at": now(),
                       "page_size": None, "association_rows": len(associations),
                       "resolved_count": len(resolved), "unresolved_count": len(unresolved),
                       "top_k_per_disease": top_k_per_disease, "min_score": min_score,
                       "mode": "local_snapshot", "direction": "disease_to_target"},
    }
    write_json(output_dir / "provenance.json", provenance)
    return {"output_dir": str(output_dir), "associations": len(associations),
            "diseases": len(disease_keys), "resolved": len(resolved),
            "unresolved": len(unresolved), "provenance": provenance}


def _clean_symbols(symbols):
    clean = []
    seen = set()
    for value in symbols:
        symbol = str(value).strip()
        key = symbol.casefold()
        if symbol and key not in seen:
            clean.append(symbol)
            seen.add(key)
    if not clean:
        raise ValueError("symbols 不能全为空")
    return clean


def collect_open_targets_local_by_targets(symbols, output_dir,
                                           top_k_per_target=None,
                                           min_score=None, data_dir=None) -> dict:
    """Collect BATMAN target -> disease rows from a local snapshot."""
    started_at = now()
    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise ValueError("symbols 必须是非空 BATMAN 基因符号列表")
    clean_symbols = _clean_symbols(symbols)
    if top_k_per_target is not None and (not isinstance(top_k_per_target, int) or top_k_per_target < 1):
        raise ValueError("top_k_per_target 必须是正整数")
    if min_score is not None and (not isinstance(min_score, (int, float)) or min_score < 0 or min_score > 1):
        raise ValueError("min_score 必须在 0—1 之间")

    output_dir = Path(output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    local_dir = resolve_data_dir(data_dir)
    manifest, grouped = _load_manifest(local_dir)
    targets, aliases, approved_symbols = _load_targets(local_dir, grouped["target"])
    diseases_by_id = _load_diseases(local_dir, grouped["disease"])
    disease_entity_types = _load_disease_entity_types(local_dir, grouped["disease"])

    resolved = []
    unresolved = []
    input_to_target = {}
    for symbol in clean_symbols:
        approved_matches = approved_symbols.get(symbol.casefold(), set())
        matches = approved_matches or aliases.get(symbol.casefold(), set())
        if len(matches) == 1:
            target_id = next(iter(matches))
            input_to_target[symbol] = target_id
            resolved.append({"symbol": symbol, "ensembl_id": target_id,
                             "approved_symbol": targets[target_id]["approved_symbol"],
                             "mapping": "approved_symbol" if approved_matches else "target_alias"})
        elif not matches:
            unresolved.append({"gene_symbol": symbol, "reason": "target symbol or alias not found"})
        else:
            unresolved.append({"gene_symbol": symbol, "reason": "target alias is ambiguous",
                               "candidate_target_ids": sorted(matches)})
    if not resolved:
        raise RuntimeError("Open Targets 本地数据未解析任何 BATMAN 靶点")

    target_to_symbols = defaultdict(list)
    for symbol, target_id in input_to_target.items():
        target_to_symbols[target_id].append(symbol)
    wanted_targets = set(target_to_symbols)
    rows_by_target = defaultdict(list)
    rows_seen_by_target = defaultdict(int)
    association_rows_seen = 0
    parquet = _parquet()
    association_columns = [
        "targetId", "diseaseId", "aggregationType", "aggregationValue",
        "associationScore", "evidenceCount", "currentNovelty",
    ]
    for entry in grouped["association_overall_direct"]:
        reader = parquet.ParquetFile(local_dir / entry["dataset"] / entry["file"])
        for batch in reader.iter_batches(columns=association_columns, batch_size=100_000):
            for row in batch.to_pylist():
                target_id = str(row.get("targetId") or "").strip()
                if target_id not in wanted_targets:
                    continue
                association_rows_seen += 1
                rows_seen_by_target[target_id] += 1
                score = row.get("associationScore")
                if not isinstance(score, (int, float)) or not math.isfinite(float(score)) or float(score) < 0:
                    continue
                if min_score is not None and float(score) < min_score:
                    continue
                row["target_id"] = target_id
                row["source_file"] = entry["file"]
                rows_by_target[target_id].append(row)

    associations = []
    disease_keys = []
    selected_records = []
    missing_disease_ids = set()
    for target_id in sorted(wanted_targets):
        rows = sorted(rows_by_target.get(target_id, []),
                      key=lambda row: (-float(row["associationScore"]), str(row.get("diseaseId") or "")))
        if top_k_per_target is not None:
            rows = rows[:top_k_per_target]
        selected_records.append({"target_id": target_id,
                                 "input_symbols": sorted(target_to_symbols[target_id]),
                                 "association_rows_seen": rows_seen_by_target.get(target_id, 0),
                                 "selected_rows": len(rows)})
        for row in rows:
            disease_id = str(row.get("diseaseId") or "").strip()
            disease_name = diseases_by_id.get(disease_id)
            if not disease_id or not disease_name:
                if disease_id:
                    missing_disease_ids.add(disease_id)
                continue
            disease_key = disease_id + " | " + disease_name
            if disease_key not in disease_keys:
                disease_keys.append(disease_key)
            extra = {
                "data_mode": "local_snapshot",
                "direction": "target_to_disease",
                "release": manifest["release"],
                "target_id": target_id,
                "ensembl_id": target_id,
                "approved_symbol": targets[target_id]["approved_symbol"],
                "disease_id": disease_id,
                "disease_name": disease_name,
                "entity_type": disease_entity_types.get(disease_id, "phenotype"),
                "aggregation_type": row.get("aggregationType"),
                "aggregation_value": row.get("aggregationValue"),
                "association_score": row.get("associationScore"),
                "evidence_count": row.get("evidenceCount"),
                "current_novelty": row.get("currentNovelty"),
                "source_file": row.get("source_file"),
            }
            for symbol in target_to_symbols[target_id]:
                associations.append({"disease": disease_key, "gene_symbol": symbol,
                                     "score": "%.12g" % float(row["associationScore"]),
                                     "source": "open_targets",
                                     "extra": json.dumps(extra, ensure_ascii=False, sort_keys=True)})

    if not associations:
        raise RuntimeError("Open Targets 本地数据未产生有效疾病—基因关联")
    with (output_dir / "associations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "score", "source", "extra"])
        writer.writeheader()
        writer.writerows(associations)
    with (raw_dir / "open_targets_local_target_selection.jsonl").open("w", encoding="utf-8") as stream:
        for record in selected_records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (raw_dir / "unresolved_symbols.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["gene_symbol", "reason", "candidate_target_ids"])
        writer.writeheader()
        for row in unresolved:
            writer.writerow({"gene_symbol": row["gene_symbol"], "reason": row["reason"],
                             "candidate_target_ids": "|".join(row.get("candidate_target_ids", []))})

    manifest_path = local_dir / "manifest.json"
    provenance = {
        "sources": {"associations": {
            "complete": True,
            "source_url": DOWNLOADS_URL,
            "accessed_at": str(manifest.get("downloaded_at") or date.today().isoformat()).split("T", 1)[0],
            "mode": "local_snapshot",
            "direction": "target_to_disease",
            "release": manifest["release"],
            "data_dir": str(local_dir),
            "manifest_sha256": digest(manifest_path),
            "raw_files": ["raw/open_targets_local_target_selection.jsonl", "raw/unresolved_symbols.csv"],
            "dataset": "association_overall_direct",
            "dataset_files": [entry for entry in grouped["association_overall_direct"]],
            "queried_symbols": clean_symbols,
            "resolved_symbols": [item["symbol"] for item in resolved],
            "unresolved_symbols": [item["gene_symbol"] for item in unresolved],
            "missing_disease_ids": sorted(missing_disease_ids),
            "diseases": disease_keys,
            "selection": {"top_k_per_target": top_k_per_target,
                           "min_score": min_score, "raw_rows_complete": True,
                           "score_field": "associationScore",
                           "datasource_scores_available": False},
            "coverage": {"target_files": len(grouped["target"]),
                         "disease_files": len(grouped["disease"]),
                         "association_files": len(grouped["association_overall_direct"]),
                         "resolved_target_count": len(wanted_targets),
                         "association_rows_seen": association_rows_seen},
        }},
        "mapping": {
            "confirmed": True,
            "method": "Open Targets approvedSymbol plus symbolSynonyms/obsoleteSymbols/synonyms; Ensembl target IDs retained",
            "version": "Open Targets Platform release " + manifest["release"],
            "raw_files": ["raw/open_targets_local_target_selection.jsonl", "raw/unresolved_symbols.csv"],
        },
        "collection": {"started_at": started_at, "finished_at": now(),
                       "page_size": None, "association_rows": len(associations),
                       "resolved_count": len(resolved), "unresolved_count": len(unresolved),
                       "top_k_per_target": top_k_per_target, "min_score": min_score,
                       "mode": "local_snapshot", "direction": "target_to_disease"},
    }
    write_json(output_dir / "provenance.json", provenance)
    return {"output_dir": str(output_dir), "associations": len(associations),
            "diseases": len(disease_keys), "resolved": len(resolved),
            "unresolved": len(unresolved), "provenance": provenance}

