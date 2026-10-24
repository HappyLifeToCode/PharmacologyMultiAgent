import json

from pharm.core.common import digest, read_json
from pharm.literature.cnki_snapshot import import_cnki_snapshot, normalise_cnki_snapshot


def paper(rank=1, title="温经汤治疗痛经", **extra):
    value = {
        "rank": rank,
        "title": title,
        "url": "https://kns.cnki.net/example?token=temporary",
        "authors": "甲;乙",
        "source": "测试期刊",
        "date": "2026-01-02",
        "keywords": ["温经汤", "痛经"],
        "abstract": "研究温经汤干预痛经。",
        "journal_level": "核心",
        "cif": 1.2,
        "is_modified": False,
        "western_diseases": ["痛经"],
        "note": None,
    }
    value.update(extra)
    return value


def test_normalises_new_cnki_snapshot_without_promoting_disease_terms():
    payload = {
        "keyword": "温经汤", "total_results": 100, "collected_at": "2026-10-22",
        "first_collected_at": "2026-10-22T01:00:00+00:00", "papers": [paper()],
    }
    result = normalise_cnki_snapshot(payload, requested_limit=1)
    source = result["source_results"][0]
    record = source["records"][0]
    assert result["schema_version"] == "literature-search-v1"
    assert result["status"] == "succeeded"
    assert result["ranking_effect"] == "none"
    assert record["authors"] == ["甲", "乙"]
    assert record["pmid"] is None and record["doi"] is None
    assert record["source_metadata"]["western_diseases"] == ["痛经"]
    assert "evidence_grade" not in record


def test_legacy_snapshot_allows_missing_optional_fields_and_uses_stable_id():
    legacy = {"keyword": "芍药甘草汤", "collected_at": "2026-08-30",
              "papers": [paper(url=None, western_diseases=None, journal_level=None)]}
    first = normalise_cnki_snapshot(legacy, requested_limit=1)
    legacy["papers"][0]["url"] = "https://kns.cnki.net/new-temporary-token"
    second = normalise_cnki_snapshot(legacy, requested_limit=1)
    assert first["source_results"][0]["records"][0]["record_id"] == second["source_results"][0]["records"][0]["record_id"]
    assert any("缺少稳定详情 URL" in warning for warning in first["source_results"][0]["warnings"])


def test_partial_snapshot_and_invalid_rows_are_reported():
    payload = {"keyword": "温经汤", "papers": [paper(), {"abstract": "no title"}, "bad"]}
    result = normalise_cnki_snapshot(payload, requested_limit=3)
    source = result["source_results"][0]
    assert result["status"] == "partial"
    assert source["collected_count"] == 1
    assert any("少于请求" in warning for warning in source["warnings"])
    assert any("缺少标题" in warning for warning in source["warnings"])


def test_import_bundle_has_hashes_and_is_idempotent(tmp_path):
    source = tmp_path / "papers.json"
    source.write_text(json.dumps({"keyword": "温经汤", "collected_at": "2026-10-22",
                                  "papers": [paper()]}, ensure_ascii=False), encoding="utf-8")
    directory, first = import_cnki_snapshot(source, tmp_path / "output", requested_limit=1)
    directory_again, second = import_cnki_snapshot(source, tmp_path / "output", requested_limit=1)
    assert directory_again == directory
    assert first == second
    manifest = read_json(directory / "manifest.json")
    assert manifest["review_status"] == "collected_unreviewed"
    assert manifest["source_sha256"] == digest(source)
    for name, expected in manifest["files"].items():
        assert digest(directory / name) == expected
