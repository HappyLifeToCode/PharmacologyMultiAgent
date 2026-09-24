"""人机协助内嵌浏览器桥：headed Chromium 画面推流 + 输入回传 + 引导消息。

生产路径一律 headed（甲方要求用户在内嵌画面中人工接管人机验证）；
headless=True 仅用于无显示环境的自动化测试，文档明示的测试例外。
headed 启动失败抛 AssistUnavailable，不静默降级 headless 假装可用。

线程模型：Playwright sync API 全部运行在专属浏览器线程（sync API 对象
不允许跨线程调用）；外部线程经任务队列交互（handle_input/evaluate/close）。
CDP screencast 事件在 Playwright 内部事件线程回调，仅做 ack 与线程安全的
帧分发。只保留最新帧，慢客户端丢帧不阻塞。

WebSocket 协议（server/app.py /ws/assist 实现）：
下行——文本帧 {"type":"frame","width":int,"height":int,"ts":float} 后紧跟
一条二进制 JPEG 帧；{"type":"guidance","text":str,"ts":str}；
{"type":"state","state":str}；{"type":"error","message":str}。
上行——{"type":"click"|"mousedown"|"mouseup"|"mousemove","x":0-1,"y":0-1}
（相对坐标，按最新帧原始尺寸换算）、{"type":"wheel","deltaX":n,"deltaY":n}、
{"type":"key","key":str}（Playwright 键名，如 Enter/Backspace）、
{"type":"text","text":str}（插入文本）。

状态机：running → waiting_human → done → closed；无会话对外呈 idle。
"""
from __future__ import annotations

import base64
import json
import queue
import threading
import time
from uuid import uuid4
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

STATES = ("idle", "running", "waiting_human", "done", "closed")
ACTIVE_STATES = ("running", "waiting_human")


class AssistUnavailable(RuntimeError):
    """headed 浏览器不可用（无显示环境等）；不静默降级。"""


def _validate_url(url):
    if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://") or url.startswith("data:")):
        raise ValueError("url 必须是 http(s) 或 data: URL")


