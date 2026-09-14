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
    # Routine DAG tests isolate the remote browser; real Venny runs are separate.
    from pharm_demo.processing import intersection
    monkeypatch.setattr(engine, "run_venny", lambda herb, disease, directory, synthetic=False: intersection(herb, disease))
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
    assert set(calls) == set(engine.STAGES) - {"intersection", "disease_targets"}
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


@pytest.mark.parametrize("outcome", ["succeeded", "blocked", "failed", "review_failed"])
def test_live_cytoscape_handoff_preserves_evidence_and_partial_status(isolated_project, monkeypatch, outcome):
    root, calls = isolated_project
    run_id = engine.start("unit_task", "fixture", background=False)
    runner = engine.Runner(run_id)
    runner.manifest["mode"] = "live"
    runner.task["network_topology"] = {"metrics": ["Degree"], "weighted": False, "purpose": "engineering_smoke"}
    monkeypatch.setattr(engine, "string_network", lambda genes, task, directory:
                        {"nodes": genes, "edges": [], "provenance": {"unmapped": []}})
    def fake_cytoscape(net, directory, topology):
        assert net["nodes"] == ["TP53"]
        assert topology == runner.task["network_topology"]
        result = {"status": "succeeded" if outcome == "review_failed" else outcome, "method": "CytoNCA test adapter", "limitation": "Research not confirmed",
                  "degree_table": [{"gene_symbol": "TP53", "degree": 0.0}]}
        write_json(directory / "cytoscape_execution.json", result)
        if outcome == "failed":
            raise ValueError("Plugin table mismatch")
        return result
    monkeypatch.setattr(engine, "run_cytoscape", fake_cytoscape)
    if outcome == "review_failed":
        def failed_review(*args, **kwargs):
            return {"status": "failed", "summary": "Evidence rejected", "blockers": ["Review mismatch"], "artifacts": []}, None
        monkeypatch.setattr(runner, "call_agent", failed_review)
    runner.analysis_stage("network_analysis", {"genes": ["TP53"], "evidence_type": "real_input"})
    stage = runner.manifest["stages"]["network_analysis"]
    assert stage["status"] == ("failed" if outcome in ("failed", "review_failed") else "partial")
    assert any(p.endswith("cytoscape_execution.json") for p in stage["artifacts"])
    assert "archive_error" not in runner.manifest
    if outcome != "failed":
        network = engine.read_json(runner.directory / stage["directory"] / "network.json")
        assert network["method"] == ("CytoNCA test adapter" if outcome in ("succeeded", "review_failed") else "NetworkX degree")
    assert list((root / "data/pharm/SYNTHETIC/04_ppi").rglob("cytoscape_execution.json"))


@pytest.mark.parametrize("error", ["Official Venny unavailable", "Venny results differ from independent Python set verification"])
def test_venny_failure_cannot_pass_analysis_or_reuse_old_analysis(isolated_project, monkeypatch, error):
    root, calls = isolated_project
    run_id = engine.start("unit_task", "fixture", background=False)
    manifest_path = root / "runs" / run_id / "manifest.json"
    manifest = engine.read_json(manifest_path)
    # Force an intersection retry while preserving previously succeeded analyses.
    manifest["stages"]["intersection"]["status"] = "failed"
    write_json(manifest_path, manifest)
    def unavailable(herb, disease, directory, synthetic=False):
        write_json(directory / "venny_execution.json", {"status": "failed", "error": error})
        raise ValueError(error)
    monkeypatch.setattr(engine, "run_venny", unavailable)
    calls.clear()
    engine.start(resume=run_id, background=False)
    result = engine.read_json(manifest_path)
    assert result["status"] != "succeeded"
    stage = result["stages"]["intersection"]
    assert stage["status"] == "failed"
    assert any(p.endswith("venny_execution.json") for p in stage["artifacts"])
    assert not (root / "runs" / run_id / stage["directory"] / "genes.txt").exists()
    for role in ("network_analysis", "enrichment_analysis"):
        assert role in calls
        assert result["stages"][role]["status"] != "succeeded"


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
    assert set(calls) == set(engine.STAGES) - {"intersection", "disease_targets"}
    assert manifest["scientific_complete"] is False


def test_disease_branches_overlap_and_resume_only_failed_branch(isolated_project, monkeypatch):
    import threading
    root, calls = isolated_project
    original = engine.Runner.call_agent
    barrier = threading.Barrier(2)
    fail_omim = [True]
    def audited(self, role, directory, instruction, evidence, browser=False):
        if role in ("genecards_targets", "omim_targets") and fail_omim[0]:
            barrier.wait(timeout=10)  # Fails if the runner executes the two branches serially.
        result, meta = original(self, role, directory, instruction, evidence, browser)
        if role == "omim_targets" and fail_omim[0]:
            result.update(status="blocked", blockers=["test: waiting for OMIM"])
        return result, meta
    monkeypatch.setattr(engine.Runner, "call_agent", audited)
    run_id = engine.start("unit_task", "fixture", background=False)
    first = engine.read_json(root / "runs" / run_id / "manifest.json")
    assert first["stages"]["genecards_targets"]["status"] == "succeeded"
    assert first["stages"]["omim_targets"]["status"] == "blocked"
    assert first["stages"]["disease_targets"]["status"] == "blocked"
    assert first["stages"]["intersection"]["status"] == "blocked"
    fail_omim[0] = False
    calls.clear()
    engine.start(resume=run_id, background=False)
    second = engine.read_json(root / "runs" / run_id / "manifest.json")
    assert second["status"] == "succeeded"
    assert "genecards_targets" not in calls
    assert "omim_targets" in calls
    assert second["stages"]["genecards_targets"]["attempt"] == 1
    assert second["stages"]["omim_targets"]["attempt"] == 2
    assert not second["stages"]["disease_targets"]["agent_session_id"]


def test_other_source_update_does_not_invalidate_genecards(isolated_project):
    root, _ = isolated_project
    run_id = engine.start("unit_task", "fixture", background=False)
    runner = engine.Runner(run_id)
    runner.manifest["mode"] = "live"
    directory = root / "data/imports/unit_task"
    directory.mkdir(parents=True)
    for filename in ("genecards.csv", "omim.csv", "mapping.csv"):
        (directory / filename).write_text("initial")
    provenance = {"sources": {name: {"raw_files": [name + ".csv"]} for name in ("genecards", "omim")}, "mapping": {"raw_files": ["mapping.csv"]}}
    write_json(directory / "provenance.json", provenance)
    gc_before = runner.signature("genecards_targets")
    omim_before = runner.signature("omim_targets")
    (directory / "omim.csv").write_text("updated")
    provenance["sources"]["omim"]["accessed_at"] = "2026-09-13"
    write_json(directory / "provenance.json", provenance)
    assert runner.signature("genecards_targets") == gc_before
    assert runner.signature("omim_targets") != omim_before


def test_legacy_run_keeps_legacy_stage_graph(isolated_project):
    root, calls = isolated_project
    run_id = engine.start("unit_task", "fixture", background=False)
    path = root / "runs" / run_id / "manifest.json"
    manifest = engine.read_json(path)
    manifest.pop("workflow_version")
    for role in ("genecards_targets", "omim_targets"):
        manifest["stages"].pop(role)
    manifest["stages"]["disease_targets"]["status"] = "blocked"
    write_json(path, manifest)
    calls.clear()
    engine.start(resume=run_id, background=False)
    resumed = engine.read_json(path)
    assert set(resumed["stages"]) == set(engine.LEGACY_STAGES)
    assert "disease_targets" in calls
    assert "genecards_targets" not in calls
    assert resumed["status"] == "succeeded"
