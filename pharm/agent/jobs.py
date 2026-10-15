"""Shared cancellable worker processes for forward and reverse queries."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from ..core.common import now, write_json


class QueryCancelled(RuntimeError):
    pass


def request_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("request_id/supersedes 必须是 1—64 个字母、数字、下划线或连字符")
    return value


def _worker(body, directory):
    from . import forward, reverse, progress, query_cache

    directory = Path(directory)
    progress.bind(progress.path_for(directory))
    progress.report("starting", "准备查询进程")
    try:
        def compute():
            if directory.name.startswith("reverse_"):
                return reverse.query(text=body.get("text"), disease=body.get("disease"),
                                     disease_id=body.get("disease_id"), top_k=body.get("top_k"),
                                     output_dir=directory, run_id=directory.name)
            return forward.query(body.get("herbs"), formula=body.get("formula"),
                                 text=body.get("text"), top_k=body.get("top_k"),
                                 batman_threshold=body.get("batman_threshold", 0.84),
                                 formula_variant_id=body.get("formula_variant_id"),
                                 formula_herbs=body.get("formula_herbs"),
                                 formula_fingerprint=body.get("formula_fingerprint"),
                                 output_dir=directory, run_id=directory.name)
        response = query_cache.execute(body, directory, "reverse" if directory.name.startswith("reverse_") else "forward", compute)
        write_json(directory / "worker_outcome.json", {"status": "succeeded", "run_id": directory.name})
        progress.report("completed", "查询完成")
    except Exception as exc:
        progress.report("failed", "查询未完成", detail=str(exc))
        write_json(directory / "worker_outcome.json", {
            "status": "failed", "error": str(exc),
            "http_status": 503 if isinstance(exc, OSError) else 400,
        })
    finally:
        progress.bind(None)


def _stop_process(process):
    if process.is_alive():
        process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    if process.is_alive():
        raise RuntimeError("无法停止查询进程，请检查服务日志")


@dataclass
class Job:
    request_id: str
    process: object
    directory: Path
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    cleanup_error: str = ""
    owner_id: str | None = None


class QueryManager:
    def __init__(self, worker=_worker, close_grace=10):
        self.worker = worker
        self.active = {}
        self.cancelled = OrderedDict()
        self.lock = threading.Lock()
        self.tabs = {}
        self.close_grace = close_grace
        self.instance_id = uuid4().hex

    def connect_tab(self, owner_id):
        request_identifier(owner_id)
        with self.lock:
            tab = self.tabs.setdefault(owner_id, {"connections": 0, "disconnected_at": time.monotonic()})
            tab["connections"] += 1

    def disconnect_tab(self, owner_id):
        with self.lock:
            tab = self.tabs.get(owner_id)
            if tab:
                tab["connections"] = max(0, tab["connections"] - 1)
                if not tab["connections"]:
                    tab["disconnected_at"] = time.monotonic()

    def tab_alive(self, owner_id):
        with self.lock:
            tab = self.tabs.setdefault(owner_id, {"connections": 0, "disconnected_at": time.monotonic()})
            return bool(tab["connections"] or time.monotonic() - tab["disconnected_at"] < self.close_grace)

    async def stop(self, request_id, reason="user_cancelled"):
        request_id = request_identifier(request_id)
        with self.lock:
            # A cancellation arriving before its POST also prevents a late start.
            self.cancelled[request_id] = reason
            while len(self.cancelled) > 1000:
                self.cancelled.popitem(last=False)
            job = self.active.get(request_id)
            if job:
                job.reason = reason
                job.cancel.set()
        if job and not await run_in_threadpool(job.done.wait, 15):
            raise RuntimeError("上一条查询尚未停止，暂不启动重复查询")
        if job and job.cleanup_error:
            raise RuntimeError(job.cleanup_error)
        return {"request_id": request_id, "stopped": True,
                "status": "cancelled" if job else "not_running"}

    async def run(self, request, body, root, request_id, supersedes=None, owner_id=None, *, direction="forward"):
        if direction not in ("forward", "reverse"):
            raise ValueError("不支持的查询方向")
        request_id = request_identifier(request_id)
        if owner_id is not None:
            request_identifier(owner_id)
            self.tab_alive(owner_id)
        if supersedes is not None:
            if request_identifier(supersedes) == request_id:
                raise ValueError("新请求不能替代自身")
            await self.stop(supersedes, "superseded_by_retry")
        directory = Path(root) / "local/agent" / direction / (direction + "_" + uuid4().hex)
        process = multiprocessing.get_context("spawn").Process(
            target=self.worker, args=(body, str(directory)), daemon=True,
        )
        job = Job(request_id, process, directory, owner_id=owner_id)
        with self.lock:
            if request_id in self.cancelled:
                raise QueryCancelled("本次查询已停止，请重新提交新请求")
            if request_id in self.active:
                raise ValueError("该查询仍在运行，请勿重复提交")
            self.active[request_id] = job
        status = {"request_id": request_id, "run_id": directory.name,
                  "created_at": now(), "status": "running", "owner_id": owner_id,
                  "service_instance_id": self.instance_id, "direction": direction}
        status_path = Path(root) / "local/agent/jobs" / (request_id + ".json")
        started = False
        try:
            write_json(status_path, status)
            process.start()
            started = True
            status["worker_pid"] = process.pid
            write_json(status_path, status)
            while True:
                tab_closed = owner_id is not None and not self.tab_alive(owner_id)
                disconnected = owner_id is None and await request.is_disconnected()
                if job.cancel.is_set() or tab_closed or disconnected:
                    job.reason = job.reason or ("tab_closed" if tab_closed else "client_disconnected")
                    raise QueryCancelled("本次查询已停止，可以重新查询")
                if not process.is_alive():
                    break
                await asyncio.sleep(0.25)
            process.join(timeout=0)
            outcome_path = directory / "worker_outcome.json"
            if not outcome_path.is_file():
                raise RuntimeError(f"查询进程异常退出（代码 {process.exitcode}），中间记录已保留")
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            if outcome["status"] != "succeeded":
                if outcome.get("http_status") == 503:
                    raise OSError(outcome["error"])
                raise RuntimeError(outcome["error"])
            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            status["status"] = result.get("status", "succeeded")
            return {"request_id": request_id, "run_id": directory.name, "output_dir": str(directory.resolve()), "result": result}
        except (QueryCancelled, asyncio.CancelledError):
            status.update(status="cancelled", reason=job.reason or "request_cancelled")
            raise
        except Exception as exc:
            status.update(status="failed", error=str(exc))
            raise
        finally:
            try:
                if started:
                    try:
                        await run_in_threadpool(_stop_process, process)
                    except Exception as exc:
                        job.cleanup_error = str(exc)
                        status.update(status="failed", reason="termination_failed", error=str(exc))
                        raise
                status["finished_at"] = now()
                write_json(status_path, status)
                write_json(directory / "job.json", status)
            finally:
                with self.lock:
                    if not job.cleanup_error:
                        self.active.pop(request_id, None)
                job.done.set()
                if started and not job.cleanup_error:
                    process.close()


manager = QueryManager()
