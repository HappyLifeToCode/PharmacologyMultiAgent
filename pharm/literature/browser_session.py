"""Persistent project browser: headless collection, explicit interactive recovery."""
from __future__ import annotations

from contextlib import contextmanager
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

    def __init__(self, message, *, relaunch_allowed=False):
        super().__init__(message)
        self.relaunch_allowed = relaunch_allowed


class BrowserSessionBusy(RuntimeError):
    failure_code = "browser_session_busy"


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
    except OSError as exc:
        raise BrowserSessionUnavailable("采集浏览器已关闭，续跑时会自动在后台恢复专用会话", relaunch_allowed=True) from exc
    except (ValueError, KeyError) as exc:
        raise BrowserSessionUnavailable("采集浏览器会话信息无效，未连接其他窗口；请检查项目专用会话") from exc
    if current.get("webSocketDebuggerUrl") != record.get("websocket"):
        raise BrowserSessionUnavailable("本机浏览器会话已变化，未连接其他窗口；请重新打开采集浏览器")
    return record["endpoint"]


@contextmanager
def session_lock(root):
    path = session_file(root).parent / "session.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise BrowserSessionBusy("采集浏览器正在切换或存在遗留锁，请稍后重试；未启动其他窗口") from exc
    try:
        with os.fdopen(fd, "w") as stream: stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def _close_owned(root, address):
    """Gracefully save this verified project profile; never kill arbitrary PIDs."""
    from playwright.sync_api import sync_playwright, Error
    if endpoint(root) != address: raise BrowserSessionUnavailable("采集浏览器会话已变化，未关闭其他窗口")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(address, timeout=10000)
        # Check again after connecting, before sending Browser.close.
        if endpoint(root) != address: raise BrowserSessionUnavailable("采集浏览器会话已变化，未关闭其他窗口")
        session = browser.new_browser_cdp_session()
        try: session.send("Browser.close")
        except Error: pass  # Disconnect can arrive before the close response.
    for _ in range(50):
        try: version(address)
        except OSError: return
        time.sleep(.1)
    raise BrowserSessionUnavailable("专用浏览器尚未退出，暂未切换模式，请稍后续跑")


def _edge_executable():
    candidates = [Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                  Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe"]
    executable = next((p for p in candidates if p.is_file()), None)
    if executable is None: raise RuntimeError("未找到Microsoft Edge，请先安装项目支持的浏览器")
    return executable


def _launch(root, mode, *, manual_url=None):
    path = session_file(root)
    executable = _edge_executable()
    profile = path.parent / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    address = "http://127.0.0.1:" + str(port)
    # A separate profile; no credentials/cookies are copied from the user's regular browser.
    url = manual_url if mode == "interactive" and manual_url else "https://www.cnki.net/" if mode == "interactive" else "about:blank"
    args = [str(executable), "--user-data-dir=" + str(profile), "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=" + str(port), "--no-first-run"]
    if mode == "background": args.append("--headless=new")
    process = subprocess.Popen([*args, url],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    for _ in range(100):
        try:
            current = version(address)
            if current.get("webSocketDebuggerUrl"):
                record = {"endpoint": address, "websocket": current["webSocketDebuggerUrl"], "profile": str(profile.resolve()), "mode": mode, "created_at": now()}
                if manual_url: record["manual_url"] = manual_url
                write_json(path, record)
                return address
        except (OSError, ValueError): pass
        time.sleep(.2)
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    raise BrowserSessionUnavailable("专用采集浏览器尚未准备好，进度已保留；请稍后续跑")


def manual_page(value):
    if not isinstance(value, str): return None
    try:
        url = urlparse(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.port not in (None, 443): return None
        if url.hostname == "www.cnki.net" and url.path in {"", "/"}: return value
        if url.hostname == "kns.cnki.net" and (url.path.startswith("/kns8s/") or url.path == "/kcms2/article/abstract"): return value
    except ValueError: pass
    return None


def remember_manual_page(root, url):
    url = manual_page(url)
    if not url: return
    with session_lock(root):
        path = session_file(root)
        if path.is_file() and endpoint(root):
            record = read_json(path); record["manual_url"] = url
            write_json(path, record)


def _ensure_session(root, mode, *, collection_owner=False):
    with session_lock(root):
        collection_lock = Path(root).resolve() / "local/agent/cnki-collection/collector.lock"
        if collection_owner:
            if not collection_lock.is_file() or collection_lock.read_text().strip() != str(os.getpid()):
                raise BrowserSessionBusy("采集未持有本进程的锁，未切换其他任务的浏览器")
        elif mode == "interactive" and collection_lock.exists():
            raise BrowserSessionBusy("文献采集正在运行，请先停止任务再手动打开窗口")
        path = session_file(root)
        record = read_json(path) if path.is_file() else {}
        try: address = endpoint(root)
        except BrowserSessionUnavailable as exc:
            if not exc.relaunch_allowed: raise
            address = None
        if address and record.get("mode", "interactive") == mode: return address
        if address: _close_owned(root, address)
        return _launch(root, mode, manual_url=manual_page(record.get("manual_url")))


def background_endpoint(root=ROOT):
    return _ensure_session(root, "background")


def interactive_collection_endpoint(root=ROOT):
    return _ensure_session(root, "interactive", collection_owner=True)


def open_session(root=ROOT):
    if (Path(root).resolve() / "local/agent/cnki-collection/collector.lock").exists():
        raise BrowserSessionBusy("文献采集正在后台运行，请先停止任务再打开人工处理窗口")
    _ensure_session(root, "interactive")
    return {"ready": True, "message": "项目专用窗口已打开。请手动完成需要的登录或验证，然后点击继续采集；任务会沿用可用模式继续，后台遭拒时会自动使用可见窗口。"}
