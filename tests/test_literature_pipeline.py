import copy

from pharm.core.common import read_json
from pharm.literature.pipeline import run_literature_pipeline, save_literature_pipeline


def discovery():
    cancer = {
        "disease_id": "MONDO_0004992", "disease_name": "cancer",
        "entity_type": "disease", "strict_support_target_count": 10,
        "support_target_count": 20, "max_association_score": 0.8,
    }
    example = {
        "disease_id": "MONDO_123", "disease_name": "example disease",
        "entity_type": "disease", "strict_support_target_count": 1,
        "support_target_count": 2, "max_association_score": 0.3,
    }
    return {"candidate_layers": {
        "complete": [cancer, example],
        "base": [cancer, example],
        "top": [cancer, example],
    }}


def papers():
    return {
        "keyword": "测试方",
        "collected_at": "2026-10-25",
        "papers": [
            {
                "rank": 1,
                "title": "测试方治疗示例病的随机对照研究",
                "abstract": "纳入80例示例病患者并随机分组。结论：测试方可显著改善示例病。",
                "authors": "甲;乙",
                "source": "示例期刊",
                "date": "2026-01-01",
                "keywords": ["示例病"],
                "is_modified": False,
                "western_diseases": ["示例病"],
            },
            {
                "rank": 2,
                "title": "加味测试方治疗另一疾病",
                "abstract": "加味测试方用于另一疾病患者。",
                "is_modified": True,
                "western_diseases": ["另一疾病"],
            },
        ],
    }


def test_pipeline_creates_a_separate_clinical_lane_and_excludes_modified_formula():
    target = discovery()
    before = copy.deepcopy(target)
    result = run_literature_pipeline(
        papers(), target, formula="测试方",
        frequency_candidates=[{"raw_names": ["频次候选"], "paper_count": 2}],
    )

    assert result["status"] == "ready"
    assert result["frequency_candidates"][0]["raw_names"] == ["频次候选"]
    assert [(row["term"], row["tier"]) for row in result["literature_priority"]] == [
        ("示例病", "P1")]
    assert result["literature_priority"][0]["ontology_merge_eligible"] is False
    assert result["screening_summary"]["eligible_original_formula_count"] == 1
    assert result["screening_summary"]["excluded_count"] == 1
    assert result["screening_summary"]["excluded_records"][0]["screening_decision"] == "excluded_modified_formula"
    assert result["comparison"]["original_top_preserved"] is True
    assert result["original_open_targets_top"][0]["disease_name"] == "cancer"
    assert target == before


def test_pipeline_uses_reviewed_exact_mapping_without_changing_ot_order(tmp_path):
    catalog = {
        "schema_version": "literature-concept-map-v1",
        "formula": "测试方",
        "entries": [{
            "term": "示例病", "review_status": "reviewed", "mappings": [{
                "entity_id": "MONDO_123", "entity_name": "example disease",
                "entity_type": "disease", "relation": "exact",
                "source": "test", "release": "1", "promote": True,
            }],
        }],
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(__import__("json").dumps(catalog, ensure_ascii=False), encoding="utf-8")
    result = run_literature_pipeline(
        papers(), discovery(), formula="测试方", concept_catalog=catalog,
        concept_catalog_path=catalog_path)

    priority = result["literature_priority"][0]
    assert priority["open_targets_disease_id"] == "MONDO_123"
    assert priority["open_targets_display_rank"] == 2
    assert result["evidence_provenance"]["concept_mapping_mode"] == "reviewed_catalog"


def test_pipeline_snapshot_has_checksum_manifest(tmp_path):
    result = run_literature_pipeline(papers(), discovery(), formula="测试方")
    output = tmp_path / "pipeline"
    save_literature_pipeline(result, output)
    manifest = read_json(output / "manifest.json")
    assert manifest["original_top_preserved"] is True
    assert set(manifest["files"]) == {"literature_evidence_pipeline.json"}


def test_pipeline_accepts_current_count_only_candidate_layers():
    target = discovery()
    target["candidates"] = target["candidate_layers"]["top"]
    target["candidate_layers"] = {"counts": {"top": 2}}
    result = run_literature_pipeline(papers(), target, formula="测试方")
    assert [row["disease_name"] for row in result["original_open_targets_top"]] == [
        "cancer", "example disease"]
