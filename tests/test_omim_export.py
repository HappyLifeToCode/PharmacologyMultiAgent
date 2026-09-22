import pytest

from pharm.diseases.omim_export import combine_gene_map_exports, convert_gene_map_export

from pharm.core.common import digest
import io
import zipfile


def _make_xlsx(path, title, header, data_rows):
    """Build a minimal xlsx in the OMIM Gene Map export shape (inline strings)."""
    def cell(value):
        return '<c t="inlineStr"><is><t>%s</t></is></c>' % value

    sheet_rows = []
    for row in ([[title], ["Downloaded:", "September 19th, 2026"],
                 ["Copyright (c) Johns Hopkins University OMIM"], header] + data_rows + [["Phenotype Mapping Key"]]):
        sheet_rows.append("<row>%s</row>" % "".join(cell(v) for v in row))
    sheet = '<?xml version="1.0"?><worksheet><sheetData>%s</sheetData></worksheet>' % "".join(sheet_rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)

from pharm.diseases.omim_export import EXPECTED_HEADER


def _row(symbol, phenotype="Hyperthyroidism, familial", mim="603373", key="3"):
    return ["14q31.1", "14:80955621-81146306", "GENE", "gene name", "603372",
            symbol, "7253", "ENSG", "", phenotype, mim, "AD", key, "Tshr"]


def test_convert_extracts_approved_symbol_and_keeps_association(tmp_path):
    path = tmp_path / "omim.xlsx"
    _make_xlsx(path, "Gene Map Search - 'Hyperthyroidism'", EXPECTED_HEADER,
               [_row("TSHR"), _row("TSHR", phenotype="other phenotype", mim="275000"), _row("TG")])
    record = convert_gene_map_export(path)
    assert record["disease"] == "Hyperthyroidism"
    assert record["genes"] == ["TSHR", "TG"]
    assert len(record["associations"]) == 3
    assert record["associations"][0]["phenotype_mim"] == "603373"
    assert record["rejected"] == []


def test_zero_gene_keyword_is_valid_result(tmp_path):
    path = tmp_path / "omim.xlsx"
    _make_xlsx(path, "Gene Map Search - 'Thyroiditis'", EXPECTED_HEADER, [_row("")])
    record = convert_gene_map_export(path)
    assert record["genes"] == []
    assert record["associations"] == []
    assert record["rejected"][0]["reason"].startswith("无 Approved Symbol")


def test_combine_writes_sidecars(tmp_path):
    path = tmp_path / "omim.xlsx"
    _make_xlsx(path, "Gene Map Search - 'Hyperthyroidism'", EXPECTED_HEADER,
               [_row("TSHR"), _row("lnc-BAD-1"), _row("")])
    out = tmp_path / "omim.csv"
    records = combine_gene_map_exports([path], out)
    assert records[0]["genes"] == 1 and records[0]["rejected"] == 2
    content = out.read_text(encoding="utf-8-sig")
    assert "Hyperthyroidism,TSHR" in content
    rejected = (tmp_path / "omim_rejected_rows.csv").read_text(encoding="utf-8-sig")
    assert "lnc-BAD-1" in rejected
    associations = (tmp_path / "omim_associations.csv").read_text(encoding="utf-8-sig")
    assert "TSHR" in associations and "603373" in associations


def test_all_empty_exports_raise(tmp_path):
    path = tmp_path / "omim.xlsx"
    _make_xlsx(path, "Gene Map Search - 'Thyroiditis'", EXPECTED_HEADER, [_row("")])
    with pytest.raises(ValueError, match="全部导出均无有效关联行"):
        combine_gene_map_exports([path], tmp_path / "omim.csv")
