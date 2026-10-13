"""主流水线使用的疾病数据采集编排。

Open Targets 在填写疾病范围时执行疾病→基因，
留空时执行 BATMAN 靶点→可能疾病；本地快照与 API 查询均写入本次
运行专用的疾病索引，不覆盖原索引。
"""
from __future__ import annotations

import os
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..core.common import now, write_json
from ..discovery import query as discovery
from .open_targets import collect_open_targets_by_diseases, collect_open_targets_by_targets

SUPPORTED_SOURCES = ("open_targets",)


def _shared_index_metadata(database):
    """Return metadata only for a production reusable Open Targets index."""
    if not database or not Path(database).is_file():
        return None
    try:
        path = Path(database).resolve()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT value FROM metadata LIMIT 1").fetchone()
        metadata = json.loads(row[0]) if row else None
    except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError):
        return None
    if (isinstance(metadata, dict) and metadata.get("shared_index") is True and
            metadata.get("import_kind") == "open_targets_batman_known_shared"):
        return metadata
    return None


def collect_and_extend(task, output_dir, database=None, herb_targets=None):
    """采集任务声明的疾病并生成本次运行专用索引。

    ``database`` 有值时复制并追加；没有本地疾病索引时，使用本地
    BATMAN 阶段产物和在线疾病记录首次建库。
    """
    diseases = list(task.get("online_diseases") or [])
    sources = task.get("online_sources", list(SUPPORTED_SOURCES))
    if not isinstance(sources, list) or not sources:
        raise ValueError("online_sources 必须为非空列表，目前只支持 open_targets")
    if any(source not in SUPPORTED_SOURCES for source in sources):
        raise ValueError("任务使用了已移除或不支持的数据源；请保存仅使用 open_targets 的新任务")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # 靶点驱动的生产主流程优先复用按 Open Targets 版本构建的 BATMAN
    # known-only 共享索引。疾病限定查询可能需要索引外靶点，仍走采集分支。
    shared = _shared_index_metadata(database) if not diseases else None
    if shared:
        collection = {
            "started_at": now(), "finished_at": now(), "diseases": [],
            "sources": sources, "records": [], "database": str(Path(database).resolve()),
            "mode": "shared_index", "reused": True,
            "release": shared.get("release"),
            "selection": shared.get("selection"),
            "known_symbol_count": shared.get("known_symbol_count"),
            "resolved_symbol_count": shared.get("resolved_symbol_count"),
            "counts": {"open_targets": int(shared.get("source_rows", {}).get("open_targets", 0)),
                       "duplicates": 0},
        }
        write_json(output_dir / "online_collection.json", collection)
        return {"database": collection["database"], "collection": collection,
                "counts": collection["counts"], "reused": True,
                "artifacts": ["online_collection.json"]}
    additions = {}
    records = []
    if "open_targets" in sources:
        ot_dir = output_dir / "open_targets"
        # 完整层默认不做 top_k/min_score 过滤（筛选只发生在候选分层）；
        # 环境变量仅用于显式试验，启用后批次 provenance 标记 complete=false
        top_k_raw = os.environ.get("PHARM_OPEN_TARGETS_TOP_K", "").strip()
        min_score_raw = os.environ.get("PHARM_OPEN_TARGETS_MIN_SCORE", "").strip()
        try:
            top_k = int(top_k_raw) if top_k_raw else None
        except ValueError as exc:
            raise ValueError("PHARM_OPEN_TARGETS_TOP_K 必须是正整数") from exc
        if top_k is not None and top_k < 1:
            raise ValueError("PHARM_OPEN_TARGETS_TOP_K 必须是正整数")
        try:
            min_score = float(min_score_raw) if min_score_raw else None
        except ValueError as exc:
            raise ValueError("PHARM_OPEN_TARGETS_MIN_SCORE 必须是 0—1 数值") from exc
        page_size = min(1000, max(1, int(os.environ.get("PHARM_OPEN_TARGETS_PAGE_SIZE", "1000"))))
        request_delay = max(0.0, float(os.environ.get("PHARM_OPEN_TARGETS_REQUEST_DELAY", "0.05")))
        if diseases:
            meta = collect_open_targets_by_diseases(
                diseases, ot_dir, page_size=page_size, request_delay=request_delay,
                top_k_per_disease=top_k, min_score=min_score)
        else:
            if not herb_targets or not herb_targets.get("genes"):
                raise RuntimeError("Open Targets 靶点驱动模式需要 BATMAN 阶段产出的基因靶点")
            meta = collect_open_targets_by_targets(
                herb_targets["genes"], ot_dir, page_size=page_size,
                request_delay=request_delay, top_k_per_target=top_k,
                min_score=min_score)
        additions["open_targets"] = ot_dir / "associations.csv"
        records.append({"source": "open_targets", "directory": str(ot_dir), "meta": meta})

    collection = {"started_at": now(), "diseases": diseases, "sources": sources,
                  "records": records}
    if database:
        result = discovery.extend_database(database, additions, output_dir / "disease_index.sqlite", collection)
    else:
        if not herb_targets:
            raise ValueError("没有本地疾病索引时，必须提供 BATMAN 靶点阶段产物")
        result = discovery.build_online_database(
            additions, output_dir / "disease_index.sqlite",
            herb_targets.get("relations", []), herb_targets.get("herbs", []), collection)
    collection["finished_at"] = now()
    collection["database"] = result["database"]
    collection["counts"] = result["counts"]
    write_json(output_dir / "online_collection.json", collection)
    return {"database": result["database"], "collection": collection, "counts": result["counts"],
            "reused": False,
            "artifacts": ["online_collection.json", "disease_index.sqlite"]}
