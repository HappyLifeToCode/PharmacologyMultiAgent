import pytest

from pharm_demo.common import read_json
from pharm_demo.string_local import string_local_network

TASK = {"taxon_id": 9606, "string_confidence": .9, "string_additional_nodes": 0,
        "string_version": "12.0", "string_source": "local_files"}

INFO = """#string_protein_id\tpreferred_name\tprotein_size\tannotation
9606.A\tTP53\t393\tTumor protein p53
9606.B\tMDM2\t491\tMDM2 proto-oncogene
9606.C\tEGFR\t1210\tEpidermal growth factor receptor
9606.D\tALB\t609\tAlbumin
9606.E\tAMBA\t100\tAmbiguous A
9606.F\tAMBB\t100\tAmbiguous B
"""
ALIASES = """#string_protein_id\talias\tsource
9606.A\tP53\tOther_Source
9606.E\tAMB\tSource_X
9606.F\tAMB\tSource_Y
9606.Z\tOUTSIDER\tSource_X
"""
LINKS = """protein1 protein2 neighborhood fusion cooccurence coexpression experimental database textmining combined_score
9606.A 9606.B 0 0 0 0 900 0 0 950
9606.A 9606.C 0 0 0 0 0 0 0 899
9606.B 9606.C 0 0 0 0 800 0 0 920
9606.A 9606.Z 0 0 0 0 999 0 0 999
9606.D 9606.Z 0 0 0 0 999 0 0 999
"""


@pytest.fixture
def data_dir(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "9606.protein.info.v12.0.txt").write_text(INFO, encoding="utf-8")
    (directory / "9606.protein.aliases.v12.0.txt").write_text(ALIASES, encoding="utf-8")
    (directory / "9606.protein.links.detailed.v12.0.txt").write_text(LINKS, encoding="utf-8")
    return directory


def local_task(data_dir, **changes):
    return {**TASK, "string_local_dir": str(data_dir), **changes}


def test_local_network_maps_filters_and_records(data_dir, tmp_path):
    out = tmp_path / "out"
    result = string_local_network(["TP53", "MDM2", "EGFR", "ALB", "AMB", "ZZZPHARMSMOKETEST"],
                                  local_task(data_dir), out)
    assert result["nodes"] == ["ALB", "EGFR", "MDM2", "TP53"]
    assert sorted(tuple(sorted((e["source"], e["target"]))) + (e["score"],) for e in result["edges"]) == [
        ("EGFR", "MDM2", .92), ("MDM2", "TP53", .95)]  # A-C at 899 and outsider edges excluded
    meta = result["provenance"]
    assert meta["unmapped"] == ["ZZZPHARMSMOKETEST"]
    assert meta["ambiguous"] == ["AMB"]
    assert meta["isolated"] == ["ALB"]
    assert meta["parameters"]["required_score"] == 900
    assert meta["parameters"]["add_nodes"] == 0
    files = read_json(out / "string_local_files.json")["files"]
    assert set(files) == {"info", "aliases", "links"}
    assert all(len(record["sha256"]) == 64 for record in files.values())
    records = {r["gene_symbol"]: r for r in read_json(out / "string_local_mapping.json")["records"]}
    assert records["TP53"]["matched_via"] == "preferred_name"
    assert records["AMB"]["status"] == "ambiguous" and records["AMB"]["candidates"] == ["9606.E", "9606.F"]
    raw = read_json(out / "string_local_network_raw.json")
    assert sorted((e["stringId_A"], e["stringId_B"], e["combined_score"]) for e in raw) == [
        ("9606.A", "9606.B", 950), ("9606.B", "9606.C", 920)]
    assert read_json(out / "string_provenance.json")["source"].startswith("STRING local files")


def test_alias_to_same_id_raises(data_dir, tmp_path):
    with pytest.raises(ValueError, match="同一 STRING ID"):
        string_local_network(["TP53", "P53"], local_task(data_dir), tmp_path / "out")


@pytest.mark.parametrize("changes", [{"string_confidence": float("nan")}, {"string_confidence": 1.5},
                                     {"string_additional_nodes": 1}, {"string_version": "11.5"},
                                     {"taxon_id": 10090}])
def test_invalid_settings_fail_before_scan(data_dir, tmp_path, changes):
    with pytest.raises(ValueError):
        string_local_network(["TP53"], local_task(data_dir, **changes), tmp_path / "out")


def test_missing_files_fail(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="本地 STRING 文件缺失"):
        string_local_network(["TP53"], local_task(empty), tmp_path / "out")


def test_threshold_is_inclusive_and_score_scaled(data_dir, tmp_path):
    links = (data_dir / "9606.protein.links.detailed.v12.0.txt")
    links.write_text(LINKS + "9606.C 9606.D 0 0 0 0 0 0 0 900\n", encoding="utf-8")
    result = string_local_network(["EGFR", "ALB"], local_task(data_dir), tmp_path / "out")
    assert result["edges"] == [{"source": "EGFR", "target": "ALB", "score": .9}]
