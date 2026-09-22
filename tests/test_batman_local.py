import gzip

import pytest

from pharm.batman.local import generate_import
from pharm.core.imports import load_herb


def _write_gz(path, text):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(text)


def _fixture_data_dir(tmp_path):
    data = tmp_path / "batman_data"
    data.mkdir(parents=True)
    (data / "herb_browse.txt").write_text(
        "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\n"
        "BAI SHAO\t白芍\tCommon Peony\tPaeonia Albiflora\tcompoundA(1001)|compoundB(1002)\n"
        "GAN CAO\t甘草\tUral Licorice\tGlycyrrhiza Uralensis\tcompoundB(1002)|compoundC(1003)\n",
        encoding="utf-8")
    _write_gz(data / "known_browse_by_ingredients.txt.gz",
              "PubChem_CID\tIUPAC_name\tknown_target_proteins\n"
              "1001\tnameA\tTP53|EGFR\n"
              "1002\tnameB\tEGFR\n")
    _write_gz(data / "known_browse_by_targets.txt.gz",
              "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n"
              "7157\tTP53\t1001\n"
              "1956\tEGFR\t1001|1002\n"
              "5290\tPIK3CA\t1003\n")
    _write_gz(data / "predicted_browse_by_ingredients.txt.gz",
              "PubChem_CID IUPAC_name predicted_target_proteins\n"
              "1002 nameB 1956(0.9)|5290(0.62)\n")
    _write_gz(data / "predicted__browse_by_targets.txt.gz",
              "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n"
              "1956\tEGFR\t1002(0.9)\n")
    return data


def _task(data_dir, **extra):
    task = {"herbs": ["白芍", "甘草"], "batman_local_dir": str(data_dir),
            "batman_accessed_at": "2026-09-17", "batman_threshold": 0.84,
            "batman_threshold_confirmed": True}
    task.update(extra)
    return task


def test_generate_import_roundtrip_through_load_herb(tmp_path):
    data = _fixture_data_dir(tmp_path)
    out = tmp_path / "import"
    stats = generate_import(_task(data), out)
    assert stats["compound_count"] == 3 and stats["relation_rows"] == 4
    assert stats["unique_known_genes"] == 2 and stats["include_predicted"] is False
    assert stats["per_herb"]["白芍"] == {"compounds": 2, "compounds_with_known_targets": 2}
    assert stats["per_herb"]["甘草"] == {"compounds": 2, "compounds_with_known_targets": 1}

    herb = load_herb(out, _task(data))
    assert herb["genes"] == ["TP53", "EGFR"]
    assert all(r["evidence"] == "known" and r["score"] is None for r in herb["relations"])
    assert herb["source_counts"]["kept_rows"] == 4


def test_generate_import_with_predicted_rows(tmp_path):
    data = _fixture_data_dir(tmp_path)
    out = tmp_path / "import"
    task = _task(data, batman_include_predicted=True)
    stats = generate_import(task, out)
    assert stats["include_predicted"] is True and stats["relation_rows"] == 8

    herb = load_herb(out, task)
    # known 全保留；predicted 中 0.9>0.84 保留、0.62 被阈值过滤
    kept = sorted((r["gene_symbol"], r["evidence"]) for r in herb["relations"])
    assert kept == [("EGFR", "known"), ("EGFR", "known"), ("EGFR", "known"),
                    ("EGFR", "predicted"), ("EGFR", "predicted"), ("TP53", "known")]


def test_generate_import_rejects_unmatched_herb(tmp_path):
    data = _fixture_data_dir(tmp_path)
    with pytest.raises(ValueError, match="未找到药材"):
        generate_import(_task(data, herbs=["白芍", "炙甘草"]), tmp_path / "out")


def test_generate_import_requires_real_access_date(tmp_path):
    data = _fixture_data_dir(tmp_path)
    with pytest.raises(ValueError, match="accessed_at"):
        generate_import(_task(data, batman_accessed_at="待填写"), tmp_path / "out")
    with pytest.raises(ValueError, match="accessed_at"):
        generate_import(_task(data, batman_accessed_at=""), tmp_path / "out")


def test_generate_import_reports_missing_files(tmp_path):
    data = _fixture_data_dir(tmp_path)
    (data / "known_browse_by_ingredients.txt.gz").unlink()
    with pytest.raises(ValueError, match="缺失"):
        generate_import(_task(data), tmp_path / "out")
    data = _fixture_data_dir(tmp_path / "case2")
    (data / "predicted__browse_by_targets.txt.gz").unlink()
    with pytest.raises(ValueError, match="缺失"):
        generate_import(_task(data, batman_include_predicted=True), tmp_path / "out2")


def test_predicted_rows_with_empty_target_column_are_not_corruption(tmp_path):
    """官方 predicted 文件中无靶点的成分行为空列/仅剩 CID（本批真实数据 288 行如此）。"""
    data = tmp_path / "batman_data"
    data.mkdir(parents=True)
    (data / "herb_browse.txt").write_text(
        "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\n"
        "BAI SHAO\t白芍\tCommon Peony\tPaeonia Albiflora\tcompoundA(1001)|compoundB(1002)\n",
        encoding="utf-8")
    _write_gz(data / "known_browse_by_ingredients.txt.gz",
              "PubChem_CID\tIUPAC_name\tknown_target_proteins\n"
              "1001\tnameA\tTP53\n")
    _write_gz(data / "known_browse_by_targets.txt.gz",
              "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n"
              "7157\tTP53\t1001\n")
    _write_gz(data / "predicted_browse_by_ingredients.txt.gz",
              "PubChem_CID IUPAC_name predicted_target_proteins\n"
              "1002 nameB \n"          # CID + 名称、无靶点
              "1001  \n")              # 仅剩 CID
    _write_gz(data / "predicted__browse_by_targets.txt.gz",
              "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n"
              "1956\tEGFR\t1002(0.9)\n")
    task = {"herbs": ["白芍"], "batman_local_dir": str(data),
            "batman_accessed_at": "2026-09-17", "batman_include_predicted": True}
    stats = generate_import(task, tmp_path / "import")
    assert stats["relation_rows"] == 1  # 只有 known 的 TP53；空 predicted 行不产生记录
