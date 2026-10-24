"""Audited abstract evidence for screened original-formula literature.

This layer records what a paper actually studies and how strongly the abstract
supports a formula--disease relation. It never changes Open Targets scores or
candidate order. Evidence claims require an exact excerpt from the archived
abstract so a later reviewer can audit every classification.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from ..core.common import digest, write_json


SCHEMA_VERSION = "literature-evidence-review-v1"
CATALOG_SCHEMA_VERSION = "literature-evidence-catalog-v1"
REVIEW_STATUSES = {"reviewed", "auto_verified", "pending", "rejected"}
VERIFIED_STATUSES = {"reviewed", "auto_verified"}
STUDY_DESIGNS = {
    "randomized_controlled_trial", "controlled_clinical_study",
    "uncontrolled_clinical_study", "systematic_review", "narrative_review",
    "case_report_or_experience", "animal_experiment", "cell_experiment",
    "computational_study", "historical_or_textual", "general_article", "unclear",
}
RELATIONS = {
    "direct_treatment", "cointervention_treatment", "preclinical_treatment",
    "mechanism_only", "historical_or_theoretical", "background_only",
}
DIRECTIONS = {"positive", "mixed", "null", "negative", "unclear", "not_applicable"}
CONTRIBUTIONS = {
    "distinguishable", "partially_distinguishable", "not_distinguishable",
    "not_applicable",
}


def _text(value):
    return str(value or "").strip()


def _records(normalized):
    if not isinstance(normalized, dict) or normalized.get("schema_version") != "literature-search-v1":
        raise ValueError("文献输入必须是 literature-search-v1")
    records = {}
    for source in normalized.get("source_results", []):
        if not isinstance(source, dict):
            continue
        for record in source.get("records", []):
            if not isinstance(record, dict) or not _text(record.get("record_id")):
                continue
            identifier = _text(record["record_id"])
            if identifier in records:
                raise ValueError("规范化文献包含重复 record_id：" + identifier)
            records[identifier] = record
    return records


def _evidence_class(design, relation):
    clinical_designs = {
        "randomized_controlled_trial", "controlled_clinical_study",
        "uncontrolled_clinical_study",
    }
    if relation == "direct_treatment" and design in clinical_designs:
        return "clinical_direct"
    if relation == "cointervention_treatment" and design in clinical_designs:
        return "clinical_cointervention"
    if design in {"systematic_review", "narrative_review"}:
        return "secondary_review"
    if design in {"animal_experiment", "cell_experiment"}:
        return "preclinical"
    if design == "computational_study" or relation == "mechanism_only":
        return "mechanistic"
    if design == "case_report_or_experience":
        return "case_or_experience"
    if design in {"historical_or_textual", "general_article"} or relation in {
            "historical_or_theoretical", "background_only"}:
        return "contextual"
    return "other"


def _alignment_map(alignment):
    if alignment is None:
        return {}
    if not isinstance(alignment, dict) or alignment.get("schema_version") != "literature-concept-alignment-v1":
        raise ValueError("术语对齐输入必须是 literature-concept-alignment-v1")
    return {item["term"]: item for item in alignment.get("terms", [])
            if isinstance(item, dict) and _text(item.get("term"))}


def review_evidence(normalized, screening, catalog, *, concept_alignment=None):
    """Apply a human-reviewed or deterministic auto-verified evidence catalog."""
    if not isinstance(screening, dict) or screening.get("schema_version") != "literature-screening-v1":
        raise ValueError("筛选输入必须是 literature-screening-v1")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("证据目录必须是 literature-evidence-catalog-v1")
    formula = _text(screening.get("formula"))
    if _text(catalog.get("formula")) not in {"", formula}:
        raise ValueError("证据目录方剂与筛选结果不一致")

    normalized_records = _records(normalized)
    included = {
        _text(item.get("record_id")): item for item in screening.get("records", [])
        if isinstance(item, dict)
        and item.get("screening_decision") == "included_original_formula"
        and _text(item.get("record_id"))
    }
    alignments = _alignment_map(concept_alignment)
    catalog_entries = catalog.get("entries")
    if not isinstance(catalog_entries, list):
        raise ValueError("证据目录缺少 entries 数组")
    by_record = {}
    for entry in catalog_entries:
        identifier = _text(entry.get("record_id") if isinstance(entry, dict) else None)
        if not identifier or identifier in by_record:
            raise ValueError("证据目录包含空或重复 record_id")
        if identifier not in included:
            raise ValueError("证据目录引用了未纳入原方集的记录：" + identifier)
        by_record[identifier] = entry

    reviewed_records = []
    term_support = defaultdict(list)
    for identifier, screened in sorted(included.items(), key=lambda pair: pair[1].get("rank") or 0):
        source = normalized_records.get(identifier)
        if source is None:
            raise ValueError("筛选记录在规范化文献中不存在：" + identifier)
        entry = by_record.get(identifier, {
            "review_status": "pending", "claims": [], "note": "证据目录未登记该文献",
        })
        status = entry.get("review_status", "pending")
        if status not in REVIEW_STATUSES:
            raise ValueError("不支持的 review_status：" + str(status))
        if status in VERIFIED_STATUSES:
            missing = [key for key in (
                "study_design", "relation", "direction", "formula_contribution", "claims",
            ) if key not in entry]
            if missing:
                raise ValueError("已复核证据记录缺少字段：" + "、".join(missing))
        design = entry.get("study_design", "unclear")
        relation = entry.get("relation", "background_only")
        direction = entry.get("direction", "unclear")
        contribution = entry.get("formula_contribution", "not_applicable")
        for value, allowed, label in (
                (design, STUDY_DESIGNS, "study_design"),
                (relation, RELATIONS, "relation"),
                (direction, DIRECTIONS, "direction"),
                (contribution, CONTRIBUTIONS, "formula_contribution")):
            if value not in allowed:
                raise ValueError(f"不支持的 {label}：{value}")
        evidence_class = _evidence_class(design, relation)
        labels = set(screened.get("disease_or_symptom_labels", []))
        raw_claims = entry.get("claims", [])
        if not isinstance(raw_claims, list):
            raise ValueError("证据 claims 必须是数组")
        claims = []
        for claim in raw_claims:
            if not isinstance(claim, dict):
                raise ValueError("证据 claim 必须是对象")
            term = _text(claim.get("term"))
            excerpt = _text(claim.get("excerpt"))
            if not term or term not in labels:
                raise ValueError(f"证据术语不在筛选标签中：{term or '<empty>'}")
            if not excerpt or excerpt not in _text(source.get("abstract")):
                raise ValueError(f"证据引文不是存档摘要的原文子串：{identifier}")
            alignment = alignments.get(term)
            promotions = list((alignment or {}).get("promotion_targets", []))
            signal_eligible = bool(
                status in VERIFIED_STATUSES
                and direction == "positive"
                and evidence_class in {"clinical_direct", "clinical_cointervention"}
                and contribution == "distinguishable"
            )
            item = {
                "term": term,
                "excerpt": excerpt,
                "concept_alignment_status": (alignment or {}).get("alignment_status", "not_aligned"),
                "promotion_targets": promotions,
                "ontology_merge_eligible": bool(promotions),
                "candidate_signal_eligible": signal_eligible,
                "candidate_signal_role": (
                    "direct_original_formula" if evidence_class == "clinical_direct"
                    else "add_on_or_cointervention" if evidence_class == "clinical_cointervention"
                    else "supporting_only"
                ),
            }
            claims.append(item)
            term_support[term].append({
                "record_id": identifier, "rank": screened.get("rank"),
                "evidence_class": evidence_class, "direction": direction,
                "formula_contribution": contribution,
                "candidate_signal_eligible": signal_eligible,
                "ontology_merge_eligible": bool(promotions),
                "candidate_signal_role": item["candidate_signal_role"],
            })
        reviewed_records.append({
            "record_id": identifier,
            "rank": screened.get("rank"),
            "title": screened.get("title"),
            "treatment_context": screened.get("treatment_context"),
            "review_status": status,
            "study_design": design,
            "relation": relation,
            "direction": direction,
            "formula_contribution": contribution,
            "evidence_class": evidence_class,
            "claims": claims,
            "note": _text(entry.get("note")) or None,
        })

    term_summaries = []
    for term in sorted(term_support):
        rows = term_support[term]
        term_summaries.append({
            "term": term,
            "record_count": len({row["record_id"] for row in rows}),
            "evidence_classes": dict(sorted(Counter(row["evidence_class"] for row in rows).items())),
            "directions": dict(sorted(Counter(row["direction"] for row in rows).items())),
            "candidate_signal_record_ids": [row["record_id"] for row in rows
                                            if row["candidate_signal_eligible"]],
            "candidate_signal_eligible": any(row["candidate_signal_eligible"] for row in rows),
            "records": rows,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "formula": formula,
        "included_record_count": len(included),
        "reviewed_record_count": sum(row["review_status"] == "reviewed" for row in reviewed_records),
        "auto_verified_record_count": sum(row["review_status"] == "auto_verified"
                                          for row in reviewed_records),
        "verified_record_count": sum(row["review_status"] in VERIFIED_STATUSES
                                     for row in reviewed_records),
        "evidence_class_counts": dict(sorted(Counter(
            row["evidence_class"] for row in reviewed_records).items())),
        "candidate_signal_term_count": sum(row["candidate_signal_eligible"] for row in term_summaries),
        "records": reviewed_records,
        "term_summaries": term_summaries,
        "policy": {
            "claim_provenance": "each claim excerpt must be an exact substring of the archived abstract",
            "modified_formula": "excluded upstream by literature-screening-v1",
            "cointervention": "kept separate from original-formula-alone evidence",
            "candidate_signal": "verified positive primary clinical evidence + distinguishable formula contribution",
            "ontology_merge": "exact reviewed disease mapping is required only to merge with Open Targets",
            "ranking_effect": "none in this stage",
            "clinical_interpretation": "literature evidence candidate; not a confirmed indication",
        },
    }


CSV_FIELDS = (
    "rank", "record_id", "review_status", "study_design", "relation", "direction",
    "formula_contribution", "evidence_class", "treatment_context", "title", "terms", "note",
)


def save_evidence_review(result, output_dir, *, normalized_path=None, screening_path=None,
                         catalog_path=None, alignment_path=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "literature_evidence_review.json"
    records_path = output_dir / "literature_evidence_records.csv"
    terms_path = output_dir / "literature_term_evidence.json"
    write_json(result_path, result)
    write_json(terms_path, {
        "schema_version": SCHEMA_VERSION,
        "formula": result["formula"],
        "term_summaries": result["term_summaries"],
        "ranking_effect": "none",
    })
    with records_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in result["records"]:
            writer.writerow({
                **{key: record.get(key) for key in CSV_FIELDS},
                "terms": "；".join(claim["term"] for claim in record["claims"]),
            })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "formula": result["formula"],
        "included_record_count": result["included_record_count"],
        "reviewed_record_count": result["reviewed_record_count"],
        "candidate_signal_term_count": result["candidate_signal_term_count"],
        "ranking_effect": "none",
        "inputs": {},
        "files": {path.name: digest(path) for path in (result_path, records_path, terms_path)},
    }
    for name, path in (("normalized", normalized_path), ("screening", screening_path),
                       ("catalog", catalog_path), ("alignment", alignment_path)):
        if path:
            manifest["inputs"][name] = {
                "path": str(Path(path).resolve()), "sha256": digest(path),
            }
    write_json(output_dir / "literature_evidence_manifest.json", manifest)
    return output_dir
