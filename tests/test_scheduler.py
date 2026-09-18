import threading

import pytest

from pharm_demo.scheduler import (GRAPH_V2, dependents_closure, execute_graph,
                                  topo_order, validate_graph)


def test_validate_defaults_and_format_fallback():
    enabled, findings = validate_graph(None)
    assert enabled == set(GRAPH_V2) and findings == []
    enabled, findings = validate_graph({"bogus": []})
    assert enabled == set(GRAPH_V2) and "回退" in findings[0]


def test_validate_unknown_stage_voids_proposal():
    enabled, findings = validate_graph({"enabled": ["herb_targets", "made_up_stage"]})
    assert enabled == set(GRAPH_V2)
    assert any("未知阶段" in f and "整案回退" in f for f in findings)


def test_validate_disable_cascades_downstream():
    enabled, findings = validate_graph({"enabled": sorted(set(GRAPH_V2) - {"intersection"})})
    assert "intersection" not in enabled
    assert {"network_analysis", "enrichment_analysis", "coordinator_review"}.isdisjoint(enabled)
    assert any("连带停用下游 network_analysis" in f for f in findings)


def test_validate_keeps_coordinator_plan():
    enabled, findings = validate_graph({"enabled": ["herb_targets"]})
    assert "coordinator_plan" in enabled
    assert any("不可停用" in f for f in findings)
    # 其余阶段及其下游均被停用
    assert "coordinator_review" not in enabled


def test_dependents_closure_and_topo_order():
    assert dependents_closure(GRAPH_V2, "intersection") == {"network_analysis", "enrichment_analysis",
                                                          "coordinator_review"}
    order = topo_order(2)
    assert order.index("genecards_targets") < order.index("disease_targets")
    assert order.index("intersection") < order.index("network_analysis")
    assert order.index("coordinator_review") == len(order) - 1


def test_execute_graph_runs_parallel_and_after_blocked_dependency():
    calls, started = [], []
    barrier = threading.Barrier(2)

    def stage_fn(name):
        started.append(name)
        if name in ("genecards_targets", "omim_targets"):
            barrier.wait(timeout=10)  # 两支必须并行，否则超时
        calls.append(name)
        return name

    done = execute_graph(set(GRAPH_V2), 2, stage_fn, max_workers=2)
    assert set(done) == set(GRAPH_V2)
    assert calls.index("disease_targets") > calls.index("omim_targets")
    assert calls.index("coordinator_review") == len(calls) - 1


def test_execute_graph_respects_disabled_set():
    enabled, _ = validate_graph({"enabled": ["coordinator_plan", "herb_targets",
                                             "genecards_targets", "omim_targets",
                                             "disease_targets"]})
    calls = []

    def stage_fn(name):
        calls.append(name)

    execute_graph(enabled, 2, stage_fn)
    assert "intersection" not in calls and "coordinator_review" not in calls
