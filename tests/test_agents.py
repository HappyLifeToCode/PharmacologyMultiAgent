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


def _write_gz(path, text):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(text)


def _batman_data_dir(tmp_path):
    data = tmp_path / "batman_data"
    data.mkdir(parents=True)
    (data / "herb_browse.txt").write_text(
        "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\n"
        "BAI SHAO\t白芍\tCommon Peony\tPaeonia Albiflora\tcompoundA(1001)\n"
        "GAN CAO\t甘草\tUral Licorice\tGlycyrrhiza Uralensis\tcompoundB(1002)\n",
        encoding="utf-8")
    _write_gz(data / "known_browse_by_ingredients.txt.gz",
              "PubChem_CID\tIUPAC_name\tknown_target_proteins\n"
              "1001\tnameA\tTP53|EGFR\n1002\tnameB\tAKT1\n")
    _write_gz(data / "known_browse_by_targets.txt.gz",
              "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n"
              "7157\tTP53\t1001\n1956\tEGFR\t1001\n1957\tAKT1\t1002\n")
    return data


@pytest.fixture
def live_root(root):
    """BATMAN 合成数据 + 合成疾病索引齐备的 live 环境。"""
    data = _batman_data_dir(root)
    engine._write_fixture_index(root / "idx.sqlite")
    (root / "configs" / "discovery_data.local.json").write_text(
        json.dumps({"database": "idx.sqlite"}), encoding="utf-8")
    return root, data


def _task_with_data(data, agents=True):
    return {"herbs": ["白芍", "甘草"], "agents": agents,
            "batman_local_dir": str(data), "batman_accessed_at": "2026-09-17"}


def _save_task(root, body):
    """batman_local_dir/batman_accessed_at 是 engine 识别的测试注入字段，
    不在网页表单白名单内，直接写任务清单（与 test_engine 同一做法）。"""
    task = dict({"task_id": "web_" + uuid.uuid4().hex[:12], "formula": None,
                 "research_notes": "", "batman_threshold": 0.84, "mode": "live"}, **body)
    from pharm.core.common import workspace_identity
    task["workspace_id"] = workspace_identity(root)["workspace_id"]
    (root / "tasks").mkdir(exist_ok=True)
    with (root / "tasks" / "tasks.local.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(task, ensure_ascii=False) + "\n")
    return task


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
                 "confidence": "high"},
                {"session_id": "sess_" + role, "model": "test-model", "elapsed_seconds": 0.1})
    monkeypatch.setattr(engine.runtime, "execute", fake_execute)
    monkeypatch.setattr(engine.Runner, "_check_agents", lambda self: (True, None))
    return calls


def _handoff(root, run_id, role):
    return json.loads((root / "runs" / run_id / role / "attempt_01" / "handoff.json").read_text(encoding="utf-8"))


