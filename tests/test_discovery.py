import csv
import json
import sqlite3
import gzip

import pytest
from fastapi.testclient import TestClient

from pharm.discovery import query as discovery
from pharm.core.common import digest, write_json
from server import app as backend


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def batch(tmp_path):
    directory = tmp_path / "batch"
    directory.mkdir()
    (directory / "raw.txt").write_text("Synthetic fixture, not research data", encoding="utf-8")
    source = {"complete": True, "source_url": "https://example.org/data",
              "accessed_at": "2026-09-21", "raw_files": ["raw.txt"]}
    write_json(directory / "provenance.json", {
        "sources": {
            "batman": dict(source, herbs=["药材甲", "药材乙"], threshold=0.84, threshold_confirmed=True),
            "genecards": dict(source, diseases=list(discovery.DISEASES)),
            "omim": dict(source, diseases=list(discovery.DISEASES)),
        }, "mapping": {"confirmed": True, "method": "synthetic exact symbol", "version": "fixture",
                       "raw_files": ["raw.txt"]},
    })
    write_csv(directory / "herb_targets.csv", ["herb", "compound_id", "gene_symbol", "score", "evidence"], [
        {"herb": "药材甲", "compound_id": "C1", "gene_symbol": "TP53", "score": "", "evidence": "known"},
        {"herb": "药材乙", "compound_id": "C2", "gene_symbol": "EGFR", "score": "0.9", "evidence": "predicted"},
        {"herb": "药材乙", "compound_id": "C3", "gene_symbol": "AKT1", "score": "0.8", "evidence": "predicted"},
    ])
    write_csv(directory / "genecards.csv", ["disease", "gene_symbol", "relevance_score"], [
        {"disease": discovery.DISEASES[0], "gene_symbol": "TP53", "relevance_score": 100},
        {"disease": discovery.DISEASES[0], "gene_symbol": "EGFR", "relevance_score": 1},
        {"disease": discovery.DISEASES[1], "gene_symbol": "TP53", "relevance_score": 2},
        {"disease": discovery.DISEASES[2], "gene_symbol": "AKT1", "relevance_score": 3},
    ])
    write_csv(directory / "omim.csv", ["disease", "gene_symbol"], [
        {"disease": discovery.DISEASES[0], "gene_symbol": "TP53"},
        {"disease": discovery.DISEASES[0], "gene_symbol": "TP53"},
    ])
    return directory


@pytest.fixture
def database(batch, tmp_path):
    path = tmp_path / "index.sqlite"
    discovery.build_database(batch, path)
    return path


def test_query_counts_duplicates_cross_disease_and_zero_matches(database):
    before = digest(database)
    result = discovery.query(database, genes=["TP53", "EGFR", "TP53", "ZZZTEST"])
    # 索引疾病集合来自批次实际值：本批次覆盖 Hyperthyroidism/Hypothyroidism/Thyroid cancer
    first, second, third = result["candidates"]
    assert result["input_count"] == 3
    assert result["matched_input_count"] == 2
    assert result["unmatched_genes"] == ["ZZZTEST"]
    assert first["matched_genes"] == ["EGFR", "TP53"]
    assert first["input_coverage"] == pytest.approx(2 / 3)
    assert first["disease_coverage"] == 1
    assert first["source_gene_counts"] == {"genecards": 2, "omim": 1}
    assert len(first["evidence"]) == 4
    assert any(row["relevance_score"] == 1 for row in first["evidence"])
    assert second["matched_count"] == 1
    assert third["disease_coverage"] == 0
    assert third["matched_count"] == 0
    assert result["scientific_complete"] is False
    assert result["dataset"]["selection"] == "all_valid_export_rows"
    assert result["database_sha256"] == before == digest(database)
    assert result["herb_relations"] == []


def test_herb_input_preserves_compound_evidence_and_batch_threshold(database):
    result = discovery.query(database, herbs=["药材乙", "药材甲"])
    assert result["input"]["genes"] == ["EGFR", "TP53"]
    assert len(result["herb_relations"]) == 2
    assert {row["evidence"] for row in result["herb_relations"]} == {"known", "predicted"}
    assert discovery.query(database, herbs=["药材甲"])["input_count"] == 1


