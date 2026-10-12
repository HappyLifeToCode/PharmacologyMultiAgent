"""Monarch 证据补充模块测试（全部使用合成数据，非真实研究数据）。"""
import json

import pytest

from pharm.core.common import write_json
from pharm.discovery import query as discovery
from pharm.diseases import monarch


def write_csv(path, columns, rows):
    import csv
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def monarch_dir(tmp_path):
    directory = tmp_path / "monarch_data"
    directory.mkdir()
    (directory / "metadata.yaml").write_text(
        "id: monarch-kg\nversion: '2099-01-01'\ngenerated_at: '2099-01-01T00:00:00Z'\n", encoding="utf-8")
    (directory / "hgnc_gene_nodes.tsv").write_text(
        "id\tcategory\tname\txref\tprovided_by\tsynonym\tfull_name\tin_taxon\tin_taxon_label\tsymbol\ttype\n"
        "HGNC:1\tbiolink:Gene\tTP53\tENSEMBL:ENSG00000141510|OMIM:119191\tinfores:hgnc\t\t\ttumor protein p53\tNCBITaxon:9606\tHomo sapiens\tTP53\tSO:0001217\n",
        encoding="utf-8")
    (directory / "phenio_nodes.tsv").write_text(
        "id\tcategory\tname\n"
        "NCBIGene:7157\tbiolink:Gene\tTP53\n"
        "MONDO:0007254\tbiolink:Disease\tbreast cancer\n"
        "HP:0002664\tbiolink:PhenotypicFeature\tNeoplasm\n"
        "HP:0000006\tbiolink:PhenotypicFeature\tAutosomal dominant inheritance\n",
        encoding="utf-8")
    (directory / "mondo.sssom.tsv").write_text(
        "# comment\nsubject_id\tsubject_label\tpredicate_id\tobject_id\tobject_label\tmapping_justification\n"
        "MONDO:0007254\tbreast cancer\tskos:exactMatch\tOMIM:114480\tLi-Fraumeni syndrome\tsemapv:UnspecifiedMatching\n",
        encoding="utf-8")
    (directory / "omim_gene_to_disease_edges.tsv").write_text(
        "id\tsubject\tpredicate\tobject\tcategory\tagent_type\taggregator_knowledge_source\tknowledge_level\tobject_category\tprimary_knowledge_source\tsubject_category\n"
        "uuid:1\tOMIM:119191\tbiolink:causes\tOMIM:114480\tbiolink:CausalGeneToDiseaseAssociation\tnot_provided\tinfores:monarchinitiative\tknowledge_assertion\t\tinfores:omim\t\n",
        encoding="utf-8")
    (directory / "hpoa_gene_to_disease_edges.tsv").write_text(
        "id\tsubject\tpredicate\tobject\tcategory\tagent_type\taggregator_knowledge_source\tknowledge_level\tprimary_knowledge_source\n"
        "uuid:2\tNCBIGene:7157\tbiolink:gene_associated_with_condition\tOrphanet:9\tbiolink:CorrelatedGeneToDiseaseAssociation\tmanual_agent\tinfores:monarchinitiative\tknowledge_assertion\tinfores:orphanet\n",
        encoding="utf-8")
    (directory / "clingen_gene_disease_edges.tsv").write_text(
        "id\tsubject\tpredicate\tobject\tcategory\tagent_type\taggregator_knowledge_source\tknowledge_level\toriginal_predicate\tprimary_knowledge_source\n"
        "uuid:3\tHGNC:1\tbiolink:causes\tMONDO:0800406\tbiolink:CausalGeneToDiseaseAssociation\tmanual_agent\tinfores:monarchinitiative\tknowledge_assertion\tPathogenic\tinfores:clingen\n",
        encoding="utf-8")
    (directory / "hpoa_disease_to_phenotype_edges.tsv").write_text(
        "id\tsubject\tpredicate\tobject\tcategory\tagent_type\taggregator_knowledge_source\tfrequency_qualifier\thas_count\thas_evidence\thas_percentage\thas_quotient\thas_total\tknowledge_level\tnegated\tonset_qualifier\tprimary_knowledge_source\tpublications\tsex_qualifier\n"
        "uuid:4\tOMIM:114480\tbiolink:has_phenotype\tHP:0002664\tx\tmanual_agent\tinfores:monarchinitiative\t\t1\tECO:0006017\t50.0\t0.5\t2\tknowledge_assertion\tFalse\t\tinfores:omim\tPMID:111|PMID:222\t\n"
        "uuid:5\tOMIM:114480\tbiolink:has_phenotype\tHP:0002664\tx\tmanual_agent\tinfores:monarchinitiative\t\t1\tECO:0006017\t\t\t\tknowledge_assertion\tTrue\t\tinfores:omim\t\t\n",
        encoding="utf-8")
    (directory / "hpoa_disease_mode_of_inheritance_edges.tsv").write_text(
        "id\tsubject\tpredicate\tobject\tcategory\tagent_type\taggregator_knowledge_source\thas_evidence\tknowledge_level\tprimary_knowledge_source\tpublications\n"
        "uuid:6\tOMIM:114480\tbiolink:has_mode_of_inheritance\tHP:0000006\tx\tmanual_agent\tinfores:monarchinitiative\tECO:0006017\tknowledge_assertion\tinfores:hpo-annotations\tPMID:333\n",
        encoding="utf-8")
    return directory


