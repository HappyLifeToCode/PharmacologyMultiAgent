import csv
import json

import pytest
from fastapi.testclient import TestClient

from pharm.batman import formula_catalog as catalog
from pharm.agent import forward
from server import app as backend


@pytest.fixture
def formula_data(tmp_path, monkeypatch):
    data = tmp_path / "batman"
    data.mkdir()
    with (data / "herb_browse.txt").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["Pinyin.Name", "Chinese.Name", "English.Name", "Latin.Name", "Ingredients"])
        for py, cn in [("DANG GUI", "当归"), ("BAI SHAO", "白芍"), ("CHI SHAO", "赤芍"),
                       ("SHU DI HUANG", "熟地黄"), ("GAN CAO", "甘草"), ("ZHI GAN CAO", "炙甘草")]:
            writer.writerow([py, cn, "", py, "compound(1)"])
    formulas = data / "formula_browse.txt"
    formulas.write_text("Pinyin.Name\tChinese.Name\tPinyin.composition\n"
                        "SI WU TANG\t四物汤\tDANG GUI,BAI SHAO,SHU DI\n"
                        "SI WU TANG\t四物汤\tSHU DI HUANG,BAI SHAO,DANG GUI,DANG GUI\n"
                        "SI WU TANG\t四物汤\tDANG GUI,CHI SHAO\n"
                        "DAN YI FANG\t单一方\tDANG GUI\n"
                        "WEI MING FANG\t未明方\tSHAO YAO,UNKNOWN\n"
                        "KONG FANG\t空方\tNA\n"
                        "WEN JING TANG\t温经汤\tDANG GUI,BAI SHAO\n"
                        "JIA WEI SI WU TANG\t加味四物汤\tDANG GUI\n", encoding="utf-8")
    monkeypatch.setenv("PHARM_BATMAN_DATA_DIR", str(data))
    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    return data


def test_same_name_same_composition_deduplicates_without_picking_first(formula_data):
    result = catalog.resolve(text="四物汤")
    assert result["status"] == "selection_required"
    assert "selected_variant_id" not in result
    assert len(result["choices"]) == 2
    first = result["choices"][0]
    assert first["source_rows"] == [2, 3]
    assert first["mappings"][-1]["canonical"] == "熟地黄"
    assert first["mappings"][-1]["status"] == "alias"
    assert "未提供出处" in first["source_note"]
    assert not any("book" in choice for choice in result["choices"])
    with pytest.raises(catalog.SelectionRequired):
        forward._resolve_input(None, None, "四物汤")


def test_variant_ids_survive_row_reordering(formula_data):
    before = {choice["id"] for choice in catalog.resolve(text="四物汤")["choices"]}
    path = formula_data / "formula_browse.txt"
    header, *lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([header, *reversed(lines)]) + "\n", encoding="utf-8")
    after = {choice["id"] for choice in catalog.resolve(text="四物汤")["choices"]}
    assert before == after


def test_explicit_choice_and_saved_default_keep_real_composition(formula_data, tmp_path):
    selected = catalog.resolve(text="四物汤")["choices"][1]
    body = {"text": "四物汤", "formula_variant_id": selected["id"]}
    result = catalog.save_preference(body)
    assert result["herbs"] == ["当归", "赤芍"]
    restored = catalog.resolve(text="四物汤")
    assert restored["status"] == "ready"
    assert restored["selected_variant_id"] == selected["id"]
    assert restored["resolution"]["saved_default"]
    assert json.loads((tmp_path / "local/agent/formula_preferences.json").read_text(encoding="utf-8"))
    assert catalog.resolve(text="四物汤", use_saved=False)["status"] == "selection_required"