@pytest.mark.parametrize("inputs", [
    {}, {"herbs": [], "genes": []}, {"herbs": []}, {"genes": []}, {"genes": "TP53"},
    {"genes": [None]}, {"genes": ["tp53"]}, {"genes": ["TP53;DROP TABLE associations"]},
    {"herbs": ["未收录"]}, {"genes": ["TP53"] * 3001},
])
def test_invalid_input_fails_explicitly(database, inputs):
    with pytest.raises(ValueError):
        discovery.query(database, **inputs)


def test_no_match_is_success_and_aliases_are_not_invented(database):
    result = discovery.query(database, genes=["P53"])
    assert result["status"] == "succeeded"
    assert result["unmatched_genes"] == ["P53"]
    assert all(candidate["matched_count"] == 0 for candidate in result["candidates"])


def test_missing_database_does_not_create_empty_file(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError):
        discovery.query(path, genes=["TP53"])
    assert not path.exists()


def test_build_tracks_sources_and_refuses_overwrite(batch, database):
    result = discovery.query(database, genes=["TP53"])
    hashes = result["dataset"]["source_sha256"]
    assert hashes["genecards.csv"] == digest(batch / "genecards.csv")
    assert "raw.txt" in hashes and "provenance.json" in hashes
    with pytest.raises(ValueError, match="已存在"):
        discovery.build_database(batch, database)


@pytest.mark.parametrize("problem", ["scope", "incomplete", "missing_raw", "duplicate", "nan", "unknown_disease"])
def test_build_rejects_invalid_sources(batch, tmp_path, problem):
    provenance = json.loads((batch / "provenance.json").read_text(encoding="utf-8"))
    if problem == "scope":
        provenance["sources"]["omim"]["diseases"] = ["Other"]
    elif problem == "incomplete":
        provenance["sources"]["genecards"]["complete"] = False
    elif problem == "missing_raw":
        (batch / "raw.txt").unlink()
    else:
        extra = {"duplicate": "Hyperthyroidism,TP53,100\n", "nan": "Thyroiditis,BAX,nan\n",
                 "unknown_disease": "Other,BAX,1\n"}[problem]
        with (batch / "genecards.csv").open("a", encoding="utf-8") as stream:
            stream.write(extra)
    write_json(batch / "provenance.json", provenance)
    path = tmp_path / "invalid.sqlite"
    with pytest.raises(ValueError):
        discovery.build_database(batch, path)
    assert not path.exists()