@pytest.fixture
def ot_database(tmp_path):
    """合成 Open Targets 批次：breast cancer 候选带 mondo_id，Rare X 无 MONDO。"""
    directory = tmp_path / "batch"
    directory.mkdir()
    (directory / "raw.txt").write_text("Synthetic fixture, not research data", encoding="utf-8")
    diseases = ["MONDO_0007254 | breast cancer", "Orphanet:9 | rare X"]
    source = {"complete": True, "source_url": "https://example.org/data",
              "accessed_at": "2026-10-01", "raw_files": ["raw.txt"]}
    write_json(directory / "provenance.json", {
        "sources": {"associations": dict(source, diseases=diseases)},
        "mapping": {"confirmed": True, "method": "synthetic exact symbol", "version": "fixture",
                    "raw_files": ["raw.txt"]},
    })
    extra_bc = json.dumps({"disease_id": "MONDO_0007254", "disease_name": "breast cancer",
                           "mondo_id": "MONDO_0007254", "mondo_mapping": "direct",
                           "ontology": "MONDO", "entity_type": "disease", "association_score": 0.9,
                           "aliases": [], "parents": [], "ancestors": []}, ensure_ascii=False)
    extra_rx = json.dumps({"disease_id": "Orphanet:9", "disease_name": "rare X",
                           "mondo_id": None, "mondo_mapping": "unmapped",
                           "ontology": "ORPHANET", "entity_type": "disease", "association_score": 0.7,
                           "aliases": [], "parents": [], "ancestors": []}, ensure_ascii=False)
    write_csv(directory / "associations.csv", ["disease", "gene_symbol", "score", "source", "extra"], [
        {"disease": diseases[0], "gene_symbol": "TP53", "score": 0.9, "source": "open_targets", "extra": extra_bc},
        {"disease": diseases[1], "gene_symbol": "TP53", "score": 0.7, "source": "open_targets", "extra": extra_rx},
    ])
    database = tmp_path / "index.sqlite"
    discovery.build_database(directory, database)
    return database


def _enable(monkeypatch, monarch_dir):
    monkeypatch.setenv("PHARM_MONARCH_ENABLED", "true")
    monkeypatch.setenv("PHARM_MONARCH_DATA_DIR", str(monarch_dir))


def test_disabled_by_default(monkeypatch, ot_database):
    monkeypatch.delenv("PHARM_MONARCH_ENABLED", raising=False)
    result = discovery.query(ot_database, genes=["TP53"])
    assert "monarch_evidence" not in result


