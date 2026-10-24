"""Reviewed concept alignment for literature disease and symptom terms.

The mapper never translates or guesses ontology entities. It applies a
reviewed mapping catalog and keeps exact disease, phenotype, broader, related,
and unresolved concepts separate. Concept role and ontology mergeability are
recorded independently from literature evidence priority.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from ..core.common import digest, write_json


SCHEMA_VERSION = "literature-concept-alignment-v1"
CATALOG_SCHEMA_VERSION = "literature-concept-map-v1"
ALLOWED_RELATIONS = {"exact", "broader", "narrower", "related", "phenotype"}
ALLOWED_ENTITY_TYPES = {"disease", "phenotype"}
ALLOWED_REVIEW_STATUS = {"reviewed", "pending", "rejected"}
ALLOWED_CONCEPT_ROLES = {"study_disease", "symptom_or_outcome", "treatment_context"}


def _text(value):
    return str(value or "").strip()


def _validate_mapping(mapping, review_status):
    if not isinstance(mapping, dict):
        raise ValueError("术语映射必须是对象")
    required = ("entity_id", "entity_name", "entity_type", "relation", "source", "release")
    missing = [key for key in required if not _text(mapping.get(key))]
    if missing:
        raise ValueError("术语映射缺少字段：" + "、".join(missing))
    if mapping["entity_type"] not in ALLOWED_ENTITY_TYPES:
        raise ValueError("entity_type 只支持 disease 或 phenotype")
    if mapping["relation"] not in ALLOWED_RELATIONS:
        raise ValueError("不支持的映射关系：" + str(mapping["relation"]))
    promote = mapping.get("promote", False)
    if not isinstance(promote, bool):
        raise ValueError("promote 必须是布尔值")
    if promote and not (
        review_status == "reviewed"
        and mapping["entity_type"] == "disease"
        and mapping["relation"] == "exact"
    ):
        raise ValueError("只有人工复核后的 exact disease 映射可以标记 promote=true")
    return {
        "entity_id": _text(mapping["entity_id"]),
        "entity_name": _text(mapping["entity_name"]),
        "entity_type": mapping["entity_type"],
        "relation": mapping["relation"],
        "source": _text(mapping["source"]),
        "release": _text(mapping["release"]),
        "promote": promote,
        "note": _text(mapping.get("note")) or None,
    }


def _alignment_status(mappings, review_status):
    if review_status == "rejected":
        return "rejected"
    if not mappings:
        return "unresolved"
    exact_diseases = [item for item in mappings
                      if item["relation"] == "exact" and item["entity_type"] == "disease"]
    phenotypes = [item for item in mappings if item["entity_type"] == "phenotype"]
    if len(exact_diseases) > 1 and not any(item["promote"] for item in exact_diseases):
        return "ambiguous_exact_disease"
    if exact_diseases:
        return "exact_disease_with_phenotype" if phenotypes else "exact_disease"
    if phenotypes and all(item["relation"] in {"exact", "phenotype"} for item in mappings):
        return "phenotype_only"
    if all(item["relation"] == "broader" for item in mappings):
        return "broader_only"
    return "related_or_partial"


def align_screened_concepts(screening, catalog):
    """Align disease/symptom labels from included original-formula records."""
    if not isinstance(screening, dict) or screening.get("schema_version") != "literature-screening-v1":
        raise ValueError("输入必须是 literature-screening-v1 结果")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("映射目录必须是 literature-concept-map-v1")
    formula = _text(screening.get("formula"))
    catalog_formula = _text(catalog.get("formula"))
    if catalog_formula and catalog_formula != formula:
        raise ValueError("映射目录方剂与筛选结果不一致")

    mentions = defaultdict(list)
    extracted_roles = defaultdict(list)
    for record in screening.get("records", []):
        if not isinstance(record, dict) or record.get("screening_decision") != "included_original_formula":
            continue
        for concept in record.get("literature_concepts", []):
            if not isinstance(concept, dict) or not _text(concept.get("term")):
                continue
            role = concept.get("concept_role", "study_disease")
            if role not in ALLOWED_CONCEPT_ROLES:
                raise ValueError("抽取概念包含不支持的 concept_role：" + str(role))
            extracted_roles[_text(concept["term"])].append(role)
        for term in record.get("disease_or_symptom_labels", []):
            term = _text(term)
            if term:
                mentions[term].append({
                    "record_id": record.get("record_id"),
                    "rank": record.get("rank"),
                    "title": record.get("title"),
                    "treatment_context": record.get("treatment_context"),
                    "study_tags": record.get("study_tags", []),
                })

    entries = catalog.get("entries")
    if not isinstance(entries, list):
        raise ValueError("映射目录缺少 entries 数组")
    by_term = {}
    for entry in entries:
        if not isinstance(entry, dict) or not _text(entry.get("term")):
            raise ValueError("映射目录包含无效 term")
        term = _text(entry["term"])
        if term in by_term:
            raise ValueError("映射目录包含重复 term：" + term)
        review_status = entry.get("review_status", "pending")
        if review_status not in ALLOWED_REVIEW_STATUS:
            raise ValueError("不支持的 review_status：" + str(review_status))
        mappings = [_validate_mapping(item, review_status) for item in entry.get("mappings", [])]
        concept_role = entry.get("concept_role")
        if concept_role is not None and concept_role not in ALLOWED_CONCEPT_ROLES:
            raise ValueError("不支持的 concept_role：" + str(concept_role))
        by_term[term] = {
            "review_status": review_status,
            "concept_role": concept_role,
            "mappings": mappings,
            "note": _text(entry.get("note")) or None,
        }

    aligned = []
    for term in sorted(mentions):
        rule = by_term.get(term, {"review_status": "pending", "concept_role": None, "mappings": [],
                                  "note": "映射目录未登记该术语"})
        mappings = rule["mappings"]
        promotion_targets = [item for item in mappings if item["promote"]]
        roles = extracted_roles.get(term, [])
        extracted_role = next((role for role in (
            "treatment_context", "symptom_or_outcome", "study_disease") if role in roles), None)
        concept_role = rule["concept_role"] or extracted_role or "study_disease"
        aligned.append({
            "term": term,
            "mention_count": len(mentions[term]),
            "mentions": mentions[term],
            "review_status": rule["review_status"],
            "concept_role": concept_role,
            "alignment_status": _alignment_status(mappings, rule["review_status"]),
            "mappings": mappings,
            "promotion_targets": promotion_targets,
            "ranking_eligible": bool(promotion_targets),
            "ontology_merge_eligible": bool(promotion_targets),
            "note": rule["note"],
        })
    statuses = Counter(item["alignment_status"] for item in aligned)
    return {
        "schema_version": SCHEMA_VERSION,
        "formula": formula,
        "source_screening_schema": screening["schema_version"],
        "term_count": len(aligned),
        "ranking_eligible_term_count": sum(item["ranking_eligible"] for item in aligned),
        "alignment_status_counts": dict(sorted(statuses.items())),
        "terms": aligned,
        "policy": {
            "translation": "no automatic translation or ontology guessing",
            "promotion": "reviewed exact disease mappings with promote=true only",
            "concept_role": "study disease, symptom/outcome, and treatment context are kept separate",
            "literature_priority": "determined downstream from evidence; not blocked by ontology mapping",
            "phenotype": "supplement display only",
            "broader_narrower_related": "retain relation; no automatic promotion",
            "ranking_effect": "none in this stage",
        },
    }


CSV_FIELDS = (
    "term", "concept_role", "mention_count", "review_status", "alignment_status",
    "ranking_eligible", "ontology_merge_eligible", "promotion_targets", "all_mappings", "note",
)


def save_concept_alignment(result, output_dir, *, screening_path=None, catalog_path=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "literature_concept_alignment.json"
    csv_path = output_dir / "literature_concept_alignment.csv"
    write_json(result_path, result)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in result["terms"]:
            mappings = [f'{row["entity_id"]} | {row["entity_name"]} | '
                        f'{row["entity_type"]} | {row["relation"]} | '
                        f'{row["source"]} {row["release"]}' for row in item["mappings"]]
            promotions = [row["entity_id"] for row in item["promotion_targets"]]
            writer.writerow({
                "term": item["term"],
                "concept_role": item["concept_role"],
                "mention_count": item["mention_count"],
                "review_status": item["review_status"],
                "alignment_status": item["alignment_status"],
                "ranking_eligible": item["ranking_eligible"],
                "ontology_merge_eligible": item["ontology_merge_eligible"],
                "promotion_targets": "；".join(promotions),
                "all_mappings": "；".join(mappings),
                "note": item["note"],
            })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "formula": result["formula"],
        "term_count": result["term_count"],
        "ranking_eligible_term_count": result["ranking_eligible_term_count"],
        "screening_path": str(Path(screening_path).resolve()) if screening_path else None,
        "screening_sha256": digest(screening_path) if screening_path else None,
        "catalog_path": str(Path(catalog_path).resolve()) if catalog_path else None,
        "catalog_sha256": digest(catalog_path) if catalog_path else None,
        "ranking_effect": "none",
        "files": {result_path.name: digest(result_path), csv_path.name: digest(csv_path)},
    }
    write_json(output_dir / "literature_concept_alignment_manifest.json", manifest)
    return output_dir
