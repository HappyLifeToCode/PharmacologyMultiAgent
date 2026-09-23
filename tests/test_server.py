from fastapi.testclient import TestClient

from server import app as backend
from pharm.core.common import write_json, workspace_identity


def test_artifact_allowlist_and_post_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    directory = tmp_path / "runs/test_run"
    directory.mkdir(parents=True)
    (directory / "allowed.csv").write_text("gene_symbol\nTP53\n")
    (directory / "stderr.log").write_text("private")
    (directory / "prompt.txt").write_text("private prompt")
    write_json(directory / "manifest.json", {"run_id": "test_run",
                                               "workspace_id": workspace_identity(tmp_path)["workspace_id"],
                                               "stages": {"herb_targets": {"artifacts": ["allowed.csv", "prompt.txt"]}}})
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


def test_index_static_assets_and_formulas_catalog():
    with TestClient(backend.app, base_url="http://localhost") as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "方剂反向疾病发现工作台" in page.text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/style.css").status_code == 200
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