def test_enrichment_matches_by_mondo_and_keeps_unmapped(monkeypatch, monarch_dir, ot_database):
    _enable(monkeypatch, monarch_dir)
    result = discovery.query(ot_database, genes=["TP53", "ZZZTEST"])
    evidence = result["monarch_evidence"]
    assert evidence["source"] == "monarch" and evidence["release"] == "2099-01-01"
    assert evidence["gene_disease_count"] == 2  # OMIM 因果边 + Orphanet 相关边（ClinGen 疾病不在 Top 候选中）
    rows = evidence["tables"]["gene_disease"]
    omim_row = next(r for r in rows if r["disease_id"] == "OMIM:114480")
    assert omim_row["mondo_id"] == "MONDO_0007254"
    assert omim_row["candidate_disease_id"] == "MONDO_0007254"
    assert omim_row["gene_symbol"] == "TP53" and omim_row["predicate"] == "biolink:causes"
    orphanet_row = next(r for r in rows if r["disease_id"] == "Orphanet:9")
    assert orphanet_row["mondo_id"] == ""  # 无 MONDO 映射的记录保留且留空
    assert evidence["unmatched_mondo_count"] == 1
    assert {g["gene_symbol"] for g in evidence["unresolved_genes"]} == {"ZZZTEST"}
    # HPO 多行保留（含 negated=True 行与多条 PMID）
    hpo_rows = evidence["tables"]["hpo"]
    assert len(hpo_rows) == 2
    assert {r["negated"] for r in hpo_rows} == {"False", "True"}
    assert any(r["publication_id"] == "PMID:111|PMID:222" for r in hpo_rows)
    assert any(r["frequency"] == "50.0" for r in hpo_rows)
    # 遗传方式保留
    assert evidence["tables"]["inheritance"][0]["inheritance_name"] == "Autosomal dominant inheritance"
    assert evidence["tables"]["inheritance"][0]["publication_id"] == "PMID:333"
    # 文献独立成行
    assert evidence["publication_count"] == 3  # PMID:111/222/333


def test_enrichment_does_not_change_open_targets(monkeypatch, monarch_dir, ot_database):
    before = discovery.query(ot_database, genes=["TP53"])
    _enable(monkeypatch, monarch_dir)
    after = discovery.query(ot_database, genes=["TP53"])
    assert before["candidates"] == after["candidates"]
    top_before = [(c["disease_id"], c["max_association_score"]) for c in before["candidate_layers"]["top"]]
    top_after = [(c["disease_id"], c["max_association_score"]) for c in after["candidate_layers"]["top"]]
    assert top_before == top_after
    assert before["candidate_layers"]["hierarchy"] == after["candidate_layers"]["hierarchy"]


def test_save_result_writes_monarch_artifacts(monkeypatch, monarch_dir, ot_database, tmp_path):
    _enable(monkeypatch, monarch_dir)
    result = discovery.query(ot_database, genes=["TP53"])
    out = tmp_path / "run"
    discovery.save_result(result, out)
    for name in monarch.MONARCH_ARTIFACTS:
        assert (out / name).is_file(), name
    hpo = (out / "monarch_hpo.csv").read_text(encoding="utf-8-sig")
    assert hpo.count("\n") == 3  # 表头 + 2 行（每条 HPO 单独一行）
    provenance = json.loads((out / "monarch_provenance.json").read_text(encoding="utf-8"))
    assert provenance["data_version"] == "2099-01-01"
    assert provenance["row_counts"]["gene_disease"] == 2
    assert "sha256" in provenance["official_artifacts"]["disease_to_phenotype"]


def test_missing_data_dir_fails_loudly(monkeypatch, ot_database, tmp_path):
    monkeypatch.setenv("PHARM_MONARCH_ENABLED", "true")
    monkeypatch.setenv("PHARM_MONARCH_DATA_DIR", str(tmp_path / "nowhere"))
    with pytest.raises(ValueError, match="缺失"):
        discovery.query(ot_database, genes=["TP53"])
