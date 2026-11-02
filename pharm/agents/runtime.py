from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from ..core.common import ROOT, write_json
from .. import llm


def load_toml(path):
    try:
        import tomllib
    except ImportError:
        from pip._vendor import tomli as tomllib
    return tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))


RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["succeeded", "partial", "blocked", "failed"]},
        "summary": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "graph": {"type": ["object", "null"], "additionalProperties": False, "properties": {
            "enabled": {"type": ["array", "null"], "items": {"type": "string"}}},
            "required": ["enabled"]},
        "rework": {"type": ["array", "null"], "items": {"type": "string"}},
        "confidence": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
    }, "required": ["status", "summary", "blockers", "findings", "artifacts", "graph", "rework", "confidence"],
}


def validate_result(result):
    if not isinstance(result, dict) or set(result) != set(RESULT_SCHEMA["required"]):
        raise ValueError("Agent 交接字段不完整或含额外字段")
    if result["status"] not in {"succeeded", "partial", "blocked", "failed"} or not isinstance(result["summary"], str):
        raise ValueError("Agent 状态或摘要格式错误")
    for key in ("blockers", "findings", "artifacts"):
        if not isinstance(result[key], list) or not all(isinstance(v, str) for v in result[key]):
            raise ValueError("Agent 数组字段格式错误：" + key)
    if result["confidence"] not in {"high", "medium", "low", None}:
        raise ValueError("Agent confidence 无效")
    if result["rework"] is not None and (not isinstance(result["rework"], list) or not all(isinstance(v, str) for v in result["rework"])):
        raise ValueError("Agent rework 无效")
    graph = result["graph"]
    if graph is not None:
        if not isinstance(graph, dict) or set(graph) != {"enabled"}:
            raise ValueError("Agent graph 无效")
        enabled = graph["enabled"]
        if enabled is not None and (not isinstance(enabled, list) or not all(isinstance(v, str) for v in enabled)):
            raise ValueError("Agent graph.enabled 无效")
    return result


def execute(prompt, directory, browser=False, on_event=None, timeout=900, home=None,
            resume_session=None, record_session=False, selection=None, root=ROOT, cancel=None):
    """Role prompts remain independent; every role uses the selected API transport."""
    if browser:
        raise RuntimeError("模型核验不直接控制浏览器；请由程序采集页面并将真实结果交给模型")
    if resume_session:
        raise RuntimeError("API 核验不恢复旧 Codex 会话；请重新执行本阶段核验")
    selection = selection if selection is not None else llm.capture(root)
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    response_path = directory / "response.json"
    response_path.unlink(missing_ok=True)
    write_json(directory / "response.schema.json", RESULT_SCHEMA)
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    start = time.monotonic()
    meta = {**llm.identity(selection), "session_id": "api_" + uuid4().hex, "browser": False,
            "mcp_tool_calls": [], "status": "running", "timed_out": False}
    try:
        if on_event: on_event({"type": "model.started", "model": meta["model"]})
        raw = llm.text_sync(prompt, root=root, selection=selection, purpose="agent_review", timeout=timeout,
                            cancel=cancel, schema=RESULT_SCHEMA, log_path=directory / "api_call.json")
        result = validate_result(llm.parse_json(raw))
        write_json(response_path, result)
        meta.update(status="succeeded", exit_code=0)
        if on_event: on_event({"type": "model.completed", "model": meta["model"]})
        return result, meta
    except Exception:
        meta.update(status="failed", exit_code=1)
        raise
    finally:
        meta["elapsed_seconds"] = round(time.monotonic() - start, 2)
        write_json(directory / "execution.json", meta)
