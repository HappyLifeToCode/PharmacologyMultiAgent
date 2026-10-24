from pharm.literature.ranking import build_ranking_preview, save_ranking_preview
from pharm.core.common import read_json


def candidate(identifier, name, strict, matched=5, score=0.7, entity_type="disease"):
    return {
        "disease_id": identifier, "disease_name": name, "entity_type": entity_type,
        "strict_support_target_count": strict, "support_target_count": matched,
        "matched_count": matched, "max_association_score": score,
    }


def inputs():
    cancer = candidate("MONDO_1", "cancer", 20)
    menstrual = candidate("EFO_1", "menstrual disorder", 0, score=0.4)
    psoriasis = candidate("MONDO_2", "psoriasis", 7)
    discovery = {
        "candidate_layers": {
            "complete": [cancer, menstrual, psoriasis],
            "base": [cancer, psoriasis, menstrual],
            "top": [cancer, psoriasis],
        },
    }
    alignment = {
        "schema_version": "literature-concept-alignment-v1", "formula": "温经汤",
        "terms": [
            {"term": "月经不调", "review_status": "reviewed",
             "concept_role": "study_disease",
             "alignment_status": "exact_disease", "ranking_eligible": True,
             "promotion_targets": [{"entity_id": "EFO_1", "entity_name": "menstrual disorder"}]},
            {"term": "银屑病", "review_status": "reviewed",
             "concept_role": "study_disease",
             "alignment_status": "exact_disease", "ranking_eligible": True,
             "promotion_targets": [{"entity_id": "MONDO_2", "entity_name": "psoriasis"}]},
            {"term": "更年期综合征", "review_status": "reviewed",
             "concept_role": "symptom_or_outcome",
             "alignment_status": "broader_only", "ranking_eligible": False,
             "promotion_targets": []},
        ],
    }
    evidence = {
        "schema_version": "literature-evidence-review-v1", "formula": "温经汤",
        "records": [
            {"record_id": "r1", "rank": 1, "title": "clinical", "review_status": "reviewed",
             "study_design": "randomized_controlled_trial",
             "evidence_class": "clinical_cointervention",
             "relation": "cointervention_treatment", "direction": "positive",
             "formula_contribution": "distinguishable",
             "treatment_context": "original_with_cointervention",
             "claims": [{"term": "月经不调", "excerpt": "positive"}]},
            {"record_id": "r2", "rank": 2, "title": "mechanism", "review_status": "reviewed",
             "study_design": "computational_study", "evidence_class": "mechanistic",
             "relation": "mechanism_only", "direction": "not_applicable",
             "formula_contribution": "not_applicable", "treatment_context": "original_formula",
             "claims": [{"term": "银屑病", "excerpt": "mechanism"}]},
            {"record_id": "r3", "rank": 3, "title": "broad", "review_status": "reviewed",
             "study_design": "randomized_controlled_trial",
             "evidence_class": "clinical_cointervention",
             "relation": "cointervention_treatment", "direction": "positive",
             "formula_contribution": "distinguishable", "treatment_context": "original_formula",
             "claims": [{"term": "更年期综合征", "excerpt": "broad"}]},
        ],
    }
    return discovery, alignment, evidence


def test_preview_promotes_exact_clinical_signal_without_changing_target_top():
    discovery, alignment, evidence = inputs()
    original_top = list(discovery["candidate_layers"]["top"])
    preview = build_ranking_preview(discovery, alignment, evidence)
    assert [(row["term"], row["tier"]) for row in preview["literature_priority"]] == [
        ("月经不调", "P2"), ("更年期综合征", "P2")]
    assert preview["literature_priority"][0]["open_targets_display_rank"] == 3
    assert preview["original_open_targets_top"][0]["disease_name"] == "cancer"
    assert discovery["candidate_layers"]["top"] == original_top
    assert preview["comparison"]["original_top_preserved"] is True


def test_mechanistic_stays_support_while_broader_clinical_enters_priority():
    discovery, alignment, evidence = inputs()
    preview = build_ranking_preview(discovery, alignment, evidence)
    priority = {(row["term"], row["tier"]) for row in preview["literature_priority"]}
    assert ("更年期综合征", "P2") in priority
    support = {row["term"]: row["tier"] for row in preview["supporting_terms"]}
    assert support["银屑病"] == "M"


def test_unmapped_direct_clinical_concept_can_be_p1_without_ot_merge():
    discovery, alignment, evidence = inputs()
    term = alignment["terms"][2]
    term.update({"alignment_status": "unresolved", "promotion_targets": []})
    record = evidence["records"][2]
    record.update({
        "study_design": "randomized_controlled_trial",
        "evidence_class": "clinical_direct",
        "relation": "direct_treatment",
        "direction": "positive",
        "formula_contribution": "distinguishable",
    })
    preview = build_ranking_preview(discovery, alignment, evidence)
    row = next(item for item in preview["literature_priority"]
               if item["term"] == "更年期综合征")
    assert row["tier"] == "P1"
    assert row["ontology_merge_eligible"] is False
    assert row["open_targets_disease_id"] is None


def test_treatment_context_never_enters_indication_priority():
    discovery, alignment, evidence = inputs()
    alignment["terms"][0]["concept_role"] = "treatment_context"
    preview = build_ranking_preview(discovery, alignment, evidence)
    assert all(row["term"] != "月经不调" for row in preview["literature_priority"])
    support = {row["term"]: row["tier"] for row in preview["supporting_terms"]}
    assert support["月经不调"] == "CTX"


def test_exact_review_or_case_support_is_p3():
    discovery, alignment, evidence = inputs()
    evidence["records"][0].update({
        "study_design": "narrative_review", "evidence_class": "secondary_review",
        "relation": "direct_treatment", "formula_contribution": "not_distinguishable",
    })
    preview = build_ranking_preview(discovery, alignment, evidence)
    row = next(item for item in preview["literature_priority"]
               if item["term"] == "月经不调")
    assert row["tier"] == "P3"


def test_case_only_support_stays_out_of_priority_lane():
    discovery, alignment, evidence = inputs()
    evidence["records"][0].update({
        "study_design": "case_report_or_experience",
        "evidence_class": "case_or_experience", "relation": "direct_treatment",
        "direction": "unclear", "formula_contribution": "partially_distinguishable",
    })
    preview = build_ranking_preview(discovery, alignment, evidence)
    assert all(row["term"] != "月经不调" for row in preview["literature_priority"])
    support = {row["term"]: row["tier"] for row in preview["supporting_terms"]}
    assert support["月经不调"] == "C"


def test_save_preview_writes_hash_manifest(tmp_path):
    discovery, alignment, evidence = inputs()
    preview = build_ranking_preview(discovery, alignment, evidence)
    source = tmp_path / "input.json"
    source.write_text("{}", encoding="utf-8")
    output = tmp_path / "output"
    save_ranking_preview(preview, output, input_paths={"source": source})
    manifest = read_json(output / "manifest.json")
    assert manifest["ranking_effect"] == "none"
    assert manifest["original_top_preserved"] is True
    assert set(manifest["files"]) == {
        "literature_ranking_preview.json", "literature_priority.csv", "report.md",
    }
