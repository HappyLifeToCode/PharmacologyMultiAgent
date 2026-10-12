"""Forward herb -> target -> candidate disease query for the chat agent."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from ..batman.local import query_local_targets
from ..core.common import ROOT, now, write_json
from ..discovery.query import CONFIDENCE_VERSION, _confidence
from ..diseases.monarch_local import collect_monarch_by_targets
from ..diseases.open_targets import collect_open_targets_by_targets


LIMITATION = (
    "候选疾病来自药材靶点与疾病关联证据的交集；结果是候选关联，不是疗效、"
    "因果影响或治疗推荐。Open Targets 与 Monarch 的原始分值不跨来源合并。"
)
DEFAULT_TOP_K = 10
MAX_TOP_K = 100


def _clean_herbs(herbs):
    if not isinstance(herbs, list) or not 1 <= len(herbs) <= 30:
        raise ValueError("herbs 必须是 1—30 个药材名称")
    values, seen = [], set()
    for value in herbs:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 300:
            raise ValueError("每个药材名称必须是 1—300 个字符的非空字符串")
        value = value.strip()
        if value.casefold() not in seen:
            values.append(value)
            seen.add(value.casefold())
    return values


def _top_k(value):
    if value is None:
        return DEFAULT_TOP_K
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TOP_K:
        raise ValueError("top_k 必须是 1—100 的整数")
    return value


def _normalise_disease_key(value):
    value = str(value or "").strip()
    if " | " not in value:
        return value
    identifier, name = value.split(" | ", 1)
    for prefix in ("MONDO", "EFO", "OBA", "HP", "DOID", "Orphanet", "DECIPHER"):
        marker = prefix + "_"
        if identifier.startswith(marker):
            identifier = prefix + ":" + identifier[len(marker):]
            break
    return identifier + " | " + name


def _read_associations(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def _record_extra(row):
    value = row.get("extra")
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _aggregate(herb_result, association_rows):
    known = {row["gene_symbol"] for row in herb_result["relations"] if row.get("evidence") == "known"}
    predicted = {row["gene_symbol"] for row in herb_result["relations"] if row.get("evidence") == "predicted"} - known
    input_genes = sorted(set(herb_result["genes"]))
    candidates = {}
    for row in association_rows:
        disease = _normalise_disease_key(row.get("disease"))
        if not disease:
            continue
        extra = _record_extra(row)
        if extra.get("entity_type") == "phenotype":
            continue
        gene = str(row.get("gene_symbol") or "").strip()
        if not gene:
            continue
        candidate = candidates.setdefault(disease, {
            "disease": disease,
            "entity_type": "disease",
            "matched_genes": set(),
            "evidence": [],
            "source_gene_counts": defaultdict(set),
            "source_scores": defaultdict(list),
        })
        candidate["matched_genes"].add(gene)
        source = str(row.get("source") or "associations")
        candidate["source_gene_counts"][source].add(gene)
        score = row.get("score")
        try:
            numeric_score = float(score) if str(score or "").strip() else None
        except (TypeError, ValueError):
            numeric_score = None
        if numeric_score is not None and math.isfinite(numeric_score):
            candidate["source_scores"][source].append(numeric_score)
        candidate["evidence"].append({
            "gene_symbol": gene,
            "disease": disease,
            "source": source,
            "relevance_score": numeric_score,
            "record": extra,
        })

    result = []
    for candidate in candidates.values():
        matched = sorted(candidate["matched_genes"])
        item = {
            "disease": candidate["disease"],
            "entity_type": candidate["entity_type"],
            "matched_genes": matched,
            "matched_count": len(matched),
            "unique_evidence_gene_count": len(matched),
            "evidence_row_count": len(candidate["evidence"]),
            "indexed_target_count": None,
            "input_coverage": len(matched) / len(input_genes) if input_genes else 0.0,
            "disease_coverage": None,
            "source_gene_counts": {source: len(genes) for source, genes in candidate["source_gene_counts"].items()},
            "source_scores": {
                source: {"max": max(scores), "count": len(scores)}
                for source, scores in candidate["source_scores"].items()
            },
            "evidence": candidate["evidence"],
        }
        item["confidence"] = _confidence(item, len(input_genes), known, predicted)
        result.append(item)
    result.sort(key=lambda item: (
        -float(item["confidence"]["value"]),
        -int(item["matched_count"]),
        -len(item["source_gene_counts"]),
        item["disease"],
    ))
    for item in result:
        item["source_gene_counts"] = dict(item["source_gene_counts"])
    return result, input_genes


def query(herbs, *, top_k=None, batman_threshold=0.84, output_dir=None,
          monarch_data_dir=None, open_targets_data_dir=None):
    herbs = _clean_herbs(herbs)
    top_k = _top_k(top_k)
    if isinstance(batman_threshold, bool) or not isinstance(batman_threshold, (int, float)):
        raise ValueError("batman_threshold 必须是数值")
    if not 0 <= float(batman_threshold) <= 1:
        raise ValueError("batman_threshold 必须在 0—1 之间")

    run_id = "forward_" + uuid4().hex
    directory = Path(output_dir) if output_dir else ROOT / "local" / "agent" / "forward" / run_id
    directory = directory.resolve()
    herb_candidates = {herb: [herb] for herb in herbs}
    herb_result = query_local_targets(herb_candidates, threshold=float(batman_threshold))
    if not herb_result.get("genes"):
        raise RuntimeError("输入药材没有解析出可用 BATMAN 人源靶点")

    # 只有在 BATMAN 输入验证通过后才创建本次运行目录，避免无效请求留下空目录。
    directory.mkdir(parents=True, exist_ok=False)

    source_status = {}
    association_rows = []
    ot_dir = directory / "open_targets"
    try:
        ot_meta = collect_open_targets_by_targets(
            herb_result["genes"], ot_dir, top_k_per_target=None, min_score=0.0,
            mode="local", data_dir=open_targets_data_dir)
        association_rows.extend(_read_associations(ot_dir / "associations.csv"))
        source_status["open_targets"] = {"status": "succeeded", "meta": ot_meta}
    except RuntimeError as exc:
        source_status["open_targets"] = {"status": "partial", "error": str(exc)}

    try:
        monarch = collect_monarch_by_targets(herb_result["genes"], data_dir=monarch_data_dir)
        association_rows.extend(monarch["records"])
        source_status["monarch"] = {"status": "succeeded", "stats": monarch["stats"], "snapshot": monarch["snapshot"]}
    except RuntimeError as exc:
        source_status["monarch"] = {"status": "partial", "error": str(exc)}

    candidates, input_genes = _aggregate(herb_result, association_rows)
    if not candidates:
        raise RuntimeError("两个本地疾病关联来源均未产生可用候选疾病")
    full_count = len(candidates)
    selected = candidates[:top_k]
    result = {
        "workflow": "forward_herb_target_disease_v1",
        "status": "succeeded" if all(item.get("status") == "succeeded" for item in source_status.values()) else "partial",
        "scientific_complete": False,
        "created_at": now(),
        "limitation": LIMITATION,
        "input": {"herbs": herbs, "top_k": top_k, "batman_threshold": float(batman_threshold)},
        "input_genes": input_genes,
        "matched_input_count": len({row["gene_symbol"] for row in association_rows if row.get("gene_symbol") in input_genes}),
        "unmatched_herbs": herb_result.get("unmatched", []),
        "unmatched_genes": sorted(set(input_genes) - {row["gene_symbol"] for row in association_rows}),
        "herb_relations": herb_result["relations"],
        "candidates": selected,
        "candidate_count": full_count,
        "source_status": source_status,
        "confidence_version": CONFIDENCE_VERSION,
    }
    write_json(directory / "result.json", result)
    write_json(directory / "herb_targets.json", herb_result)
    return {"run_id": run_id, "output_dir": str(directory), "result": result}

