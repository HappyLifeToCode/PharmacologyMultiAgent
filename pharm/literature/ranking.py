"""Preview-only fusion of literature evidence and target-based candidates.

The preview keeps the original Open Targets order and scores untouched. It
creates a separate literature-priority lane whose tiers are determined by
verified evidence, formula specificity, and ontology mapping quality.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from ..core.common import digest, write_json


SCHEMA_VERSION = "literature-ranking-preview-v1"
TIER_ORDER = {"P1": 1, "P2": 2, "P3": 3}
SUPPORT_ORDER = {"CTX": 1, "C": 2, "R": 3, "S1": 4, "S2": 5, "M": 6, "U": 7}


def _text(value):
    return str(value or "").strip()


def _normalise_identifier(value):
    return _text(value).replace(":", "_")


def _claim_rows(evidence_review):
    rows = defaultdict(list)
    for record in evidence_review.get("records", []):
        if (not isinstance(record, dict)
                or record.get("review_status") not in {"reviewed", "auto_verified"}):
            continue
        for claim in record.get("claims", []):
            if not isinstance(claim, dict) or not _text(claim.get("term")):
                continue
            rows[_text(claim["term"])].append({
                "record_id": record.get("record_id"),
                "rank": record.get("rank"),
                "title": record.get("title"),
                "study_design": record.get("study_design"),
                "evidence_class": record.get("evidence_class"),
                "relation": record.get("relation"),
                "direction": record.get("direction"),
                "formula_contribution": record.get("formula_contribution"),
                "treatment_context": record.get("treatment_context"),
                "excerpt": claim.get("excerpt"),
            })
    return rows


def _candidate_lookup(discovery_result):
    layers = discovery_result.get("candidate_layers") or {}
    complete = layers.get("complete") or discovery_result.get("candidates") or []
    base = layers.get("base") or []
    by_id = {}
    for candidate in complete:
        identifier = _normalise_identifier(candidate.get("disease_id"))
        if identifier:
            by_id[identifier] = candidate
    disease_base = [row for row in base if row.get("entity_type") == "disease"]
    display_ranks = {
        _normalise_identifier(row.get("disease_id")): index
        for index, row in enumerate(disease_base, 1)
        if _normalise_identifier(row.get("disease_id"))
    }
    top = list(layers.get("top") or [])
    top_snapshot = [{
        "rank": index,
        "disease_id": row.get("disease_id"),
        "disease_name": row.get("disease_name") or row.get("canonical_name") or row.get("disease"),
        "entity_type": row.get("entity_type"),
        "strict_support_target_count": row.get("strict_support_target_count", 0),
        "support_target_count": row.get("support_target_count", row.get("matched_count", 0)),
        "max_association_score": row.get("max_association_score"),
    } for index, row in enumerate(top, 1)]
    return by_id, display_ranks, top_snapshot


def _target_context(mappings, by_id, display_ranks):
    candidates = []
    for mapping in mappings:
        identifier = _text(mapping.get("entity_id")) if isinstance(mapping, dict) else ""
        normalised = _normalise_identifier(identifier)
        candidate = by_id.get(normalised)
        candidates.append({
            "entity_id": identifier,
            "entity_name": mapping.get("entity_name") if isinstance(mapping, dict) else None,
            "open_targets_disease_id": candidate.get("disease_id") if candidate else None,
            "open_targets_name": (
                candidate.get("disease_name") or candidate.get("canonical_name")
                if candidate else None
            ),
            "open_targets_display_rank": display_ranks.get(normalised),
            "strict_support_target_count": candidate.get("strict_support_target_count", 0)
            if candidate else 0,
            "support_target_count": candidate.get("support_target_count", candidate.get("matched_count", 0))
            if candidate else 0,
            "max_association_score": candidate.get("max_association_score") if candidate else None,
        })
    ranked = [row for row in candidates if row["open_targets_display_rank"] is not None]
    best = min(ranked, key=lambda row: row["open_targets_display_rank"]) if ranked else None
    return candidates, best


def _priority_tier(alignment, claims):
    concept_role = alignment.get("concept_role", "study_disease")
    status = alignment.get("alignment_status")
    if concept_role == "treatment_context":
        return "CTX", "治疗场景或使用情境，不作为独立疾病适应症排序"

    positive = [row for row in claims if row.get("direction") == "positive"]
    if any(row.get("evidence_class") == "clinical_direct"
           and row.get("formula_contribution") == "distinguishable" for row in positive):
        return "P1", "精确疾病映射 + 阳性人体原方直接证据"
    if any(row.get("evidence_class") == "clinical_cointervention"
           and row.get("formula_contribution") == "distinguishable" for row in positive):
        return "P2", "精确疾病映射 + 可区分的阳性联合/加用证据"
    if any(row.get("evidence_class") in {
            "clinical_direct", "clinical_cointervention", "secondary_review"} for row in claims):
        return "P3", "较低级人体、贡献不可完全区分或综述支持"
    if any(row.get("evidence_class") == "case_or_experience" for row in claims):
        return "C", "只有验案/经验支持，不进入文献优先区"
    if any(row.get("evidence_class") in {"preclinical", "mechanistic"} for row in claims):
        return "M", "只有前临床或计算机制证据"
    if status == "phenotype_only":
        return "S1", "只有表型映射"
    if status in {"broader_only", "related_or_partial"}:
        return "S2", "只有上位或部分映射"
    if status in {"unresolved", "rejected"}:
        return "U", "未获得安全本体映射"
    return "R", "精确映射已建立，但暂无可用的疗效证据类型"


def build_ranking_preview(discovery_result, concept_alignment, evidence_review,
                          *, max_featured=10):
    """Return a separate literature-priority lane without mutating candidates."""
    if concept_alignment.get("schema_version") != "literature-concept-alignment-v1":
        raise ValueError("术语对齐必须是 literature-concept-alignment-v1")
    if evidence_review.get("schema_version") != "literature-evidence-review-v1":
        raise ValueError("证据输入必须是 literature-evidence-review-v1")
    formula = _text(concept_alignment.get("formula"))
    if formula != _text(evidence_review.get("formula")):
        raise ValueError("术语对齐与证据结果的方剂不一致")
    if not isinstance(max_featured, int) or not 1 <= max_featured <= 50:
        raise ValueError("max_featured 必须为 1–50")

    by_id, display_ranks, top_snapshot = _candidate_lookup(discovery_result)
    claims_by_term = _claim_rows(evidence_review)
    all_rows = []
    for alignment in concept_alignment.get("terms", []):
        if not isinstance(alignment, dict) or not _text(alignment.get("term")):
            continue
        term = _text(alignment["term"])
        claims = claims_by_term.get(term, [])
        tier, reason = _priority_tier(alignment, claims)
        target_rows, best_target = _target_context(
            alignment.get("promotion_targets", []), by_id, display_ranks)
        evidence_counts = dict(sorted(Counter(row.get("evidence_class") for row in claims).items()))
        positive_primary = sum(
            row.get("direction") == "positive"
            and row.get("evidence_class") in {"clinical_direct", "clinical_cointervention"}
            for row in claims
        )
        direct_priority = 0 if any(row.get("evidence_class") == "clinical_direct" for row in claims) else 1
        all_rows.append({
            "term": term,
            "concept_role": alignment.get("concept_role", "study_disease"),
            "tier": tier,
            "tier_reason": reason,
            "alignment_status": alignment.get("alignment_status"),
            "mapping_review_status": alignment.get("review_status"),
            "evidence_record_count": len({row.get("record_id") for row in claims}),
            "evidence_classes": evidence_counts,
            "positive_primary_record_count": positive_primary,
            "claims": claims,
            "promotion_targets": alignment.get("promotion_targets", []),
            "ontology_merge_eligible": bool(alignment.get("promotion_targets")),
            "target_candidates": target_rows,
            "open_targets_display_rank": best_target.get("open_targets_display_rank") if best_target else None,
            "open_targets_disease_id": best_target.get("open_targets_disease_id") if best_target else None,
            "open_targets_name": best_target.get("open_targets_name") if best_target else None,
            "strict_support_target_count": best_target.get("strict_support_target_count", 0) if best_target else 0,
            "support_target_count": best_target.get("support_target_count", 0) if best_target else 0,
            "max_association_score": best_target.get("max_association_score") if best_target else None,
            "_direct_priority": direct_priority,
        })

    priority = [row for row in all_rows if row["tier"] in TIER_ORDER]
    priority.sort(key=lambda row: (
        TIER_ORDER[row["tier"]],
        row["_direct_priority"],
        -row["positive_primary_record_count"],
        -min(row["evidence_record_count"], 3),
        -row["strict_support_target_count"],
        row["open_targets_display_rank"] if row["open_targets_display_rank"] is not None else 10**9,
        row["term"],
    ))
    support = [row for row in all_rows if row["tier"] not in TIER_ORDER]
    support.sort(key=lambda row: (
        SUPPORT_ORDER.get(row["tier"], 99),
        -row["evidence_record_count"], row["term"],
    ))
    for rows in (priority, support):
        for row in rows:
            row.pop("_direct_priority", None)
    featured = priority[:max_featured]
    return {
        "schema_version": SCHEMA_VERSION,
        "formula": formula,
        "status": "preview",
        "ranking_effect": "none",
        "featured_limit": max_featured,
        "literature_priority": featured,
        "literature_priority_overflow": priority[max_featured:],
        "supporting_terms": support,
        "original_open_targets_top": top_snapshot,
        "comparison": {
            "literature_priority_count": len(priority),
            "featured_count": len(featured),
            "featured_outside_open_targets_top_count": sum(
                row["open_targets_display_rank"] is None
                or row["open_targets_display_rank"] > len(top_snapshot)
                for row in featured
            ),
            "original_open_targets_top_count": len(top_snapshot),
            "original_top_preserved": True,
        },
        "policy": {
            "P1": "positive distinguishable direct original-formula clinical evidence",
            "P2": "positive distinguishable add-on/cointervention clinical evidence",
            "P3": "lower-tier human, partially distinguishable, or review support",
            "ontology_merge": "exact reviewed disease mapping controls Open Targets merge, not literature priority",
            "CTX": "treatment context or application scenario; not ranked as an independent indication",
            "C": "case/experience only; supporting display without promotion",
            "S1": "phenotype only; display outside disease ranking",
            "S2": "broader/partial mapping; no automatic promotion",
            "M": "preclinical/mechanistic only; no clinical promotion",
            "R": "manual mapping/evidence review required",
            "U": "unresolved concept",
            "within_tier": "direct formula, positive primary count, capped independent records, strict target support, original target rank",
            "open_targets": "original order and association scores preserved",
        },
    }


def save_ranking_preview(result, output_dir, *, input_paths=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    result_path = output_dir / "literature_ranking_preview.json"
    csv_path = output_dir / "literature_priority.csv"
    report_path = output_dir / "report.md"
    write_json(result_path, result)
    fields = (
        "display_rank", "term", "concept_role", "tier", "tier_reason", "alignment_status",
        "evidence_record_count", "evidence_classes", "open_targets_display_rank",
        "open_targets_disease_id", "open_targets_name", "strict_support_target_count",
        "support_target_count", "max_association_score",
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, row in enumerate(result["literature_priority"], 1):
            writer.writerow({
                **{key: row.get(key) for key in fields},
                "display_rank": index,
                "evidence_classes": ";".join(
                    f"{key}:{value}" for key, value in row["evidence_classes"].items()),
            })
    lines = [
        f"# {result['formula']} 文献排序预览", "",
        "本文件仅验证展示排序，不修改 Open Targets 原始分数或 Top 顺序。", "",
        "| 预览排名 | 术语 | 等级 | OT 疾病名次 | 证据数 | 原因 |",
        "| ---: | --- | --- | ---: | ---: | --- |",
    ]
    for index, row in enumerate(result["literature_priority"], 1):
        lines.append(
            f"| {index} | {row['term']} | {row['tier']} | "
            f"{row['open_targets_display_rank'] or '—'} | {row['evidence_record_count']} | "
            f"{row['tier_reason']} |"
        )
    lines.extend(["", "## 原 Open Targets Top", "",
                  "| 排名 | 疾病 | disease_id |", "| ---: | --- | --- |"])
    for row in result["original_open_targets_top"]:
        lines.append(f"| {row['rank']} | {row['disease_name']} | {row['disease_id']} |")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "formula": result["formula"],
        "ranking_effect": "none",
        "original_top_preserved": result["comparison"]["original_top_preserved"],
        "inputs": {},
        "files": {path.name: digest(path) for path in (result_path, csv_path, report_path)},
    }
    for name, path in (input_paths or {}).items():
        if path:
            manifest["inputs"][name] = {
                "path": str(Path(path).resolve()), "sha256": digest(path),
            }
    write_json(output_dir / "manifest.json", manifest)
    return output_dir
