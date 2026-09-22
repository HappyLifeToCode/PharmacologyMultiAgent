import json
import pytest
from pharm.pipeline.tasks import save_task, prepare_task


def body():
    return {"formula": "温经汤", "research_notes": "保留原始来源与访问日期"}


def test_fresh_checkout_ignores_shared_and_legacy_tasks(tmp_path, monkeypatch):
    from pharm.core import common
    monkeypatch.setattr(common, "ROOT", tmp_path)
    assert common.task_list() == []
    directory = tmp_path / "tasks"
    directory.mkdir()
    for name in ("tasks.jsonl", "tasks.example.jsonl"):
        (directory / name).write_text('{"task_id":"shared_test"}\n', encoding="utf-8")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert common.task_list() == []
    task, created = save_task(body(), tmp_path)
    assert created and common.task_list() == [task]
    for name, content in before.items():
        assert (directory / name).read_bytes() == content


def test_cli_empty_tasks_and_resume_without_task_list(tmp_path, monkeypatch, capsys):
    import runpy
    import sys
    from pathlib import Path
    from pharm.core import common
    from pharm.pipeline import engine
    script = Path(__file__).resolve().parents[1] / "scripts/run_tasks.py"
    monkeypatch.setattr(common, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(engine, "start", lambda *args, **kwargs: calls.append((args, kwargs)) or "old_run")
    monkeypatch.setattr(sys, "argv", [str(script)])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(script), run_name="__main__")
    assert error.value.code == 2 and not calls
    assert "tasks/README.md" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", [str(script), "--resume", "old_run"])
    runpy.run_path(str(script), run_name="__main__")
    assert calls == [((None, None), {"resume": "old_run", "background": False})]


def test_save_preserves_existing_tasks_and_deduplicates_retries(tmp_path):
    p = tmp_path / "tasks/tasks.local.jsonl"
    p.parent.mkdir()
    p.write_text('{"task_id":"existing","extra":"用户已有字段"}', encoding="utf-8-sig")
    task, created = save_task(body(), tmp_path)
    assert created and task["herbs"] == ["吴茱萸", "当归", "芍药", "川芎", "人参", "桂枝",
                                       "阿胶", "牡丹皮", "生姜", "甘草", "半夏", "麦冬"]
    assert task["composition"] == "formula"
    before = p.read_bytes()
    assert save_task(body(), tmp_path) == (task, False)
    assert p.read_bytes() == before
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == {"task_id": "existing", "extra": "用户已有字段"}
    assert rows[1]["research_notes"] == body()["research_notes"]
    assert task["batman_threshold"] == 0.84 and task["mode"] == "live"
    assert not (p.parent / ".tasks.lock").exists()


def test_herbs_override_formula_composition(tmp_path):
    task, created = save_task({"formula": "温经汤", "herbs": ["白芍", "炙甘草", "白芍"]}, tmp_path)
    assert created
    assert task["herbs"] == ["白芍", "炙甘草"]
    assert task["composition"] == "herbs_override"
    task, _ = save_task({"herbs": ["白芍", "甘草"]}, tmp_path)
    assert task["formula"] is None and task["composition"] == "custom_herbs"


def test_task_requires_formula_or_herbs():
    with pytest.raises(ValueError, match="方剂名称或药材清单"):
        prepare_task({})


@pytest.mark.parametrize("change", [{"formula":"../escape"}, {"formula":"CON"}, {"formula":"未知方"}, {"herbs":[]}, {"herbs":"白芍"}, {"herbs":["bad\nentry"]}, {"research_notes":"x"*4001}, {"diseases":["Hyperthyroidism"]}, {"import_batch":"../outside"}, {"model":"other"}, {"mode":"bogus"}, {"batman_threshold":"high"}])
def test_invalid_tasks_are_not_persisted(tmp_path, change):
    with pytest.raises(ValueError):
        save_task(dict(body(), **change), tmp_path)
    assert not (tmp_path / "tasks/tasks.local.jsonl").exists()


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
