import pytest

from pharm_demo.common import read_json
from pharm_demo.genecards_export import collect_disease, combine_diseases, parse_results_html


def _page(rows, total=None):
    """Synthetic page in the real GeneCards 6.1 structure (calibrated 2026-09-18)."""
    body = "".join(
        '<tr><td></td><td>%d</td><td><a href="/card/%s?search=X">%s</a></td>'
        '<td>desc</td><td>Protein Coding</td><td>%s</td><td>80</td></tr>'
        % (index, gene, gene, score) for index, (gene, score) in enumerate(rows, 1))
    info = '<div class="dt-info">of %s</div>' % format(total, ",") if total else ""
    return "<html><body>%s<table><tbody>%s</tbody></table></body></html>" % (info, body)


def test_parse_results_html_extracts_gene_and_score(tmp_path):
    page = tmp_path / "p1.html"
    page.write_text(_page([("TP53", "12.5"), ("EGFR", "3.0")], total=2), encoding="utf-8")
    parsed = parse_results_html(page)
    assert parsed["rows"] == [{"gene_symbol": "TP53", "relevance_score": 12.5},
                              {"gene_symbol": "EGFR", "relevance_score": 3.0}]
    assert parsed["total"] == 2
    assert len(parsed["file_sha256"]) == 64


def test_collect_merges_pages_and_passes_when_counts_match(tmp_path):
    p1 = tmp_path / "p1.html"
    p1.write_text(_page([("TP53", 12.5), ("EGFR", 3.0)], total=3), encoding="utf-8")
    p2 = tmp_path / "p2.html"
    p2.write_text(_page([("AKT1", 1.5)]), encoding="utf-8")
    record, out_csv = collect_disease("Hyperthyroidism", [p1, p2], tmp_path / "out")
    assert record["status"] == "complete"
    assert record["parsed_rows"] == 3 and record["declared_total"] == 3
    assert read_json(tmp_path / "out" / "genecards_Hyperthyroidism_collection.json")["status"] == "complete"
    content = out_csv.read_text(encoding="utf-8-sig")
    assert "Hyperthyroidism,TP53,12.5" in content and "Hyperthyroidism,AKT1,1.5" in content


def test_collect_rejects_incomplete_pagination(tmp_path):
    p1 = tmp_path / "p1.html"
    p1.write_text(_page([("TP53", 12.5)], total=5), encoding="utf-8")
    with pytest.raises(ValueError, match="不一致"):
        collect_disease("Hyperthyroidism", [p1], tmp_path / "out")
    # 失败也保留证据记录
    record = read_json(tmp_path / "out" / "genecards_Hyperthyroidism_collection.json")
    assert record["status"] == "incomplete" and record["declared_total"] == 5


def test_collect_rejects_duplicate_rows_across_pages(tmp_path):
    p1 = tmp_path / "p1.html"
    p1.write_text(_page([("TP53", 1.0)]), encoding="utf-8")
    with pytest.raises(ValueError, match="重复行"):
        collect_disease("Hypothyroidism", [p1, p1], tmp_path / "out")


def test_collect_rejects_empty_parse(tmp_path):
    p1 = tmp_path / "p1.html"
    p1.write_text("<html><body>no table</body></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="未解析到任何结果行"):
        collect_disease("Thyroiditis", [p1], tmp_path / "out")


def test_combine_diseases_builds_contract_csv(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("disease,gene_symbol,relevance_score\nHyperthyroidism,TP53,12.5\n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text("disease,gene_symbol,relevance_score\nThyroid cancer,TP53,30.0\nThyroid cancer,BRCA1,9\n",
                 encoding="utf-8")
    out = tmp_path / "genecards.csv"
    assert combine_diseases([a, b], out) == 3
    content = out.read_text(encoding="utf-8-sig")
    assert "Thyroid cancer,TP53,30.0" in content  # 跨疾病同一基因保留各自分数
    dup = tmp_path / "dup.csv"
    dup.write_text("disease,gene_symbol,relevance_score\nHyperthyroidism,TP53,9.9\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        combine_diseases([a, dup], tmp_path / "genecards2.csv")
