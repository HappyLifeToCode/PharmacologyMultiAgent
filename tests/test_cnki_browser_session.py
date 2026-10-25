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


def owned_record(root, mode=None):
    path = browser_session.session_file(root)
    value = {"endpoint": "http://127.0.0.1:9222", "websocket": "ws://127.0.0.1:9222/devtools/browser/owned", "profile": str(path.parent / "profile")}
    if mode: value["mode"] = mode
    write_json(path, value)
    return value


def test_background_is_default_and_reuses_existing_hidden_session(tmp_path, monkeypatch):
    record = owned_record(tmp_path, "background")
    monkeypatch.setattr(browser_session, "version", lambda _: {"webSocketDebuggerUrl": record["websocket"]})
    monkeypatch.setattr(browser_session, "_launch", lambda *a, **k: pytest.fail("must reuse"))
    monkeypatch.setattr(browser_session, "_close_owned", lambda *a: pytest.fail("must not close"))
    assert browser_session.background_endpoint(tmp_path) == record["endpoint"]
    assert browser_session.background_endpoint(tmp_path) == record["endpoint"]
    assert not (browser_session.session_file(tmp_path).parent / "session.lock").exists()


def test_legacy_visible_session_is_saved_then_switched_to_background(tmp_path, monkeypatch):
    record = owned_record(tmp_path)  # Legacy records have no mode.
    profile = browser_session.session_file(tmp_path).parent / "profile"
    profile.mkdir(); (profile / "fixture-cookie").write_text("keep")
    calls = []
    monkeypatch.setattr(browser_session, "version", lambda _: {"webSocketDebuggerUrl": record["websocket"]})
    monkeypatch.setattr(browser_session, "_close_owned", lambda root, address: calls.append(("close", address)))
    monkeypatch.setattr(browser_session, "_launch", lambda root, mode, **k: calls.append(("launch", mode)) or "http://127.0.0.1:9223")
    assert browser_session.background_endpoint(tmp_path).endswith(":9223")
    assert calls == [("close", record["endpoint"]), ("launch", "background")]
    assert (profile / "fixture-cookie").read_text() == "keep"


def test_closed_session_resumes_in_background_without_opening_a_window(tmp_path, monkeypatch):
    owned_record(tmp_path, "background")
    def closed(_): raise OSError("closed")
    monkeypatch.setattr(browser_session, "version", closed)
    calls=[]
    monkeypatch.setattr(browser_session, "_close_owned", lambda *a: pytest.fail("not live"))
    monkeypatch.setattr(browser_session, "_launch", lambda root, mode, **k: calls.append(mode) or "http://127.0.0.1:9223")
    browser_session.background_endpoint(tmp_path)
    assert calls == ["background"]


def test_changed_session_identity_never_closes_or_launches_over_another_session(tmp_path, monkeypatch):
    owned_record(tmp_path, "background")
    monkeypatch.setattr(browser_session, "version", lambda _: {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/other"})
    monkeypatch.setattr(browser_session, "_close_owned", lambda *a: pytest.fail("foreign session"))
    monkeypatch.setattr(browser_session, "_launch", lambda *a, **k: pytest.fail("must not overwrite"))
    with pytest.raises(browser_session.BrowserSessionUnavailable): browser_session.background_endpoint(tmp_path)
    assert browser_session.session_file(tmp_path).is_file()


def test_explicit_manual_open_switches_only_owned_browser_and_resume_goes_back(tmp_path, monkeypatch):
    record=owned_record(tmp_path, "background")
    calls=[]
    monkeypatch.setattr(browser_session, "version", lambda _: {"webSocketDebuggerUrl": record["websocket"]})
    monkeypatch.setattr(browser_session, "_close_owned", lambda root, address: calls.append("close"))
    def launch(root, mode, **kwargs):
        calls.append(mode); record["mode"] = mode
        write_json(browser_session.session_file(root), record)
        return record["endpoint"]
    monkeypatch.setattr(browser_session, "_launch", launch)
    assert browser_session.open_session(tmp_path)["ready"]
    browser_session.background_endpoint(tmp_path)
    assert calls == ["close", "interactive", "close", "background"]


def test_manual_open_does_not_interrupt_active_collection_or_remove_external_lock(tmp_path, monkeypatch):
    record=owned_record(tmp_path, "background")
    lock=tmp_path / "local/agent/cnki-collection/collector.lock"
    lock.parent.mkdir(); lock.write_text("external")
    monkeypatch.setattr(browser_session, "_close_owned", lambda *a: pytest.fail("busy"))
    monkeypatch.setattr(browser_session, "_launch", lambda *a, **k: pytest.fail("busy"))
    with pytest.raises(browser_session.BrowserSessionBusy): browser_session.open_session(tmp_path)
    assert lock.read_text() == "external"
    lock.unlink()
    switch_lock=browser_session.session_file(tmp_path).parent / "session.lock"
    switch_lock.write_text("external")
    with pytest.raises(browser_session.BrowserSessionBusy): browser_session.background_endpoint(tmp_path)
    assert switch_lock.read_text() == "external"


@pytest.mark.parametrize("mode", ["background", "interactive"])
def test_browser_launch_uses_headless_only_for_background_and_same_project_profile(tmp_path, monkeypatch, mode):
    calls=[]
    monkeypatch.setattr(browser_session, "_edge_executable", lambda: tmp_path / "msedge.exe")
    monkeypatch.setattr(browser_session.subprocess, "Popen", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(browser_session, "version", lambda address: {"webSocketDebuggerUrl": address.replace("http:", "ws:") + "/devtools/browser/owned"})
    address=browser_session._launch(tmp_path, mode)
    assert ("--headless=new" in calls[0]) is (mode == "background")
    assert calls[0][-1] == ("about:blank" if mode == "background" else "https://www.cnki.net/")
    assert "--user-data-dir=" + str(browser_session.session_file(tmp_path).parent / "profile") in calls[0]
    record=json.loads(browser_session.session_file(tmp_path).read_text(encoding="utf-8"))
    assert record["mode"] == mode and record["endpoint"] == address


def test_manual_recovery_url_is_private_and_limited_to_normal_cnki_pages(tmp_path, monkeypatch):
    record=owned_record(tmp_path, "background")
    monkeypatch.setattr(browser_session, "version", lambda _: {"webSocketDebuggerUrl": record["websocket"]})
    browser_session.remember_manual_page(tmp_path, "https://kns.cnki.net/kcms2/article/abstract?v=fixture")
    saved=json.loads(browser_session.session_file(tmp_path).read_text(encoding="utf-8"))
    assert saved["manual_url"].endswith("v=fixture")
    for value in ("https://other.example/login", "file:///C:/private", "https://kns.cnki.net/reader/fulltext", "https://user:secret@kns.cnki.net/kns8s/search"):
        assert browser_session.manual_page(value) is None


def test_manual_browser_api_reports_busy_without_interrupting_the_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    lock=tmp_path / "local/agent/cnki-collection/collector.lock"
    lock.parent.mkdir(parents=True); lock.write_text("external")
    with TestClient(backend.app,base_url="http://localhost") as client:
        assert client.post('/api/literature/browser/open',json={}).status_code==409
    assert lock.read_text()=="external"
