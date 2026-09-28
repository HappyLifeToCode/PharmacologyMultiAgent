"""主流水线使用的在线疾病数据采集编排。

在线采集只在任务显式启用时执行。当前采集器支持限定疾病关键词测试；正式的
靶点驱动疾病枚举仍需接入 GeneCards/OMIM 的合规基因→疾病接口或疾病目录。
采集器遇到登录/验证码会通过工作台协助，完成后把结果写入本次运行专用的疾病索引，不覆盖原索引。
"""
from __future__ import annotations

import os
from pathlib import Path

from ..core.common import now, write_json
from ..discovery import query as discovery
from .genecards_online import collect_online as collect_genecards
from .omim_export import combine_gene_map_exports
from .omim_online import collect_online as collect_omim

SUPPORTED_SOURCES = ("genecards", "omim")


def collect_and_extend(task, output_dir, database=None, herb_targets=None):
    """采集任务声明的疾病并生成本次运行专用索引。

    ``database`` 有值时复制并追加；没有本地疾病索引时，使用本地
    BATMAN 阶段产物和在线疾病记录首次建库。
    """
    diseases = list(task.get("online_diseases") or [])
    sources = list(task.get("online_sources") or SUPPORTED_SOURCES)
    if not diseases:
        raise RuntimeError(
            "当前 GeneCards/OMIM 页面采集器只支持按疾病关键词读取；"
            "目标驱动的基因→疾病接口尚未具备可核验的官方批量入口，"
            "不能用空关键词假装完成全疾病反查。请先导入合规疾病目录，"
            "或使用限定范围测试模式。")
    if any(source not in SUPPORTED_SOURCES for source in sources):
        raise ValueError("online_sources 只支持 genecards、omim")
    if not sources:
        raise ValueError("online_sources 不能为空")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assist_url = os.environ.get("PHARM_ASSIST_URL", "http://127.0.0.1:8766")
    additions = {}
    records = []
    if "genecards" in sources:
        gc_dir = output_dir / "genecards"
        meta = collect_genecards(diseases, gc_dir, headless=False, assist_base_url=assist_url)
        additions["genecards"] = gc_dir / "genecards.csv"
        records.append({"source": "genecards", "directory": str(gc_dir), "meta": meta})
    if "omim" in sources:
        omim_dir = output_dir / "omim"
        meta = collect_omim(diseases, omim_dir, headless=False, assist_base_url=assist_url)
        exports = sorted(omim_dir.glob("raw/*_genemap.xlsx"))
        if not exports:
            raise RuntimeError("OMIM 在线采集未产生 Gene Map xlsx，无法导入疾病索引")
        omim_csv = omim_dir / "omim.csv"
        combine_gene_map_exports(exports, omim_csv)
        additions["omim"] = omim_csv
        records.append({"source": "omim", "directory": str(omim_dir), "meta": meta,
                        "exports": [str(path) for path in exports]})

    collection = {"started_at": now(), "diseases": diseases, "sources": sources,
                  "assist_url": assist_url, "records": records}
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
