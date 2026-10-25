import io
import json

import pytest
from fastapi.testclient import TestClient

from pharm.core.common import write_json
from pharm.literature import browser_session, collector
from server import app as backend


def test_remote_browser_endpoints_are_rejected_before_any_network_call(monkeypatch):
    monkeypatch.setattr(browser_session, "build_opener", lambda *a: pytest.fail("must not connect"))
    for endpoint in ("http://other.example:9222", "https://127.0.0.1:9222", "http://user:secret@127.0.0.1:9222", "http://127.0.0.1:9222/path"):
        with pytest.raises(ValueError): browser_session.version(endpoint)


def test_websocket_cannot_redirect_the_session_to_an_external_host(monkeypatch):
    class Opener:
        def open(self, *a, **k):
            return io.StringIO(json.dumps({"webSocketDebuggerUrl":"ws://other.example:9222/devtools/browser/id"}))
    monkeypatch.setattr(browser_session, "build_opener", lambda *a: Opener())
    with pytest.raises(ValueError): browser_session.version("http://127.0.0.1:9222")


def test_cdp_requires_the_project_profile_and_same_session_identity(tmp_path, monkeypatch):
    path=browser_session.session_file(tmp_path)
    record={"endpoint":"http://127.0.0.1:9222","websocket":"ws://127.0.0.1:9222/devtools/browser/owned","profile":str(path.parent/'profile')}
    write_json(path,record)
    monkeypatch.setattr(browser_session,"version",lambda address:{"webSocketDebuggerUrl":record['websocket']})
    config=collector.mcp_configuration(tmp_path)['mcp_servers']['playwright']
    assert '--cdp-endpoint' in config['args'] and '--headless' not in config['args']
    assert config['default_tools_approval_mode']=='approve' and len(config['enabled_tools'])==3
    monkeypatch.setattr(browser_session,"version",lambda address:{"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/browser/other"})
    with pytest.raises(RuntimeError,match="会话已变化"): browser_session.endpoint(tmp_path)
    record['profile']=str(tmp_path/'regular-browser-profile'); write_json(path,record)
    with pytest.raises(ValueError): browser_session.endpoint(tmp_path)


def test_browser_open_api_only_accepts_project_controlled_settings(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(backend,"ROOT",tmp_path)
    monkeypatch.setattr(browser_session,"open_session",lambda root:calls.append(root) or {"ready":True,"message":"ready"})
    with TestClient(backend.app,base_url="http://localhost") as client:
        assert client.post('/api/literature/browser/open',json={'endpoint':'http://other.example'}).status_code==400
        assert client.post('/api/literature/browser/open',json={},headers={'Origin':'https://other.example'}).status_code==403
        response=client.post('/api/literature/browser/open',json={})
        assert response.status_code==200 and response.json()=={'ready':True,'message':'ready'}
    assert calls==[tmp_path]


def test_closed_browser_has_a_recoverable_code_without_using_another_profile(tmp_path, monkeypatch):
    path = browser_session.session_file(tmp_path)
    write_json(path, {"endpoint": "http://127.0.0.1:9222", "profile": str(path.parent / "profile")})
    def closed(address): raise OSError("closed")
    monkeypatch.setattr(browser_session, "version", closed)
    with pytest.raises(browser_session.BrowserSessionUnavailable) as raised:
        collector.mcp_configuration(tmp_path)
    assert raised.value.failure_code == "browser_session_unavailable"


def test_preparing_a_task_does_not_require_reconnecting_a_closed_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_session, "endpoint", lambda *a: pytest.fail("prepare must not connect to a browser"))
    task, work = collector.prepare("测试方", root=tmp_path)
    assert task["status"] == "prepared" and (work / "mcp.example.json").is_file()