def test_data_change_invalidates_saved_choice(formula_data):
    selected = catalog.resolve(text="四物汤")["choices"][0]
    catalog.save_preference({"text": "四物汤", "formula_variant_id": selected["id"]})
    with (formula_data / "formula_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("OTHER\t另一方\tGAN CAO\n")
    result = catalog.resolve(text="四物汤")
    assert result["status"] == "selection_required"
    assert any("更新" in warning for warning in result["warnings"])


def test_frozen_selection_rejects_changed_source_before_computation(formula_data):
    ready = catalog.resolve(text="单一方")
    with (formula_data / "herb_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("OTHER\t其他药材\t\tother\tcompound(2)\n")
    with pytest.raises(ValueError, match="已更新"):
        catalog.resolve(**ready["query_input"])


def test_existing_builtin_retained_and_can_be_explicitly_changed(formula_data):
    result = catalog.resolve(text="温经汤")
    assert result["status"] == "ready"
    assert len(result["herbs"]) == 12
    assert result["resolution"]["source"] == "standard_reference_pending_user_confirmation"
    choice = next(choice for choice in result["choices"] if choice["id"].startswith("batman_"))
    selected = catalog.resolve(text="温经汤", formula_variant_id=choice["id"])
    assert selected["herbs"] == ["当归", "白芍"]


def test_unmatched_ambiguous_and_prepared_herbs_are_not_substituted(formula_data):
    preview = catalog.resolve(text="未明方")
    assert preview["status"] == "selection_required"
    result = catalog.resolve(text="未明方", formula_variant_id=preview["choices"][0]["id"])
    assert result["herbs"] == ["SHAO YAO", "UNKNOWN"]
    assert result["resolution"]["unresolved"] == ["SHAO YAO", "UNKNOWN"]
    assert not result["resolution"]["composition_complete"]
    direct = catalog.resolve(herbs=["白芍", "赤芍", "甘草", "炙甘草"])
    assert direct["herbs"] == ["白芍", "赤芍", "甘草", "炙甘草"]
    assert direct["herb_candidates"]["甘草"] == ["甘草"]
    assert direct["herb_candidates"]["炙甘草"] == ["炙甘草"]


def test_user_edited_composition_is_distinct_from_original_and_persists(formula_data):
    choice = catalog.resolve(text="未明方")["choices"][0]
    body = {"text": "未明方", "formula_variant_id": choice["id"], "formula_herbs": ["白芍", "炙甘草"]}
    result = catalog.save_preference(body)
    assert result["resolution"]["kind"] == "user_composition"
    assert result["resolution"]["raw_composition"] == ["SHAO YAO", "UNKNOWN"]
    assert result["herbs"] == ["白芍", "炙甘草"]
    assert catalog.resolve(text="未明方")["herbs"] == ["白芍", "炙甘草"]
    with pytest.raises(ValueError, match="选择"):
        catalog.resolve(text="未明方", formula_herbs=["赤芍"])


def test_natural_input_longest_name_and_multiple_formulas(formula_data):
    assert catalog.resolve(text="请问，单一方关联哪些疾病？")["formula"] == "单一方"
    assert catalog.resolve(text="请分析加味四物汤关联哪些疾病")["formula"] == "加味四物汤"
    with pytest.raises(ValueError, match="单独输入"):
        catalog.resolve(text="四物汤和单一方")
    with pytest.raises(ValueError, match="实际|完整"):
        catalog.resolve(text="四物汤加甘草")
    with pytest.raises(ValueError, match="排除"):
        catalog.resolve(text="不用四物汤")


def test_missing_composition_needs_edit_and_wrong_choice_is_rejected(formula_data):
    choice = catalog.resolve(text="空方")
    assert choice["status"] == "selection_required"
    with pytest.raises(ValueError, match="不属于"):
        catalog.resolve(text="单一方", formula_variant_id=choice["choices"][0]["id"])
    with pytest.raises(ValueError, match="选择"):
        catalog.resolve(text="四物汤", formula_herbs=["白芍"])
    with pytest.raises(ValueError, match="只适用"):
        catalog.resolve(herbs=["白芍"], formula_variant_id="bad")


def test_duplicate_herb_identity_does_not_pick_first(formula_data):
    with (formula_data / "herb_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("DANG GUI\t另一药材\t\tother species\tcompound(2)\n")
    result = catalog.resolve(text="单一方")
    assert result["status"] == "selection_required"
    selected = catalog.resolve(text="单一方", formula_variant_id=result["selected_variant_id"])
    assert selected["resolution"]["mappings"][0]["status"] == "ambiguous"
    assert selected["herb_candidates"]["DANG GUI"] == []


def test_missing_chinese_label_uses_registered_alias_without_replacing_compounds(formula_data):
    with (formula_data / "herb_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("BAI SHAO YAO\tNA\t\tNA\tother-compound(999)\n")
    mapping = catalog.catalog()["map_herb"]("BAI SHAO YAO")
    assert mapping["display_name"] == "白芍"
    assert "原记录" in mapping["display_note"]
    assert mapping["chinese_name_missing"]
    assert mapping["canonical"] == "BAI SHAO YAO"
    assert mapping["candidates"] == ["BAI SHAO YAO"]
    assert mapping["status"] == "exact"
    resolved = catalog.resolve(herbs=["BAI SHAO YAO"])
    assert resolved["herb_candidates"] == {"BAI SHAO YAO": ["BAI SHAO YAO"]}


def test_missing_chinese_name_without_registered_alias_is_not_invented(formula_data):
    with (formula_data / "herb_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("CHI SHAO YAO\tNA\t\tNA\tother-compound(999)\n")
    mapping = catalog.catalog()["map_herb"]("CHI SHAO YAO")
    assert mapping["display_name"] == "CHI SHAO YAO"
    assert mapping["chinese_name_missing"]
    assert mapping["status"] == "exact"
    assert mapping["candidates"] == ["CHI SHAO YAO"]


def test_shao_yao_chinese_is_recognized_without_substituting_a_specific_material(formula_data):
    for raw in ("SHAO YAO", "芍药"):
        mapping = catalog.catalog()["map_herb"](raw)
        assert mapping["display_name"] == "芍药"
        assert mapping["unresolved_label"] == "需明确白芍/赤芍"
        assert mapping["status"] == "unmatched" and mapping["canonical"] == raw
        assert mapping["candidates"] == [raw]


def test_gan_di_huang_display_preserves_exact_original_compound_record(formula_data):
    with (formula_data / "herb_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("GAN DI HUANG\tNA\t\tNA\tother-compound(999)\n")
    mapping = catalog.catalog()["map_herb"]("GAN DI HUANG")
    assert mapping["display_name"] == "干地黄" and mapping["chinese_name_missing"]
    assert mapping["status"] == "exact" and mapping["candidates"] == ["GAN DI HUANG"]
    assert mapping["canonical"] == "GAN DI HUANG"


def test_display_only_labels_leave_formula_ids_and_saved_preferences_valid(formula_data, tmp_path, monkeypatch):
    labels = tmp_path / "display.json"
    labels.write_text('{"labels":{}}', encoding="utf-8")
    monkeypatch.setattr(catalog, "DISPLAY_FILE", labels)
    before = catalog.catalog()
    preview = catalog.resolve(text="单一方")
    catalog.save_preference({"text": "单一方", "formula_variant_id": preview["selected_variant_id"]})
    labels.write_text(json.dumps({"labels":{"SHAO YAO":{"name":"芍药","note":"仅中文显示","unresolved_label":"需明确白芍/赤芍"}}}), encoding="utf-8")
    after = catalog.catalog()
    assert before["fingerprint"] == after["fingerprint"]
    assert set(before["versions"]) == set(after["versions"])
    assert catalog.resolve(text="单一方")["resolution"]["saved_default"]


def test_api_preflight_does_not_start_jobs_and_direct_ambiguous_post_is_blocked(formula_data, monkeypatch):
    calls = []

    async def fake_run(*args, **kwargs):
        calls.append(args[1])
        return {"run_id": "forward_fixture", "result": {"data_origin": "synthetic_engineering"}}

    monkeypatch.setattr(backend.forward_jobs.manager, "run", fake_run)
    with TestClient(backend.app, base_url="http://localhost") as client:
        preview = client.post("/api/formulas/resolve", json={"text": "四物汤"}).json()
        assert preview["status"] == "selection_required"
        assert client.post("/api/agent/forward", json={"text": "四物汤"}).status_code == 409
        assert not calls
        body = {"text": "四物汤", "formula_variant_id": preview["choices"][1]["id"]}
        assert client.post("/api/formulas/preference", json=body).status_code == 200
        assert client.post("/api/agent/forward", json={"text": "四物汤"}).status_code == 200
        assert calls[0]["formula_variant_id"] == body["formula_variant_id"]
        assert client.post("/api/formulas/preference", json=body, headers={"Origin": "https://other.example"}).status_code == 403
        assert client.post("/api/formulas/resolve", json={"text": "四物汤", "use_saved": "yes"}).status_code == 400
        assert client.post("/api/formulas/resolve", json={"text": "四物汤", "path": "../bad"}).status_code == 400


def test_forward_archive_preserves_selected_raw_and_edited_composition(formula_data, monkeypatch, tmp_path):
    selected = catalog.resolve(text="未明方")["choices"][0]

    def targets(candidates, threshold):
        assert candidates == {"白芍": ["BAI SHAO"], "UNKNOWN": ["UNKNOWN"]}
        return {"genes": ["TP53"], "relations": [{"herb": "白芍", "gene_symbol": "TP53", "evidence": "known"}],
                "unmatched": ["UNKNOWN"], "per_herb": {"白芍": {"batman_name": "BAI SHAO", "compounds": 1, "relations": 1}}}

    def diseases(symbols, output_dir, **kwargs):
        output_dir.mkdir(parents=True)
        with (output_dir / "associations.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "score", "source", "extra"])
            writer.writeheader()
            writer.writerow({"disease": "MONDO:1 | Engineering fixture", "gene_symbol": "TP53", "score": "0.8",
                             "source": "open_targets", "extra": '{"entity_type":"disease"}'})
        return {"data_origin": "synthetic_engineering"}

    monkeypatch.setattr(forward, "query_local_targets", targets)
    monkeypatch.setattr(forward, "collect_open_targets_by_targets", diseases)
    monkeypatch.setattr(forward, "collect_monarch_by_targets", lambda *a, **kw: {"records": [], "stats": {}, "snapshot": {}})
    response = forward.query(text="未明方", formula_variant_id=selected["id"], formula_herbs=["白芍", "UNKNOWN"],
                             output_dir=tmp_path / "fixture-run")
    result = response["result"]
    resolution = result["input"]["formula_resolution"]
    assert resolution["variant_id"] == selected["id"]
    assert resolution["kind"] == "user_composition"
    assert resolution["raw_composition"] == ["SHAO YAO", "UNKNOWN"]
    assert resolution["unresolved"] == ["UNKNOWN"]
    assert resolution["formula_sha256"]
    assert result["input"]["herbs"] == ["白芍", "UNKNOWN"]
    assert result["unmatched_herbs"] == ["UNKNOWN"]
    assert result["scientific_complete"] is False
    saved = json.loads((tmp_path / "fixture-run/result.json").read_text(encoding="utf-8"))
    assert saved["input"] == result["input"]