def test_save_result_and_no_overwrite(database, tmp_path):
    result = discovery.query(database, genes=["ZZZTEST"])
    output = tmp_path / "run"
    discovery.save_result(result, output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["artifacts"].items():
        assert digest(output / name) == expected
    assert (output / "evidence.csv").read_text(encoding="utf-8-sig").startswith("disease,gene_symbol")
    with pytest.raises(FileExistsError):
        discovery.save_result(result, output)


def test_api_roundtrip_and_boundaries(batch, tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    discovery.build_database(batch, tmp_path / discovery.DEFAULT_DATABASE)
    with TestClient(backend.app, base_url="http://localhost") as client:
        catalog = client.get("/api/discovery/catalog").json()
        assert len(catalog["diseases"]) == 3
        response = client.post("/api/discovery/query", json={"herbs": ["药材甲"]})
        assert response.status_code == 200
        body = response.json()
        assert body["result"]["input_count"] == 1
        prefix = "/discovery/artifacts/" + body["run_id"]
        assert client.get(prefix + "/result.json").status_code == 200
        assert client.get(prefix + "/report.md").status_code == 200
        assert client.get(prefix + "/index.sqlite").status_code == 404
        assert client.post("/api/discovery/query", json={"diseases": ["X"]}).status_code == 400
        assert client.post("/api/discovery/query", json=[]).status_code == 400
        assert client.post("/api/discovery/query", content="bad JSON").status_code == 400
        assert client.post("/api/discovery/query", json={"genes": ["TP53"]}, headers={"Origin": "https://other.example"}).status_code == 403
        assert client.post("/api/discovery/query", content="x" * 100001).status_code == 413


def test_catalog_unavailable_and_corrupt_schema(tmp_path, database, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert client.get("/api/discovery/catalog").status_code == 503
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE metadata SET value = ?", (json.dumps({"schema_version": 999}),))
    with pytest.raises(ValueError):
        discovery.query(database, genes=["TP53"])


@pytest.fixture
def full_batman(batch, tmp_path):
    from pharm.batman.local import REQUIRED_FILES, PREDICTED_FILES

    directory = tmp_path / "batman"
    directory.mkdir()
    contents = {
        "herbs": "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\n"
                 "YAO CAI JIA\t药材甲\tA\tLatin A\tCompound one(1)\n"
                 "YAO CAI YI\t药材乙\tB\tLatin B\tCompound two(2)\n"
                 "HUANG QI A\t黄芪\tC\tLatin C\tCompound one(1)|Bad CID\n"
                 "HUANG QI B\t黄芪\tD\tLatin D\tCompound two(2)\n"
                 "NO CHINESE\tNA\tE\tLatin E\tCompound three(3)\n"
                 "NO TARGET\t无靶点\tF\tLatin F\tCompound four(4)\n",
        "known_by_ingredients": "PubChem_CID\tIUPAC_name\tknown_target_proteins\n1\tOne\tTP53|Akr1b1\n",
        "known_by_targets": "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n7157\tTP53\t1\n",
        "predicted_by_ingredients": "PubChem_CID IUPAC_name predicted_target_proteins\n2 Two 1956(0.9)|207(0.84)|999(0.99)\n3 Three 207(0.91)\n4\n",
        "predicted_by_targets": "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n1956\tEGFR\t2\n207\tAKT1\t2|3\n",
    }
    manifest = {"files": {}}
    filenames = dict(REQUIRED_FILES, **{key: names[0] for key, names in PREDICTED_FILES.items()})
    for kind, content in contents.items():
        path = directory / filenames[kind]
        if path.suffix == ".gz":
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                stream.write(content)
        else:
            path.write_text(content, encoding="utf-8")
        manifest["files"][kind] = {"filename": path.name, "sha256": digest(path)}
    manifest_path = batch / "raw/batman_full_files.manifest.json"
    write_json(manifest_path, manifest)
    provenance_path = batch / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["sources"]["batman"]["raw_files"].append("raw/batman_full_files.manifest.json")
    write_json(provenance_path, provenance)
    base = tmp_path / "base.sqlite"
    discovery.build_database(batch, base)
    return base, directory, manifest_path


def test_full_batman_expansion_preserves_scope_and_disambiguates(full_batman, tmp_path):
    from pharm.batman.catalog import expand_database

    base, directory, manifest = full_batman
    original_hash = digest(base)
    expanded = tmp_path / "expanded.sqlite"
    stats = expand_database(base, expanded, directory, manifest)
    assert stats["herb_count"] == 6
    assert stats["queryable_herb_count"] == 5
    assert stats["rejected_count"] == 3
    assert digest(base) == original_hash
    catalog = discovery.catalog(expanded)
    assert len(catalog["diseases"]) == 3
    duplicates = [name for name in catalog["herbs"] if name.startswith("黄芪")]
    assert len(duplicates) == 2 and duplicates[0] != duplicates[1]
    assert discovery.query(expanded, herbs=[duplicates[0]])["input"]["genes"] == ["TP53"]
    assert discovery.query(expanded, herbs=[duplicates[1]])["input"]["genes"] == ["EGFR"]
    assert discovery.query(expanded, herbs=["NO CHINESE"])["input"]["genes"] == ["AKT1"]
    assert discovery.query(expanded, herbs=["药材甲", "药材乙"])["input"]["genes"] == ["EGFR", "TP53"]
    assert discovery.query(expanded, genes=["TP53"])["candidates"] == discovery.query(base, genes=["TP53"])["candidates"]
    assert discovery.query(expanded, genes=["TP53"])["dataset"]["herb_catalog"] == []
    with pytest.raises(ValueError, match="没有可用靶点"):
        discovery.query(expanded, herbs=["药材甲", "无靶点"])
    with pytest.raises(ValueError, match="未收录"):
        discovery.query(expanded, herbs=["黄芪"])
    with pytest.raises(ValueError, match="已存在"):
        expand_database(base, expanded, directory, manifest)


def test_full_batman_rejects_changed_download_and_manifest(full_batman, tmp_path):
    from pharm.batman.catalog import expand_database

    base, directory, manifest = full_batman
    altered_manifest = tmp_path / "altered.json"
    altered_manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="清单"):
        expand_database(base, tmp_path / "bad1.sqlite", directory, altered_manifest)
    with (directory / "herb_browse.txt").open("a", encoding="utf-8") as stream:
        stream.write("changed")
    with pytest.raises(ValueError, match="原始文件"):
        expand_database(base, tmp_path / "bad2.sqlite", directory, manifest)
    assert not (tmp_path / "bad2.sqlite").exists()


def test_database_config_selects_expanded_snapshot(full_batman, tmp_path, monkeypatch):
    from pharm.batman.catalog import expand_database

    base, directory, manifest = full_batman
    expanded = tmp_path / "expanded.sqlite"
    expand_database(base, expanded, directory, manifest)
    write_json(tmp_path / "configs/discovery_data.local.json", {"database": "expanded.sqlite"})
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    with TestClient(backend.app, base_url="http://localhost") as client:
        assert len(client.get("/api/discovery/catalog").json()["herbs"]) == 6
        response = client.post("/api/discovery/query", json={"herbs": ["NO CHINESE"]})
        assert response.status_code == 200
        assert response.json()["result"]["input"]["genes"] == ["AKT1"]


def test_predicted_targets_ignores_numbers_in_spaced_chemical_name(tmp_path):
    from pharm.batman.catalog import predicted_targets

    path = tmp_path / "predicted.txt"
    path.write_text("PubChem_CID IUPAC_name predicted_target_proteins\n"
                    "1 methyl 8(17)-tetraene acid 7157(0.9)|207(0.7)\n"
                    "2 name without targets\n3\n", encoding="utf-8")
    assert list(predicted_targets(path, {"1", "2", "3"})) == [("1", [("7157", "0.9"), ("207", "0.7")])]


def test_generalized_disease_set_from_batch_values(batch, tmp_path):
    """疾病集合来自批次 disease 列实际值，首次出现序（genecards.csv 先于 omim.csv）。"""
    provenance = json.loads((batch / "provenance.json").read_text(encoding="utf-8"))
    diseases = ["Disease Alpha", "Disease Beta", "Disease Gamma"]
    for name in ("genecards", "omim"):
        provenance["sources"][name]["diseases"] = diseases
    write_json(batch / "provenance.json", provenance)
    write_csv(batch / "genecards.csv", ["disease", "gene_symbol", "relevance_score"], [
        {"disease": "Disease Beta", "gene_symbol": "TP53", "relevance_score": 5},
        {"disease": "Disease Alpha", "gene_symbol": "EGFR", "relevance_score": 9},
    ])
    write_csv(batch / "omim.csv", ["disease", "gene_symbol"], [
        {"disease": "Disease Gamma", "gene_symbol": "AKT1"},
    ])
    path = tmp_path / "wide.sqlite"
    metadata = discovery.build_database(batch, path)
    assert metadata["import_kind"] == "genecards_omim_batch"
    assert metadata["diseases"] == ["Disease Beta", "Disease Alpha", "Disease Gamma"]
    result = discovery.query(path, genes=["TP53", "AKT1", "TNF"])
    assert [c["disease"] for c in result["candidates"]] == ["Disease Beta", "Disease Alpha", "Disease Gamma"]
    assert result["candidates"][0]["matched_count"] == 1
    assert result["candidates"][1]["matched_count"] == 0  # 零匹配如实显示
    assert result["candidates"][2]["matched_count"] == 1
    catalog = discovery.catalog(path)
    assert [d["name"] for d in catalog["diseases"]] == ["Disease Beta", "Disease Alpha", "Disease Gamma"]


def test_legacy_index_without_diseases_field_falls_back_to_distinct(database):
    """旧索引 metadata 没有疾病清单时，回退到 associations 表实际值（字典序）。"""
    with sqlite3.connect(database) as connection:
        metadata = json.loads(connection.execute("SELECT value FROM metadata").fetchone()[0])
        metadata.pop("diseases")
        connection.execute("UPDATE metadata SET value=?", (json.dumps(metadata, ensure_ascii=False),))
    result = discovery.query(database, genes=["TP53"])
    assert [c["disease"] for c in result["candidates"]] == [
        "Hyperthyroidism", "Hypothyroidism", "Thyroid cancer"]
    assert discovery.catalog(database)["diseases"][0]["name"] == "Hyperthyroidism"


@pytest.fixture
def assoc_batch(tmp_path):
    directory = tmp_path / "assoc_batch"
    directory.mkdir()
    (directory / "raw.txt").write_text("Synthetic fixture, not research data", encoding="utf-8")
    write_json(directory / "provenance.json", {
        "sources": {"associations": {"complete": True, "source_url": "https://example.org/assoc",
                                     "accessed_at": "2026-09-22", "raw_files": ["raw.txt"],
                                     "diseases": ["Disease Alpha", "Disease Beta", "Disease Gamma"]}},
        "mapping": {"confirmed": True, "method": "synthetic exact symbol", "version": "fixture",
                    "raw_files": ["raw.txt"]},
    })
    write_csv(directory / "associations.csv", ["disease", "gene_symbol", "score", "source", "extra"], [
        {"disease": "Disease Alpha", "gene_symbol": "TP53", "score": "12.5", "source": "genecards", "extra": "row-1"},
        {"disease": "Disease Alpha", "gene_symbol": "EGFR", "score": "", "source": "omim", "extra": ""},
        {"disease": "Disease Beta", "gene_symbol": "TP53", "score": "3", "source": "genecards", "extra": ""},
    ])
    return directory


def test_generic_associations_build_query_catalog(assoc_batch, tmp_path):
    from pharm.diseases.associations import build_associations_database
    path = tmp_path / "generic.sqlite"
    metadata = build_associations_database(assoc_batch, path)
    assert metadata["import_kind"] == "generic_associations"
    # 声明的 Disease Gamma 零关联，不进索引
    assert metadata["diseases"] == ["Disease Alpha", "Disease Beta"]
    assert metadata["rejected_symbol_rows"] == 0
    result = discovery.query(path, genes=["TP53", "TNF"])
    assert [c["disease"] for c in result["candidates"]] == ["Disease Alpha", "Disease Beta"]
    alpha = result["candidates"][0]
    assert alpha["matched_count"] == 1
    assert alpha["source_gene_counts"] == {"genecards": 1}
    assert alpha["evidence"][0]["record"]["extra"] == "row-1"
    assert alpha["evidence"][0]["relevance_score"] == 12.5
    assert result["candidates"][1]["matched_count"] == 1
    catalog = discovery.catalog(path)
    assert [d["name"] for d in catalog["diseases"]] == ["Disease Alpha", "Disease Beta"]
    assert catalog["source_rows"] == {"associations": 3}
    with pytest.raises(ValueError, match="未收录"):
        discovery.query(path, herbs=["白芍"])


@pytest.mark.parametrize("problem", ["missing_provenance", "incomplete", "bad_url", "escape"])
def test_generic_batch_provenance_rejected(assoc_batch, problem):
    from pharm.diseases.associations import build_associations_database
    if problem == "missing_provenance":
        (assoc_batch / "provenance.json").unlink()
    else:
        provenance = json.loads((assoc_batch / "provenance.json").read_text(encoding="utf-8"))
        if problem == "incomplete":
            provenance["sources"]["associations"]["complete"] = False
        elif problem == "bad_url":
            provenance["sources"]["associations"]["source_url"] = "ftp://example.org/data"
        else:
            provenance["sources"]["associations"]["raw_files"] = ["../outside.txt"]
        write_json(assoc_batch / "provenance.json", provenance)
    with pytest.raises(ValueError):
        build_associations_database(assoc_batch, assoc_batch / "out.sqlite")
    assert not (assoc_batch / "out.sqlite").exists()


def test_generic_batch_duplicate_rows_rejected(assoc_batch, tmp_path):
    from pharm.diseases.associations import build_associations_database
    with (assoc_batch / "associations.csv").open("a", encoding="utf-8") as stream:
        stream.write("Disease Alpha,TP53,12.5,genecards,row-dup\n")
    with pytest.raises(ValueError, match="重复行"):
        build_associations_database(assoc_batch, tmp_path / "dup.sqlite")
    assert not (tmp_path / "dup.sqlite").exists()


def test_generic_batch_invalid_symbols_archived_not_invented(assoc_batch, tmp_path):
    from pharm.diseases.associations import build_associations_database
    with (assoc_batch / "associations.csv").open("a", encoding="utf-8") as stream:
        stream.write("Disease Beta,tp53lower,9,genecards,\n")
        # Disease Gamma 只有非法符号行 → 零有效关联，不进索引
        stream.write("Disease Gamma,BAD GENE,1,genecards,\n")
    path = tmp_path / "generic.sqlite"
    metadata = build_associations_database(assoc_batch, path)
    assert metadata["rejected_symbol_rows"] == 2
    assert "Disease Gamma" not in metadata["diseases"]
    archived = tmp_path / "generic.rejected_symbols.csv"
    assert archived.is_file()
    text = archived.read_text(encoding="utf-8-sig")
    assert "tp53lower" in text and "BAD GENE" in text
    result = discovery.query(path, genes=["TP53"])
    assert result["candidates"][0]["matched_count"] == 1


def test_generic_batch_disease_cap(assoc_batch, tmp_path):
    from pharm.diseases.associations import build_associations_database
    provenance = json.loads((assoc_batch / "provenance.json").read_text(encoding="utf-8"))
    provenance["sources"]["associations"].pop("diseases")
    write_json(assoc_batch / "provenance.json", provenance)
    rows = ["disease,gene_symbol\n"] + ["Disease %03d,TP%d\n" % (i, i) for i in range(501)]
    (assoc_batch / "associations.csv").write_text("".join(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="上限"):
        build_associations_database(assoc_batch, tmp_path / "cap.sqlite")


def test_prepare_batch_auto_detects_and_rejects_mixed(assoc_batch, batch, tmp_path):
    metadata = discovery.prepare_batch(assoc_batch, tmp_path / "a.sqlite")
    assert metadata["import_kind"] == "generic_associations"
    metadata = discovery.prepare_batch(batch, tmp_path / "b.sqlite")
    assert metadata["import_kind"] == "genecards_omim_batch"
    (assoc_batch / "genecards.csv").write_text(
        "disease,gene_symbol,relevance_score\nDisease Alpha,TP53,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="无法识别批次类型"):
        discovery.prepare_batch(assoc_batch, tmp_path / "c.sqlite")
    empty = tmp_path / "empty_batch"
    empty.mkdir()
    with pytest.raises(ValueError, match="缺少"):
        discovery.prepare_batch(empty, tmp_path / "d.sqlite")


# ---- 启发式置信度（heuristic_v1） ----

def test_confidence_present_with_components_and_version(database):
    result = discovery.query(database, genes=["TP53", "EGFR", "ZZZTEST"])
    for candidate in result["candidates"]:
        confidence = candidate["confidence"]
        assert 0.0 <= confidence["value"] <= 1.0
        assert set(confidence["components"]) == {"match_score", "input_coverage",
                                                 "disease_coverage", "evidence_quality"}
        assert confidence["formula_version"] == "heuristic_v1"
        assert "启发式" in confidence["note"]


def test_confidence_matches_hand_calculated_values(database):
    """对拍：手工按 heuristic_v1 公式计算的预期值。

    索引：Hyperthyroidism 有 TP53(100)+EGFR(1)（genecards）与 TP53×2（omim），
    Hypothyroidism 有 TP53(2)，Thyroid cancer 有 AKT1(3)；索引 BATMAN 表内
    TP53=known、EGFR=predicted；genecards 最大分值 100。输入 TP53,EGFR。
    """
    import math
    result = discovery.query(database, genes=["TP53", "EGFR"])
    hyper, hypo, cancer = result["candidates"]
    # Hyper：match=ln3/ln3=1，input_cov=1，disease_cov=2/2=1，
    # quality=(known占比 1/2 + 分值均值 (100+1)/2/100) / 2 = 0.5025
    assert hyper["confidence"]["components"]["match_score"] == pytest.approx(1.0)
    assert hyper["confidence"]["components"]["evidence_quality"] == pytest.approx(0.5025)
    assert hyper["confidence"]["value"] == pytest.approx(0.3 + 0.3 + 0.2 + 0.2 * 0.5025, abs=2e-4)
    # Hypo：match=ln2/ln3，input_cov=0.5，disease_cov=1，quality=(1.0 + 2/100)/2=0.51
    expected = (0.3 * math.log1p(1) / math.log1p(2) + 0.3 * 0.5 + 0.2 * 1.0 + 0.2 * 0.51)
    assert hypo["confidence"]["value"] == pytest.approx(expected, abs=2e-4)
    # 零匹配：value=0.0，组件如实（quality 无数据为 None）
    assert cancer["matched_count"] == 0
    assert cancer["confidence"]["value"] == 0.0
    assert cancer["confidence"]["components"]["evidence_quality"] is None


def test_confidence_excludes_null_disease_coverage():
    candidate = {"matched_genes": ["TP53"], "matched_count": 1, "input_coverage": 0.5,
                 "disease_coverage": None, "evidence": []}
    confidence = discovery._confidence(candidate, 2, {"TP53"}, set(), None)
    assert confidence["components"]["disease_coverage"] is None
    # 剔除后按剩余权重归一：0.3*ln2/ln3 + 0.3*0.5 + 0.2*1.0（known 占比）除以 0.8
    import math
    expected = (0.3 * math.log1p(1) / math.log1p(2) + 0.15 + 0.2) / 0.8
    assert confidence["value"] == pytest.approx(expected, abs=2e-4)
    assert 0.0 <= confidence["value"] <= 1.0


def test_confidence_known_evidence_outranks_predicted():
    base = {"matched_count": 1, "input_coverage": 0.5, "disease_coverage": 0.5, "evidence": []}
    known = discovery._confidence({**base, "matched_genes": ["AAA"]}, 2, {"AAA"}, set(), None)
    predicted = discovery._confidence({**base, "matched_genes": ["AAA"]}, 2, set(), {"AAA"}, None)
    assert known["components"]["evidence_quality"] == 1.0
    assert predicted["components"]["evidence_quality"] == 0.0
    assert known["value"] > predicted["value"]


def test_confidence_in_csv_and_report(database, tmp_path):
    result = discovery.query(database, genes=["TP53"])
    output = tmp_path / "run"
    discovery.save_result(result, output)
    header = (output / "candidates.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert "confidence" in header.split(",")
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "置信度" in report and "heuristic_v1" in report
    assert "非统计检验" in report


def test_confidence_on_generic_associations_index(assoc_batch, tmp_path):
    """通用批次无 BATMAN 表：known 组件剔除，relevance 组件用 genecards 分值。"""
    from pharm.diseases.associations import build_associations_database
    path = tmp_path / "generic.sqlite"
    build_associations_database(assoc_batch, path)
    result = discovery.query(path, genes=["TP53"])
    alpha, beta = result["candidates"]
    # 单输入：match=1、input_cov=1；Alpha disease_cov=1/2，quality=12.5/12.5=1.0
    assert alpha["confidence"]["value"] == pytest.approx(0.3 + 0.3 + 0.2 * 0.5 + 0.2 * 1.0, abs=2e-4)
    # Beta：disease_cov=1/1=1.0，quality=3/12.5=0.24
    assert beta["confidence"]["components"]["evidence_quality"] == pytest.approx(0.24)
    assert beta["confidence"]["value"] == pytest.approx(0.3 + 0.3 + 0.2 + 0.2 * 0.24, abs=2e-4)