def _ts():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AssistSession:
    def __init__(self, url, guidance=None, headless=False, viewport=(1280, 800), quality=60):
        _validate_url(url)
        self.url = url
        self.state = "running"
        self.last_error = None
        self._headless = headless
        self._viewport = viewport
        self._quality = quality
        self._guidance = []
        if guidance is not None:
            self._guidance.append({"text": str(guidance), "ts": _ts()})
        self._subs = set()
        self._latest = None  # (meta dict, jpeg bytes)
        self._tasks = queue.Queue()
        self._lock = threading.Lock()
        self._start_error = None
        self._started = threading.Event()
        self._current_url = url
        self._last_frame_at = 0.0
        self._thread = threading.Thread(target=self._run, name="pharm-assist", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=60):
            raise AssistUnavailable("浏览器启动超时")
        if self._start_error is not None:
            raise AssistUnavailable(self._start_error)

    # ---- 浏览器线程 ----

    def _run(self):
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context(viewport={"width": self._viewport[0], "height": self._viewport[1]})
            self._page = self._context.new_page()
            self._page.goto(self.url, wait_until="domcontentloaded", timeout=45000)
            self._current_url = self._page.url
            self._cdp = self._context.new_cdp_session(self._page)
            self._cdp.on("Page.screencastFrame", self._on_frame)
            self._cdp.send("Page.startScreencast", {"format": "jpeg", "quality": self._quality, "everyNthFrame": 1})
            # screencast 只在页面重绘时出帧；等首绘完成后轻拍焦点，让内嵌画面亮起
            self._page.wait_for_timeout(500)
            self._page.keyboard.press("Tab")
        except Exception as exc:
            self._start_error = str(exc)
            self._started.set()
            self._cleanup()
            return
        self._started.set()
        while True:
            try:
                task = self._tasks.get(timeout=0.2)
            except queue.Empty:
                # 某些 Cloudflare/OMIM 验证页不触发 CDP screencast 重绘；
                # 定期截图作为首帧和静态页面兜底，避免内嵌画布黑屏。
                if time.monotonic() - self._last_frame_at > 1.0:
                    self._publish_screenshot()
                continue
            if task is None:
                break
            fn, reply = task
            try:
                result = fn()
                if reply is not None:
                    reply.put((True, result))
            except Exception as exc:
                if reply is not None:
                    reply.put((False, exc))
                else:
                    self.last_error = str(exc)
        self._cleanup()

    def _publish_screenshot(self):
        try:
            image = self._page.screenshot(type="jpeg", quality=self._quality)
            meta = {"width": self._viewport[0], "height": self._viewport[1], "ts": time.time()}
            with self._lock:
                self._latest = (meta, image)
                subs = list(self._subs)
            self._last_frame_at = time.monotonic()
            self._broadcast(("frame", meta, image), subs)
        except Exception as exc:
            # 页面尚未完成导航或已关闭时允许下一轮重试；保留错误便于工作台诊断。
            self.last_error = "截图兜底失败：" + str(exc)

    def _cleanup(self):
        for action in (
            lambda: self._cdp.send("Page.stopScreencast"),
            self._context.close if hasattr(self, "_context") else None,
            self._browser.close if hasattr(self, "_browser") else None,
            self._pw.stop if hasattr(self, "_pw") else None,
        ):
            if action is None:
                continue
            try:
                action()
            except Exception:
                pass

    def _on_frame(self, params):
        # Playwright 内部事件线程；ack 失败说明目标已关闭，直接丢弃
        try:
            self._cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:
            return
        metadata = params.get("metadata") or {}
        meta = {"width": metadata.get("deviceWidth"), "height": metadata.get("deviceHeight"),
                "ts": metadata.get("timestamp") or time.time()}
        try:
            jpeg = base64.b64decode(params["data"])
        except Exception:
            return
        with self._lock:
            self._latest = (meta, jpeg)
            subs = list(self._subs)
        self._last_frame_at = time.monotonic()
        self._broadcast(("frame", meta, jpeg), subs)

    # ---- 订阅分发（线程安全） ----

    def _broadcast(self, item, subs=None):
        if subs is None:
            with self._lock:
                subs = list(self._subs)
        for subscriber in subs:
            try:
                subscriber.put_nowait(item)
            except queue.Full:
                pass  # 慢客户端丢帧不阻塞

    def subscribe(self):
        """新订阅队列；立即收到状态、全量 guidance 历史与最新帧快照。"""
        subscriber = queue.Queue(maxsize=256)
        with self._lock:
            self._subs.add(subscriber)
            snapshot = [("json", {"type": "state", "state": self.state})]
            snapshot += [("json", {"type": "guidance", **entry}) for entry in self._guidance]
            latest = self._latest
        for item in snapshot:
            subscriber.put(item)
        if latest is not None:
            subscriber.put(("frame", latest[0], latest[1]))
        return subscriber

    def unsubscribe(self, subscriber):
        with self._lock:
            self._subs.discard(subscriber)
        try:
            subscriber.put_nowait(None)
        except queue.Full:
            try:
                subscriber.get_nowait()
                subscriber.put_nowait(None)
            except (queue.Empty, queue.Full):
                pass

    # ---- 对外操作（线程安全） ----

    @property
    def current_url(self):
        return self._current_url

    @property
    def guidance_history(self):
        with self._lock:
            return list(self._guidance)

    def set_state(self, state):
        if state not in STATES or state == "idle":
            raise ValueError("非法协助会话状态：" + str(state))
        with self._lock:
            self.state = state
        self._broadcast(("json", {"type": "state", "state": state}))

    def post_guidance(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise ValueError("引导文本需为 1—2000 字")
        entry = {"text": text.strip(), "ts": _ts()}
        with self._lock:
            self._guidance.append(entry)
        self._broadcast(("json", {"type": "guidance", **entry}))

    def _submit(self, fn):
        if self.state == "closed":
            raise RuntimeError("协助会话已关闭")
        self._tasks.put((fn, None))

    def _coords(self, event):
        with self._lock:
            latest = self._latest
        if latest and latest[0].get("width") and latest[0].get("height"):
            width, height = latest[0]["width"], latest[0]["height"]
        else:
            width, height = self._viewport
        try:
            x = float(event.get("x", 0)) * width
            y = float(event.get("y", 0)) * height
        except (TypeError, ValueError):
            raise ValueError("坐标必须为 0—1 的相对数值") from None
        return min(max(x, 0), width), min(max(y, 0), height)

    def handle_input(self, event):
        """校验并入队一个输入事件；坐标为相对值，按最新帧原始尺寸换算。"""
        if not isinstance(event, dict):
            raise ValueError("输入事件必须是 JSON 对象")
        kind = event.get("type")
        if kind in ("click", "mousedown", "mouseup", "mousemove"):
            x, y = self._coords(event)
            if kind == "click":
                self._submit(lambda: self._page.mouse.click(x, y))
            elif kind == "mousedown":
                self._submit(lambda: (self._page.mouse.move(x, y), self._page.mouse.down()))
            elif kind == "mouseup":
                self._submit(lambda: (self._page.mouse.move(x, y), self._page.mouse.up()))
            else:
                self._submit(lambda: self._page.mouse.move(x, y))
        elif kind == "wheel":
            try:
                delta_x = float(event.get("deltaX", 0) or 0)
                delta_y = float(event.get("deltaY", 0) or 0)
            except (TypeError, ValueError):
                raise ValueError("deltaX/deltaY 必须为数值") from None
            self._submit(lambda: self._page.mouse.wheel(delta_x, delta_y))
        elif kind == "key":
            key = event.get("key")
            if not isinstance(key, str) or not key:
                raise ValueError("key 事件需要非空键名")
            self._submit(lambda: self._page.keyboard.press(key))
        elif kind == "text":
            text = event.get("text")
            if not isinstance(text, str):
                raise ValueError("text 事件需要文本")
            self._submit(lambda: self._page.keyboard.insert_text(text))
        else:
            raise ValueError("未知的输入事件类型：" + str(kind))

    def evaluate(self, expression, timeout=15):
        """在浏览器线程同步执行 JS 并返回结果（测试与后续编排用）。"""
        if self.state == "closed":
            raise RuntimeError("协助会话已关闭")
        reply = queue.Queue(maxsize=1)
        self._tasks.put((lambda: self._page.evaluate(expression), reply))
        ok, value = reply.get(timeout=timeout)
        if not ok:
            raise value
        return value

    def storage_state(self, timeout=15):
        """返回当前协助浏览器的 Playwright storage state，供原采集会话续用。"""
        if self.state == "closed":
            raise RuntimeError("协助会话已关闭")
        reply = queue.Queue(maxsize=1)
        self._tasks.put((lambda: self._context.storage_state(), reply))
        ok, value = reply.get(timeout=timeout)
        if not ok:
            raise value
        return value

    def close(self):
        if self.state == "closed":
            return
        with self._lock:
            self.state = "closed"
        self._broadcast(("json", {"type": "state", "state": "closed"}))
        self._tasks.put(None)
        self._thread.join(timeout=15)
        with self._lock:
            subs = list(self._subs)
        for subscriber in subs:
            self.unsubscribe(subscriber)


class AssistManager:
    """全局唯一协助会话；同一时刻至多一个（与 runs/.runner.lock 纪律一致），
    重复启动报 RuntimeError 而不是顶替。"""

    def __init__(self, headless=False):
        self._headless = headless
        self._lock = threading.Lock()
        self._session = None
        self._pending = None

    def request(self, url, guidance=None, context=None):
        """由采集端登记当前页面，等待用户从工作台接管。"""
        _validate_url(url)
        with self._lock:
            if self._session is not None and self._session.state in ACTIVE_STATES:
                raise RuntimeError("已有进行中的协助会话，请先停止")
            if self._pending is not None:
                raise RuntimeError("已有待处理的协助请求，请先启动或取消")
            self._pending = {"request_id": uuid4().hex, "url": url,
                             "guidance": str(guidance or ""), "context": context or {},
                             "requested_at": _ts()}
            return dict(self._pending)

    def start(self, url=None, guidance=None, request_id=None):
        with self._lock:
            if self._session is not None and self._session.state in ACTIVE_STATES:
                raise RuntimeError("已有进行中的协助会话，请先停止")
            pending = self._pending
            if url is None:
                if pending is None:
                    raise ValueError("当前没有待处理的 Agent 协助请求")
                if request_id is not None and request_id != pending["request_id"]:
                    raise ValueError("协助请求已变化，请刷新页面")
                url, guidance = pending["url"], guidance or pending["guidance"]
            else:
                _validate_url(url)
            self._pending = None
            try:
                self._session = AssistSession(url, guidance=guidance, headless=self._headless)
            except Exception:
                if pending is not None:
                    self._pending = pending
                raise
            return self._session

    def current(self):
        return self._session

    def stop(self):
        with self._lock:
            session = self._session
            self._pending = None
        if session is not None:
            session.close()
        return session

    def complete(self):
        """用户确认验证完成，通知等待中的采集端继续。"""
        with self._lock:
            session = self._session
        if session is None or session.state not in ACTIVE_STATES:
            raise RuntimeError("当前没有进行中的协助会话")
        session.set_state("done")
        return session

    def status(self):
        session = self._session
        if session is None:
            if self._pending is not None:
                return {"state": "pending", "url": self._pending["url"],
                        "guidance": ([{"text": self._pending["guidance"], "ts": self._pending["requested_at"]}]
                                     if self._pending["guidance"] else []),
                        "pending": dict(self._pending), "last_error": None}
            return {"state": "idle", "url": None, "guidance": [], "pending": None}
        return {"state": session.state, "url": session.current_url,
                "guidance": session.guidance_history, "pending": None,
                "last_error": session.last_error}
