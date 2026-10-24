import json

from pharm.core.common import read_json
from pharm.literature.extraction import (
    apply_extracted_concepts,
    extract_literature_concepts,
    save_concept_extraction,
)


def inputs(records):
    normalized = {
        "schema_version": "literature-search-v1",
        "source_results": [{"source": "cnki", "records": records}],
    }
    screening = {
        "schema_version": "literature-screening-v1",
        "formula": "芍药甘草汤",
        "formula_aliases": ["芍甘汤"],
        "records": [{
            "record_id": record["record_id"],
            "rank": record["rank"],
            "title": record["title"],
            "screening_decision": record.get("decision", "included_original_formula"),
            "treatment_context": record.get("treatment_context", "original_formula"),
            "disease_or_symptom_labels": [],
        } for record in records],
    }
    return normalized, screening


def record(identifier, rank, title, abstract, keywords=None, diseases=None, **extra):
    return {
        "record_id": identifier,
        "rank": rank,
        "title": title,
        "abstract": abstract,
        "keywords": keywords or [],
        "source_metadata": {"western_diseases": diseases or []},
        **extra,
    }


def test_structured_metadata_is_preserved_with_exact_excerpt():
    normalized, screening = inputs([record(
        "r1", 1, "芍药甘草汤治疗膝骨关节炎",
        "目的：观察芍药甘草汤治疗膝骨关节炎的效果。",
        diseases=["膝骨关节炎"],
    )])
    result = extract_literature_concepts(normalized, screening)
    concept = result["records"][0]["concepts"][0]
    assert concept["term"] == "膝骨关节炎"
    assert concept["concept_role"] == "study_disease"
    assert concept["confidence"] == 0.98
    assert "膝骨关节炎" in concept["evidence_excerpt"]


def test_keywords_are_classified_as_disease_symptom_and_context():
    normalized, screening = inputs([
        record("r1", 1, "芍药甘草汤眼部熏蒸对视疲劳的研究",
               "芍药甘草汤眼部熏蒸可改善视疲劳。",
               keywords=["芍药甘草汤", "视疲劳", "炎症因子"]),
        record("r2", 2, "芍药甘草汤联合冲击波治疗臀中肌综合征",
               "芍药甘草汤联合冲击波治疗臀中肌综合征。",
               keywords=["臀中肌综合征", "体外冲击波"]),
        record("r3", 3, "芍药甘草汤用于PFN-A术后镇痛",
               "研究芍药甘草汤在PFN-A术后镇痛中的效果。",
               keywords=["术后镇痛", "临床研究"]),
    ])
    result = extract_literature_concepts(normalized, screening)
    concepts = {item["term"]: item for row in result["records"] for item in row["concepts"]}
    assert concepts["视疲劳"]["concept_role"] == "symptom_or_outcome"
    assert concepts["臀中肌综合征"]["concept_role"] == "study_disease"
    assert concepts["术后镇痛"]["concept_role"] == "treatment_context"
    assert "炎症因子" not in concepts


def test_inflammation_biomarker_and_title_suffix_are_not_disease_terms():
    normalized, screening = inputs([record(
        "r1", 1, "芍药甘草汤眼部熏蒸对视疲劳干预效应的研究",
        "芍药甘草汤可改善视疲劳并降低炎症因子。",
        keywords=["视疲劳", "炎症", "炎症因子"],
    )])
    result = extract_literature_concepts(normalized, screening)
    terms = [item["term"] for item in result["records"][0]["concepts"]]
    assert terms == ["视疲劳"]


def test_title_relation_rule_supplies_missing_keyword_term():
    normalized, screening = inputs([record(
        "r1", 1, "芍药甘草汤治疗月经不调患者的效果观察",
        "纳入月经不调患者并给予芍药甘草汤。",
    )])
    result = extract_literature_concepts(normalized, screening)
    assert [item["term"] for item in result["records"][0]["concepts"]] == ["月经不调"]
    assert result["records"][0]["concepts"][0]["sources"] == ["title_relation_rule"]


def test_excluded_record_is_not_reintroduced_and_screening_is_enriched():
    records = [
        record("r1", 1, "芍药甘草汤治疗视疲劳", "芍药甘草汤改善视疲劳。",
               keywords=["视疲劳"]),
        record("r2", 2, "加味芍药甘草汤治疗腰痛", "加味方治疗腰痛。",
               keywords=["腰痛"], decision="excluded_modified_formula"),
    ]
    normalized, screening = inputs(records)
    result = extract_literature_concepts(normalized, screening)
    enriched = apply_extracted_concepts(screening, result)
    assert [row["record_id"] for row in result["records"]] == ["r1"]
    first, excluded = enriched["records"]
    assert first["disease_or_symptom_labels"] == ["视疲劳"]
    assert first["literature_concepts"][0]["concept_role"] == "symptom_or_outcome"
    assert excluded["disease_or_symptom_labels"] == []
    assert excluded["screening_decision"] == "excluded_modified_formula"


def test_save_extraction_writes_hash_manifest(tmp_path):
    normalized, screening = inputs([record(
        "r1", 1, "芍药甘草汤治疗膝骨关节炎",
        "芍药甘草汤治疗膝骨关节炎。", keywords=["膝骨关节炎"],
    )])
    result = extract_literature_concepts(normalized, screening)
    enriched = apply_extracted_concepts(screening, result)
    normalized_path = tmp_path / "normalized.json"
    screening_path = tmp_path / "screening.json"
    normalized_path.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
    screening_path.write_text(json.dumps(screening, ensure_ascii=False), encoding="utf-8")
    save_concept_extraction(result, enriched, tmp_path / "out",
                            normalized_path=normalized_path, screening_path=screening_path)
    manifest = read_json(tmp_path / "out/literature_concept_extraction_manifest.json")
    assert manifest["unique_term_count"] == 1
    assert set(manifest["files"]) == {
        "literature_concept_extraction.json",
        "literature_concept_mentions.csv",
        "literature_screening_extracted.json",
    }
