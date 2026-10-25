"""Project-owned interactive browser session for legitimate CNKI access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from ..core.common import ROOT, now, read_json, write_json


class BrowserSessionUnavailable(RuntimeError):
    failure_code = "browser_session_unavailable"


def session_file(root=ROOT):
    return Path(root).resolve() / "local/agent/cnki-browser/session.json"


def version(endpoint):
    url = urlparse(endpoint)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("只允许连接项目专用的本机采集浏览器")
    with build_opener(ProxyHandler({})).open(endpoint + "/json/version", timeout=1) as response:
        value = json.load(response)
    websocket = urlparse(value.get("webSocketDebuggerUrl", ""))
    if websocket.scheme != "ws" or websocket.hostname != "127.0.0.1" or websocket.port != url.port or websocket.username or websocket.password or not websocket.path.startswith("/devtools/browser/") or websocket.query or websocket.fragment:
        raise ValueError("浏览器连接不属于本机专用会话")
    return value


def endpoint(root=ROOT):
    path = session_file(root)
    if not path.is_file(): return None
    record = read_json(path)
    expected_profile = (path.parent / "profile").resolve()
    if Path(record.get("profile", "")).resolve() != expected_profile:
        raise ValueError("采集浏览器会话不是项目专用目录")
    try:
        current = version(record["endpoint"])
    except (OSError, ValueError, KeyError) as exc:
        raise BrowserSessionUnavailable("采集浏览器窗口已关闭或会话不可用，请重新打开采集浏览器后续跑") from exc
    if current.get("webSocketDebuggerUrl") != record.get("websocket"):
        raise BrowserSessionUnavailable("本机浏览器会话已变化，未连接其他窗口；请重新打开采集浏览器")
    return record["endpoint"]


def open_session(root=ROOT):
    path = session_file(root)
    if path.is_file():
        try:
            if endpoint(root): return {"ready": True, "message": "采集浏览器已打开。请在该窗口确认知网能正常搜索，再点击继续采集。"}
        except (OSError, ValueError, RuntimeError): pass
    candidates = [Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                  Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe"]
    executable = next((p for p in candidates if p.is_file()), None)
    if executable is None: raise RuntimeError("未找到Microsoft Edge，请先安装项目支持的浏览器")
    profile = path.parent / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    address = "http://127.0.0.1:" + str(port)
    # A separate profile; no credentials/cookies are copied from the user's regular browser.
    subprocess.Popen([str(executable), "--user-data-dir=" + str(profile), "--remote-debugging-address=127.0.0.1",
                      "--remote-debugging-port=" + str(port), "--no-first-run", "https://www.cnki.net/"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    for _ in range(80):
        try:
            current = version(address)
            if current.get("webSocketDebuggerUrl"):
                write_json(path, {"endpoint": address, "websocket": current["webSocketDebuggerUrl"], "profile": str(profile.resolve()), "created_at": now()})
                return {"ready": True, "message": "项目专用Edge窗口已打开。请正常搜索本次关键词；如需登录或验证，请在窗口内手动完成，再点击继续采集。"}
        except (OSError, ValueError): pass
        time.sleep(.2)
    raise RuntimeError("采集浏览器尚未准备好，请检查新打开的Edge窗口")
