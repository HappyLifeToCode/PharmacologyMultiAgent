from scripts.update_disease_labels import build_glossary
import json
from pathlib import Path


def test_glossary_language_priority_conflicts_and_exact_overrides():
    def row(identifier, label, language, item="Q1"):
        return {"mondo": {"value": identifier}, "label": {"value": label, "xml:lang": language},
                "item": {"value": "http://www.wikidata.org/entity/" + item}}
    data = build_glossary({"results": {"bindings": [
        row("MONDO_0000001", "繁體疾病", "zh"), row("MONDO_0000001", "简体疾病", "zh-hans"),
        row("MONDO_0000002", "名称甲", "zh", "Q2"), row("MONDO_0000002", "名称乙", "zh", "Q3"),
        row("MONDO_0000003", "English only", "zh"), row("not-an-id", "错误实体", "zh"),
    ]}}, {"description": "display", "entries": [
        {"id": "MONDO:0000004", "name": "Disease A", "label": "疾病甲", "input_aliases": ["甲病"]},
    ]})
    assert data["by_id"]["MONDO:0000001"]["label"] == "简体疾病"
    assert data["excluded_conflicting_ids"] == ["MONDO:0000002"]
    assert "MONDO:0000003" not in data["by_id"]
    assert data["by_name"]["disease a"]["label"] == "疾病甲"
    assert data["by_id"]["MONDO:0000004"]["input_aliases"] == ["甲病"]


def test_obesity_and_binge_eating_are_distinct_display_entities():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "server/static/disease-labels.json").read_text(encoding="utf-8"))
    assert data["by_id"]["MONDO:0011122"]["label"] == "肥胖症"
    assert data["by_id"]["MONDO:0005582"]["label"] == "暴食障碍"
    assert data["by_id"]["MONDO:0005452"]["label"] == "神经性贪食症"
    assert data["by_name"]["obesity disorder"]["label"] != data["by_name"]["binge eating disorder"]["label"]
    assert data["by_name"]["depressive disorder"]["label"] == "抑郁障碍"
    assert data["by_name"]["major depressive disorder"]["label"] == "重性抑郁障碍"
    assert data["by_id"]["EFO:0009833"]["label"] == "肾损伤"
    assert "指标" in data["by_id"]["EFO:0600064"]["label"]
