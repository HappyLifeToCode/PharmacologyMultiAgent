"""DAG 调度器：discovery 与 analysis 两条流水线共用最小执行器。

节点与依赖固定在注册表内；阶段在其全部启用依赖到达终态后放行，
blocked/failed 也是终态，是否继续由各阶段自行判断。
"""
from __future__ import annotations

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

GRAPHS = {
    "discovery": {
        "preflight": [],
        "herb_targets": ["preflight"],
        "disease_reverse": ["herb_targets"],
        "review": ["disease_reverse"],
    },
    "analysis": {
        "shared_targets": [],
        "network": ["shared_targets"],
        "enrichment": ["shared_targets"],
        "analysis_review": ["network", "enrichment"],
    },
}
DEFAULT_PIPELINE = "discovery"
# 兼容既有引用：discovery 流水线的阶段顺序
STAGES = list(GRAPHS[DEFAULT_PIPELINE])


def stages_for(pipeline):
    if pipeline not in GRAPHS:
        raise ValueError("未注册的流水线：" + str(pipeline))
    return list(GRAPHS[pipeline])


def topo_order(enabled=None, pipeline=DEFAULT_PIPELINE):
    graph = GRAPHS.get(pipeline)
    if graph is None:
        raise ValueError("未注册的流水线：" + str(pipeline))
    enabled = set(graph) if enabled is None else set(enabled)
    unknown = enabled - set(graph)
    if unknown:
        raise ValueError("未注册的阶段：" + ", ".join(sorted(unknown)))
    order, placed = [], set()
    while len(placed) < len(enabled):
        ready = [n for n in graph if n in enabled and n not in placed
                 and all(d in placed or d not in enabled for d in graph[n])]
        if not ready:
            raise RuntimeError("任务图存在环")
        order.extend(ready)  # 注册表声明序
        placed.update(ready)
    return order


def execute_graph(stage_fn, enabled=None, max_workers=2, pipeline=DEFAULT_PIPELINE):
    """按依赖序执行启用阶段；依赖到达终态即放行。返回 {阶段: stage_fn 返回值}。"""
    registry = GRAPHS.get(pipeline)
    if registry is None:
        raise ValueError("未注册的流水线：" + str(pipeline))
    enabled = set(registry) if enabled is None else set(enabled)
    graph = {n: [d for d in deps if d in enabled]
             for n, deps in registry.items() if n in enabled}
    if len(graph) != len(enabled):
        raise ValueError("未注册的阶段：" + ", ".join(sorted(enabled - set(graph))))
    done, pending, futures = {}, set(graph), {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while pending or futures:
            for name in [n for n in graph if n in pending and all(d in done for d in graph[n])]:
                futures[pool.submit(stage_fn, name)] = name
                pending.discard(name)
            if not futures:
                raise RuntimeError("任务图存在环或无可用阶段")
            finished = next(concurrent.futures.as_completed(futures))
            done[futures.pop(finished)] = finished.result()
    return done
