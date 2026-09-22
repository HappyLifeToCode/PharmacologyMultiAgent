"""Validated, persistent research tasks created by the local workbench."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from ..batman.formulas import formula_herbs, list_formulas

MODES = ("live", "fixture")


def prepare_task(body):
    if not isinstance(body, dict):
        raise ValueError("任务必须是 JSON 对象")
    allowed = {"formula", "herbs", "research_notes", "batman_threshold", "mode"}
    if set(body) - allowed:
        raise ValueError("包含不支持的任务字段")
    formula = body.get("formula")
    if formula is not None:
        if not isinstance(formula, str) or formula not in list_formulas():
            raise ValueError("方剂名称须为内置四方之一：" + "、".join(list_formulas()))
    herbs = body.get("herbs")
    if herbs is None and formula is None:
        raise ValueError("请填写方剂名称或药材清单")
    if herbs is not None:
        if not isinstance(herbs, list) or not 1 <= len(herbs) <= 30:
            raise ValueError("药材需填写 1—30 项")
        output, seen = [], set()
        for value in herbs:
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120 or any(ord(c) < 32 for c in value):
                raise ValueError("药材每项需为 1—120 字的单行文本")
            value = value.strip()
            if value.casefold() not in seen:
                output.append(value)
                seen.add(value.casefold())
        herbs = output
        composition = "herbs_override" if formula else "custom_herbs"
    else:
        herbs = formula_herbs(formula)
        composition = "formula"
    notes = body.get("research_notes", "")
    if not isinstance(notes, str) or len(notes) > 4000 or "\x00" in notes:
        raise ValueError("研究说明最多 4000 字")
    threshold = body.get("batman_threshold", 0.84)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
        raise ValueError("batman_threshold 须为有限数值")
    mode = body.get("mode", "live")
    if mode not in MODES:
        raise ValueError("mode 只支持 live 或 fixture")
    task = {"formula": formula, "herbs": herbs, "research_notes": notes.strip(),
            "batman_threshold": float(threshold), "mode": mode, "composition": composition}
    key = hashlib.sha256(json.dumps(task, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return dict(task_id="web_" + key, **task)


def save_task(body, project_root):
    task = prepare_task(body)
    directory = Path(project_root) / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    path, lock = directory / "tasks.local.jsonl", directory / ".tasks.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("任务清单正在写入，请稍后重试")
    os.close(fd)
    temp = None
    try:
        original = path.read_bytes() if path.exists() else b""
        text = original.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        for line in text.splitlines():
            if not line.strip():
                continue
            current = json.loads(line)
            if current.get("task_id") == task["task_id"]:
                if current != task:
                    raise ValueError("同名任务内容已被修改，请使用不同的研究说明保存新任务")
                return task, False
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=str(directory), delete=False) as f:
            temp = Path(f.name)
            f.write(text + ("\n" if text and not text.endswith("\n") else ""))
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if (path.read_bytes() if path.exists() else b"") != original:
            raise RuntimeError("任务清单刚被修改，请重试以保留最新内容")
        os.replace(str(temp), str(path))
        return task, True
    finally:
        if temp and temp.exists():
            temp.unlink()
        lock.unlink(missing_ok=True)
