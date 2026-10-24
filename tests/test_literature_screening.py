import csv

from pharm.core.common import read_json
from pharm.literature.screening import save_screening_result, screen_literature_result


def normalised(records, formula="温经汤"):
    return {
        "schema_version": "literature-search-v1",
        "request": {"formula": formula, "formula_aliases": []},
        "source_results": [{"source": "cnki", "records": records}],
    }


def record(title, rank=1, keywords=None, diseases=None, abstract="摘要"):
    return {
        "record_id": f"id-{rank}", "rank": rank, "title": title,
        "abstract": abstract, "keywords": keywords or [], "source": "cnki",
        "url": None, "source_metadata": {"western_diseases": diseases or []},
    }


def test_modified_and_related_formulas_are_excluded():
    result = screen_literature_result(normalised([
        record("加味温经汤治疗原发性痛经", 1),
        record("加减温经汤治疗异常出血", 2),
        record("温经汤加减治疗异常出血", 3),
        record("温经逐瘀汤治疗子宫腺肌症", 4),
        record("大温经汤辨治月经病", 5),
    ]), excluded_formula_names=["温经逐瘀汤", "大温经汤"])
    assert result["eligible_original_formula_count"] == 0
    assert result["decision_counts"] == {
        "excluded_modified_formula": 3,
        "excluded_related_formula": 2,
    }


def test_modified_treatment_disclosed_only_in_abstract_is_excluded():
    result = screen_literature_result(normalised([
        record("温经汤治疗女性功能性便秘经验", 1,
               abstract="本文总结运用温经汤加减化裁治疗本病的临床经验。"),
        record("温经汤历史沿革考证", 2,
               abstract="《妇人大全良方》所载方由《金匮要略》温经汤加减而成。"),
    ]))
    assert result["records"][0]["screening_decision"] == "excluded_modified_formula"
    assert "摘要" in result["records"][0]["screening_reason"]
    assert result["records"][1]["screening_decision"] == "included_original_formula"


def test_original_formula_is_retained_but_treatment_context_is_separate():
    result = screen_literature_result(normalised([
        record("温经汤治疗更年期综合征", 1, diseases=["更年期综合征"]),
        record("金匮温经汤联合针刺治疗卵巢功能减退", 2),
        record("妇人腹痛验案一则", 3, keywords=["温经汤"]),
    ]), aliases=["金匮温经汤"])
    assert result["eligible_original_formula_count"] == 2
    assert result["records"][0]["treatment_context"] == "original_formula"
    assert result["records"][1]["treatment_context"] == "original_with_cointervention"
    assert result["records"][2]["screening_decision"] == "excluded_mention_only"


def test_method_combination_is_not_mistaken_for_combination_treatment():
    result = screen_literature_result(normalised([
        record("WGCNA联合机器学习探究温经汤保护作用机制", 1),
        record("温经汤历史沿革考证及临床应用", 2),
    ]))
    assert result["records"][0]["treatment_context"] == "original_formula"
    assert "human_clinical" not in result["records"][1]["study_tags"]


def test_other_named_formula_combination_is_excluded():
    result = screen_literature_result(normalised([
        record("四逆汤合芍药甘草汤治疗血管炎", 1),
        record("牵正散合芍药甘草汤治疗面瘫", 2),
        record("芍药甘草汤联合冲击波治疗膝骨关节炎", 3),
    ], formula="芍药甘草汤"))
    assert [item["screening_decision"] for item in result["records"]] == [
        "excluded_formula_combination",
        "excluded_formula_combination",
        "included_original_formula",
    ]


def test_screening_outputs_keep_complete_audit(tmp_path):
    source = tmp_path / "normalized_papers.json"
    source.write_text("{}", encoding="utf-8")
    result = screen_literature_result(normalised([
        record("温经汤治疗痛经", 1, diseases=["痛经"]),
        record("加味温经汤治疗痛经", 2, diseases=["痛经"]),
    ]))
    save_screening_result(result, tmp_path, source_path=source)
    manifest = read_json(tmp_path / "literature_screening_manifest.json")
    assert manifest["eligible_original_formula_count"] == 1
    assert manifest["excluded_count"] == 1
    with (tmp_path / "eligible_original_formula_records.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["title"] for row in rows] == ["温经汤治疗痛经"]
