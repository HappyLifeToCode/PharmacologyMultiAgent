"""Local-first literature work after the independent target query has returned."""
from __future__ import annotations

import hashlib
from pathlib import Path
import threading
from uuid import uuid4

from ..core.common import now, write_json
from . import jobs, service


class LiteratureFlowManager:
    def __init__(self, collections=None, query=None):
        self.collections = collections or jobs.manager
        self.query = query or service.query
        self.lock = threading.RLock()
        self.watchers = {}

    def _path(self, root, run_id):
        archive, direction = service.research_archive(run_id, root)
        path = Path(root).resolve() / "local/agent/literature-flows" / (run_id + ".json")
        return path, archive, direction

    def _save(self, path, record):
        record["updated_at"] = now()
        write_json(path, record)

    def _finish(self, root, run_id, attempt):
        with self.lock:
            path, archive, _ = self._path(root, run_id)
            record = jobs.read(path, {})
            if record.get("attempt") != attempt or record.get("state") == "ready": return
            if record.get("archive_sha256") != hashlib.sha256(archive.read_bytes()).hexdigest():
                record.update(state="failed", error="原研究归档已变化，未沿用文献流程")
            else:
                try:
                    payload = self.query(run_id, root=root)
                    if payload["result"]["status"] != "ready": raise ValueError(payload["result"].get("message", "文献尚未准备好"))
                    record.update(state="ready", snapshot_id=payload["snapshot_id"], error=None)
                except Exception as exc:
                    record.update(state="failed", error=str(exc))
            self._save(path, record)

    def _watch(self, root, run_id, record):
        key = str(Path(root).resolve()) + ":" + run_id
        prior = self.watchers.get(key)
        if prior and prior[0].is_alive(): return
        stop = threading.Event()
        attempt, collection_id = record["attempt"], record["collection_id"]
        path, _, _ = self._path(root, run_id)
        def watch():
            try:
                while not stop.wait(.25):
                    state = self.collections.status(root, collection_id)
                    if state["status"] == "succeeded":
                        self._finish(root, run_id, attempt)
                        return
                    if state["status"] in {"failed_or_partial", "cancelled", "interrupted"}:
                        with self.lock:
                            saved = jobs.read(path, {})
                            if saved.get("attempt") == attempt:
                                saved.update(state="cancelled" if state["status"] == "cancelled" else "failed", error=state.get("error") or "文献任务中断，进度保留，可显式续跑")
                                self._save(path, saved)
                        return
            except Exception as exc:
                with self.lock:
                    saved = jobs.read(path, {})
                    if saved.get("attempt") == attempt:
                        saved.update(state="failed", error=str(exc)); self._save(path, saved)
        thread = threading.Thread(target=watch, name="literature-" + run_id, daemon=True)
        self.watchers[key] = thread, stop
        thread.start()

    def resolve(self, root, run_id, *, enabled=True):
        path, archive, direction = self._path(root, run_id)
        if type(enabled) is not bool: raise ValueError("enabled必须为布尔值")
        if not enabled: return {"run_id": run_id, "state": "disabled", "collection": None, "literature": None}
        with self.lock:
            existing = jobs.read(path)
            if existing is not None: return self.status(root, run_id)
            record = {"run_id": run_id, "direction": direction, "state": "checking_local", "created_at": now(),
                      "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "collection_id": None,
                      "snapshot_id": None, "attempt": uuid4().hex}
            payload = self.query(run_id, root=root)
            result = payload["result"]
            record["keyword"] = result.get("subject")
            if result["status"] == "ready":
                record.update(state="ready", snapshot_id=payload["snapshot_id"])
            elif result["status"] in {"not_found", "not_configured", "needs_extraction"}:
                keyword = service.collection_keyword(run_id, root=root)
                collection = self.collections.start(root, keyword, service.collection_request_id(run_id), local_only=result["status"] == "needs_extraction")
                record.update(state="collecting", collection_id=collection["id"], keyword=keyword)
            else:
                record.update(state="unsupported_input", error=result.get("message"))
            self._save(path, record)
            if record["collection_id"]: self._watch(root, run_id, record)
            return self.status(root, run_id)

    def status(self, root, run_id):
        with self.lock:
            path, archive, _ = self._path(root, run_id)
            record = jobs.read(path)
            if record is None: return {"run_id": run_id, "state": "not_started", "collection": None, "literature": None}
            if record.get("archive_sha256") != hashlib.sha256(archive.read_bytes()).hexdigest():
                raise ValueError("原研究归档已变化，不能沿用文献流程")
            collection = self.collections.status(root, record["collection_id"]) if record.get("collection_id") else None
            if record["state"] == "collecting" and collection:
                if collection["status"] == "succeeded":
                    self._finish(root, run_id, record["attempt"]); record = jobs.read(path)
                elif collection["status"] in {"failed_or_partial", "cancelled", "interrupted"}:
                    record.update(state="cancelled" if collection["status"] == "cancelled" else "failed", error=collection.get("error") or "文献任务中断，进度保留，可显式续跑")
                    self._save(path, record)
            literature = self.query(run_id, root=root, snapshot=record["snapshot_id"]) if record.get("snapshot_id") else None
            return {**record, "collection": collection, "literature": literature}

    def resume(self, root, run_id):
        with self.lock:
            path, _, _ = self._path(root, run_id)
            state = self.status(root, run_id)
            if state["state"] == "ready": return state
            record = jobs.read(path)
            if not record or not record.get("collection_id"): return self.resolve(root, run_id)
            self.collections.resume(root, record["collection_id"])
            record.update(state="collecting", attempt=uuid4().hex, error=None)
            self._save(path, record)
            key = str(Path(root).resolve()) + ":" + run_id
            old = self.watchers.pop(key, None)
            if old: old[1].set()
            self._watch(root, run_id, record)
            return self.status(root, run_id)

    def cancel(self, root, run_id):
        state = self.status(root, run_id)
        if state.get("collection_id"): self.collections.cancel(root, state["collection_id"])
        return self.status(root, run_id)

    def shutdown(self):
        with self.lock:
            values = list(self.watchers.values())
            for _, stop in values: stop.set()
        for thread, _ in values: thread.join(timeout=1)


manager = LiteratureFlowManager()
