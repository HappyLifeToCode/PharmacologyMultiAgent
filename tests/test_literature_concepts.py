import json

import pytest

from pharm.core.common import read_json
from pharm.literature.concepts import align_screened_concepts, save_concept_alignment


def screening():
    return {
        "schema_version": "literature-screening-v1",
        "formula": "温经汤",
        "records": [
            {"record_id": "r1", "rank": 1, "title": "温经汤治疗痛经",
             "screening_decision": "included_original_formula",
             "treatment_context": "original_formula", "study_tags": ["human_clinical"],
             "disease_or_symptom_labels": ["痛经", "功能性便秘"]},
            {"record_id": "r2", "rank": 2, "title": "加味温经汤治疗痛经",
             "screening_decision": "excluded_modified_formula",
             "treatment_context": "modified_formula", "study_tags": ["human_clinical"],
             "disease_or_symptom_labels": ["痛经"]},
        ],
    }


def catalog():
    return {
        "schema_version": "literature-concept-map-v1", "formula": "温经汤",
        "entries": [
            {"term": "痛经", "concept_role": "symptom_or_outcome",
             "review_status": "reviewed", "mappings": [
                {"entity_id": "MONDO:1060205", "entity_name": "dysmenorrhea",
                 "entity_type": "disease", "relation": "exact", "source": "monarch",
                 "release": "2026-10-07", "promote": True},
                {"entity_id": "HP_0100607", "entity_name": "Dysmenorrhea",
                 "entity_type": "phenotype", "relation": "phenotype", "source": "open_targets",
                 "release": "26.09", "promote": False},
            ]},
            {"term": "功能性便秘", "review_status": "reviewed", "mappings": [
                {"entity_id": "MONDO_0002203", "entity_name": "constipation disorder",
                 "entity_type": "disease", "relation": "broader", "source": "open_targets",
                 "release": "26.09", "promote": False},
            ]},
        ],
    }


def test_only_included_original_formula_mentions_are_aligned():
    result = align_screened_concepts(screening(), catalog())
    pain = next(item for item in result["terms"] if item["term"] == "痛经")
    constipation = next(item for item in result["terms"] if item["term"] == "功能性便秘")
    assert pain["mention_count"] == 1
    assert pain["alignment_status"] == "exact_disease_with_phenotype"
    assert pain["concept_role"] == "symptom_or_outcome"
    assert pain["ranking_eligible"] is True
    assert pain["ontology_merge_eligible"] is True
    assert constipation["alignment_status"] == "broader_only"
    assert constipation["ranking_eligible"] is False


def test_missing_catalog_term_stays_unresolved():
    value = catalog()
    value["entries"] = [value["entries"][0]]
    result = align_screened_concepts(screening(), value)
    unresolved = next(item for item in result["terms"] if item["term"] == "功能性便秘")
    assert unresolved["alignment_status"] == "unresolved"
    assert unresolved["review_status"] == "pending"


def test_extracted_role_is_kept_when_mapping_catalog_has_no_term():
    value = screening()
    value["records"][0]["literature_concepts"] = [{
        "term": "功能性便秘", "concept_role": "symptom_or_outcome",
    }]
    mapping = catalog()
    mapping["entries"] = [mapping["entries"][0]]
    result = align_screened_concepts(value, mapping)
    unresolved = next(item for item in result["terms"] if item["term"] == "功能性便秘")
    assert unresolved["concept_role"] == "symptom_or_outcome"


def test_broader_or_phenotype_mapping_cannot_be_promoted():
    value = catalog()
    value["entries"][1]["mappings"][0]["promote"] = True
    with pytest.raises(ValueError, match="exact disease"):
        align_screened_concepts(screening(), value)


def test_invalid_concept_role_is_rejected():
    value = catalog()
    value["entries"][0]["concept_role"] = "biomarker"
    with pytest.raises(ValueError, match="concept_role"):
        align_screened_concepts(screening(), value)


def test_alignment_outputs_include_source_hashes(tmp_path):
    screening_path = tmp_path / "screening.json"
    catalog_path = tmp_path / "catalog.json"
    screening_path.write_text(json.dumps(screening(), ensure_ascii=False), encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog(), ensure_ascii=False), encoding="utf-8")
    result = align_screened_concepts(screening(), catalog())
    save_concept_alignment(result, tmp_path / "out", screening_path=screening_path,
                           catalog_path=catalog_path)
    manifest = read_json(tmp_path / "out/literature_concept_alignment_manifest.json")
    assert manifest["term_count"] == 2
    assert manifest["ranking_eligible_term_count"] == 1
