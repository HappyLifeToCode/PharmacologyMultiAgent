import json

import pytest

from pharm_demo import engine
from pharm_demo.benchmark import collect_metrics, compare_runs
from pharm_demo.common import write_json


def test_collect_metrics_reads_sessions_attempts_and_tool_calls(tmp_path):
    run = tmp_path / "run_x"
    attempt = run / "coordinator_plan" / "attempt_01"
    attempt.mkdir(parents=True)
    write_json(run / "manifest.json", {
        "run_id": "run_x", "mode": "fixture", "status": "succeeded",
        "created_at": "2026-09-17T10:00:00+00:00", "finished_at": "2026-09-17T10:05:30+00:00",
        "agent_strategy": "shared",
        "stages": {"coordinator_plan": {"status": "succeeded", "attempt": 1, "agent_session_id": "sess_1"},
                   "herb_targets": {"status": "failed", "attempt": 2, "agent_session_id": "sess_1",
                                    "auto_retries_used": 1}}})
    write_json(attempt / "execution.json", {"elapsed_seconds": 12.5, "mcp_tool_calls": [{"tool": "a"}, {"tool": "b"}]})
    write_json(run / "herb_targets" / "attempt_02" / "execution.json",
               {"elapsed_seconds": 5.0, "mcp_tool_calls": []})
    (run / "herb_targets" / "attempt_02").mkdir(parents=True, exist_ok=True)
    write_json(run / "herb_targets" / "attempt_02" / "execution.json",
               {"elapsed_seconds": 5.0, "mcp_tool_calls": []})
    metrics = collect_metrics(run)
    assert metrics["session_count"] == 1
    assert metrics["wall_seconds"] == 330.0
    assert metrics["agent_elapsed_seconds"] == 17.5
    assert metrics["tool_calls"] == 2
    assert metrics["stages"]["herb_targets"] == {"status": "failed", "attempt": 2, "auto_retries_used": 1}
    assert metrics["agent_strategy"] == "shared"


def test_compare_runs_writes_report(tmp_path):
    run_a = tmp_path / "run_a"
    run_b = tmp_path / "run_b"
    for run, strategy in ((run_a, "independent"), (run_b, "shared")):
        run.mkdir()
        write_json(run / "manifest.json", {
            "run_id": run.name, "mode": "fixture", "status": "succeeded",
            "created_at": "2026-09-17T10:00:00+00:00", "finished_at": "2026-09-17T10:01:00+00:00",
            "agent_strategy": strategy,
            "stages": {"coordinator_plan": {"status": "succeeded", "attempt": 1,
                                            "agent_session_id": "s_" + strategy}}})
    result = compare_runs(run_a, run_b, tmp_path / "out")
    report = (tmp_path / "out" / "benchmark_report.md").read_text(encoding="utf-8")
    assert "不构成药理分析质量结论" in report
    assert "independent" in report and "shared" in report
    assert result["metrics"]["a"]["session_count"] == 1
    assert result["scientific_complete"] is False


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    source = engine.ROOT
    from pharm_demo import string_local
    import shutil
    for name in ("configs", "agents", "examples"):
        shutil.copytree(source / name, tmp_path / name, ignore=shutil.ignore_patterns("*.local.json"))
    (tmp_path / "pharm_demo").mkdir()
    shutil.copy2(source / "pharm_demo/engine.py", tmp_path / "pharm_demo/engine.py")
    task = {"task_id": "unit_task", "formula": "SYNTHETIC", "herbs": ["TEST"], "diseases": ["TEST"], "fdr_lt": .05}
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    monkeypatch.setattr(string_local, "ROOT", tmp_path)
    monkeypatch.setattr(engine, "task_list", lambda: [task])
    monkeypatch.setattr(engine, "prepare_home", lambda: tmp_path)
    from pharm_demo.processing import intersection
    monkeypatch.setattr(engine, "run_venny", lambda herb, disease, directory, synthetic=False: intersection(herb, disease))
    calls = []
    def fake_execute(prompt, directory, browser=False, on_event=None, timeout=360, home=None, resume_session=None):
        calls.append({"resume_session": resume_session})
        if on_event:
            on_event({"type": "thread.started", "thread_id": "sess_shared"})
        write_json(directory / "execution.json", {"session_id": "sess_shared", "elapsed_seconds": 1,
                                                  "mcp_tool_calls": []})
        return ({"status": "succeeded", "summary": "ok", "blockers": [], "findings": [],
                 "artifacts": []}, {"session_id": "sess_shared"})
    monkeypatch.setattr(engine, "execute", fake_execute)
    return tmp_path, calls


def test_shared_strategy_resumes_single_session(isolated_project):
    root, calls = isolated_project
    run_id = engine.start("unit_task", "fixture", background=False, agent_strategy="shared")
    manifest = engine.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    assert len(calls) > 1
    assert calls[0]["resume_session"] is None
    assert all(c["resume_session"] == "sess_shared" for c in calls[1:])
    sessions = {s.get("agent_session_id") for s in manifest["stages"].values() if s.get("agent_session_id")}
    assert sessions == {"sess_shared"}
    assert "Agent 会话策略：shared" in (root / "runs" / run_id / "report.md").read_text(encoding="utf-8")


def test_independent_strategy_is_default_and_never_resumes(isolated_project):
    root, calls = isolated_project
    run_id = engine.start("unit_task", "fixture", background=False)
    manifest = engine.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    assert all(c["resume_session"] is None for c in calls)
    assert manifest.get("agent_strategy", "independent") == "independent"
    with pytest.raises(ValueError):
        engine.start("unit_task", "fixture", background=False, agent_strategy="bogus")