def test_full_chain_with_agents(live_root, mock_execute):
    root, data = live_root
    task = _save_task(root, _task_with_data(data))
    run_id = engine.start(task["task_id"], background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    # 开场协调 → 三个阶段的核验会话，顺序固定
    assert mock_execute == ["coordinator", "batman_targets", "disease_discovery", "review"]
    assert manifest["coordinator_opening"]["status"] == "succeeded"
    for role in ("herb_targets", "disease_reverse", "review"):
        review = _handoff(root, run_id, role)["agent_review"]
        assert review["session_id"] == "sess_" + engine.AGENT_ROLES[role]
        assert review["status"] == "succeeded" and review["confidence"] == "high"
        # 程序产物仍在且阶段未被降级
        assert manifest["stages"][role]["status"] == "succeeded"
    assert any(p.endswith("agent/execution.json")
               for p in manifest["stages"]["herb_targets"]["artifacts"])


def test_agent_failed_marks_partial_but_keeps_artifacts(live_root, monkeypatch, mock_execute):
    root, data = live_root
    original = engine.runtime.execute

    def fail_on_batman(prompt, directory, **kwargs):
        result, meta = original(prompt, directory, **kwargs)
        if "角色：batman_targets" in prompt:
            result = dict(result, status="failed", summary="计数与产物不一致")
        return result, meta

    monkeypatch.setattr(engine.runtime, "execute", fail_on_batman)
    task = _save_task(root, _task_with_data(data))
    run_id = engine.start(task["task_id"], background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    stage = manifest["stages"]["herb_targets"]
    assert stage["status"] == "partial"
    assert any("Agent 核验未完成或未通过" in b for b in stage["blockers"])
    # 程序产物保留，不被 Agent 结论覆盖
    assert (root / "runs" / run_id / manifest["verified_targets"]["herb_targets"]).is_file()
    review = _handoff(root, run_id, "herb_targets")["agent_review"]
    assert review["status"] == "failed"
    assert manifest["status"] == "partial"


def test_agent_session_error_marks_stage_partial_and_keeps_program_result(live_root, monkeypatch, mock_execute):
    root, data = live_root
    original = engine.runtime.execute

    def boom_on_reverse(prompt, directory, **kwargs):
        if "角色：disease_discovery" in prompt:
            raise RuntimeError("CLI 进程异常退出")
        return original(prompt, directory, **kwargs)

    monkeypatch.setattr(engine.runtime, "execute", boom_on_reverse)
    task = _save_task(root, _task_with_data(data))
    run_id = engine.start(task["task_id"], background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    # 程序产物保留，Agent 错误使阶段降为 partial
    assert manifest["stages"]["disease_reverse"]["status"] == "partial"
    handoff = _handoff(root, run_id, "disease_reverse")
    assert "CLI 进程异常退出" in handoff["agent_review"]["error"]
    assert any("Agent 核验未完成" in f for f in handoff["findings"])
    assert manifest["status"] == "partial"


def test_agents_false_live_runs_pure_program(root, live_root, monkeypatch, calls):
    _, data = live_root
    monkeypatch.setattr(engine.Runner, "_check_agents",
                        lambda self: (_ for _ in ()).throw(AssertionError("不应检查 Agent 环境")))
    monkeypatch.setattr(engine.runtime, "execute",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用模型")))
    task = _save_task(root, _task_with_data(data, agents=False))
    run_id = engine.start(task["task_id"], background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    assert "agent_review" not in _handoff(root, run_id, "herb_targets")
    report = (root / "runs" / run_id / "report.md").read_text(encoding="utf-8")
    assert "无 Agent 核验" in report


def test_agents_unavailable_fails_run_early(root, monkeypatch, calls):
    monkeypatch.setattr(engine.Runner, "_check_agents", lambda self: (False, "找不到 Codex CLI"))
    monkeypatch.setattr(engine.runtime, "execute",
                        lambda *a, **k: calls.append("called"))
    task, _ = save_task({"herbs": ["白芍"], "agents": True}, root)
    run_id = engine.start(task["task_id"], background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "failed"
    assert "agents=false" in manifest["fatal_error"]
    assert all(stage["status"] == "skipped" for stage in manifest["stages"].values())
    assert calls == []  # 不启动任何会话
    assert (root / "runs" / run_id / "report.md").is_file()


def test_prompt_hash_participates_in_signature(live_root, mock_execute):
    root, data = live_root
    task = _save_task(root, _task_with_data(data))
    run_id = engine.start(task["task_id"], background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["stages"]["herb_targets"]["attempt"] == 1
    # 提示词不变：resume 复用成功阶段
    engine.start(resume=run_id, background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["stages"]["herb_targets"]["attempt"] == 1
    assert manifest["stages"]["disease_reverse"]["attempt"] == 1
    # 改提示词内容 → 签名变化 → 对应阶段重跑新 attempt
    prompt_path = root / "agents" / "batman_targets.md"
    prompt_path.write_text(prompt_path.read_text(encoding="utf-8") + "\n补充核验点。\n", encoding="utf-8")
    engine.start(resume=run_id, background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["stages"]["herb_targets"]["attempt"] == 2
    assert (root / "runs" / run_id / "herb_targets" / "attempt_02" / "handoff.json").is_file()


def test_fixture_never_calls_model(root, monkeypatch, calls):
    monkeypatch.setattr(engine.runtime, "execute",
                        lambda *a, **k: calls.append("called"))
    task, _ = save_task({"formula": "济川煎", "mode": "fixture", "agents": True}, root)
    # fixture 强制 agents=false
    assert task["agents"] is False
    run_id = engine.start(task["task_id"], background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    assert calls == []
