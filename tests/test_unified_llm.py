"""Unified API checks with isolated models and documents; never call real providers."""
import asyncio
import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from pharm import llm
from pharm.chat import config
from pharm.chat.engine import ResearchManager
from pharm.agent import disease_translation
from pharm.literature import collector, dataset
from pharm.pipeline import engine


def test_all_model_entrypoints_share_selection_and_pin_running_tasks(tmp_path, monkeypatch):
    a = config.save(tmp_path, {"profile_name": "A", "model": "api-A", "base_url": "https://a.example/v1", "api_key": "secret-A", "storage": "session"})
    b = config.save(tmp_path, {"new_profile": True, "profile_name": "B", "model": "api-B", "base_url": "https://b.example/v1", "api_key": "secret-B", "storage": "session"})
    config.select(tmp_path, a["profile_id"])
    calls, batch_count = [], [0]
    async def complete(selected, messages, tools=None, **kwargs):
        prompt = messages[-1]["content"]
        calls.append((selected["model"], prompt))
        if "本次为小批量处理" in prompt:
            batch_count[0] += 1
            if batch_count[0] == 1: config.select(tmp_path, b["profile_id"])
            inputs = json.loads(prompt.split("\n输入：", 1)[1])
            content = json.dumps({"papers": [{"rank": p["rank"], "triples": []} for p in inputs]})
        elif "Translate the disease name" in prompt:
            content = '{"terms":["example disease"]}'
        elif "角色：" in prompt:
            content = json.dumps({"status": "succeeded", "summary": "engineering only", "findings": [], "blockers": [], "artifacts": [], "graph": None, "rework": None, "confidence": "low"})
        else:
            content = "工程问答，不是医学结论。"
        return {"content": content, "tool_calls": [], "finish_reason": "stop"}
    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("LLM must not launch a CLI"))

    source = tmp_path / "source/cnki_示例方"; source.mkdir(parents=True)
    (source / "papers.json").write_text(json.dumps({"keyword": "示例方", "papers": [
        {"rank": i, "title": "工程样本" + str(i), "abstract": "仅用于程序检查", "url": "https://kns.cnki.net/fixture/" + str(i)} for i in range(1, 6)]}, ensure_ascii=False), encoding="utf-8")
    dataset.import_data(source.parent, tmp_path / "data/cnki", keywords=["示例方"])
    task, work = collector.prepare("示例方", root=tmp_path, local_only=True)
    collector.execute(task, work, root=tmp_path, extract=True)
    assert task["model"] == "api-A" and batch_count[0] == 2
    assert [m for m, p in calls if "本次为小批量处理" in p] == ["api-A", "api-A"]
    assert json.loads((work / "extracted/paper_0005.json").read_text(encoding="utf-8"))["llm"]["model"] == "api-A"

    translated = disease_translation.suggest("示例疾病", root=tmp_path)
    assert translated.model_info["model"] == "api-B" and translated[1] is False
    assert disease_translation.suggest("示例疾病", root=tmp_path)[1] is True
    config.select(tmp_path, a["profile_id"])
    assert disease_translation.suggest("示例疾病", root=tmp_path)[1] is False

    roles = ("coordinator", "batman_targets", "disease_discovery", "network_analysis", "enrichment_analysis", "review")
    (tmp_path / "agents").mkdir()
    for role in roles: (tmp_path / "agents" / (role + ".md")).write_text("核验工程数据，不修改产物。", encoding="utf-8")
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    runner = object.__new__(engine.Runner)
    runner.task = {}; runner.manifest = {}; runner.llm_selection = None
    assert runner._check_agents() == (True, None)
    config.select(tmp_path, b["profile_id"])
    for role in roles:
        _, meta = runner._agent_session(role, tmp_path / "reviews" / role, "核验", {"synthetic_engineering": True})
        assert meta["model"] == "api-A" and meta["runtime"] == "configured_api"

    async def chat():
        manager = ResearchManager()
        request = {"session_id": uuid4().hex, "request_id": uuid4().hex, "text": "解释工程样本"}
        manager.start(tmp_path, request)
        config.select(tmp_path, a["profile_id"])
        await manager.tasks[(str(tmp_path.resolve()), request["request_id"]) ]
        record = manager.status(tmp_path, request["request_id"])
        assert record["status"] == "succeeded" and record["model"] == "api-B"
    asyncio.run(chat())
    for path in list(work.rglob("*.log")) + list((tmp_path / "reviews").rglob("execution.json")):
        raw = path.read_text(encoding="utf-8")
        assert "secret-A" not in raw and "secret-B" not in raw
        assert "a.example" not in raw and "b.example" not in raw


def test_no_configuration_never_falls_back_to_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("no fallback CLI"))
    with pytest.raises(RuntimeError, match="模型 API 未就绪"):
        collector.run_model("test", tmp_path / "model.log", root=tmp_path)
    with pytest.raises(RuntimeError, match="模型 API 未就绪"):
        disease_translation.suggest("未知疾病", root=tmp_path)


def test_role_review_rejects_malformed_schema():
    from pharm.agents.runtime import validate_result
    with pytest.raises(ValueError): validate_result({"status": "succeeded"})
