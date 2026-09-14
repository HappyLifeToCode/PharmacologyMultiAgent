from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(temporary), str(path))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_name(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("标识只能包含字母、数字、下划线和连字符")
    return value


def task_list():
    # Never load shared examples or the legacy tracked task list automatically.
    path = ROOT / "tasks/tasks.local.jsonl"
    if not path.exists():
        return []
    tasks = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    ids = [safe_name(t["task_id"]) for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("任务清单存在重复 task_id")
    return tasks


def public_artifact(path):
    """Expose curated outputs only, never prompts, raw model logs or browser traces."""
    path = Path(path)
    if any(part.lower() in ("traces", "input_snapshot", "sources") for part in path.parts):
        return False
    name = path.name.lower()
    if name in ("prompt.txt", "response.json", "response.schema.json", "stderr.log", "codex.events.jsonl"):
        return False
    return path.suffix.lower() in (".png", ".csv", ".tsv", ".json", ".txt", ".md")
