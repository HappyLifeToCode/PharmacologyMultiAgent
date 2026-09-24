import json
import queue
import time
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from pharm.assist import bridge
from pharm.assist.bridge import AssistManager, AssistSession, AssistUnavailable

PAGE_URL = "data:text/html," + quote(
    "<html><body><input id='t' style='width:600px;height:40px'></body></html>")


def wait_item(subscriber, kind, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            item = subscriber.get(timeout=max(deadline - time.time(), 0.1))
        except queue.Empty:
            break
        if item and item[0] == kind:
            return item
    raise AssertionError("等待 %s 超时" % kind)


def wait_json(subscriber, payload_type, timeout=20):
    item = wait_item(subscriber, "json", timeout)
    while item[1].get("type") != payload_type:
        item = wait_item(subscriber, "json", timeout)
    return item[1]


def ensure_frame(session, subscriber, timeout=40):
    """headless 下合成器按需出帧：Tab 切换焦点引发重绘，轻拍直到拿到一帧。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        session.handle_input({"type": "key", "key": "Tab"})
        try:
            item = subscriber.get(timeout=2)
            while item is not None:
                if item[0] == "frame":
                    return item
                item = subscriber.get_nowait()
        except queue.Empty:
            pass
    raise AssertionError("screencast 未出帧")


def wait_eval(session, expression, expected, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if session.evaluate(expression) == expected:
            return
        time.sleep(0.1)
    raise AssertionError("表达式未达预期：%s != %r" % (expression, expected))


@pytest.fixture
def session():
    s = AssistSession(PAGE_URL, headless=True)
    yield s
    s.close()


def test_screencast_frames_flow_with_ack(session):
    subscriber = session.subscribe()
    _, meta, jpeg = ensure_frame(session, subscriber)
    assert jpeg[:2] == b"\xff\xd8"
    assert meta["width"] > 0 and meta["height"] > 0
    # 每帧都回 ack，流不停：继续交互还有后续帧
    _, meta2, jpeg2 = ensure_frame(session, subscriber)
    assert jpeg2[:2] == b"\xff\xd8"
    assert (meta2["width"], meta2["height"]) == (meta["width"], meta["height"])


def test_input_injection_changes_input_value(session):
    subscriber = session.subscribe()
    ensure_frame(session, subscriber)
    session.handle_input({"type": "click", "x": 0.2, "y": 0.03})
    session.handle_input({"type": "text", "text": "baishao"})
    wait_eval(session, "document.getElementById('t').value", "baishao")
    session.handle_input({"type": "key", "key": "Backspace"})
    wait_eval(session, "document.getElementById('t').value", "baisha")
    with pytest.raises(ValueError, match="未知"):
        session.handle_input({"type": "explode"})


def test_guidance_history_and_increment():
    s = AssistSession(PAGE_URL, guidance="请先完成人机验证", headless=True)
    try:
        subscriber = s.subscribe()
        assert wait_json(subscriber, "state")["state"] == "running"
        assert wait_json(subscriber, "guidance")["text"] == "请先完成人机验证"
        assert s.guidance_history[0]["text"] == "请先完成人机验证"
        s.post_guidance("第二步：点击继续")
        assert wait_json(subscriber, "guidance")["text"] == "第二步：点击继续"
        assert [g["text"] for g in s.guidance_history] == ["请先完成人机验证", "第二步：点击继续"]
        with pytest.raises(ValueError):
            s.post_guidance("  ")
    finally:
        s.close()


def test_states_and_close(session):
    assert session.state == "running"
    session.set_state("waiting_human")
    subscriber = session.subscribe()
    assert wait_json(subscriber, "state")["state"] == "waiting_human"
    session.set_state("done")
    session.close()
    assert session.state == "closed"
    with pytest.raises(RuntimeError, match="已关闭"):
        session.handle_input({"type": "click", "x": 0.5, "y": 0.5})
    with pytest.raises(RuntimeError, match="已关闭"):
        session.evaluate("1")
    session.close()  # 幂等


def test_launch_failure_raises_unavailable(monkeypatch):
    class BrokenPlaywright:
        def start(self):
            raise OSError("no display available")

    monkeypatch.setattr(bridge, "sync_playwright", lambda: BrokenPlaywright())
    with pytest.raises(AssistUnavailable, match="no display"):
        AssistSession(PAGE_URL, headless=False)


def test_manager_single_session():
    manager = AssistManager(headless=True)
    first = manager.start(PAGE_URL)
    try:
        with pytest.raises(RuntimeError, match="进行中"):
            manager.start(PAGE_URL)
        assert manager.status()["state"] == "running"
        manager.stop()
        assert first.state == "closed"
        second = manager.start(PAGE_URL)  # 关闭后允许新会话
        manager.stop()
        assert second.state == "closed"
    finally:
        manager.stop()


@pytest.fixture
def backend(tmp_path, monkeypatch):
    from server import app as backend_module
    monkeypatch.setattr(backend_module, "ROOT", tmp_path)
    manager = AssistManager(headless=True)
    monkeypatch.setattr(backend_module, "assist_manager", manager)
    yield backend_module
    manager.stop()


def test_assist_endpoints_single_session(backend):
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/assist/start", json={"url": PAGE_URL}).status_code == 200
        assert client.post("/api/assist/start", json={"url": PAGE_URL}).status_code == 409
        status = client.get("/api/assist/status").json()
        assert status["state"] == "running" and status["url"].startswith("data:")
        assert client.post("/api/assist/start", json=[]).status_code == 400
        assert client.post("/api/assist/start", json={"url": "javascript:alert(1)"}).status_code == 400
        assert client.post("/api/assist/stop").json()["state"] == "closed"
        assert client.post("/api/assist/start", json={"url": PAGE_URL}).status_code == 200
        assert client.post("/api/assist/stop").json()["state"] == "closed"
        assert client.get("/api/assist/status").json()["state"] == "closed"


def test_agent_handoff_supplies_url_for_user_assistance(backend):
    with TestClient(backend.app, base_url="http://localhost") as client:
        response = client.post("/api/assist/request", json={
            "url": PAGE_URL, "guidance": "请完成页面验证", "context": {"source": "genecards"}})
        assert response.status_code == 200
        request = response.json()
        assert request["state"] == "pending"
        status = client.get("/api/assist/status").json()
        assert status["state"] == "pending"
        assert status["pending"]["request_id"] == request["request_id"]
        assert status["pending"]["url"] == PAGE_URL
        started = client.post("/api/assist/start", json={"request_id": request["request_id"]})
        assert started.status_code == 200
        assert started.json()["url"] == PAGE_URL
        assert client.post("/api/assist/stop").status_code == 200


def ws_collect(ws, predicate, timeout=40):
    """读下行消息直到 predicate 命中；frame 文本帧后紧跟二进制帧。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        message = ws.receive()
        if message.get("text"):
            payload = json.loads(message["text"])
            binary = ws.receive_bytes() if payload.get("type") == "frame" else None
            if predicate(payload, binary):
                return payload, binary
    raise AssertionError("等待 WS 消息超时")


def test_ws_frames_guidance_and_input_roundtrip(backend):
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.post("/api/assist/start", json={"url": PAGE_URL, "guidance": "请接管验证"}).status_code == 200
        with client.websocket_connect("/ws/assist") as ws:
            payload, _ = ws_collect(ws, lambda p, b: p["type"] == "state")
            assert payload["state"] == "running"
            payload, _ = ws_collect(ws, lambda p, b: p["type"] == "guidance")
            assert payload["text"] == "请接管验证"
            session = backend.assist_manager.current()
            for _ in range(8):  # headless 按需出帧，连续轻拍确保有帧在途
                session.handle_input({"type": "key", "key": "Tab"})
                time.sleep(0.4)
            payload, jpeg = ws_collect(ws, lambda p, b: p["type"] == "frame" and b)
            assert payload["width"] > 0 and payload["height"] > 0
            assert jpeg[:2] == b"\xff\xd8"
            ws.send_json({"type": "bogus"})
            payload, _ = ws_collect(ws, lambda p, b: p["type"] == "error")
            assert "未知" in payload["message"]
            ws.send_json({"type": "click", "x": 0.2, "y": 0.03})
            ws.send_json({"type": "text", "text": "甘草"})
        wait_eval(session, "document.getElementById('t').value", "甘草")
        assert client.post("/api/assist/stop").status_code == 200


def test_ws_without_session_reports_idle(backend):
    with TestClient(backend.app, base_url="http://localhost") as client:
        with client.websocket_connect("/ws/assist") as ws:
            payload = json.loads(ws.receive_text())
            assert payload == {"type": "state", "state": "idle"}


def test_ws_rejects_foreign_origin(backend):
    from starlette.websockets import WebSocketDisconnect
    with TestClient(backend.app, base_url="http://localhost") as client:
        client.post("/api/assist/start", json={"url": PAGE_URL})
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/assist", headers={"Origin": "http://evil.example"}):
                pass
        client.post("/api/assist/stop")
