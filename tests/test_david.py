"""Synthetic transport fixtures; these tests do not call or emulate DAVID statistics."""
from copy import deepcopy

import pytest
import requests

from pharm.core.common import read_json, write_json
from pharm.enrich.david import DavidBlocked, DavidClient, annotation_ids, chart_rows, conversion_report, mapping_report, run_david, validate_config

GENES = ["TP53", "MDM2", "ZZZPHARMSMOKETEST"]
CATEGORIES = ["GOTERM_BP_DIRECT", "GOTERM_CC_DIRECT", "GOTERM_MF_DIRECT", "KEGG_PATHWAY"]
CONFIG = {"purpose": "engineering_smoke", "test": "EASE", "correction": "Benjamini", "fdr_lt": .05,
          "categories": CATEGORIES, "background": {"mode": "species", "name": "Homo sapiens", "rationale": "test only"}}
TASK = {"taxon_id": 9606, "david_enrichment": CONFIG}
CURRENT = {"columns": ["ID Type", "User ID", "User Value", "DAVID ID", "DAVID Gene Name", "Taxonomy ID"],
           "taxon_id": 9606, "list_name": "pharm_query", "unmapped_user_ids": [["ZZZPHARMSMOKETEST"]],
           "data": [["OFFICIAL_GENE_SYMBOL", "TP53", "", 7157, "test TP53", 9606],
                    ["OFFICIAL_GENE_SYMBOL", "MDM2", "", 4193, "test MDM2", 9606]]}
CHART = [{"categoryName": c, "termNameRaw": "TEST:term", "LH": 2, "LT": 2, "PH": 5, "PT": 100,
          "ease": .01, "benjamini": .04, "fisher": .001, "bonferroni": .1, "fdr": .5,
          "foldEnrichment": 20.0, "geneIds": "7157,4193", "userIds": "TP53,MDM2"} for c in CATEGORIES]


class FakeDavid:
    def __init__(self, directory, *, rows=None, wrong_background=False, offline=False):
        self.directory = directory
        self.rows = deepcopy(CHART if rows is None else rows)
        self.calls = []
        self.wrong_background, self.offline = wrong_background, offline
        self.bg = "Homo sapiens"

    def request(self, method, path, name, **kwargs):
        self.calls.append((method, path, name, kwargs))
        if path == "/": return "DAVID Knowledgebase v2026_1"
        if path == "/getAnnotationSummary":
            return [{"summaryRecords": [{"category": c, "categoryId": i, "count": 2} for i,c in enumerate(CATEGORIES, 10)]}]
        if path == "/getAnnotationChart":
            assert kwargs["params"] == {"annot": "10,11,12,13", "ease": 1, "count": 1}
            if self.offline: raise DavidBlocked("DAVID service unavailable")
            write_json(self.directory / "david_chart_raw.json", self.rows)
            return self.rows
        raise AssertionError(path)

    def add_list(self, genes, taxon, name, kind):
        self.calls.append(("add", name, genes, kind))
        if kind == 1:
            self.bg = "pharm_background"
            return {"data": [["OFFICIAL_GENE_SYMBOL", "TP53", 7157, "test TP53", ""],
                             ["OFFICIAL_GENE_SYMBOL", "MDM2", 4193, "test MDM2", ""]], "unmapped_user_ids": ["ZZZPHARMSMOKETEST"]}
        return {"data": [[r[0], r[1], r[3], r[4], r[2]] for r in CURRENT["data"]],
                "unmapped_user_ids": ["ZZZPHARMSMOKETEST"]}

    def manager(self, action, name, **kwargs):
        self.calls.append(("manager", action, name, kwargs))
        if action == "getCurrentList": return deepcopy(CURRENT)
        if action == "getCurrentListDIDCount": return {"count": 2}
        if action == "getAllPopulationNames": return [self.bg]
        if action == "setCurrentPopulation":
            assert kwargs["position"] == 0
            return {"message": "Current population set successfully."}
        if action == "getCurrentPopulationName": return "wrong" if self.wrong_background else self.bg
        raise AssertionError(action)


def test_real_workflow_contract_uses_benjamini_not_other_fdr(tmp_path):
    client = FakeDavid(tmp_path)
    result = run_david(GENES, TASK, tmp_path, client=client)
    assert result["status"] == "succeeded"
    assert result["significant_count"] == 4  # the separate fdr=.5 field must not drive this
    assert result["unmapped"] == ["ZZZPHARMSMOKETEST"]
    assert result["scientific_complete"] is False
    assert read_json(tmp_path / "david_execution.json")["finished_at"]
    assert "david_chart_raw.json" in read_json(tmp_path / "david_artifacts.json")["sha256"]
    assert "KEGG_PATHWAY" in (tmp_path / "KEGG_PATHWAY_all.csv").read_text()


@pytest.mark.parametrize("rows", [[], [{**r, "benjamini": .5} for r in CHART]])
def test_zero_significant_results_are_valid_with_header_exports(tmp_path, rows):
    result = run_david(GENES, TASK, tmp_path, client=FakeDavid(tmp_path, rows=rows))
    assert result["status"] == "succeeded"
    assert result["significant_count"] == 0
    assert len((tmp_path / "david_significant_terms.csv").read_text().splitlines()) == 1


