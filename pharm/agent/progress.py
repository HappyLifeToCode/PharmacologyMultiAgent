"""Worker-owned progress snapshots, separate from immutable query results."""
from __future__ import annotations

import re
import time
from contextvars import ContextVar
from pathlib import Path

from ..core.common import now, read_json, write_json

_writer = ContextVar("query_progress", default=None)


def path_for(directory):
    directory = Path(directory)
    return directory.parent.parent / "jobs" / (directory.name + ".progress.json")


def bind(path):
    _writer.set({"path": Path(path), "started": time.monotonic(), "last": 0, "phase": None} if path else None)


def report(phase, label, *, current=None, total=None, unit=None, detail=None):
    writer = _writer.get()
    if writer is None:
        return
    tick = time.monotonic()
    if phase == writer["phase"] and tick - writer["last"] < .3 and current != total:
        return
    value = {"phase": phase, "label": label, "updated_at": now(),
             "elapsed_seconds": round(tick - writer["started"], 1)}
    for key, item in (("current", current), ("total", total), ("unit", unit), ("detail", detail)):
        if item is not None:
            value[key] = item
    try:
        write_json(writer["path"], value)
    except OSError:
        # Telemetry availability must not change a scientific calculation.
        return
    writer.update(last=tick, phase=phase)


def snapshot(root, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"(?:forward|reverse)_[A-Za-z0-9_-]{1,64}", run_id):
        return None
    path = Path(root) / "local/agent/jobs" / (run_id + ".progress.json")
    try:
        value = read_json(path)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None
