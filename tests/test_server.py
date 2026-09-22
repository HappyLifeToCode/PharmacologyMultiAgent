from fastapi.testclient import TestClient

from server import app as backend
from pharm.core.common import write_json


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
