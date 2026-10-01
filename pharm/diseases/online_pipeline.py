"""主流水线使用的疾病数据采集编排。

Open Targets 在填写疾病范围时执行疾病→基因，
留空时执行 BATMAN 靶点→可能疾病；本地快照与 API 查询均写入本次
运行专用的疾病索引，不覆盖原索引。
"""
from __future__ import annotations

import os
from pathlib import Path

from ..core.common import now, write_json
from ..discovery import query as discovery
from .open_targets import collect_open_targets_by_diseases, collect_open_targets_by_targets

SUPPORTED_SOURCES = ("open_targets",)


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
    additions = {}
    records = []
    if "open_targets" in sources:
        ot_dir = output_dir / "open_targets"
        try:
            top_k = int(os.environ.get("PHARM_OPEN_TARGETS_TOP_K", "100"))
        except ValueError as exc:
            raise ValueError("PHARM_OPEN_TARGETS_TOP_K 必须是正整数") from exc
        try:
            min_score = float(os.environ.get("PHARM_OPEN_TARGETS_MIN_SCORE", "0.2"))
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
            "artifacts": ["online_collection.json", "disease_index.sqlite"]}
