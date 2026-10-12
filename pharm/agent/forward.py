"""Forward herb -> target -> candidate disease query for the chat agent."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from ..batman.local import query_local_targets
from ..batman.formulas import SOURCE as FORMULA_SOURCE, formula_candidates, list_formulas
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


def _resolve_input(herbs, formula, text):
    """Resolve one chat text, an explicit formula or a herb list without guessing composition."""
    if sum(value is not None for value in (herbs, formula, text)) > 1:
        raise ValueError("text、formula 与 herbs 只能选择一种输入")
    if text is not None:
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError("text 必须是 1—10000 个字符的非空字符串")
        names = _clean_herbs([name.strip() for name in re.split(r"[\n,，、;；]+", text) if name.strip()])
        recognised = [name for name in names if name in list_formulas()]
        if recognised:
            if len(names) != 1:
                raise ValueError("请单独输入一个方剂名；加减方请直接输入完整的药材组合")
            formula = names[0]
        else:
            herbs = names
    if formula is not None:
        if not isinstance(formula, str) or not formula.strip():
            raise ValueError("formula 必须是非空方剂名称")
        formula = formula.strip()
        if formula not in list_formulas():
            raise ValueError("未收录的内置方剂：" + formula)
        entries = formula_candidates(formula)
        names = [canonical for canonical, _ in entries]
        return names, {canonical: candidates for canonical, candidates in entries}, formula

    names = _clean_herbs(herbs)
    return names, {name: [name] for name in names}, None


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


def _aggregate(herb_result, iter_associations, top_k):
    """Summarise rows first; load full evidence only for the selected candidates."""
    known = {row["gene_symbol"] for row in herb_result["relations"] if row.get("evidence") == "known"}
    predicted = {row["gene_symbol"] for row in herb_result["relations"] if row.get("evidence") == "predicted"} - known
    input_genes = sorted(set(herb_result["genes"]))
    associated_genes = set()
    candidates = {}
    for row in iter_associations():
        gene = str(row.get("gene_symbol") or "").strip()
        if gene:
            associated_genes.add(gene)
        disease = _normalise_disease_key(row.get("disease"))
        if not disease:
            continue
        extra = _record_extra(row)
        if extra.get("entity_type") == "phenotype":
            continue
        if not gene:
            continue
        candidate = candidates.setdefault(disease, {
            "disease": disease,
            "entity_type": "disease",
            "matched_genes": set(),
            "evidence_row_count": 0,
            "source_gene_counts": defaultdict(set),
            "source_scores": {},
        })
        candidate["matched_genes"].add(gene)
        source = str(row.get("source") or "associations")
        candidate["source_gene_counts"][source].add(gene)
        candidate["evidence_row_count"] += 1
        score = row.get("score")
        try:
            numeric_score = float(score) if str(score or "").strip() else None
        except (TypeError, ValueError):
            numeric_score = None
        if numeric_score is not None and math.isfinite(numeric_score):
            score_summary = candidate["source_scores"].setdefault(source, {"max": numeric_score, "count": 0})
            score_summary["max"] = max(score_summary["max"], numeric_score)
            score_summary["count"] += 1

    result = []
    for candidate in candidates.values():
        matched = sorted(candidate["matched_genes"])
        item = {
            "disease": candidate["disease"],
            "entity_type": candidate["entity_type"],
            "matched_genes": matched,
            "matched_count": len(matched),
            "unique_evidence_gene_count": len(matched),
            "evidence_row_count": candidate["evidence_row_count"],
            "indexed_target_count": None,
            "input_coverage": len(matched) / len(input_genes) if input_genes else 0.0,
            "disease_coverage": None,
            "source_gene_counts": {source: len(genes) for source, genes in candidate["source_gene_counts"].items()},
            "source_scores": candidate["source_scores"],
            "evidence": [],
        }
        item["confidence"] = _confidence(item, len(input_genes), known, predicted)
        result.append(item)
    result.sort(key=lambda item: (
        -float(item["confidence"]["value"]),
        -int(item["matched_count"]),
        -len(item["source_gene_counts"]),
        item["disease"],
    ))
    full_count = len(result)
    selected = result[:top_k]
    selected_by_disease = {item["disease"]: item for item in selected}
    # Release unselected sets before rereading the frozen source files.
    del candidates, result
    if selected:
        for row in iter_associations():
            disease = _normalise_disease_key(row.get("disease"))
            if disease not in selected_by_disease:
                continue
            gene = str(row.get("gene_symbol") or "").strip()
            extra = _record_extra(row)
            if not gene or extra.get("entity_type") == "phenotype":
                continue
            try:
                numeric_score = float(row["score"]) if str(row.get("score") or "").strip() else None
            except (TypeError, ValueError):
                numeric_score = None
            selected_by_disease[disease]["evidence"].append({
                "gene_symbol": gene, "disease": disease,
                "source": str(row.get("source") or "associations"),
                "relevance_score": numeric_score, "record": extra,
            })
    return selected, input_genes, full_count, associated_genes


def query(herbs=None, *, formula=None, text=None, top_k=None, batman_threshold=0.84, output_dir=None,
          monarch_data_dir=None, open_targets_data_dir=None):
    herbs, herb_candidates, formula = _resolve_input(herbs, formula, text)
    top_k = _top_k(top_k)
    if isinstance(batman_threshold, bool) or not isinstance(batman_threshold, (int, float)):
        raise ValueError("batman_threshold 必须是数值")
    if not 0 <= float(batman_threshold) <= 1:
        raise ValueError("batman_threshold 必须在 0—1 之间")

    run_id = "forward_" + uuid4().hex
    directory = Path(output_dir) if output_dir else ROOT / "local" / "agent" / "forward" / run_id
    directory = directory.resolve()
    herb_result = query_local_targets(herb_candidates, threshold=float(batman_threshold))
    if not herb_result.get("genes"):
        raise RuntimeError("输入药材没有解析出可用 BATMAN 人源靶点")

    # 只有在 BATMAN 输入验证通过后才创建本次运行目录，避免无效请求留下空目录。
    directory.mkdir(parents=True, exist_ok=False)

    source_status = {}
    association_paths = []
    monarch_rows = []
    ot_dir = directory / "open_targets"
    try:
        ot_meta = collect_open_targets_by_targets(
            herb_result["genes"], ot_dir, top_k_per_target=None, min_score=0.0,
            mode="local", data_dir=open_targets_data_dir)
        association_paths.append(ot_dir / "associations.csv")
        source_status["open_targets"] = {"status": "succeeded", "meta": ot_meta}
    except RuntimeError as exc:
        source_status["open_targets"] = {"status": "partial", "error": str(exc)}

    try:
        monarch = collect_monarch_by_targets(herb_result["genes"], data_dir=monarch_data_dir)
        monarch_rows = monarch["records"]
        source_status["monarch"] = {"status": "succeeded", "stats": monarch["stats"], "snapshot": monarch["snapshot"]}
    except RuntimeError as exc:
        source_status["monarch"] = {"status": "partial", "error": str(exc)}

    def iter_associations():
        for path in association_paths:
            yield from _read_associations(path)
        yield from monarch_rows

    selected, input_genes, full_count, associated_genes = _aggregate(herb_result, iter_associations, top_k)
    if not selected:
        raise RuntimeError("两个本地疾病关联来源均未产生可用候选疾病")
    result = {
        "workflow": "forward_herb_target_disease_v1",
        "status": "succeeded" if all(item.get("status") == "succeeded" for item in source_status.values()) else "partial",
        "scientific_complete": False,
        "created_at": now(),
        "limitation": LIMITATION,
        "input": {"text": text, "formula": formula, "formula_source": FORMULA_SOURCE if formula else None,
                  "herbs": herbs, "top_k": top_k,
                  "batman_threshold": float(batman_threshold)},
        "resolved_herbs": [
            {"canonical": herb, "batman_candidates": list(herb_candidates[herb]),
             "batman_name": herb_result.get("per_herb", {}).get(herb, {}).get("batman_name"),
             "matched": herb in herb_result.get("per_herb", {}),
             "compound_count": herb_result.get("per_herb", {}).get(herb, {}).get("compounds", 0),
             "relation_count": herb_result.get("per_herb", {}).get(herb, {}).get("relations", 0)}
            for herb in herbs
        ],
        "input_genes": input_genes,
        "matched_input_count": len(set(input_genes) & associated_genes),
        "unmatched_herbs": herb_result.get("unmatched", []),
        "unmatched_genes": sorted(set(input_genes) - associated_genes),
        "herb_relations": herb_result["relations"],
        "candidates": selected,
        "candidate_count": full_count,
        "source_status": source_status,
        "confidence_version": CONFIDENCE_VERSION,
    }
    write_json(directory / "result.json", result)
    write_json(directory / "herb_targets.json", herb_result)
    return {"run_id": run_id, "output_dir": str(directory), "result": result}

