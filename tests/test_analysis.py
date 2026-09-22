import gzip
import json
import uuid

import pytest

from pharm.core import common
from pharm.batman import local as batman_local
from pharm.pipeline import engine
from pharm.pipeline.tasks import save_task


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "runtime.json").write_text("{}", encoding="utf-8")
    (tmp_path / "agents").mkdir()
    for name in ("coordinator", "batman_targets", "disease_discovery",
                 "network_analysis", "enrichment_analysis", "review"):
        (tmp_path / "agents" / (name + ".md")).write_text("# " + name + "\n核验职责。", encoding="utf-8")
    monkeypatch.setattr(common, "ROOT", tmp_path)
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    monkeypatch.setattr(batman_local, "ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def calls():
    return []


@pytest.fixture
def mock_execute(monkeypatch, calls):
    def fake_execute(prompt, directory, **kwargs):
        role = next(name for name in ("coordinator", "batman_targets", "disease_discovery",
                                      "network_analysis", "enrichment_analysis", "review")
                    if ("角色：" + name) in prompt)
        calls.append(role)
        from pathlib import Path
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / "execution.json").write_text(json.dumps(
            {"session_id": "sess_" + role, "model": "test-model", "elapsed_seconds": 0.1}), encoding="utf-8")
        return ({"status": "succeeded", "summary": role + " 核验通过", "blockers": [],
                 "findings": ["核验点正常"], "artifacts": [], "graph": None, "rework": None,
                 "confidence": "medium"},
                {"session_id": "sess_" + role, "model": "test-model", "elapsed_seconds": 0.1})
    monkeypatch.setattr(engine.runtime, "execute", fake_execute)
    monkeypatch.setattr(engine.Runner, "_check_agents", lambda self: (True, None))
    return calls


def fixture_discovery_run(root, body=None):
    task, _ = save_task(body or {"herbs": ["白芍", "甘草"], "mode": "fixture"}, root)
    run_id = engine.start(task["task_id"], background=False)
    return run_id


def _handoff(root, run_id, role):
    return json.loads((root / "runs" / run_id / role / "attempt_01" / "handoff.json").read_text(encoding="utf-8"))


