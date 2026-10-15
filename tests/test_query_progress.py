import pytest
from fastapi.testclient import TestClient

from pharm.agent import progress
from pharm.core.common import write_json
from server import app as backend


def test_progress_sidecar_does_not_create_query_archive(tmp_path):
    directory = tmp_path / "local/agent/forward/forward_check"
    progress.bind(progress.path_for(directory))
    try:
        progress.report("read", "读取分片", current=2, total=14, unit="个分片")
        assert not directory.exists()
        value = progress.snapshot(tmp_path, directory.name)
        assert (value["current"], value["total"], value["label"]) == (2, 14, "读取分片")
        assert value["elapsed_seconds"] >= 0
    finally:
        progress.bind(None)
    assert progress.snapshot(tmp_path, "../outside") is None


def test_status_routes_expose_progress_for_both_directions(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    for direction in ("forward", "reverse"):
        run_id = direction + "_check"
        write_json(tmp_path / "local/agent/jobs/check.json", {
            "request_id": "check", "run_id": run_id, "status": "running", "direction": direction,
            "service_instance_id": backend.forward_jobs.manager.instance_id,
        })
        progress.bind(progress.path_for(tmp_path / "local/agent" / direction / run_id))
        try: progress.report("matching", "匹配证据", current=4, total=9)
        finally: progress.bind(None)
        with TestClient(backend.app, base_url="http://localhost") as client:
            status = client.get("/api/agent/" + direction + "/request/check").json()
        assert status["status"] == "running" and status["progress"]["current"] == 4


def test_arrow_filter_matches_original_python_with_nulls_whitespace_and_duplicates():
    pa = pytest.importorskip("pyarrow")
    from pharm.diseases.open_targets_local import _matching_rows
    values = [None, "", " T1 ", "\x1cT1\x1f", "T2", "\u3000T1\u00a0", "T1", "T1x"]
    batch = pa.record_batch({"targetId": values, "value": list(range(len(values)))})
    expected = [row for row in batch.to_pylist() if str(row.get("targetId") or "").strip() in {"T1"}]
    assert _matching_rows(batch, "targetId", {"T1"}) == expected
