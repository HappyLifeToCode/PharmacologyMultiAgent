import json
from pathlib import Path

import pytest

from pharm_demo import engine
from pharm_demo.common import write_json


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    source = engine.ROOT
    import shutil
    for name in ("configs", "agents", "examples"):
        shutil.copytree(source / name, tmp_path / name)
    (tmp_path / "pharm_demo").mkdir()
    shutil.copy2(source / "pharm_demo/engine.py", tmp_path / "pharm_demo/engine.py")
    task = {"task_id": "unit_task", "formula": "SYNTHETIC", "herbs": ["TEST"], "diseases": ["TEST"], "fdr_lt": .05}
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    monkeypatch.setattr(engine, "task_list", lambda: [task])
    monkeypatch.setattr(engine, "prepare_home", lambda: tmp_path)
    calls = []
    def fake_agent(self, role, directory, instruction, evidence, browser=False):
        calls.append(role)
        metadata = {"session_id": "test_" + role}
        write_json(directory / "execution.json", metadata)
        return {"status": "succeeded", "summary": "合成测试审核完成", "blockers": [], "findings": [], "artifacts": []}, metadata
    monkeypatch.setattr(engine.Runner, "call_agent", fake_agent)
    return tmp_path, calls


def test_fixture_dag_and_resume_validates_hashes(isolated_project):
    root, calls = isolated_project
    run_id = engine.start("unit_task", "fixture", background=False)
    manifest = engine.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    assert manifest["scientific_complete"] is False
    assert manifest["metrics"]["intersection_count"] == 3
    assert set(calls) == set(engine.STAGES) - {"intersection"}
    assert not (root / "runs/.runner.lock").exists()
    calls.clear()
    engine.start(resume=run_id, background=False)
    assert calls == ["coordinator_review"]
    manifest = engine.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["stages"]["herb_targets"]["attempt"] == 1
    assert manifest["stages"]["coordinator_review"]["attempt"] == 2
    # Corrupted output invalidates reuse; previous attempt remains available.
    path = next(p for p in manifest["stages"]["herb_targets"]["artifacts"] if p.endswith("targets.json"))
    (root / "runs" / run_id / path).write_text("{}")
    calls.clear()
    engine.start(resume=run_id, background=False)
    assert "herb_targets" in calls
    assert (root / "runs" / run_id / path).exists()


def test_missing_live_data_blocks_science_but_checks_independent_sources(isolated_project, monkeypatch):
    root, calls = isolated_project
    monkeypatch.setattr(engine, "probe", lambda name, directory: {"source": name, "status": "http_error"})
    run_id = engine.start("unit_task", "live", background=False)
    result = engine.read_json(root / "runs" / run_id / "manifest.json")
    assert result["status"] == "partial"
    assert result["stages"]["intersection"]["status"] == "blocked"
    assert "network_analysis" in calls and "enrichment_analysis" in calls
    assert result.get("metrics") == {}
    assert not result["scientific_complete"]


def test_duplicate_run_lock_and_invalid_task_cleanup(isolated_project):
    root, _ = isolated_project
    (root / "runs").mkdir()
    (root / "runs/.runner.lock").write_text("existing")
    with pytest.raises(RuntimeError):
        engine.start("unit_task", background=False)
    assert (root / "runs/.runner.lock").read_text() == "existing"
    (root / "runs/.runner.lock").unlink()
    with pytest.raises(ValueError):
        engine.start("unknown", background=False)
    assert not (root / "runs/.runner.lock").exists()


def test_workbench_task_uses_existing_agent_pipeline(isolated_project, monkeypatch):
    from pharm_demo.tasks import save_task
    root, calls = isolated_project
    task, _ = save_task({"formula":"界面协作验证", "herbs":["测试药材"], "diseases":["TEST"], "research_notes":"逐步保留来源"}, root)
    monkeypatch.setattr(engine, "task_list", lambda: [task])
    run_id = engine.start(task["task_id"], "fixture", background=False)
    manifest = engine.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["task"] == task
    assert manifest["status"] == "succeeded"
    assert set(calls) == set(engine.STAGES) - {"intersection"}
    assert manifest["scientific_complete"] is False
