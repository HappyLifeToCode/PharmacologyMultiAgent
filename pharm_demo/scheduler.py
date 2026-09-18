"""Dependency-graph scheduling for stage execution.

Model proposals are advisory only: nodes and edges come from this registry,
unknown stages void the whole proposal, and disabling cascades downstream.
"""
from __future__ import annotations

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

GRAPH_V2 = {
    "coordinator_plan": [],
    "herb_targets": ["coordinator_plan"],
    "genecards_targets": ["coordinator_plan"],
    "omim_targets": ["coordinator_plan"],
    "disease_targets": ["genecards_targets", "omim_targets"],
    "intersection": ["herb_targets", "disease_targets"],
    "network_analysis": ["intersection"],
    "enrichment_analysis": ["intersection"],
    "coordinator_review": ["network_analysis", "enrichment_analysis"],
}

GRAPH_LEGACY = {
    "coordinator_plan": [],
    "herb_targets": ["coordinator_plan"],
    "disease_targets": ["coordinator_plan"],
    "intersection": ["herb_targets", "disease_targets"],
    "network_analysis": ["intersection"],
    "enrichment_analysis": ["intersection"],
    "coordinator_review": ["network_analysis", "enrichment_analysis"],
}


def graph_for(workflow_version):
    return GRAPH_V2 if workflow_version >= 2 else GRAPH_LEGACY


def dependents_closure(graph, stage):
    """All stages that transitively depend on `stage` (excluding itself)."""
    result, frontier = set(), [stage]
    while frontier:
        current = frontier.pop()
        for name, deps in graph.items():
            if current in deps and name not in result:
                result.add(name)
                frontier.append(name)
    return result


def validate_graph(proposal, workflow_version=2):
    """Validate a coordinator plan proposal; return (enabled_set, findings).

    Only enable/disable is accepted. Unknown stages void the entire proposal.
    Disabling a stage cascades to its dependents. coordinator_plan cannot be disabled.
    """
    graph = graph_for(workflow_version)
    enabled = set(graph)
    findings = []
    if proposal is None:
        return enabled, findings
    if not isinstance(proposal, dict) or not isinstance(proposal.get("enabled"), list):
        findings.append("任务图建议格式无法识别，已回退默认图")
        return enabled, findings
    names = proposal["enabled"]
    unknown = [n for n in names if not isinstance(n, str) or n not in graph]
    if unknown:
        findings.append("任务图建议包含未知阶段 %s，整案回退默认图" % unknown)
        return enabled, findings
    requested = set(names)
    if "coordinator_plan" not in requested:
        findings.append("coordinator_plan 不可停用，已保留")
        requested.add("coordinator_plan")
    disabled = set(graph) - requested
    for stage in sorted(disabled):
        for dependent in sorted(dependents_closure(graph, stage)):
            if dependent not in disabled:
                disabled.add(dependent)
                findings.append("停用 %s 连带停用下游 %s" % (stage, dependent))
    enabled -= disabled
    if disabled:
        findings.append("协调规划停用阶段：" + ", ".join(sorted(disabled)))
    return enabled, findings


def topo_order(workflow_version, enabled=None):
    graph = graph_for(workflow_version)
    enabled = set(graph) if enabled is None else set(enabled)
    order, placed = [], set()
    while len(placed) < len(enabled):
        ready = [n for n in graph if n in enabled and n not in placed
                 and all(d in placed or d not in enabled for d in graph[n])]
        if not ready:
            raise RuntimeError("任务图存在环")
        order.extend(sorted(ready))
        placed.update(ready)
    return order


def execute_graph(enabled, workflow_version, stage_fn, max_workers=2):
    """Run enabled stages in dependency order with bounded parallelism.

    A stage runs once all its enabled dependencies reached a terminal state,
    regardless of that state; blocked/failed semantics stay inside each stage.
    Returns {stage: stage_fn result}.
    """
    graph = {n: [d for d in deps if d in enabled]
             for n, deps in graph_for(workflow_version).items() if n in enabled}
    done, pending, futures = {}, set(graph), {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while pending or futures:
            for name in sorted(n for n in pending if all(d in done for d in graph[n])):
                futures[pool.submit(stage_fn, name)] = name
                pending.discard(name)
            if not futures:
                raise RuntimeError("任务图存在环或无可用阶段")
            finished = next(concurrent.futures.as_completed(futures))
            done[futures.pop(finished)] = finished.result()
    return done
