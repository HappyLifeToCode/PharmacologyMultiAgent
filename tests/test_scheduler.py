import pytest

from pharm.pipeline import scheduler


def test_topo_order_is_the_fixed_chain():
    assert scheduler.topo_order() == ["preflight", "herb_targets", "disease_reverse", "review"]
    assert scheduler.STAGES == ["preflight", "herb_targets", "disease_reverse", "review"]


def test_execute_graph_runs_in_dependency_order():
    calls = []
    done = scheduler.execute_graph(lambda name: calls.append(name) or name)
    assert calls == ["preflight", "herb_targets", "disease_reverse", "review"]
    assert set(done) == set(scheduler.STAGES)


def test_downstream_runs_after_upstream_terminal_state():
    """blocked/failed 也是终态：依赖到终态即放行，是否继续由阶段自行判断。"""
    calls = []

    def fn(name):
        calls.append(name)
        return "blocked" if name == "herb_targets" else "succeeded"

    done = scheduler.execute_graph(fn)
    assert calls == ["preflight", "herb_targets", "disease_reverse", "review"]
    assert done["herb_targets"] == "blocked" and done["review"] == "succeeded"


def test_enabled_subset_and_unknown_stage():
    assert scheduler.topo_order({"herb_targets", "preflight"}) == ["preflight", "herb_targets"]
    done = scheduler.execute_graph(lambda name: name, enabled={"preflight", "herb_targets"})
    assert set(done) == {"preflight", "herb_targets"}
    with pytest.raises(ValueError, match="未注册"):
        scheduler.topo_order({"venny"})
    with pytest.raises(ValueError, match="未注册"):
        scheduler.execute_graph(lambda name: name, enabled={"preflight", "ghost"})
