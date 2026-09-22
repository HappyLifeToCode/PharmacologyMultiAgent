"""STRING network handoff to Cytoscape and the version-pinned CytoNCA bridge.

No research defaults: computation requires an explicit topology configuration.
Raw responses and a terminal execution record survive partial failures.
"""
from __future__ import annotations

import csv
import os
import uuid
from pathlib import Path

import requests

from ..core.common import now, write_json
from .metrics import analyze_network


class CytoscapeUnavailable(RuntimeError):
    pass


def _csv(path, fields, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class CyREST:
    def __init__(self, url=None):
        self.url = (url or os.environ.get("PHARM_CYREST_URL", "http://127.0.0.1:1234/v1")).rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False  # local software must not go through an HTTP proxy

    def request(self, method, path, **kwargs):
        response = self.session.request(method, self.url + path, timeout=(5, 60), **kwargs)
        response.raise_for_status()
        try:
            value = response.json()
        except ValueError:
            return response.text
        if isinstance(value, dict) and (value.get("errors") or
                isinstance(value.get("data"), dict) and value["data"].get("error")):
            raise RuntimeError("CyREST command failed: " + str(value))
        return value

    def capabilities(self):
        result = {"checked_at": now(), "endpoint": self.url}
        try:
            result["version"] = self.request("GET", "/version")
            result["commands"] = self.request("GET", "/commands")
            result["cytonca_status"] = self.request("POST", "/commands/apps/status", json={"app": "CytoNCA"})
            result["bridge_available"] = "pharmCytoNCA" in str(result["commands"])
            result["status"] = "reachable"
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            result.update(status="unavailable", error=str(exc), bridge_available=False)
        return result


def run_cytoscape(net, directory, topology=None, *, client=None):
    """Import all mapped nodes including isolates, then optionally run CytoNCA DC.

    topology: {metrics: ['Degree'], weighted: false, purpose: 'engineering_smoke'
               or 'research', confirmed: bool}. Missing/unsupported settings import
    the network but do not compute or silently substitute another algorithm.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "cytoscape_execution.json").exists():
        raise ValueError("Cytoscape execution already exists; use a new attempt directory")
    client = client or CyREST()
    checked = analyze_network(net["nodes"], net["edges"])
    execution = {"started_at": now(), "status": "running", "topology": topology,
                 "method": None, "scientific_complete": False}
    write_json(directory / "cytoscape_execution.json", execution)
    write_json(directory / "cytoscape_input.json", net)
    _csv(directory / "network_nodes.csv", ["gene_symbol", "isolated"],
         [{"gene_symbol": r["gene_symbol"], "isolated": r["degree"] == 0} for r in checked["degree_table"]])
    _csv(directory / "network_edges.csv", ["source", "target", "score"], net["edges"])
    try:
        if not net["nodes"]:
            execution.update(status="blocked", limitation="No mapped STRING nodes; nothing submitted to Cytoscape")
            return execution
        capabilities = client.capabilities()
        write_json(directory / "cytoscape_capabilities.json", capabilities)
        if capabilities["status"] != "reachable":
            raise CytoscapeUnavailable("Cytoscape/CyREST unavailable; network input retained")
        payload = {"data": {"name": "Pharmacology-" + uuid.uuid4().hex[:12], "pharm_bridge_input": True},
                   "elements": {"nodes": [{"data": {"id": n, "name": n, "gene_symbol": n}}
                                          for n in net["nodes"]],
                                "edges": []}}
        write_json(directory / "cytoscape_import_request.json", payload)
        imported = client.request("POST", "/networks", json=payload)
        write_json(directory / "cytoscape_import_response.json", imported)
        suid = imported["networkSUID"]
        execution["network_suid"] = suid
        initial = client.request("GET", f"/networks/{suid}")
        node_ids = {n["data"]["gene_symbol"]: int(n["data"]["id"]) for n in initial["elements"]["nodes"]}
        if net["edges"]:
            edge_request = [{"source": node_ids[e["source"]], "target": node_ids[e["target"]],
                             "directed": False, "interaction": "STRING"} for e in net["edges"]]
            write_json(directory / "cytoscape_edges_request.json", edge_request)
            created = client.request("POST", f"/networks/{suid}/edges", json=edge_request)
            write_json(directory / "cytoscape_edges_response.json", created)
            scores = {frozenset((node_ids[e["source"]], node_ids[e["target"]])): e["score"] for e in net["edges"]}
            updates = [{"SUID": e["SUID"], "score": scores[frozenset((e["source"], e["target"]))]} for e in created]
            client.request("PUT", f"/networks/{suid}/tables/defaultedge",
                           json={"key": "SUID", "dataKey": "SUID", "data": updates})
        # Use exact SUIDs, never the user's current network. Retain the created network.
        snapshot = client.request("GET", f"/networks/{suid}")
        write_json(directory / "cytoscape_network_raw.json", snapshot)
        elements = snapshot["elements"]
        rows = [n["data"] for n in elements["nodes"]]
        actual_nodes = [n["gene_symbol"] for n in rows]
        if sorted(actual_nodes) != sorted(net["nodes"]):
            raise ValueError("Cytoscape imported node set differs from STRING input")
        names = {str(n["id"]): n["gene_symbol"] for n in rows}
        actual_edges = [{"source": names[str(e["data"]["source"])],
                         "target": names[str(e["data"]["target"])], "score": e["data"]["score"]}
                        for e in elements["edges"]]
        def edge_keys(edges):
            return sorted((min(e["source"], e["target"]), max(e["source"], e["target"]), e["score"]) for e in edges)
        if edge_keys(actual_edges) != edge_keys(net["edges"]):
            raise ValueError("Cytoscape imported edges or scores differ from STRING input")
        reason = None
        if not topology:
            reason = "Topology metrics and weighting are unconfirmed; network imported without calculation"
        elif topology.get("purpose") not in ("engineering_smoke", "research"):
            reason = "Explicit topology purpose required"
        elif topology.get("purpose") == "research" and topology.get("confirmed") is not True:
            reason = "Research topology settings are not confirmed"
        elif topology.get("metrics") != ["Degree"] or topology.get("weighted") is not False:
            reason = "Bridge currently supports explicit unweighted Degree only"
        elif not capabilities["bridge_available"]:
            reason = "CytoNCA has no native CyREST command; install Pharmacology CytoNCA Bridge"
        elif capabilities["version"].get("cytoscapeVersion") != "3.10.0":
            reason = "This bridge is verified only with Cytoscape 3.10.0"
        if reason:
            execution.update(status="partial", limitation=reason)
            return execution
        raw = client.request("POST", "/commands/pharmCytoNCA/degree", json={"network": str(suid)})
        write_json(directory / "cytonca_command_raw.json", raw)
        data = raw["data"]
        if (data.get("networkSUID") != suid or data.get("cytoncaVersion") != "2.1.6"
                or data.get("nodeCount") != len(net["nodes"])
                or data.get("bridgeVersion") != "0.1.0" or data.get("weighted") is not False
                or data.get("implementation") != "org.cytoscape.CytoNCA.internal.algorithm.DC.run"):
            raise ValueError("Unexpected CytoNCA bridge execution identity")
        table = client.request("GET", f"/networks/{suid}/tables/defaultnode")
        write_json(directory / "cytonca_node_table_raw.json", table)
        degrees = [{"gene_symbol": r["gene_symbol"], "degree": r["CytoNCA_DC"]} for r in table["rows"]]
        degrees.sort(key=lambda r: r["gene_symbol"])
        # NetworkX is an independent check, not the source of the exported topology values.
        if degrees != checked["degree_table"]:
            raise ValueError("CytoNCA Degree differs from independent NetworkX validation")
        _csv(directory / "cytonca_topology.csv", ["gene_symbol", "degree"], degrees)
        write_json(directory / "cytonca_validation.json", {"status": "passed", "reference": "NetworkX degree",
                                                            "node_count": len(degrees), "edge_count": len(actual_edges)})
        execution.update(status="succeeded", method="CytoNCA 2.1.6 DC via compatibility bridge 0.1.0",
                         degree_table=degrees, tools=data,
                         limitation="Degree integration validated; full research method remains unconfirmed")
        return execution
    except CytoscapeUnavailable as exc:
        execution.update(status="blocked", limitation=str(exc))
        return execution
    except Exception as exc:
        execution.update(status="failed", error=str(exc))
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            write_json(directory / "cytoscape_error_response.json",
                       {"status_code": exc.response.status_code, "body": exc.response.text})
        raise
    finally:
        execution["finished_at"] = now()
        write_json(directory / "cytoscape_execution.json", execution)
