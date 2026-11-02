import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from pharm.agent import disease_input as resolver, disease_translation as translation
from server import app as backend


@pytest.fixture
def catalog(monkeypatch):
    entities = {key: {"disease_id": key, "disease_name": name} for key, name in
                [("MONDO_1", "breast cancer"), ("MONDO_2", "breast cancer subtype")]}
    names = {"breast cancer": {"MONDO_1"}, "乳腺癌": {"MONDO_1"},
             "breast cancer subtype": {"MONDO_2"}, "shared alias": {"MONDO_1", "MONDO_2"}}
    monkeypatch.setattr(resolver, "_catalog_result", lambda *args: (
        entities, names, {key.casefold(): {key} for key in entities}, ("乳腺癌",), {}))
    return entities, names


def test_translation_opt_in_exact_lookup_and_explicit_selection(catalog, monkeypatch):
    calls = []
    def suggest(query):
        calls.append(query)
        return ["breast cancer subtype", "invented disease", "breast"], False
    monkeypatch.setattr(translation, "suggest", suggest)
    assert resolver.resolve("未登记病名")["status"] == "not_found"
    assert resolver.resolve("乳腺癌", allow_translation=True)["status"] == "ready"
    assert calls == []
    result = resolver.resolve("未登记病名", allow_translation=True)
    assert result["status"] == "needs_selection"
    assert [item["disease_id"] for item in result["choices"]] == ["MONDO_2"]
    assert result["match_type"] == "translation_exact"
    # Raw Chinese input cannot become a permanent alias or trust a forged ID.
    with pytest.raises(ValueError, match="不属于"):
        resolver.resolve("未登记病名", disease_id="MONDO_2")
    assert resolver.resolve(disease_id="MONDO_2")["status"] == "ready"


def test_unknown_subtype_never_auto_selects_parent(catalog):
    for name in ["遗传性乳腺癌", "乳腺癌未知亚型", "乳腺癌和其他病"]:
        assert resolver.resolve(name)["status"] != "ready"
    assert resolver.resolve("什么中药可以治疗乳腺癌？")["status"] == "ready"


def test_ambiguous_translation_and_failure_remain_reviewable(catalog, monkeypatch):
    monkeypatch.setattr(translation, "suggest", lambda query: (["shared alias"], True))
    assert resolver.resolve("未登记病名", allow_translation=True)["candidate_count"] == 2
    monkeypatch.setattr(translation, "suggest", lambda query: (["breast"], False))
    result = resolver.resolve("未登记病名", allow_translation=True)
    assert result["status"] == "not_found" and result["translation"]["status"] == "no_exact_match"
    def fail(query):
        raise RuntimeError("翻译服务暂不可用")
    monkeypatch.setattr(translation, "suggest", fail)
    assert resolver.resolve("未登记病名", allow_translation=True)["translation"]["status"] == "unavailable"


def test_cache_reuses_terms_but_rechecks_current_catalog(tmp_path, catalog, monkeypatch):
    calls = []
    def runner(prompt, log, **kwargs):
        calls.append(kwargs)
        assert '"input": "未登记病名"' in prompt
        return '{"terms":["breast cancer"]}'
    original = translation.suggest
    monkeypatch.setattr(translation, "suggest", lambda query: original(query, root=tmp_path, runner=runner))
    assert resolver.resolve("未登记病名", allow_translation=True)["translation"]["cache_hit"] is False
    catalog[1].pop("breast cancer")
    result = resolver.resolve("未登记病名", allow_translation=True)
    assert result["status"] == "not_found" and result["translation"]["cache_hit"] is True
    assert len(calls) == 1 and calls[0]["browser"] is False and calls[0]["timeout"] == 60
    assert calls[0]["model"] == "test-runner"


@pytest.mark.parametrize("payload", [{"terms": "cancer"}, {"terms": ["MONDO:123"]}, {"terms": ["疾病"]},
                                      {"terms": ["cancer"] * 6}, {"terms": [1]}, {"terms": [], "id": "MONDO_1"}])
def test_invalid_model_payload_rejected(payload):
    with pytest.raises(ValueError):
        translation.validate(payload)


def test_failed_translation_is_not_cached_and_lock_released(tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fixture", 60)
    with pytest.raises(RuntimeError, match="暂不可用"):
        translation.suggest("病名", root=tmp_path, runner=timeout)
    assert not list((tmp_path / "local/agent/disease-translation").glob("*.json"))
    assert translation.suggest("病名", root=tmp_path, runner=lambda *a, **k: '{"terms":[]}') == ([], False)


def test_api_translation_flag_only_on_resolver(catalog, monkeypatch):
    monkeypatch.setattr(translation, "suggest", lambda query: (["breast cancer"], False))
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/agent/reverse/resolve", json={"text": "未知病名"}).json()["status"] == "not_found"
        result = client.post("/api/agent/reverse/resolve", json={"text": "未知病名", "allow_translation": True})
        assert result.status_code == 200 and result.json()["status"] == "needs_selection"
        assert client.post("/api/agent/reverse/resolve", json={"text": "未知病名", "allow_translation": "true"}).status_code == 400
        assert client.post("/api/agent/reverse", json={"text": "未知病名", "allow_translation": True}).status_code == 400
