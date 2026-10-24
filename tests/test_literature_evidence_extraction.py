import json

from pharm.core.common import read_json
from pharm.literature.evidence import review_evidence
from pharm.literature.evidence_extraction import (
    extract_literature_evidence,
    save_evidence_extraction,
)


def build(record, *, context="original_formula", role="study_disease", term="膝骨关节炎"):
    normalized = {
        "schema_version": "literature-search-v1",
        "source_results": [{"records": [record]}],
    }
    screening = {
        "schema_version": "literature-screening-v1",
        "formula": "芍药甘草汤",
        "records": [{
            "record_id": record["record_id"], "rank": 1, "title": record["title"],
            "screening_decision": "included_original_formula",
            "treatment_context": context,
            "study_tags": record.get("study_tags", []),
            "disease_or_symptom_labels": [term],
            "literature_concepts": [{
                "term": term, "concept_role": role,
                "evidence_excerpt": next(sentence for sentence in record["abstract"].split("。")
                                           if term in sentence) + "。",
            }],
        }],
    }
    return normalized, screening


def test_randomized_direct_clinical_study_is_positive_and_distinguishable():
    record = {
        "record_id": "r1", "title": "芍药甘草汤治疗膝骨关节炎的随机对照研究",
        "abstract": "纳入80例膝骨关节炎患者，随机分为治疗组和对照组。结论：芍药甘草汤可显著改善膝骨关节炎。",
    }
    normalized, screening = build(record)
    result = extract_literature_evidence(normalized, screening)
    row = result["records"][0]
    assert row["study_design"] == "randomized_controlled_trial"
    assert row["relation"] == "direct_treatment"
    assert row["direction"] == "positive"
    assert row["formula_contribution"] == "distinguishable"
    assert row["claims"][0]["excerpt"] in record["abstract"]


def test_add_on_trial_is_cointervention_and_distinguishable():
    record = {
        "record_id": "r1", "title": "芍药甘草汤联合冲击波治疗膝骨关节炎",
        "abstract": "纳入80例膝骨关节炎患者，随机分为对照组和观察组。观察组在对照组治疗基础上加用芍药甘草汤。结论：联合治疗显著改善膝骨关节炎。",
    }
    normalized, screening = build(record, context="original_with_cointervention")
    row = extract_literature_evidence(normalized, screening)["records"][0]
    assert row["relation"] == "cointervention_treatment"
    assert row["formula_contribution"] == "distinguishable"


def test_add_on_wording_with_de_is_not_misclassified_as_direct_treatment():
    record = {
        "record_id": "r1", "title": "温经汤治疗更年期综合征的随机研究",
        "abstract": (
            "纳入70例更年期综合征患者并随机分组。对照组接受西药治疗，"
            "观察组在对照组的基础上给予温经汤。结论：观察组疗效显著提高。"
        ),
    }
    normalized, screening = build(record, term="更年期综合征")
    row = extract_literature_evidence(normalized, screening)["records"][0]
    assert row["relation"] == "cointervention_treatment"
    assert row["formula_contribution"] == "distinguishable"


def test_add_on_wording_with_increase_is_distinguishable():
    record = {
        "record_id": "r1", "title": "温经汤联合针灸治疗月经不调",
        "abstract": (
            "纳入100例月经不调患者并随机分组。观察组在参照组的治疗基础上，"
            "增加温经汤给予治疗。结论：观察组疗效显著提高。"
        ),
    }
    normalized, screening = build(record, term="月经不调")
    row = extract_literature_evidence(normalized, screening)["records"][0]
    assert row["relation"] == "cointervention_treatment"
    assert row["formula_contribution"] == "distinguishable"


def test_animal_and_computational_studies_do_not_become_clinical():
    animal = {
        "record_id": "r1", "title": "芍药甘草汤对类风湿关节炎大鼠的作用",
        "abstract": "建立类风湿关节炎大鼠模型。结论：芍药甘草汤可改善类风湿关节炎。",
        "study_tags": ["animal"],
    }
    normalized, screening = build(animal, term="类风湿关节炎")
    row = extract_literature_evidence(normalized, screening)["records"][0]
    assert (row["study_design"], row["relation"]) == (
        "animal_experiment", "preclinical_treatment")

    computational = {
        "record_id": "r2", "title": "网络药理学探讨芍药甘草汤治疗三叉神经痛机制",
        "abstract": "采用网络药理学分析三叉神经痛靶点。结论：揭示芍药甘草汤干预三叉神经痛的潜在机制。",
        "study_tags": ["computational"],
    }
    normalized, screening = build(computational, term="三叉神经痛")
    row = extract_literature_evidence(normalized, screening)["records"][0]
    assert (row["study_design"], row["relation"], row["direction"]) == (
        "computational_study", "mechanism_only", "not_applicable")


def test_mammalian_protein_name_does_not_turn_computational_paper_into_animal_study():
    record = {
        "record_id": "r1",
        "title": "网络药理学探讨芍药甘草汤治疗三叉神经痛机制",
        "abstract": (
            "采用网络药理学、机器学习和分子对接分析三叉神经痛靶点，"
            "筛得哺乳动物雷帕霉素靶蛋白。结论：揭示芍药甘草汤干预"
            "三叉神经痛的潜在机制。"
        ),
        "study_tags": ["computational"],
    }
    normalized, screening = build(record, term="三叉神经痛")
    row = extract_literature_evidence(normalized, screening)["records"][0]
    assert (row["study_design"], row["relation"]) == (
        "computational_study", "mechanism_only")


def test_auto_catalog_is_accepted_by_evidence_review_without_ontology_mapping():
    record = {
        "record_id": "r1", "title": "芍药甘草汤治疗视疲劳的随机研究",
        "abstract": "纳入60例视疲劳患者并随机分组。结论：芍药甘草汤可显著改善视疲劳。",
    }
    normalized, screening = build(record, role="symptom_or_outcome", term="视疲劳")
    extracted = extract_literature_evidence(normalized, screening)
    alignment = {
        "schema_version": "literature-concept-alignment-v1",
        "terms": [{"term": "视疲劳", "alignment_status": "unresolved",
                   "promotion_targets": []}],
    }
    reviewed = review_evidence(normalized, screening, extracted["catalog"],
                               concept_alignment=alignment)
    claim = reviewed["records"][0]["claims"][0]
    assert reviewed["auto_verified_record_count"] == 1
    assert claim["candidate_signal_eligible"] is True
    assert claim["ontology_merge_eligible"] is False


def test_save_evidence_extraction_writes_catalog_and_hashes(tmp_path):
    record = {
        "record_id": "r1", "title": "芍药甘草汤治疗膝骨关节炎",
        "abstract": "纳入膝骨关节炎患者。结论：芍药甘草汤可改善膝骨关节炎。",
    }
    normalized, screening = build(record)
    result = extract_literature_evidence(normalized, screening)
    normalized_path = tmp_path / "normalized.json"
    screening_path = tmp_path / "screening.json"
    normalized_path.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
    screening_path.write_text(json.dumps(screening, ensure_ascii=False), encoding="utf-8")
    save_evidence_extraction(result, tmp_path / "out",
                             normalized_path=normalized_path, screening_path=screening_path)
    manifest = read_json(tmp_path / "out/literature_evidence_extraction_manifest.json")
    assert manifest["claim_count"] == 1
    assert set(manifest["files"]) == {
        "literature_evidence_extraction.json",
        "literature_evidence_catalog_auto.json",
        "literature_evidence_extraction.csv",
    }
