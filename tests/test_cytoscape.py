from copy import deepcopy

import pytest

from pharm_demo.common import read_json
from pharm_demo.cytoscape import run_cytoscape


NET = {"nodes": ["TP53", "MDM2", "ALB"],
       "edges": [{"source": "TP53", "target": "MDM2", "score": .95}]}
CONFIG = {"metrics": ["Degree"], "weighted": False, "purpose": "engineering_smoke"}


class FakeCyREST:
    def __init__(self, *, bridge=True, reachable=True, wrong_degree=False, wrong_edge=False):
        self.bridge, self.reachable = bridge, reachable
        self.wrong_degree, self.wrong_edge = wrong_degree, wrong_edge
        self.calls = []
        self.edges = []

    def capabilities(self):
        return {"status": "reachable" if self.reachable else "unavailable", "bridge_available": self.bridge,
                "version": {"cytoscapeVersion": "3.10.0"}}

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/networks":
            assert kwargs["json"]["elements"]["edges"] == []
            self.nodes = [{"data": {"id": str(i+10), **n["data"]}} for i,n in enumerate(kwargs["json"]["elements"]["nodes"])]
            for i, n in enumerate(self.nodes):
                n["data"]["id"] = str(i + 10)
            return {"networkSUID": 42}
        if path == "/networks/42/edges":
            assert all(e["directed"] is False for e in kwargs["json"])
            self.edges = [{"data": dict(e, SUID=100+i)} for i,e in enumerate(kwargs["json"])]
            return [e["data"] for e in self.edges]
        if path == "/networks/42/tables/defaultedge":
            for edge, update in zip(self.edges, kwargs["json"]["data"]):
                edge["data"]["score"] = update["score"] if not self.wrong_edge else .5
            return ""
        if path == "/networks/42":
            return deepcopy({"elements": {"nodes": self.nodes, "edges": self.edges}})
        if path == "/commands/pharmCytoNCA/degree":
            assert kwargs["json"] == {"network": "42"}
            return {"data": {"networkSUID": 42, "nodeCount": len(self.nodes), "cytoncaVersion": "2.1.6",
                             "bridgeVersion": "0.1.0", "weighted": False,
                             "implementation": "org.cytoscape.CytoNCA.internal.algorithm.DC.run"}}
        if path == "/networks/42/tables/defaultnode":
            return {"rows": [{"gene_symbol": "TP53", "CytoNCA_DC": 1.0},
                             {"gene_symbol": "MDM2", "CytoNCA_DC": 1.0},
                             {"gene_symbol": "ALB", "CytoNCA_DC": 1.0 if self.wrong_degree else 0.0}]}
        raise AssertionError((method, path))


def test_degree_preserves_isolate_and_validates_actual_table(tmp_path):
    result = run_cytoscape(NET, tmp_path, CONFIG, client=FakeCyREST())
    assert result["status"] == "succeeded"
    assert result["degree_table"][0] == {"gene_symbol": "ALB", "degree": 0.0}
    assert result["scientific_complete"] is False
    assert read_json(tmp_path / "cytonca_validation.json")["status"] == "passed"
    assert (tmp_path / "cytonca_topology.csv").exists()


@pytest.mark.parametrize("config", [None, {}, {**CONFIG, "weighted": True},
                                    {**CONFIG, "metrics": ["Degree", "BC"]},
                                    {**CONFIG, "purpose": "research", "confirmed": False}])
def test_unconfirmed_or_unsupported_imports_without_computing(tmp_path, config):
    client = FakeCyREST()
    assert run_cytoscape(NET, tmp_path, config, client=client)["status"] == "partial"
    assert not any("/degree" in c[1] for c in client.calls)
    assert not (tmp_path / "cytonca_topology.csv").exists()


@pytest.mark.parametrize("options,status", [({"bridge": False}, "partial"), ({"reachable": False}, "blocked")])
def test_capability_gaps_are_not_fake_success(tmp_path, options, status):
    result = run_cytoscape(NET, tmp_path, CONFIG, client=FakeCyREST(**options))
    assert result["status"] == status
    assert (tmp_path / "network_nodes.csv").exists()
    assert read_json(tmp_path / "cytoscape_execution.json")["finished_at"]


@pytest.mark.parametrize("options", [{"wrong_degree": True}, {"wrong_edge": True}])
def test_corrupted_results_fail_with_evidence(tmp_path, options):
    with pytest.raises(ValueError):
        run_cytoscape(NET, tmp_path, CONFIG, client=FakeCyREST(**options))
    assert read_json(tmp_path / "cytoscape_execution.json")["status"] == "failed"
    assert not (tmp_path / "cytonca_topology.csv").exists()


def test_unmapped_only_skips_external_calls(tmp_path):
    client = FakeCyREST()
    result = run_cytoscape({"nodes": [], "edges": []}, tmp_path, CONFIG, client=client)
    assert result["status"] == "blocked"
    assert client.calls == []


def test_retry_cannot_reuse_stale_topology(tmp_path):
    run_cytoscape(NET, tmp_path, CONFIG, client=FakeCyREST())
    before = (tmp_path / "cytoscape_execution.json").read_bytes()
    with pytest.raises(ValueError, match="new attempt"):
        run_cytoscape(NET, tmp_path, None, client=FakeCyREST())
    assert (tmp_path / "cytoscape_execution.json").read_bytes() == before
