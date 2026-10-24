import pytest

from pharm.core.common import read_json
from pharm.literature.evidence import review_evidence, save_evidence_review


def inputs():
    normalized = {
        "schema_version": "literature-search-v1",
        "source_results": [{"records": [{
            "record_id": "r1", "title": "温经汤联合针灸治疗月经不调",
            "abstract": "观察组增加温经汤后总有效率更高。",
        }]}],
    }
    screening = {
        "schema_version": "literature-screening-v1", "formula": "温经汤",
        "records": [{
            "record_id": "r1", "rank": 1, "title": "温经汤联合针灸治疗月经不调",
            "screening_decision": "included_original_formula",
            "treatment_context": "original_with_cointervention",
            "disease_or_symptom_labels": ["月经不调"],
        }],
    }
    alignment = {
        "schema_version": "literature-concept-alignment-v1",
        "terms": [{
            "term": "月经不调", "alignment_status": "exact_disease",
            "promotion_targets": [{"entity_id": "EFO_1"}],
        }],
    }
    catalog = {
        "schema_version": "literature-evidence-catalog-v1", "formula": "温经汤",
        "entries": [{
            "record_id": "r1", "review_status": "reviewed",
            "study_design": "randomized_controlled_trial",
            "relation": "cointervention_treatment", "direction": "positive",
            "formula_contribution": "distinguishable",
            "claims": [{"term": "月经不调", "excerpt": "观察组增加温经汤后总有效率更高。"}],
        }],
    }
    return normalized, screening, catalog, alignment


def test_review_keeps_cointervention_separate_and_marks_candidate_signal():
    normalized, screening, catalog, alignment = inputs()
    result = review_evidence(normalized, screening, catalog,
                             concept_alignment=alignment)
    record = result["records"][0]
    assert record["evidence_class"] == "clinical_cointervention"
    assert record["claims"][0]["candidate_signal_eligible"] is True
    assert record["claims"][0]["candidate_signal_role"] == "add_on_or_cointervention"
    assert result["candidate_signal_term_count"] == 1
    assert result["policy"]["ranking_effect"] == "none in this stage"


def test_nonclinical_does_not_become_candidate_signal():
    normalized, screening, catalog, alignment = inputs()
    catalog["entries"][0].update({
        "study_design": "animal_experiment", "relation": "preclinical_treatment",
    })
    alignment["terms"][0]["promotion_targets"] = []
    result = review_evidence(normalized, screening, catalog,
                             concept_alignment=alignment)
    assert result["records"][0]["evidence_class"] == "preclinical"
    assert result["records"][0]["claims"][0]["candidate_signal_eligible"] is False


def test_clinical_signal_does_not_require_exact_ontology_mapping():
    normalized, screening, catalog, alignment = inputs()
    alignment["terms"][0]["promotion_targets"] = []
    result = review_evidence(normalized, screening, catalog,
                             concept_alignment=alignment)
    claim = result["records"][0]["claims"][0]
    assert claim["candidate_signal_eligible"] is True
    assert claim["ontology_merge_eligible"] is False


def test_claim_must_quote_archived_abstract_exactly():
    normalized, screening, catalog, alignment = inputs()
    catalog["entries"][0]["claims"][0]["excerpt"] = "不存在的引文"
    with pytest.raises(ValueError, match="存档摘要"):
        review_evidence(normalized, screening, catalog, concept_alignment=alignment)


def test_catalog_cannot_reintroduce_excluded_modified_formula():
    normalized, screening, catalog, alignment = inputs()
    screening["records"][0]["screening_decision"] = "excluded_modified_formula"
    with pytest.raises(ValueError, match="未纳入原方集"):
        review_evidence(normalized, screening, catalog, concept_alignment=alignment)


def test_reviewed_entry_requires_explicit_evidence_fields():
    normalized, screening, catalog, alignment = inputs()
    del catalog["entries"][0]["direction"]
    with pytest.raises(ValueError, match="缺少字段"):
        review_evidence(normalized, screening, catalog, concept_alignment=alignment)


def test_save_review_writes_manifest_hashes(tmp_path):
    normalized, screening, catalog, alignment = inputs()
    result = review_evidence(normalized, screening, catalog,
                             concept_alignment=alignment)
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    save_evidence_review(result, tmp_path, normalized_path=source)
    manifest = read_json(tmp_path / "literature_evidence_manifest.json")
    assert manifest["reviewed_record_count"] == 1
    assert set(manifest["files"]) == {
        "literature_evidence_review.json", "literature_evidence_records.csv",
        "literature_term_evidence.json",
    }
