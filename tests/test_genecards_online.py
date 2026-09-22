import pytest

from pharm.diseases.genecards_online import (parse_declared_total, parse_rows_from_html,
                                         parse_site_version)

# 按 2026-09-18 真实页面（local/gc_page_sample.html，GeneCards 6.1）校准的结构
ROW = ('<tr class="even"><td class="dt-control"></td><td class="dt-type-numeric">1</td>'
       '<td><a target="_blank" href="/card/TSHR?search=Hyperthyroidism">TSHR</a></td>'
       '<td>Thyroid Stimulating Hormone Receptor</td><td>Protein Coding</td>'
       '<td class="text-end dt-type-numeric">370.4</td>'
       '<td class="text-center"><knowledge-ring value="98"><span>98</span></knowledge-ring></td></tr>')


def test_parse_rows_uses_relevance_column_not_knowledge():
    rows = parse_rows_from_html("<html><body><table><tbody>%s</tbody></table></body></html>" % ROW)
    assert rows == [{"gene_symbol": "TSHR", "relevance_score": 370.4, "gene_type": "Protein Coding"}]
    # 第 7 列 Knowledge（98）不能被误当成分数


def test_parse_rows_rejects_missing_score_and_bad_symbol():
    bad = '<tr><td></td><td>1</td><td><a href="/card/TP53?search=X">TP53</a></td><td>x</td></tr>'
    with pytest.raises(ValueError, match="列数不足"):
        parse_rows_from_html("<table>%s</table>" % bad)
    bad2 = '<tr><td></td><td>1</td><td><a href="/card/TP53?search=X">TP53</a></td><td>x</td><td>y</td><td>abc</td></tr>'
    with pytest.raises(ValueError, match="Relevance Score"):
        parse_rows_from_html("<table>%s</table>" % bad2)


def test_parse_declared_total_variants():
    assert parse_declared_total('<div class="dt-info">of 1,930</div>') == 1930
    assert parse_declared_total('<strong>1,930</strong> results by:') == 1930
    assert parse_declared_total("<div>nothing</div>") is None


def test_parse_site_version():
    assert parse_site_version("Version 6.1 Build August 17, 2026") == "Version 6.1 Build August 17, 2026"
    assert parse_site_version("no version") is None


def test_real_sample_page_parses_exactly():
    import pathlib
    sample = pathlib.Path("local/gc_page_sample.html")
    if not sample.exists():
        pytest.skip("真实页面样本只在本机 local/ 下")
    html = sample.read_text(encoding="utf-8")
    rows = parse_rows_from_html(html)
    assert len(rows) == 20
    assert rows[0] == {"gene_symbol": "TSHR", "relevance_score": 370.4, "gene_type": "Protein Coding"}
    assert rows[-1]["gene_symbol"] == "IL6" and rows[-1]["relevance_score"] == 77.4
    assert parse_declared_total(html) == 1930
    assert parse_site_version(html) == "Version 6.1 Build August 17, 2026"
