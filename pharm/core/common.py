from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE_STATE = Path("local/workspace.json")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows 上杀毒/索引可能短暂占用目标文件，导致 os.replace 被拒绝；稍作重试
    for attempt in range(10):
        try:
            os.replace(str(temporary), str(path))
            return
        except PermissionError:
            if attempt == 9:
                raise
            import time
            time.sleep(0.05 * (attempt + 1))


def workspace_identity(root=None):
    """Return the machine-local workspace identity used to isolate run data.

    Runs created before this marker exists (or in another checkout) deliberately
    remain on disk but are not loaded into the workbench. This prevents stale
    test runs and another user's results from being presented as current work.
    """
    root = Path(root or ROOT)
    path = root / WORKSPACE_STATE
    if path.is_file():
        value = read_json(path)
        identity = value.get("workspace_id") if isinstance(value, dict) else None
        if isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{32}", identity):
            return value
        raise ValueError("本地工作区标识文件无效，请检查 local/workspace.json")
    value = {"workspace_id": uuid.uuid4().hex, "created_at": now(),
             "policy": "仅展示本工作区创建的运行"}
    write_json(path, value)
    return value


def owned_run(manifest, root=None):
    """Whether a run belongs to the current local workbench."""
    if not isinstance(manifest, dict):
        return False
    return manifest.get("workspace_id") == workspace_identity(root)["workspace_id"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_name(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("标识只能包含字母、数字、下划线和连字符")
    return value


def task_list():
    # Never load shared examples, legacy tracked tasks, or tasks from another
    # local workbench. They remain on disk for audit but are not selectable.
    path = ROOT / "tasks/tasks.local.jsonl"
    if not path.exists():
        return []
    workspace_id = workspace_identity(ROOT)["workspace_id"]
    tasks = [task for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()
             for task in [json.loads(line)] if task.get("workspace_id") == workspace_id]
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
