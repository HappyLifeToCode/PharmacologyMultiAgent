"""Isolated engineering cases; all papers/model outputs are synthetic."""
import asyncio
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from pharm.chat import config, engine, provider, tools
from pharm.core.common import write_json
from pharm.literature.dataset import FORMAT


def install_corpus(root):
    directory = root / "data/cnki/cnki_示例方"
    directory.mkdir(parents=True)
    path = directory / "papers.json"
    write_json(path, {"keyword": "示例方", "papers": [{"rank": 1, "title": "示例方工程样本，非研究证据", "abstract": "仅用于验证检索与引用，不含真实医学结论。", "authors": "工程样本", "date": "2026-11-02"}]})
    raw = path.read_bytes()
    write_json(root / "data/cnki/manifest.json", {"format": FORMAT, "corpora": [{"keyword": "示例方", "directory": "cnki_示例方", "papers": 1,
        "files": {"papers.json": {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}}]})


def test_configuration_never_returns_secret_and_endpoint_change_drops_key(tmp_path):
    result = config.save(tmp_path, {"model": "fixture", "base_url": "https://provider.example/v1", "api_key": "fake-secret"})
    assert result["has_key"] and "api_key" not in result
    assert "fake-secret" not in config.settings_path(tmp_path).read_text()
    assert config.read(tmp_path, secret=True)["api_key"] == "fake-secret"
    config.save(tmp_path, {"base_url": "https://other.example/v1"})
    assert not config.read(tmp_path)["has_key"]


@pytest.mark.parametrize("url", ["http://remote.example/v1", "https://name:secret@example.com/v1", "file:///etc/passwd", "https://example.com/?key=secret"])
def test_unsafe_model_addresses_are_rejected(url):
    with pytest.raises(ValueError): config.validate({"base_url": url, "model": "fixture"})


def test_real_local_retrieval_and_model_loop_with_unknown_citation(tmp_path):
    install_corpus(tmp_path)
    config.save(tmp_path, {"base_url": "http://127.0.0.1:9/v1", "model": "fixture"})
    calls = []
    async def fake_complete(settings, messages, definitions):
        calls.append(messages[:])
        if not any(m["role"] == "tool" for m in messages):
            return {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search_local_literature", "arguments": '{"query":"示例方"}'}}]}
        return {"content": "这是工程样本[E1]，无效编号[E999]。", "tool_calls": []}
    async def scenario():
        manager = engine.ResearchManager(complete=fake_complete)
        body = {"session_id": uuid4().hex, "request_id": uuid4().hex, "text": "查示例方", "network": False}
        manager.start(tmp_path, body)
        task = manager.tasks[(str(tmp_path.resolve()), body["request_id"])]
        assert manager.start(tmp_path, body)["id"] == body["request_id"]
        await task
        record = manager.status(tmp_path, body["request_id"])
        assert record["status"] == "succeeded" and len(record["sources"]) == 1
        assert "[E1]" in record["answer"] and "[未核验引用]" in record["answer"]
        assert record["sources"][0]["metadata"]["source_sha256"]
        assert len(calls) == 2
        assert "api_key" not in json.dumps(record)
        return manager, body
    asyncio.run(scenario())


def test_network_tool_honors_disabled_toggle_without_request(tmp_path):
    result = asyncio.run(tools.execute("search_online_literature", {"query": "test"}, {"evidence": []}, tmp_path, False))
    assert result["status"] == "disabled"


def test_cancel_before_worker_starts_releases_session(tmp_path):
    config.save(tmp_path, {"base_url": "http://127.0.0.1:9/v1", "model": "fixture"})
    async def scenario():
        manager = engine.ResearchManager()
        body = {"session_id": uuid4().hex, "request_id": uuid4().hex, "text": "cancel fixture"}
        manager.start(tmp_path, body)
        result = await manager.cancel(tmp_path, body["request_id"])
        assert result["status"] == "cancelled"
        assert not manager.tasks and not manager.active_sessions
    asyncio.run(scenario())


def test_responses_transport_replays_reasoning_and_call_outputs():
    reasoning = {"type": "reasoning", "id": "r1", "encrypted_content": "fixture"}
    call = {"type": "function_call", "call_id": "c1", "name": "list_local_resources", "arguments": "{}"}
    messages = [{"role": "assistant", "content": None, "tool_calls": [], "_response_items": [reasoning, call]}, {"role": "tool", "tool_call_id": "c1", "content": "{}"}]
    assert provider.response_input(messages) == [reasoning, call, {"type": "function_call_output", "call_id": "c1", "output": "{}"}]
    def handle(request):
        payload = json.loads(request.content)
        assert payload["store"] is False and payload["input"][0] == reasoning
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "工程结果"}]}]})
    setting = {**config.DEFAULT, "model": "fixture", "api_style": "responses", "configured": True, "api_key": "fake"}
    result = asyncio.run(provider.complete(setting, messages, transport=httpx.MockTransport(handle)))
    assert result["content"] == "工程结果"


def test_provider_never_echoes_sensitive_error_response():
    setting = {**config.DEFAULT, "model": "fixture", "configured": True, "api_key": "fake-secret"}
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="fake-secret provider internal detail"))
    with pytest.raises(provider.ModelError) as exc:
        asyncio.run(provider.complete(setting, [{"role": "user", "content": "test"}], transport=transport))
    assert "fake-secret" not in str(exc.value)
