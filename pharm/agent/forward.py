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
from ..batman.resolver import query_targets
from ..batman.formulas import SOURCE as FORMULA_SOURCE, formula_candidates, list_formulas
from ..batman import formula_catalog
from ..core.common import ROOT, now, write_json
from ..discovery.query import (
    BASE_ASSOCIATION_SCORE,
    CONFIDENCE_VERSION,
    STRICT_ASSOCIATION_SCORE,
    _confidence,
    candidate_layer_sort_key,
)
from ..diseases.monarch_local import collect_monarch_by_targets
from ..diseases.open_targets import collect_open_targets_by_targets
from .progress import report
from . import disease_background


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


def _resolve_input(herbs, formula, text, **selection):
    """Resolve one chat text, an explicit formula or a herb list without guessing composition."""
    resolved = formula_catalog.resolve(herbs, formula, text, **selection)
    if resolved["status"] != "ready":
        raise formula_catalog.SelectionRequired(resolved)
    return resolved["herbs"], resolved["herb_candidates"], resolved["formula"]


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


def _aggregate(herb_result, iter_associations, top_k, iter_selected_associations=None, *, background=None, selection=None, audit=None):
    """Summarise rows first; load full evidence only for the selected candidates."""
    known = {row["gene_symbol"] for row in herb_result["relations"] if row.get("evidence") == "known"}
    predicted = {row["gene_symbol"] for row in herb_result["relations"] if row.get("evidence") == "predicted"} - known
    input_genes = sorted(set(herb_result["genes"]))
    associated_genes = set()
    candidates = {}
    for row_number, row in enumerate(iter_associations(), 1):
        if row_number % 100000 == 0:
            report("aggregating", "汇总候选疾病", current=row_number, unit="条关联")
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
            "base_supported_genes": set(),
            "strict_supported_genes": set(),
            "evidence_row_count": 0,
            "source_gene_counts": defaultdict(set),
            "source_scores": {},
            "max_association_score": None,
            "score_sum": 0.0,
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
        # Monarch is an OMIM-like evidence supplement. Only Open Targets scores
        # determine the base/strict reference layers, not the default order.
        if source == "open_targets" and numeric_score is not None and math.isfinite(numeric_score):
            candidate["max_association_score"] = numeric_score if candidate["max_association_score"] is None else max(
                candidate["max_association_score"], numeric_score)
            candidate["score_sum"] += numeric_score
            if numeric_score > BASE_ASSOCIATION_SCORE:
                candidate["base_supported_genes"].add(gene)
            if numeric_score >= STRICT_ASSOCIATION_SCORE:
                candidate["strict_supported_genes"].add(gene)

    prepared = disease_background.prepare_inputs(input_genes, background) if background else None
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
            "support_target_count": len(candidate["base_supported_genes"]),
            "strict_support_target_count": len(candidate["strict_supported_genes"]),
            "strict_supported": bool(candidate["strict_supported_genes"]),
            "max_association_score": candidate["max_association_score"],
            "score_sum": round(candidate["score_sum"], 12),
            "evidence": [],
        }
        item["confidence"] = _confidence(item, len(input_genes), known, predicted)
        scope, specificity = disease_background.describe(
            candidate["disease"], candidate["source_gene_counts"].get("open_targets", set()), input_genes, background, prepared=prepared)
        item.update(scope, specificity=specificity)
        result.append(item)
    result.sort(key=lambda item: (-float(item["confidence"]["value"]), -item["matched_count"],
                                 -len(item["source_gene_counts"]), item["disease"]))
    full_count = len(result)
    base = [item for item in result if item["support_target_count"] > 0]
    layered = sorted(base, key=candidate_layer_sort_key)[:top_k]
    layer_counts = {"complete": full_count, "base": len(base),
                    "strict": sum(item["strict_supported"] for item in base), "top": len(layered)}
    for rank, item in enumerate(result, 1):
        item["raw_rank"] = rank
    raw_ranking = [{key: item[key] for key in ("disease", "disease_id", "raw_rank", "matched_count", "confidence", "disease_scope")}
                   for item in result[:top_k]]
    categories = [item for item in result if item["disease_scope"] == "category"]
    specific = [item for item in result if item["disease_scope"] != "category"]
    selected, broad = specific[:top_k], categories[:top_k]
    if audit is not None:
        audit.update({item["disease"]: {"disease_id": item["disease_id"], "support_rank": rank,
                      "background_rank": None, "support_score": item["confidence"]["value"],
                      "background_score": (item["specificity"] or {}).get("score")}
                      for rank, item in enumerate(specific, 1)})
    if background:
        specific.sort(key=lambda item: (
            item["specificity"] is None, -(item["specificity"] or {}).get("score", 0),
            -float(item["confidence"]["value"]), -item["matched_count"], item["disease"],
        ))
    alternative = specific[:top_k] if background else []
    if audit is not None and background:
        for rank, item in enumerate(specific, 1):
            audit[item["disease"]]["background_rank"] = rank
    if selection is not None:
        selection.update(broad_candidates=broad, raw_ranking=raw_ranking,
                         background_candidates=alternative,
                         layered_candidates=layered, layer_counts=layer_counts,
                         specific_candidate_count=len(specific), category_count=len(categories))
    selected_by_disease = {item["disease"]: item for item in selected + alternative + broad + layered}
    report("evidence", "整理两种排序的完整证据", detail=f"默认 {len(selected)} 项，背景对照 {len(alternative)} 项，大类 {len(broad)} 项")
    # Release unselected sets before rereading the frozen source files.
    del candidates, result, specific, categories, base
    if selected_by_disease:
        selected_rows = iter_selected_associations(set(selected_by_disease)) if iter_selected_associations is not None else iter_associations()
        for row in selected_rows:
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
          monarch_data_dir=None, open_targets_data_dir=None, run_id=None,
          formula_variant_id=None, formula_herbs=None, formula_fingerprint=None):
    report("input", "核对方剂或药材输入")
    resolved = formula_catalog.resolve(herbs, formula, text, formula_variant_id=formula_variant_id,
                                       formula_herbs=formula_herbs, formula_fingerprint=formula_fingerprint)
    if resolved["status"] != "ready":
        raise formula_catalog.SelectionRequired(resolved)
    herbs, herb_candidates, formula = resolved["herbs"], resolved["herb_candidates"], resolved["formula"]
    top_k = _top_k(top_k)
    if isinstance(batman_threshold, bool) or not isinstance(batman_threshold, (int, float)):
        raise ValueError("batman_threshold 必须是数值")
    if not 0 <= float(batman_threshold) <= 1:
        raise ValueError("batman_threshold 必须在 0—1 之间")

    run_id = run_id or "forward_" + uuid4().hex
    directory = Path(output_dir) if output_dir else ROOT / "local" / "agent" / "forward" / run_id
    directory = directory.resolve()
    report("batman", "解析 BATMAN 药材成分与已知靶点")
    herb_result = query_targets(herb_candidates, threshold=float(batman_threshold), local_query=query_local_targets)
    if not herb_result.get("genes"):
        raise RuntimeError("输入药材没有解析出可用 BATMAN 人源靶点")

    # 只有在 BATMAN 输入验证通过后才创建本次运行目录，避免无效请求留下空目录。
    directory.mkdir(parents=True, exist_ok=False)

    source_status = {}
    association_paths = []
    monarch_rows = []
    ot_stream = None
    ot_dir = directory / "open_targets"
    report("open_targets", "读取 Open Targets 靶点关联")
    try:
        ot_source = collect_open_targets_by_targets(
            herb_result["genes"], ot_dir, top_k_per_target=None, min_score=None,
            mode="local", data_dir=open_targets_data_dir, stream=True)
        if hasattr(ot_source, "iter_rows") and hasattr(ot_source, "metadata"):
            ot_stream = ot_source
            source_status["open_targets"] = {"status": "succeeded", "meta": None}
        else:
            # Compatibility path for injected collectors and explicit legacy
            # adapters that still materialise the generic CSV contract.
            association_paths.append(ot_dir / "associations.csv")
            source_status["open_targets"] = {"status": "succeeded", "meta": ot_source}
    except RuntimeError as exc:
        source_status["open_targets"] = {"status": "partial", "error": str(exc)}

    report("monarch", "补充 Monarch 遗传学证据")
    try:
        monarch = collect_monarch_by_targets(herb_result["genes"], data_dir=monarch_data_dir)
        monarch_rows = monarch["records"]
        source_status["monarch"] = {"status": "succeeded", "stats": monarch["stats"], "snapshot": monarch["snapshot"]}
    except RuntimeError as exc:
        source_status["monarch"] = {"status": "partial", "error": str(exc)}

    def iter_associations():
        if ot_stream is not None:
            yield from ot_stream.iter_rows()
        for path in association_paths:
            yield from _read_associations(path)
        yield from monarch_rows

    background, background_error = None, None
    ot_status = source_status.get("open_targets", {})
    metadata = (ot_status.get("meta") or {}).get("provenance", {}).get("sources", {}).get("associations", {})
    if ot_status.get("status") == "succeeded" and (getattr(ot_stream, "local_dir", None) is not None or metadata.get("mode") == "local_snapshot"):
        try:
            background = disease_background.load(open_targets_data_dir)
        except (RuntimeError, OSError, ValueError) as exc:
            background_error = str(exc)
    else:
        background_error = "完整 Open Targets 背景不可用，本次只分出已登记大类，沿用原支持度排序"
    selection = {}
    def iter_selected_associations(selected_diseases):
        if ot_stream is not None:
            yield from ot_stream.iter_rows(selected_diseases=selected_diseases, full_extra=True)
        for path in association_paths:
            for row in _read_associations(path):
                if _normalise_disease_key(row.get("disease")) in selected_diseases:
                    yield row
        for row in monarch_rows:
            if _normalise_disease_key(row.get("disease")) in selected_diseases:
                yield row

    report("aggregating", "汇总候选疾病")
    selected, input_genes, full_count, associated_genes = _aggregate(
        herb_result, iter_associations, top_k, iter_selected_associations,
        background=background, selection=selection)
    layer_counts = selection.pop("layer_counts")
    if ot_stream is not None:
        source_status["open_targets"]["meta"] = ot_stream.metadata()
    if not selected and not selection["broad_candidates"]:
        raise RuntimeError("两个本地疾病关联来源均未产生可用候选疾病")
    result = {
        "workflow": "forward_herb_target_disease_v1",
        "status": "succeeded" if all(item.get("status") == "succeeded" for item in source_status.values()) else "partial",
        "scientific_complete": False,
        "created_at": now(),
        "limitation": LIMITATION,
        "input": {"text": text, "formula": formula,
                  "formula_source": resolved["resolution"].get("source") if formula else None,
                  "formula_resolution": resolved["resolution"],
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
        "batman_source": herb_result.get("retrieval", {"mode": "local"}),
        "candidates": selected,
        "candidate_count": full_count,
        "candidate_layers": {
            "counts": layer_counts,
            "purpose": "reference_only; default_order=support",
            "rules": {"base_score": "> %.6g" % BASE_ASSOCIATION_SCORE,
                      "strict_score": ">= %.6g" % STRICT_ASSOCIATION_SCORE,
                      "ranking": ["strict_supported", "strict_support_target_count",
                                  "support_target_count", "max_association_score", "score_sum", "disease_id"],
                      "monarch_ranking": "supplement_only"},
        },
        "source_status": source_status,
        "confidence_version": CONFIDENCE_VERSION,
        **selection,
        "ranking": {
            "version": "forward_support_comparison_v1",
            "default_order": "support",
            "available_orders": ["support", "background"] if background else ["support"],
            "background_algorithm": disease_background.VERSION if background else None,
            "scientific_validation": "pending_literature_benchmark",
            "background_status": "succeeded" if background else "unavailable",
            "background_error": background_error,
            "source_fingerprint": background.get("fingerprint") if background else None,
            "release": background.get("release") if background else None,
            "background_disease_count": background.get("disease_count") if background else None,
            "background_gene_count": background.get("gene_count") if background else None,
            "note": "非大类候选默认按原支持度排列；背景校正为探索性对照，可切换查看完整 Top-K，尚未完成文献案例有效性验证。两种指标均不是疗效概率。" if background else "默认候选已分出登记大类，按原支持度排列；完整背景不可用，背景排序暂不可选。",
            "category_policy": "仅 Open Targets therapeutic area 本身及明确登记的肿瘤/癌症大类；不因是其后代而排除，不合并疾病 ID、来源分数或父子证据",
            "coverage_scope": "specificity.disease_coverage 只用 Open Targets 唯一批准基因计数；不以输入靶点交集冒充完整疾病分母，不混入 Monarch 分值",
        },
    }
    report("writing", "保存查询结果与来源记录")
    write_json(directory / "result.json", result)
    write_json(directory / "herb_targets.json", herb_result)
    return {"run_id": run_id, "output_dir": str(directory), "result": result}

