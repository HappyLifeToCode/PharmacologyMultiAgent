import gzip
import json

import pytest

from pharm_demo import sources
from pharm_demo.sources import string_network


def write_gzip(path, text):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        stream.write(text)


def local_string_files(tmp_path):
    write_gzip(tmp_path / "9606.protein.info.v12.0.txt.gz", """#string_protein_id\tpreferred_name\tprotein_size\tannotation
9606.ENSP_A\tGENEA\t100\tA protein
9606.ENSP_B\tGENEB\t200\tB protein
9606.ENSP_C\tOTHER\t300\tC protein
""")
    write_gzip(tmp_path / "9606.protein.aliases.v12.0.txt.gz", """#string_protein_id\talias\tsource
9606.ENSP_C\tGENEC\tEnsembl_HGNC_symbol
""")
    write_gzip(tmp_path / "9606.protein.links.detailed.v12.0.txt.gz", """protein1 protein2 neighborhood fusion cooccurence coexpression experimental database textmining combined_score
9606.ENSP_A 9606.ENSP_B 0 0 0 0 900 0 0 950
9606.ENSP_B 9606.ENSP_A 0 0 0 0 900 0 0 950
9606.ENSP_A 9606.ENSP_C 0 0 0 0 700 0 0 899
9606.ENSP_B 9606.ENSP_C 0 0 0 0 800 0 0 900
""")


def test_local_string_mapping_threshold_dedup_and_provenance(tmp_path, monkeypatch):
    local_string_files(tmp_path)
    monkeypatch.setenv("PHARM_STRING_DATA_DIR", str(tmp_path))
    output = tmp_path / "output"
    result = string_network(
        ["GENEA", "GENEB", "GENEC", "MISSING"],
        {"taxon_id": 9606, "string_version": "12.0", "string_confidence": 0.9, "string_additional_nodes": 0},
        output,
    )
    assert result["nodes"] == ["GENEA", "GENEB", "GENEC"]
    assert result["edges"] == [
        {"source": "GENEA", "target": "GENEB", "score": 0.95},
        {"source": "GENEB", "target": "GENEC", "score": 0.9},
    ]
    provenance = json.loads((output / "string_provenance.json").read_text(encoding="utf-8"))
    assert provenance["source"] == "STRING local download"
    assert provenance["version"] == "12.0"
    assert provenance["unmapped"] == ["MISSING"]
    assert provenance["parameters"]["required_score"] == 900
    assert all(record["sha256"] for record in provenance["files"].values())
    assert all("path" not in record and record["filename"].endswith(".gz") for record in provenance["files"].values())
    assert len(json.loads((output / "string_network_raw.json").read_text(encoding="utf-8"))) == 2


def test_local_string_rejects_additional_nodes_and_missing_files(tmp_path, monkeypatch):
    local_string_files(tmp_path)
    monkeypatch.setenv("PHARM_STRING_DATA_DIR", str(tmp_path))
    task = {"taxon_id": 9606, "string_version": "12.0", "string_confidence": 0.9, "string_additional_nodes": 1}
    with pytest.raises(ValueError, match="additional_nodes"):
        string_network(["GENEA"], task, tmp_path / "output")
    (tmp_path / "9606.protein.info.v12.0.txt.gz").unlink()
    with pytest.raises(ValueError, match="文件缺失"):
        string_network(["GENEA"], {**task, "string_additional_nodes": 0}, tmp_path / "output2")


def test_team_local_config_accepts_project_relative_path(tmp_path, monkeypatch):
    data = tmp_path / "data/string/v12.0"
    data.mkdir(parents=True)
    local_string_files(data)
    config = tmp_path / "configs/string_data.local.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"data_dir": "data/string/v12.0"}), encoding="utf-8")
    monkeypatch.delenv("PHARM_STRING_DATA_DIR", raising=False)
    monkeypatch.setattr(sources, "ROOT", tmp_path)
    result = string_network(
        ["GENEA", "GENEB"],
        {"taxon_id": 9606, "string_version": "12.0", "string_confidence": 0.9, "string_additional_nodes": 0},
        tmp_path / "output",
    )
    assert result["provenance"]["source"] == "STRING local download"
    assert len(result["edges"]) == 1