def test_fixture_analysis_end_to_end(root, monkeypatch, calls):
    monkeypatch.setattr(engine.runtime, "execute", lambda *a, **k: calls.append("called"))
    # 温经汤 12 味覆盖合成靶点池全部 6 个基因
    source_id = fixture_discovery_run(root, {"formula": "温经汤", "mode": "fixture"})
    run_id = engine.start(pipeline="analysis", mode="fixture",
                          analysis={"discovery_run_id": source_id, "disease": "Hyperthyroidism"},
                          background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["pipeline"] == "analysis"
    assert manifest["task"]["agents"] is False  # fixture 强制不调模型
    assert manifest["status"] == "succeeded"
    assert calls == []
    for role in ("shared_targets", "network", "enrichment", "analysis_review"):
        assert manifest["stages"][role]["status"] == "succeeded"
    # fixture 索引中 Hyperthyroidism 关联 TP53/VEGFA，与来源靶点集（6 个池基因）交集
    shared = json.loads((root / "runs" / run_id / manifest["verified_targets"]["shared_targets"]).read_text(encoding="utf-8"))
    assert shared["genes"] == ["TP53", "VEGFA"]
    assert shared["counts"] == {"input_targets": 6, "disease_targets": 2, "shared": 2}
    assert shared["evidence_type"] == "synthetic_engineering"
    assert shared["source"]["run_id"] == source_id and shared["source"]["database_sha256"]
    network = json.loads((root / "runs" / run_id / manifest["verified_targets"]["network"]).read_text(encoding="utf-8"))
    assert network["node_count"] == 2 and network["edge_count"] == 1
    assert network["method"] == "NetworkX degree"
    assert network["evidence_type"] == "synthetic_engineering"
    fixture = json.loads((root / "runs" / run_id / "enrichment" / "attempt_01" / "enrichment_fixture.json").read_text(encoding="utf-8"))
    assert "非 DAVID" in fixture["method"] and fixture["significant_count"] == 1
    assert manifest["metrics"]["significant_terms"] == 1
    report = (root / "runs" / run_id / "report.md").read_text(encoding="utf-8")
    assert "机制分析运行报告" in report and "scientific_complete：false" in report
    # fixture 不进归档
    assert not (root / "data" / "pharm").exists()


def _custom_index(path, disease, genes):
    from pharm.discovery.query import _create_database
    metadata = {"schema_version": 1, "created_at": "2026-09-22T00:00:00+00:00", "batch_name": "test",
                "import_kind": "generic_associations", "diseases": [disease], "herbs": [],
                "source_rows": {"associations": len(genes)}, "selection": "synthetic",
                "identifier_policy": "exact", "disease_identifier_policy": "labels",
                "provenance": {"synthetic": True}, "source_sha256": {}, "limitation": "test"}
    _create_database(path, metadata,
                     [(gene, disease, "genecards", i + 2, 5.0, "{}") for i, gene in enumerate(genes)], [])


def _craft_source_run(root, targets_genes, disease, disease_genes):
    """手工搭一个最小来源 discovery 运行（manifest + 两阶段产物）。"""
    run_id = "src_" + uuid.uuid4().hex[:8]
    run = root / "runs" / run_id
    herb_dir = run / "herb_targets" / "attempt_01"
    reverse_dir = run / "disease_reverse" / "attempt_01" / "reverse"
    herb_dir.mkdir(parents=True)
    reverse_dir.mkdir(parents=True)
    db = root / (run_id + ".sqlite")
    _custom_index(db, disease, disease_genes)
    common.write_json(herb_dir / "targets.json", {"evidence_type": "user_local_batman",
                                                  "herbs": ["白芍"], "genes": targets_genes})
    common.write_json(reverse_dir / "result.json", {
        "input_count": len(targets_genes), "candidates": [{"disease": disease}],
        "database": str(db), "database_sha256": common.digest(db)})
    manifest = {"run_id": run_id, "pipeline": "discovery", "workflow_version": engine.WORKFLOW_VERSION,
                "mode": "live", "status": "succeeded",
                "task": {"task_id": "web_src", "formula": None, "herbs": ["白芍"],
                         "batman_threshold": 0.84, "composition": "custom_herbs"},
                "stages": {"disease_reverse": {"status": "succeeded"},
                           "herb_targets": {"status": "succeeded"}},
                "verified_targets": {"herb_targets": "herb_targets/attempt_01/targets.json",
                                     "disease_reverse": "disease_reverse/attempt_01/reverse/result.json"}}
    common.write_json(run / "manifest.json", manifest)
    return run_id


def test_empty_shared_blocks_downstream(root, monkeypatch, calls):
    monkeypatch.setattr(engine.runtime, "execute", lambda *a, **k: calls.append("called"))
    source_id = _craft_source_run(root, ["TP53"], "Nothing", ["BRCA1"])
    run_id = engine.start(pipeline="analysis",
                          analysis={"discovery_run_id": source_id, "disease": "Nothing", "agents": False},
                          background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    stages = manifest["stages"]
    shared = json.loads((root / "runs" / run_id / manifest["verified_targets"]["shared_targets"]).read_text(encoding="utf-8"))
    assert shared["genes"] == [] and shared["counts"]["shared"] == 0
    assert stages["shared_targets"]["status"] == "succeeded"   # 空交集如实保留
    assert stages["network"]["status"] == "blocked"
    assert stages["enrichment"]["status"] == "blocked"
    assert stages["analysis_review"]["status"] == "succeeded"  # 报告照常
    assert (root / "runs" / run_id / "report.md").is_file()
    assert manifest["status"] == "partial"
    assert calls == []


def test_analysis_validation_errors(root):
    with pytest.raises(ValueError, match="来源运行不存在"):
        engine.start(pipeline="analysis", analysis={"discovery_run_id": "nope", "disease": "X"}, background=False)
    source_id = fixture_discovery_run(root)
    with pytest.raises(ValueError, match="候选清单"):
        engine.start(pipeline="analysis",
                     analysis={"discovery_run_id": source_id, "disease": "不存在病"}, background=False)
    with pytest.raises(ValueError, match="不支持"):
        engine.start(pipeline="analysis",
                     analysis={"discovery_run_id": source_id, "disease": "Hyperthyroidism", "bogus": 1},
                     background=False)
    # 来源疾病反查未成功
    blocked_source = _craft_source_run(root, ["TP53"], "Nothing", ["BRCA1"])
    m = common.read_json(root / "runs" / blocked_source / "manifest.json")
    m["stages"]["disease_reverse"]["status"] = "blocked"
    common.write_json(root / "runs" / blocked_source / "manifest.json", m)
    with pytest.raises(ValueError, match="未成功"):
        engine.start(pipeline="analysis",
                     analysis={"discovery_run_id": blocked_source, "disease": "Nothing"}, background=False)


def _string_local_dir(tmp_path):
    data = tmp_path / "string_data"
    data.mkdir()
    (data / "9606.protein.info.v12.0.txt").write_text(
        "#string_protein_id\tpreferred_name\tprotein_size\tannotation\n"
        "9606.ENSP00000000001\tTP53\t393\tx\n9606.ENSP00000000010\tVEGFA\t412\ty\n", encoding="utf-8")
    (data / "9606.protein.aliases.v12.0.txt").write_text(
        "#string_protein_id\talias\tsource\n", encoding="utf-8")
    (data / "9606.protein.links.detailed.v12.0.txt").write_text(
        "protein1 protein2 neighborhood fusion cooccurence coexpression experimental database textmining combined_score\n"
        "9606.ENSP00000000001 9606.ENSP00000000010 0 0 0 0 900 0 0 950\n", encoding="utf-8")
    return data


def test_live_analysis_agent_sequence(root, mock_execute, tmp_path, monkeypatch):
    source_id = fixture_discovery_run(root, {"formula": "温经汤", "mode": "fixture"})
    string_dir = _string_local_dir(tmp_path)
    monkeypatch.setattr(engine.david, "run_david", lambda genes, task, directory, **kw: {
        "status": "succeeded", "significant_count": 2, "mapped_count": 2,
        "method": "DAVID EASE (modified Fisher exact test)"})
    run_id = engine.start(pipeline="analysis",
                          analysis={"discovery_run_id": source_id, "disease": "Hyperthyroidism",
                                    "agents": True, "string_source": "local_files",
                                    "string_local_dir": str(string_dir),
                                    "david_enrichment": {"purpose": "engineering_smoke"}},
                          background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    assert mock_execute == ["network_analysis", "enrichment_analysis", "review"]
    for role in ("network", "enrichment", "analysis_review"):
        assert _handoff(root, run_id, role)["agent_review"]["status"] == "succeeded"
    network = json.loads((root / "runs" / run_id / manifest["verified_targets"]["network"]).read_text(encoding="utf-8"))
    assert network["node_count"] == 2 and network["edge_count"] == 1
    assert network["method"] == "NetworkX degree"  # 未配置 network_topology，不冒充 CytoNCA
    assert manifest["metrics"]["network_nodes"] == 2
    assert manifest["metrics"]["significant_terms"] == 2


def test_analysis_resume_reuses_and_v3_rejected(root, monkeypatch, calls):
    monkeypatch.setattr(engine.runtime, "execute", lambda *a, **k: calls.append("called"))
    source_id = fixture_discovery_run(root)
    run_id = engine.start(pipeline="analysis", mode="fixture",
                          analysis={"discovery_run_id": source_id, "disease": "Hyperthyroidism"},
                          background=False)
    engine.start(resume=run_id, background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    for role in ("shared_targets", "network", "enrichment"):
        assert manifest["stages"][role]["attempt"] == 1
    assert manifest["stages"]["analysis_review"]["attempt"] == 2  # 验收每次重核
    # 旧 workflow_version 拒绝恢复
    old = root / "runs" / "old_v3"
    old.mkdir()
    common.write_json(old / "manifest.json", {"run_id": "old_v3", "workflow_version": 3})
    with pytest.raises(ValueError, match="旧结构"):
        engine.start(resume="old_v3", background=False)


def test_analysis_archive_layout(root, tmp_path, monkeypatch, calls):
    monkeypatch.setattr(engine.runtime, "execute", lambda *a, **k: calls.append("called"))
    source_id = fixture_discovery_run(root, {"formula": "温经汤", "mode": "fixture"})
    string_dir = _string_local_dir(tmp_path)
    monkeypatch.setattr(engine.david, "run_david", lambda genes, task, directory, **kw: {
        "status": "succeeded", "significant_count": 0, "mapped_count": 2,
        "method": "DAVID EASE (modified Fisher exact test)"})
    run_id = engine.start(pipeline="analysis",
                          analysis={"discovery_run_id": source_id, "disease": "Hyperthyroidism",
                                    "agents": False, "string_source": "local_files",
                                    "string_local_dir": str(string_dir),
                                    "david_enrichment": {"purpose": "engineering_smoke"}},
                          background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    archive_root = root / "data" / "pharm" / "温经汤"  # 归档根从来源任务继承方剂名
    for stage_dir in ("05_analysis/shared", "05_analysis/network", "05_analysis/enrich", "05_analysis/review"):
        assert (archive_root / stage_dir / ("run_" + run_id) / "attempt_01" / "_archive.json").is_file()
