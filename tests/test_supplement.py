import pytest

from pharm_demo.common import read_json
from pharm_demo.supplement import (batman_backtrack, degree_top, outside_intersection,
                                   run_supplement)


def table(pairs):
    return [{"gene_symbol": g, "degree": d} for g, d in pairs]


def test_degree_top_ranks_and_truncates():
    result = degree_top(table([("A", 5), ("B", 3), ("C", 4), ("D", 1)]), 2)
    assert [r["gene_symbol"] for r in result["top"]] == ["A", "C"]
    assert result["cutoff_degree"] == 4
    assert result["ties_beyond_cutoff_included"] == 0
    assert [r["gene_symbol"] for r in result["ranked"]] == ["A", "C", "B", "D"]


def test_degree_top_keeps_all_ties_at_cutoff():
    result = degree_top(table([("A", 5), ("B", 4), ("C", 4), ("D", 4), ("E", 1)]), 2)
    assert [r["gene_symbol"] for r in result["top"]] == ["A", "B", "C", "D"]
    assert result["ties_beyond_cutoff_included"] == 2
    assert result["tie_policy"] == "include_all_ties"
    assert result["tie_policy_status"] == "provisional"


def test_degree_top_n_exceeds_list_and_invalid_n():
    result = degree_top(table([("A", 1)]), 50)
    assert [r["gene_symbol"] for r in result["top"]] == ["A"]
    with pytest.raises(ValueError):
        degree_top(table([("A", 1)]), 0)
    with pytest.raises(ValueError):
        degree_top(table([("A", 1)]), 2.5)


def test_outside_intersection():
    result = outside_intersection(["A", "B", "C"], ["B", "X"])
    assert result["candidates"] == ["A", "C"]
    assert result["excluded_by_intersection"] == ["B"]


def test_batman_backtrack_rows_and_unhit():
    relations = [{"herb": "白芍", "compound_id": "MOL1", "gene_symbol": "A", "score": 20.0},
                 {"herb": "炙甘草", "compound_id": "MOL2", "gene_symbol": "A", "score": 30.0},
                 {"herb": "白芍", "compound_id": "MOL3", "gene_symbol": "B", "score": 10.0}]
    result = batman_backtrack(["A", "B", "C"], relations)
    assert [(r["gene_symbol"], r["herb"]) for r in result["rows"]] == [
        ("A", "炙甘草"), ("A", "白芍"), ("B", "白芍")]  # 按 Unicode 码位确定性排序
    assert result["unhit_candidates"] == ["C"]
    assert batman_backtrack([], relations) == {"rows": [], "unhit_candidates": []}


def _base_config(tmp_path, genes=("G1", "G2")):
    return {"diseases": ["Thyroid cancer"], "cancer_genes": list(genes),
            "evidence_type": "synthetic_engineering", "taxon_id": 9606,
            "string_confidence": .9, "string_additional_nodes": 0, "string_version": "12.0",
            "top_n": 5, "intersection_genes": ["G2"]}


def test_run_supplement_blocks_without_intersection_source(tmp_path, monkeypatch):
    import pharm_demo.supplement as supplement
    monkeypatch.setattr(supplement, "_string_dispatch", lambda config: (
        lambda genes, task, directory: {"nodes": genes, "edges": [], "provenance": {}}))
    monkeypatch.setattr(supplement, "run_cytoscape", lambda net, directory, topology:
                        {"status": "blocked", "limitation": "test: offline"})
    config = _base_config(tmp_path)
    config.pop("intersection_genes")
    result = run_supplement(config, tmp_path / "out")
    stages = {s["stage"]: s for s in result["stages"]}
    assert stages["cancer_targets"]["status"] == "succeeded"
    assert stages["cancer_network"]["status"] == "partial"
    assert stages["outside_intersection"]["status"] == "blocked"
    assert result["status"] == "partial"
    assert result["scientific_complete"] is False
    pending = {n["input"]: n["status"] for n in result["optional_inputs"]}
    assert pending == {"核心七药名单": "pending", "跨朝代聚类表": "pending"}


def test_run_supplement_full_chain_with_synthetic_data(tmp_path, monkeypatch):
    import pharm_demo.supplement as supplement
    edges = [{"source": "G1", "target": "G2", "score": .95},
             {"source": "G1", "target": "G3", "score": .9}]
    monkeypatch.setattr(supplement, "_string_dispatch", lambda config: (
        lambda genes, task, directory: {"nodes": genes, "edges": edges, "provenance": {}}))
    def fake_cytoscape(net, directory, topology):
        return {"status": "succeeded", "method": "CytoNCA test adapter",
                "degree_table": [{"gene_symbol": "G1", "degree": 2.0},
                                 {"gene_symbol": "G2", "degree": 1.0},
                                 {"gene_symbol": "G3", "degree": 1.0}]}
    monkeypatch.setattr(supplement, "run_cytoscape", fake_cytoscape)
    relations = tmp_path / "relations.csv"
    relations.write_text("herb,compound_id,gene_symbol,score,evidence\n白芍,MOL1,G1,,known\n", encoding="utf-8")
    config = _base_config(tmp_path, genes=("G1", "G2", "G3"))
    config["herb_relations_file"] = str(relations)
    config["top_n"] = 2  # G3 与第 2 名 G2 并列，全部保留
    result = run_supplement(config, tmp_path / "out")
    assert result["status"] == "succeeded"
    top = read_json(tmp_path / "out" / "03_degree_top" / "degree_top.json")
    assert [r["gene_symbol"] for r in top["top"]] == ["G1", "G2", "G3"]
    assert top["degree_method"] == "CytoNCA test adapter"
    outside = read_json(tmp_path / "out" / "04_outside_intersection" / "outside_intersection.json")
    assert outside["candidates"] == ["G1", "G3"]  # G2 在主交集中被排除
    backtrack = read_json(tmp_path / "out" / "05_batman_backtrack" / "batman_backtrack.json")
    assert backtrack["rows"] == [{"gene_symbol": "G1", "herb": "白芍", "compound_id": "MOL1", "score": None, "evidence": "known"}]
    assert backtrack["unhit_candidates"] == ["G3"]
    assert "BATMAN 网页在线回溯尚未实现" in backtrack["limitation"]
    report = (tmp_path / "out" / "supplement_report.md").read_text(encoding="utf-8")
    assert "scientific_complete=false" in report


def test_full_import_relations_backtracks_beyond_threshold(tmp_path):
    """回溯用全量关系（含低于阈值的 predicted 与 known），evidence/score 原值保留。"""
    import csv as csv_module
    from pharm_demo.supplement import _load_full_import_relations, batman_backtrack
    path = tmp_path / "herb_targets.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv_module.writer(stream)
        writer.writerow(["herb", "compound_id", "gene_symbol", "score", "evidence"])
        writer.writerow(["白芍", "MOL1", "G1", "0.5", "predicted"])   # 低于 0.84 阈值，过滤口径下不可见
        writer.writerow(["炙甘草", "MOL2", "G1", "", "known"])
        writer.writerow(["白芍", "MOL3", "G2", "0.9", "predicted"])
    relations = _load_full_import_relations(path)
    assert len(relations) == 3
    result = batman_backtrack(["G1", "G2", "G3"], relations)
    assert [(r["gene_symbol"], r["evidence"], r["score"]) for r in result["rows"]] == [
        ("G1", "known", None), ("G1", "predicted", 0.5), ("G2", "predicted", 0.9)]
    assert result["unhit_candidates"] == ["G3"]
