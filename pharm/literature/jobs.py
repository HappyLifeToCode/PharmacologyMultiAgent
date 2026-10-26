"""Explicit, single-task web collection; model logs and credentials stay private."""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading

from ..core.common import now, write_json
from . import collector, dataset


def task_directory(root, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"cnki_collect_[a-f0-9]{32}", job_id):
        raise ValueError("文献任务编号无效")
    base = (Path(root).resolve() / "local/agent/cnki-collection").resolve()
    work = (base / job_id).resolve()
    if work.parent != base: raise ValueError("文献任务路径无效")
    return work


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class CollectionManager:
    def __init__(self, execute=None):
        self.execute = execute or collector.execute
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.active = {}
        self.running = set()

    def status(self, root, job_id):
        work = task_directory(root, job_id)
        task = read(work / "task.json")
        if not isinstance(task, dict) or task.get("id") != job_id: raise FileNotFoundError("文献任务不存在")
        with self.lock:
            running = str(work) in self.active
        state = task.get("status", "prepared")
        if state in {"running", "queued"} and not running:
            state = "interrupted"  # Never claim a previous server's process is still owned here.
        if running and (work / "cancel.requested.json").exists(): state = "cancelling"
        papers = [p for path in (work / "papers").glob("paper_*.json") if isinstance(p := read(path), dict)]
        scan, meta = read(work / "collection_progress.json", {}), read(work / "meta.json", {})
        if not isinstance(scan, dict): scan = {}
        if not isinstance(meta, dict): meta = {}
        pages = scan.get("pages", []) if isinstance(scan, dict) else []
        extracted = len(list((work / "extracted").glob("paper_*.json")))
        phase = "extracting" if task.get("collection_mode") == "local_analysis" or extracted or (scan.get("complete") and running) else "collecting"
        return {"id": job_id, "keyword": task["keyword"], "model": task.get("model", collector.DEFAULT_MODEL),
                "browser_mode": task.get("browser_mode", "background"), "browser_fallback": task.get("browser_fallback"),
                "collection_mode": task.get("collection_mode", "all" if task.get("limit") is None else "incremental"), "limit": task.get("limit"),
                "status": state, "phase": phase, "created_at": task.get("created_at"), "started_at": task.get("started_at"), "finished_at": task.get("finished_at"),
                "paper_count": len(papers), "new_papers": sum(p.get("rank") not in task.get("base_ranks", []) for p in papers),
                "abstract_count": sum(bool(p.get("abstract")) for p in papers), "pages_recorded": len(pages),
                "total_results": scan.get("total_results", meta.get("total_results")), "extracted_papers": extracted,
                "failure_code": task.get("failure_code"),
                "error": task.get("error") if state in {"failed_or_partial", "cancelled", "interrupted"} else None}

    def list(self, root):
        base = Path(root).resolve() / "local/agent/cnki-collection"
        rows = []
        for path in base.glob("cnki_collect_*/task.json"):
            try:
                rows.append(self.status(root, path.parent.name))
            except (ValueError, FileNotFoundError, KeyError):
                continue
        return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)

    def _start(self, root, task, work):
        key = str(work)
        (work / "cancel.requested.json").unlink(missing_ok=True)
        task.update(status="queued")
        task.pop("error", None); task.pop("finished_at", None)
        write_json(work / "task.json", task)
        def run():
            try:
                with self.condition:
                    while True:
                        if (work / "cancel.requested.json").exists(): raise collector.CollectionCancelled("文献采集已停止，进度已保留")
                        candidates = [k for k in self.active if Path(k).parent == work.parent]
                        if not any(Path(k).parent == work.parent for k in self.running) and candidates and candidates[0] == key:
                            self.running.add(key)
                            task.update(status="running", started_at=now())
                            write_json(work / "task.json", task)
                            break
                        self.condition.wait(timeout=0.5)
                self.execute(task, work, root=root, extract=True)
            except BaseException as exc:
                if task.get("status") not in {"cancelled", "failed_or_partial"}:
                    task.update(status="cancelled" if isinstance(exc, collector.CollectionCancelled) else "failed_or_partial", error=str(exc), finished_at=now())
                    write_json(work / "task.json", task)
            finally:
                with self.condition:
                    self.active.pop(key, None)
                    self.running.discard(key)
                    self.condition.notify_all()
        thread = threading.Thread(target=run, name=task["id"], daemon=True)
        self.active[key] = thread
        thread.start()

    def start(self, root, keyword, request_id, *, local_only=False, limit=None):
        root = Path(root).resolve()
        keyword = dataset.safe_keyword(keyword)
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", request_id):
            raise ValueError("启动请求编号无效")
        with self.lock:
            base = root / "local/agent/cnki-collection"
            for path in base.glob("cnki_collect_*/task.json"):
                task = read(path, {})
                if not isinstance(task, dict): continue
                if task.get("web_request_id") == request_id:
                    if task.get("keyword") != keyword: raise ValueError("相同请求编号不能更改关键词")
                    return self.status(root, path.parent.name)
            for key in self.active:
                if Path(key).parent == base:
                    current = self.status(root, Path(key).name)
                    if current["keyword"] == keyword: return current
            if (base / "collector.lock").exists() and not any(Path(k).parent == base for k in self.running):
                raise RuntimeError("已有终端采集任务或遗留锁，请先核对，不能并发启动")
            task, work = collector.prepare(keyword, limit, root=root, local_only=local_only)
            task["web_request_id"] = request_id
            self._start(root, task, work)
            return self.status(root, task["id"])

    def resume(self, root, job_id):
        work = task_directory(root, job_id)
        with self.lock:
            current = self.status(root, job_id)
            if str(work) in self.active or current["status"] == "succeeded": return current
            if (work.parent / "collector.lock").exists() and not any(Path(k).parent == work.parent for k in self.running):
                raise RuntimeError("已有终端采集任务或遗留锁，请先核对")
            task = read(work / "task.json")
            self._start(Path(root).resolve(), task, work)
            return self.status(root, job_id)

    def find_request(self, root, request_id):
        for path in (Path(root).resolve() / "local/agent/cnki-collection").glob("cnki_collect_*/task.json"):
            task = read(path, {})
            if isinstance(task, dict) and task.get("web_request_id") == request_id:
                return self.status(root, path.parent.name)
        return None

    def cancel(self, root, job_id):
        work = task_directory(root, job_id)
        with self.lock:
            self.status(root, job_id)
            if str(work) not in self.active: return self.status(root, job_id)
            write_json(work / "cancel.requested.json", {"requested_at": now()})
            return self.status(root, job_id)

    def shutdown(self):
        with self.lock:
            threads = list(self.active.items())
            for key, _ in threads: write_json(Path(key) / "cancel.requested.json", {"requested_at": now()})
        for _, thread in threads: thread.join(timeout=15)


manager = CollectionManager()
