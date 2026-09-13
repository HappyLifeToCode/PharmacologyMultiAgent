"""Validated, persistent research tasks created by the local workbench."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .archive import _component


def prepare_task(body):
    if not isinstance(body, dict):
        raise ValueError("任务必须是 JSON 对象")
    allowed = {"formula", "herbs", "diseases", "research_notes", "import_batch"}
    if set(body) - allowed:
        raise ValueError("包含不支持的任务字段")
    formula = body.get("formula")
    if not isinstance(formula, str) or not 1 <= len(formula.strip()) <= 80:
        raise ValueError("请填写方剂或研究名称（最多 80 字）")
    formula = formula.strip()
    try:
        _component(formula)
    except ValueError:
        raise ValueError("研究名称不能包含路径符号或 Windows 保留名称")
    def entries(key, label):
        values = body.get(key)
        if not isinstance(values, list) or not 1 <= len(values) <= 30:
            raise ValueError(label + "需填写 1—30 项")
        output, seen = [], set()
        for value in values:
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120 or any(ord(c) < 32 for c in value):
                raise ValueError(label + "每项需为 1—120 字的单行文本")
            value = value.strip()
            if value.casefold() not in seen:
                output.append(value)
                seen.add(value.casefold())
        return output
    notes = body.get("research_notes", "")
    if not isinstance(notes, str) or len(notes) > 4000 or "\x00" in notes:
        raise ValueError("研究说明最多 4000 字")
    task = {"formula": formula, "herbs": entries("herbs", "药材"), "diseases": entries("diseases", "疾病关键词"),
            "research_notes": notes.strip(), "organism": "Homo sapiens", "taxon_id": 9606,
            "batman_threshold": None, "batman_threshold_confirmed": False,
            "genecards_filter": "relevance_score > median_of_complete_query_results",
            "string_confidence": 0.9, "string_additional_nodes": 0,
            "enrichment_input": "herb_disease_intersection", "enrichment_background": None,
            "enrichment_test_required": "hypergeometric", "multiple_testing": "Benjamini-Hochberg", "fdr_lt": 0.05}
    batch = body.get("import_batch", "")
    if not isinstance(batch, str):
        raise ValueError("导入批次需为文本")
    if batch.strip():
        task["import_batch"] = _component(batch.strip())
    key = hashlib.sha256(json.dumps(task, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return dict(task_id="web_" + key, **task)


def save_task(body, project_root):
    task = prepare_task(body)
    directory = Path(project_root) / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    path, lock = directory / "tasks.jsonl", directory / ".tasks.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("任务清单正在写入，请稍后重试")
    os.close(fd)
    temp = None
    try:
        original = path.read_bytes() if path.exists() else b""
        text = original.decode("utf-8-sig")
        for line in text.splitlines():
            if not line.strip():
                continue
            current = json.loads(line)
            if current.get("task_id") == task["task_id"]:
                if current != task:
                    raise ValueError("同名任务内容已被修改，请使用不同的研究说明保存新任务")
                return task, False
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(directory), delete=False) as f:
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
