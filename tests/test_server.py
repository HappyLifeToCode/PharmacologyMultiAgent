from fastapi.testclient import TestClient

from server import app as backend
from pharm.core.common import write_json, workspace_identity


def test_artifact_allowlist_and_post_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    directory = tmp_path / "runs/test_run"
    directory.mkdir(parents=True)
    (directory / "allowed.csv").write_text("gene_symbol\nTP53\n")
    (directory / "report.md").write_text("# report\n")
    (directory / "stderr.log").write_text("private")
    (directory / "prompt.txt").write_text("private prompt")
    write_json(directory / "manifest.json", {"run_id": "test_run",
                                               "workspace_id": workspace_identity(tmp_path)["workspace_id"],
                                               "report": "report.md",
                                               "stages": {"herb_targets": {"artifacts": ["allowed.csv", "prompt.txt"]}}})
    calls = []
    monkeypatch.setattr(backend, "start", lambda *args, **kwargs: calls.append(args) or "test_run")
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.get("/artifacts/test_run/allowed.csv").status_code == 200
        report = client.get("/artifacts/test_run/report.md")
        assert report.status_code == 200
        assert report.headers["content-disposition"] == 'attachment; filename="report.md"'
        assert client.get("/artifacts/test_run/stderr.log").status_code == 404
        assert client.get("/artifacts/test_run/prompt.txt").status_code == 404
        assert client.get("/api/runs/test_run").json()["stages"]["herb_targets"]["artifacts"] == ["allowed.csv"]
        assert client.post("/api/run", json={"task_id": "x"}, headers={"Origin": "https://other.example"}).status_code == 403
        assert client.post("/api/run", json=[]).status_code == 400
        assert client.get("/api/runs/test_run", headers={"Host": "other.example"}).status_code == 403
        assert not calls
        assert client.post("/api/run", json={"task_id": "x"}, headers={"Origin": "http://localhost"}).status_code == 200
        assert len(calls) == 1


def test_index_static_assets_and_formulas_catalog():
    with TestClient(backend.app, base_url="http://localhost") as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "药理研究助手" in page.text
        assert "chat-input" in page.text
        assert "history-list" in page.text
        assert "Open Targets" in page.text
        assert "GeneCards" not in page.text and "omim.org" not in page.text
        # Monarch 作为 OMIM 功能替代来源出现在页面中是预期内容（直接 OMIM 数据源已退役）
        assert "assist-canvas" not in page.text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/style.css").status_code == 200
        assert client.get("/assets/chat.js").status_code == 200
        assert client.get("/assets/chat.css").status_code == 200
        assert client.get("/assets/evidence-graph.js").status_code == 200
        assert client.get("/assets/evidence-graph.css").status_code == 200
        assert client.get("/assets/disease-labels.js").status_code == 200
        assert client.get("/assets/query-presence.js").status_code == 200
        glossary = client.get("/assets/disease-labels.json").json()
        assert glossary["by_id"]["MONDO:0007254"]["label"] == "乳腺癌"
        assert client.get("/assets/icons/leaf.svg").status_code == 200
        assert client.get("/assets/icons/private.svg").status_code == 404
        workbench = client.get("/workbench")
        assert workbench.status_code == 200
        assert "forward-herbs-input" in workbench.text
        # 旧页面与旧静态资源已下线
        assert client.get("/legacy").status_code == 404
        assert client.get("/assets/discovery.js").status_code == 404
        assert client.get("/assets/workbench.js").status_code == 404
        formulas = client.get("/api/formulas").json()["formulas"]
        assert [f["name"] for f in formulas] == ["温经汤", "半夏白术天麻汤", "济川煎", "桃核承气汤"]
        wenjing = formulas[0]
        assert len(wenjing["herbs"]) == 12
        assert wenjing["source"] == "standard_reference_pending_user_confirmation"
        assert any(herb["canonical"] == "甘草" and herb["candidates"] == ["甘草", "炙甘草"]
                   for herb in wenjing["herbs"])


