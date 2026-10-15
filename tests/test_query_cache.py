import pytest

from pharm.agent import query_cache as cache
from pharm.core.common import read_json, write_json


@pytest.fixture
def environment(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("snapshot version 1", encoding="utf-8")
    code = tmp_path / "calculation.py"
    code.write_text("version = 1", encoding="utf-8")
    monkeypatch.setattr(cache, "_sources", lambda: [source, code])
    monkeypatch.delenv("PHARM_AGENT_QUERY_CACHE", raising=False)
    return source, code


def calculate(directory, *, status="succeeded"):
    directory.mkdir(parents=True, exist_ok=False)
    result = {"status": status, "created_at": "2026-10-14T00:00:00Z", "scientific_complete": False,
              "candidates": [{"gene": "TP53", "score": .82, "evidence": [{"id": 2}, {"id": 1}]}]}
    write_json(directory / "result.json", result)
    write_json(directory / "herb_targets.json", {"genes": ["TP53"]})
    (directory / "associations.csv").write_text("original source evidence", encoding="utf-8")
    return {"run_id": directory.name, "output_dir": str(directory), "result": result}


def directory(root, name):
    return root / "local/agent/forward" / ("forward_" + name)


def test_hit_avoids_compute_and_keeps_original_evidence(tmp_path, environment):
    body = {"herbs": ["白芍"], "top_k": 5}
    origin = directory(tmp_path, "original")
    first = cache.execute(body, origin, "forward", lambda: calculate(origin))
    before = {path.name: path.read_bytes() for path in origin.iterdir()}
    # Primary job metadata is mutable and intentionally not part of evidence.
    write_json(origin / "job.json", {"status": "succeeded"})
    second_dir = directory(tmp_path, "reused")
    second = cache.execute(body, second_dir, "forward", lambda: pytest.fail("cache hit recalculated"))
    assert second["run_id"] != first["run_id"]
    assert second["result"]["execution"]["reused_from"] == origin.name
    assert second["result"]["execution"]["cache_hit"] is True
    assert second["result"]["candidates"] == first["result"]["candidates"]
    assert second["result"]["scientific_complete"] is False
    assert read_json(second_dir / "herb_targets.json") == {"genes": ["TP53"]}
    assert all((origin / name).read_bytes() == data for name, data in before.items())


@pytest.mark.parametrize("change", ["input", "top_k", "source", "code", "environment", "missing", "origin", "payload", "manifest", "disabled"])
def test_changes_and_corruption_recompute(tmp_path, monkeypatch, environment, change):
    body = {"herbs": ["白芍"], "top_k": 5}
    origin = directory(tmp_path, "first")
    first = cache.execute(body, origin, "forward", lambda: calculate(origin))
    entry = tmp_path / "local/agent/query-cache" / first["result"]["execution"]["source_fingerprint"]
    if change == "input": body["herbs"] = ["甘草"]
    elif change == "top_k": body["top_k"] = 10
    elif change in {"source", "code"}: environment[change == "code"].write_text("new bytes", encoding="utf-8")
    elif change == "environment": monkeypatch.setenv("PHARM_OPEN_TARGETS_RELEASE", "changed")
    elif change == "missing": environment[0].unlink()
    elif change == "origin": (origin / "associations.csv").write_text("changed evidence", encoding="utf-8")
    elif change == "payload": (entry / "result.json.gz").write_bytes(b"broken gzip")
    elif change == "manifest": write_json(entry / "manifest.json", [])
    elif change == "disabled": monkeypatch.setenv("PHARM_AGENT_QUERY_CACHE", "false")
    target = directory(tmp_path, "second")
    calls = []
    def compute():
        calls.append(True)
        return calculate(target)
    second = cache.execute(body, target, "forward", compute)
    assert calls == [True]
    assert second["result"]["execution"]["cache_hit"] is False


def test_partial_and_source_changed_during_compute_are_not_cached(tmp_path, environment):
    for name in ("partial", "changed"):
        target = directory(tmp_path, name)
        def compute():
            if name == "changed": environment[0].write_text("changed during query", encoding="utf-8")
            return calculate(target, status="partial" if name == "partial" else "succeeded")
        cache.execute({"herbs": ["白芍"]}, target, "forward", compute)
    root = tmp_path / "local/agent/query-cache"
    assert not root.exists()


def test_cache_store_failure_does_not_fail_completed_result(tmp_path, monkeypatch, environment):
    def fail(*args): raise OSError("cache disk unavailable")
    monkeypatch.setattr(cache, "store", fail)
    target = directory(tmp_path, "success")
    response = cache.execute({"text": "白芍"}, target, "forward", lambda: calculate(target))
    assert response["result"]["status"] == "succeeded"
    assert read_json(target / "result.json")["status"] == "succeeded"


def test_restore_write_failure_does_not_restart_compute(tmp_path, monkeypatch, environment):
    body = {"text": "白芍"}
    origin = directory(tmp_path, "original")
    cache.execute(body, origin, "forward", lambda: calculate(origin))
    target = directory(tmp_path, "failed_write")
    def fail(*args): raise OSError("archive write failed")
    monkeypatch.setattr(cache, "write_json", fail)
    with pytest.raises(OSError, match="archive write failed"):
        cache.execute(body, target, "forward", lambda: pytest.fail("partial archive recalculated"))


def test_manifest_cannot_escape_original_archive(tmp_path, environment):
    body = {"text": "白芍"}
    origin = directory(tmp_path, "original")
    response = cache.execute(body, origin, "forward", lambda: calculate(origin))
    key = response["result"]["execution"]["source_fingerprint"]
    manifest_path = tmp_path / "local/agent/query-cache" / key / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["origin_files"]["../outside"] = "not a checksum"
    write_json(manifest_path, manifest)
    target = directory(tmp_path, "fresh")
    assert cache.execute(body, target, "forward", lambda: calculate(target))["result"]["execution"]["cache_hit"] is False


def test_cache_budget_eviction_preserves_research_archives(tmp_path, monkeypatch, environment):
    monkeypatch.setattr(cache, "MAX_ENTRIES", 1)
    for number in range(3):
        target = directory(tmp_path, str(number))
        cache.execute({"text": "白芍", "top_k": number + 1}, target, "forward", lambda: calculate(target))
    root = tmp_path / "local/agent/query-cache"
    assert len(list(root.iterdir())) == 1
    assert all((directory(tmp_path, str(number)) / "associations.csv").is_file() for number in range(3))
