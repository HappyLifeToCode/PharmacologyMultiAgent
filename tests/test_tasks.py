import json
import pytest
from pharm_demo.tasks import save_task, prepare_task


def body():
    return {"formula": "芍药甘草汤", "herbs": ["白芍", "炙甘草", "白芍"], "diseases": ["Hyperthyroidism"], "research_notes": "保留原始来源与访问日期"}


def test_save_preserves_existing_tasks_and_deduplicates_retries(tmp_path):
    p = tmp_path / "tasks/tasks.jsonl"
    p.parent.mkdir()
    p.write_text('{"task_id":"existing","extra":"用户已有字段"}', encoding="utf-8-sig")
    task, created = save_task(body(), tmp_path)
    assert created and task["herbs"] == ["白芍", "炙甘草"]
    before = p.read_bytes()
    assert save_task(body(), tmp_path) == (task, False)
    assert p.read_bytes() == before
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == {"task_id": "existing", "extra": "用户已有字段"}
    assert rows[1]["research_notes"] == body()["research_notes"]
    assert task["batman_threshold"] is None and task["enrichment_background"] is None
    assert not (p.parent / ".tasks.lock").exists()


@pytest.mark.parametrize("change", [{"formula":"../escape"}, {"formula":"CON"}, {"herbs":[]}, {"herbs":"白芍"}, {"diseases":["bad\nentry"]}, {"research_notes":"x"*4001}, {"import_batch":"../outside"}, {"model":"other"}])
def test_invalid_tasks_are_not_persisted(tmp_path, change):
    with pytest.raises(ValueError):
        save_task(dict(body(), **change), tmp_path)
    assert not (tmp_path / "tasks/tasks.jsonl").exists()


def test_task_api_persists_and_starts_selected_task(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import app as backend
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(backend, "start", lambda *args: calls.append(args) or "new_run")
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/tasks", json=body(), headers={"Origin":"https://other.example"}).status_code == 403
        assert client.post("/api/tasks", json=[]).status_code == 400
        response = client.post("/api/tasks", json=body())
        assert response.status_code == 200
        task_id = response.json()["task"]["task_id"]
        assert client.post("/api/tasks", json=body()).json()["created"] is False
        assert client.post("/api/run", json={"task_id":task_id,"mode":"live"}).status_code == 200
        assert calls == [(task_id, "live")]