def test_browser_collection_endpoints_are_removed():
    from starlette.websockets import WebSocketDisconnect
    import pytest
    with TestClient(backend.app, base_url="http://localhost") as client:
        for name in ("start", "request", "stop", "complete"):
            assert client.post("/api/assist/" + name, json={}).status_code == 404
        for name in ("status", "storage-state"):
            assert client.get("/api/assist/" + name).status_code == 404
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/assist"):
                pass


def test_forward_agent_endpoint(monkeypatch):
    calls = []

    def fake_query(herbs=None, *, formula=None, text=None, top_k=None, batman_threshold=0.84):
        calls.append((herbs, formula, text, top_k, batman_threshold))
        return {"run_id": "forward_test", "result": {"candidates": []}}

    async def fake_run(request, body, root, request_id, supersedes=None, owner_id=None):
        backend.forward_jobs.request_identifier(request_id)
        if supersedes is not None:
            backend.forward_jobs.request_identifier(supersedes)
        if owner_id is not None:
            backend.forward_jobs.request_identifier(owner_id)
        return fake_query(body.get("herbs"), formula=body.get("formula"), text=body.get("text"),
                          top_k=body.get("top_k"), batman_threshold=body.get("batman_threshold", 0.84))

    monkeypatch.setattr(backend.forward_jobs.manager, "run", fake_run)
    with TestClient(backend.app, base_url="http://localhost") as client:
        response = client.post("/api/agent/forward", json={"herbs": ["白芍"], "top_k": 3})
        assert response.status_code == 200
        assert response.json()["run_id"] == "forward_test"
        assert calls == [(["白芍"], None, None, 3, 0.84)]
        response = client.post("/api/agent/forward", json={"formula": "温经汤", "top_k": 2})
        assert response.status_code == 200
        assert calls[-1] == (None, "温经汤", None, 2, 0.84)
        response = client.post("/api/agent/forward", json={"text": "温经汤", "top_k": 2})
        assert response.status_code == 200
        assert calls[-1] == (None, None, "温经汤", 2, 0.84)
        assert client.post("/api/agent/forward", json={"text": "温经汤", "request_id": "../bad"}).status_code == 400
        assert client.post("/api/agent/forward", json={"text": "温经汤", "owner_id": "../bad"}).status_code == 400
        assert client.post("/api/agent/forward", json={"herbs": ["白芍"], "unknown": 1}).status_code == 400


def test_forward_cancel_endpoint_and_origin(monkeypatch):
    calls = []

    async def fake_stop(request_id):
        backend.forward_jobs.request_identifier(request_id)
        calls.append(request_id)
        return {"request_id": request_id, "stopped": True, "status": "cancelled"}

    monkeypatch.setattr(backend.forward_jobs.manager, "stop", fake_stop)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/agent/forward/cancel", json={"request_id": "test-query"}).json()["stopped"]
        assert calls == ["test-query"]
        assert client.post("/api/agent/forward/cancel", json={"request_id": "../bad"}).status_code == 400
        assert client.post("/api/agent/forward/cancel", json={"request_id": "test-query", "extra": 1}).status_code == 400
        assert client.post("/api/agent/forward/cancel", json={"request_id": "test-query"},
                           headers={"Origin": "https://other.example"}).status_code == 403


