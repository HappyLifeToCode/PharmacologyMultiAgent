from fastapi.testclient import TestClient

from server import app as backend
from pharm_demo.common import write_json


def test_artifact_allowlist_and_post_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    directory = tmp_path / "runs/test_run"
    directory.mkdir(parents=True)
    (directory / "allowed.csv").write_text("gene_symbol\nTP53\n")
    (directory / "stderr.log").write_text("private")
    (directory / "prompt.txt").write_text("private prompt")
    write_json(directory / "manifest.json", {"run_id": "test_run", "stages": {"herb_targets": {"artifacts": ["allowed.csv", "prompt.txt"]}}})
    calls = []
    monkeypatch.setattr(backend, "start", lambda *args, **kwargs: calls.append(args) or "test_run")
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.get("/artifacts/test_run/allowed.csv").status_code == 200
        assert client.get("/artifacts/test_run/stderr.log").status_code == 404
        assert client.get("/artifacts/test_run/prompt.txt").status_code == 404
        assert client.get("/api/runs/test_run").json()["stages"]["herb_targets"]["artifacts"] == ["allowed.csv"]
        assert client.post("/api/run", json={"task_id": "x"}, headers={"Origin": "https://other.example"}).status_code == 403
        assert client.post("/api/run", json=[]).status_code == 400
        assert client.get("/api/runs/test_run", headers={"Host": "other.example"}).status_code == 403
        assert not calls
        assert client.post("/api/run", json={"task_id": "x"}, headers={"Origin": "http://localhost"}).status_code == 200
        assert len(calls) == 1


def test_signoff_endpoint_validates_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    run = tmp_path / "runs" / "test_run"
    inter = run / "intersection" / "attempt_01"
    inter.mkdir(parents=True)
    write_json(inter / "intersection.json", {"herb_count": 1, "disease_count": 1,
                                             "intersection_count": 1, "genes": ["A"]})
    (inter / "venny.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    write_json(inter / "venny_execution.json", {"status": "succeeded"})
    (inter / "venny_results.txt").write_text("A", encoding="utf-8")
    write_json(run / "manifest.json", {
        "run_id": "test_run", "mode": "live", "status": "partial",
        "metrics": {"herb_count": 1, "disease_count": 1, "intersection_count": 1},
        "stages": {"intersection": {"status": "succeeded", "directory": "intersection/attempt_01"}}})
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/runs/test_run/signoff", json={"reviewer": ""}).status_code == 400
        assert client.post("/api/runs/test_run/signoff", json={"reviewer": "x" * 41}).status_code == 400
        assert client.post("/api/runs/nope/signoff", json={"reviewer": "测试员"}).status_code == 404
        response = client.post("/api/runs/test_run/signoff",
                               json={"reviewer": "测试员", "note": "复核完成"})
        assert response.status_code == 200
        assert response.json()["human_review"]["reviewer"] == "测试员"
        detail = client.get("/api/runs/test_run").json()
        assert detail["human_review"]["reviewer"] == "测试员"
        again = client.post("/api/runs/test_run/signoff", json={"reviewer": "另一个人"})
        assert again.status_code == 200  # 重复签字产生新的复核记录（覆盖为最新一次）
