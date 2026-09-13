import csv

import pytest

from pharm_demo.processing import (
    analyze_network,
    filter_genecards,
    intersection,
    normalize_symbols,
    read_gene_csv,
    write_network,
    write_venn,
)


def test_normalize_exact_dedup_and_reject_without_alias_mapping():
    symbols, rejected = normalize_symbols(["TP53", "TP53", "p53", "HLA-DRA", "C1orf12", {"gene_symbol": "BAD value"}])
    assert symbols == ["TP53", "HLA-DRA", "C1orf12"]
    assert rejected == ["p53", {"gene_symbol": "BAD value"}]


def test_read_gene_csv_requires_header_and_reports_invalid(tmp_path):
    good = tmp_path / "genes.csv"
    good.write_text("gene_symbol\nTP53\nTP53\n", encoding="utf-8")
    assert read_gene_csv(good) == ["TP53"]
    bad = tmp_path / "bad.csv"
    bad.write_text("symbol\nTP53\n", encoding="utf-8")
    with pytest.raises(ValueError, match="gene_symbol"):
        read_gene_csv(bad)


def test_filter_genecards_requires_complete_and_rejects_nan_bool():
    with pytest.raises(ValueError, match="complete"):
        filter_genecards([], complete=False)
    rows = [{"gene_symbol": "A", "relevance_score": 1}, {"gene_symbol": "B", "relevance_score": 3}, {"gene_symbol": "C", "relevance_score": 2}]
    out = filter_genecards(rows, complete=True)
    assert out["median"] == 2.0 and [r["gene_symbol"] for r in out["kept"]] == ["B"]
    for score in (float("nan"), True):
        with pytest.raises(ValueError):
            filter_genecards([{"gene_symbol": "A", "relevance_score": score}], complete=True)


def test_empty_intersection_is_truthful():
    assert intersection(["A", "B"], ["C"]) == {
        "herb_count": 2, "disease_count": 1, "intersection_count": 0,
        "genes": [], "herb_only": ["A", "B"], "disease_only": ["C"],
    }


def test_network_validates_and_preserves_isolates():
    out = analyze_network(["A", "B", "C"], [{"source": "A", "target": "B", "score": 0.9}])
    assert out["node_count"] == 3
    assert {x["gene_symbol"] for x in out["degree_table"] if x["degree"] == 0} == {"C"}
    with pytest.raises(ValueError, match="dangling"):
        analyze_network(["A"], [{"source": "A", "target": "B", "score": 1}])
    with pytest.raises(ValueError, match="self-loop"):
        analyze_network(["A"], [{"source": "A", "target": "A", "score": 1}])
    with pytest.raises(ValueError, match="between 0 and 1"):
        analyze_network(["A", "B"], [{"source": "A", "target": "B", "score": 1.1}])
    with pytest.raises(ValueError, match="duplicates"):
        analyze_network(["A", "B"], [
            {"source": "A", "target": "B", "score": 0.8},
            {"source": "B", "target": "A", "score": 0.7},
        ])


def test_writers_create_pngs(tmp_path):
    result = intersection(["A"], ["A", "B"])
    venn = tmp_path / "venn.png"
    write_venn(result, venn)
    network = tmp_path / "network.png"
    write_network(analyze_network(["A", "B"], [{"source": "A", "target": "B", "score": 1}]), network)
    assert venn.stat().st_size > 0 and network.stat().st_size > 0