def test_forward_request_status_restores_only_existing_job(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    path = tmp_path / "local/agent/jobs/active-query.json"
    write_json(path, {"request_id": "active-query", "run_id": "forward_existing", "status": "running",
                      "owner_id": "tab-one", "worker_pid": 1234, "private": "not public"})
    original = path.read_bytes()
    with TestClient(backend.app, base_url="http://localhost") as client:
        result = client.get("/api/agent/forward/request/active-query").json()
        assert result["run_id"] == "forward_existing" and result["status"] == "running"
        assert "private" not in result and "worker_pid" not in result
        assert client.get("/api/agent/forward/request/missing-query").status_code == 404
        assert client.get("/api/agent/forward/presence/bad!id").status_code == 400
        assert client.get("/api/agent/forward/request/bad!id").status_code == 404
        assert client.get("/api/agent/forward/request/active-query", headers={"Host": "other.example"}).status_code == 403
    assert path.read_bytes() == original
    write_json(path, {"status": "running", "service_instance_id": "previous-service"})
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.get("/api/agent/forward/request/active-query").json()["status"] == "interrupted"


def test_forward_archive_read_and_path_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    result = {"candidates": [{"disease": "Disease A", "matched_count": 1}], "scientific_complete": False}
    write_json(tmp_path / "local/agent/forward/forward_test/result.json", result)
    write_json(tmp_path / "local/agent/forward/other_test/result.json", {"private": True})
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.get("/api/agent/forward/forward_test").json() == result
        assert client.get("/api/agent/forward/forward_missing").status_code == 404
        assert client.get("/api/agent/forward/other_test").status_code == 404
        assert client.get("/api/agent/forward/forward_test", headers={"Host": "other.example"}).status_code == 403


def test_analysis_endpoint_and_pipeline_field(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    from pharm.pipeline import engine as engine_module
    monkeypatch.setattr(engine_module, "ROOT", tmp_path)
    write_json(tmp_path / "runs" / "ana_run" / "manifest.json",
               {"run_id": "ana_run", "workspace_id": workspace_identity(tmp_path)["workspace_id"],
                "pipeline": "analysis", "created_at": "2026-09-22T00:00:00+00:00",
                "stages": {}})
    calls = []

    def fake_start(*args, **kwargs):
        calls.append((args, kwargs))
        if kwargs.get("analysis", {}).get("discovery_run_id") == "missing":
            raise ValueError("来源运行不存在：missing")
        return "ana_run"

    monkeypatch.setattr(backend, "start", fake_start)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/analysis", json=[]).status_code == 400
        assert client.post("/api/analysis", json={"discovery_run_id": "missing", "disease": "X"}).status_code == 400
        response = client.post("/api/analysis",
                               json={"discovery_run_id": "r1", "disease": "Hyperthyroidism",
                                     "research_notes": "机制分析"})
        assert response.status_code == 200 and response.json()["run_id"] == "ana_run"
        assert calls[-1][1]["pipeline"] == "analysis"
        assert calls[-1][1]["analysis"]["disease"] == "Hyperthyroidism"
        runs = client.get("/api/runs").json()["runs"]
        assert runs[0]["pipeline"] == "analysis"
        assert client.post("/api/analysis", json={"discovery_run_id": "r1", "disease": "X"},
                           headers={"Origin": "https://other.example"}).status_code == 403


def test_old_or_other_workspace_runs_are_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    from pharm.pipeline import engine as engine_module
    monkeypatch.setattr(engine_module, "ROOT", tmp_path)
    current = workspace_identity(tmp_path)["workspace_id"]
    runs = tmp_path / "runs"
    runs.mkdir()
    write_json(runs / "old" / "manifest.json", {"run_id": "old", "workspace_id": "0" * 32,
                                                  "created_at": "2026-09-22T00:00:00+00:00",
                                                  "stages": {}})
    write_json(runs / "legacy" / "manifest.json", {"run_id": "legacy",
                                                     "created_at": "2026-09-22T00:00:00+00:00",
                                                     "stages": {}})
    write_json(runs / "current" / "manifest.json", {"run_id": "current", "workspace_id": current,
                                                      "created_at": "2026-09-22T00:00:00+00:00",
                                                      "stages": {}})
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert [item["run_id"] for item in client.get("/api/runs").json()["runs"]] == ["current"]
        assert client.get("/api/runs/old").status_code == 404
        assert client.get("/api/runs/legacy").status_code == 404