@pytest.mark.parametrize("config", [None, {**CONFIG, "purpose": "research"}, {**CONFIG, "test": "hypergeometric"},
                                    {**CONFIG, "background": None}, {**CONFIG, "fdr_lt": float("nan")},
                                    {**CONFIG, "categories": ["KEGG_PATHWAY"]}])
def test_missing_or_unconfirmed_parameters_never_submit(tmp_path, config):
    client = FakeDavid(tmp_path)
    result = run_david(GENES, {**TASK, "david_enrichment": config}, tmp_path, client=client)
    assert result["status"] == "blocked"
    assert not result["submitted"]
    assert client.calls == []


def test_research_method_conflict_is_not_silently_relabelled():
    with pytest.raises(DavidBlocked, match="不一致"):
        validate_config(GENES, {**TASK, "enrichment_test_required": "hypergeometric",
                               "david_enrichment": {**CONFIG, "purpose": "research", "confirmed": True}})


def test_custom_background_is_selected_without_changing_foreground(tmp_path):
    task = {**TASK, "david_enrichment": {**CONFIG, "background": {"mode": "custom", "genes": GENES, "rationale": "test only"}}}
    client = FakeDavid(tmp_path, rows=[{**r, "PH": 2, "PT": 2} for r in CHART])
    result = run_david(GENES, task, tmp_path, client=client)
    assert result["background"]["actual_name"] == "pharm_background"
    assert result["background"]["mapped_david_ids"] == [4193, 7157]
    assert sum(c[0:2] == ("manager", "getCurrentList") for c in client.calls) == 2


def test_background_conversion_cannot_silently_drop_inputs():
    with pytest.raises(ValueError, match="account for all"):
        conversion_report({"data": [], "unmapped_user_ids": []}, GENES)


def test_all_unmapped_retains_explicit_report_without_chart(tmp_path):
    client = FakeDavid(tmp_path)
    client.add_list = lambda *args: {"data": [], "unmapped_user_ids": GENES}
    result = run_david(GENES, TASK, tmp_path, client=client)
    assert result["status"] == "blocked"
    assert read_json(tmp_path / "david_identification.json")["david_id_count"] == 0
    assert not any(c[1] == "/getAnnotationChart" for c in client.calls)


def test_wrong_background_is_a_failure_with_terminal_evidence(tmp_path):
    with pytest.raises(ValueError, match="background differs"):
        run_david(GENES, TASK, tmp_path, client=FakeDavid(tmp_path, wrong_background=True))
    assert read_json(tmp_path / "david_execution.json")["status"] == "failed"
    assert not (tmp_path / "david_all_terms.csv").exists()


def test_late_outage_preserves_identification_and_partial_status(tmp_path):
    result = run_david(GENES, TASK, tmp_path, client=FakeDavid(tmp_path, offline=True))
    assert result["status"] == "partial"
    assert (tmp_path / "david_mapping.csv").exists()
    assert not (tmp_path / "enrichment_david.json").exists()


def test_retry_preserves_old_evidence(tmp_path):
    run_david(GENES, TASK, tmp_path, client=FakeDavid(tmp_path))
    before = (tmp_path / "david_execution.json").read_bytes()
    with pytest.raises(ValueError, match="new directory"):
        run_david(GENES, TASK, tmp_path, client=FakeDavid(tmp_path))
    assert (tmp_path / "david_execution.json").read_bytes() == before


@pytest.mark.parametrize("mutation", [lambda x: x["data"][0].__setitem__(5, 10090),
                                     lambda x: x.__setitem__("unmapped_user_ids", []),
                                     lambda x: x["data"][0].__setitem__(1, "EGFR")])
def test_mapping_rejects_taxon_input_and_accounting_errors(mutation):
    raw = deepcopy(CURRENT)
    mutation(raw)
    with pytest.raises(ValueError): mapping_report(raw, GENES, 9606)


@pytest.mark.parametrize("patch", [{"benjamini": "NaN"}, {"LH": 3}, {"geneIds": "99999,7157"},
                                  {"categoryName": "OTHER"}, {"PT": 1}])
def test_chart_rejects_corrupt_statistics_or_identifiers(patch):
    with pytest.raises(ValueError):
        chart_rows([{**CHART[0], **patch}], CATEGORIES, mapping_report(CURRENT, GENES, 9606))


def test_missing_categories_do_not_look_like_zero_significance():
    with pytest.raises(DavidBlocked): annotation_ids([], CATEGORIES)


def test_http_access_denial_is_not_retried_or_bypassed(tmp_path, monkeypatch):
    client = DavidClient(tmp_path)
    calls = []
    def denied(*args, **kwargs):
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = 403
        response._content = b"Access denied"
        return response
    monkeypatch.setattr(client.session, "request", denied)
    monkeypatch.setattr("pharm.enrich.david.time.sleep", lambda _: None)
    with pytest.raises(DavidBlocked): client.request("PUT", "/listManager", "denied", json={})
    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False
    assert read_json(tmp_path / "david_requests.json")[0]["status_code"] == 403
    client.close()
