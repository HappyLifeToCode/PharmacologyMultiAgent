import gzip

import pytest

from pharm_demo.common import read_json
from pharm_demo.string_local import string_local_network, string_local_signature

TASK = {"taxon_id": 9606, "string_confidence": .9, "string_additional_nodes": 0,
        "string_version": "12.0"}

INFO = """#string_protein_id\tpreferred_name\tprotein_size\tannotation
9606.ENSP00000000001\tTP53\t393\tTumor protein p53
9606.ENSP00000000002\tMDM2\t491\tMDM2 proto-oncogene
9606.ENSP00000000003\tEGFR\t1210\tEpidermal growth factor receptor
9606.ENSP00000000004\tALB\t609\tAlbumin
9606.ENSP00000000005\tAMBA\t100\tAmbiguous A
9606.ENSP00000000006\tAMBB\t100\tAmbiguous B
"""
ALIASES = """#string_protein_id\talias\tsource
9606.ENSP00000000001\tP53\tOther_Source
9606.ENSP00000000005\tAMB\tSource_X
9606.ENSP00000000006\tAMB\tSource_Y
9606.ENSP00000000099\tOUTSIDER\tSource_X
"""
LINKS = """protein1 protein2 neighborhood fusion cooccurence coexpression experimental database textmining combined_score
9606.ENSP00000000001 9606.ENSP00000000002 0 0 0 0 900 0 0 950
9606.ENSP00000000001 9606.ENSP00000000003 0 0 0 0 0 0 0 899
9606.ENSP00000000002 9606.ENSP00000000003 0 0 0 0 800 0 0 920
9606.ENSP00000000001 9606.ENSP00000000099 0 0 0 0 999 0 0 999
9606.ENSP00000000004 9606.ENSP00000000099 0 0 0 0 999 0 0 999
"""


@pytest.fixture(params=["txt", "gz"])
def data_dir(tmp_path, request):
    directory = tmp_path / "data"
    directory.mkdir()
    for stem, text in (("9606.protein.info.v12.0.txt", INFO),
                       ("9606.protein.aliases.v12.0.txt", ALIASES),
                       ("9606.protein.links.detailed.v12.0.txt", LINKS)):
        if request.param == "gz":
            with gzip.open(directory / (stem + ".gz"), "wt", encoding="utf-8") as stream:
                stream.write(text)
        else:
            (directory / stem).write_text(text, encoding="utf-8")
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
    assert meta["source"] == "STRING local download"
    assert meta["unmapped"] == ["ZZZPHARMSMOKETEST"]
    assert meta["ambiguous"] == ["AMB"]
    assert meta["isolated"] == ["ALB"]
    assert meta["parameters"] == {"species": 9606, "required_score": 900, "add_nodes": 0}
    files = meta["files"]
    assert set(files) == {"info", "aliases", "links"}
    assert all(len(record["sha256"]) == 64 for record in files.values())
    assert read_json(out / "string_version_raw.json")["string_version"] == "12.0"
    records = {r["query"]: r for r in read_json(out / "string_mapping_raw.json")["records"]}
    assert records["TP53"] == {"query": "TP53", "status": "mapped", "string_id": "9606.ENSP00000000001",
                               "method": "preferred_name"}
    assert records["AMB"]["status"] == "ambiguous" and records["AMB"]["candidates"] == ["9606.ENSP00000000005", "9606.ENSP00000000006"]
    raw = read_json(out / "string_network_raw.json")
    assert [(r["protein1"], r["protein2"], r["combined_score"]) for r in raw] == [
        ("9606.ENSP00000000001", "9606.ENSP00000000002", 950), ("9606.ENSP00000000002", "9606.ENSP00000000003", 920)]
    assert raw[0]["experimental"] == 900  # 各证据通道分值保留在原始边表
    assert read_json(out / "string_provenance.json")["mapping_method"].startswith("exact match")


def test_preferred_name_wins_without_alias_ambiguity(data_dir, tmp_path):
    aliases_txt = data_dir / "9606.protein.aliases.v12.0.txt"
    if not aliases_txt.exists():
        pytest.skip("gz variant covered by txt case")
    with aliases_txt.open("a", encoding="utf-8") as stream:
        stream.write("9606.ENSP00000000098\tTP53\tDubious_Source\n")  # 唯一 preferred_name 时不再查别名
    result = string_local_network(["TP53"], local_task(data_dir), tmp_path / "out")
    assert result["nodes"] == ["TP53"]
    assert result["provenance"]["ambiguous"] == []


def test_alias_to_same_id_raises(data_dir, tmp_path):
    with pytest.raises(ValueError, match="同一 STRING ID"):
        string_local_network(["TP53", "P53"], local_task(data_dir), tmp_path / "out")


@pytest.mark.parametrize("changes", [{"string_confidence": float("nan")}, {"string_confidence": 1.5},
                                     {"string_additional_nodes": 1}, {"string_version": "11.5"},
                                     {"taxon_id": 10090}])
def test_invalid_settings_fail_before_scan(data_dir, tmp_path, changes):
    with pytest.raises(ValueError):
        string_local_network(["TP53"], local_task(data_dir, **changes), tmp_path / "out")


def test_broken_explicit_directory_fails_loudly(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="本地 STRING 文件缺失"):
        string_local_network(["TP53"], local_task(empty), tmp_path / "out")


def test_explicit_local_source_without_files_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("pharm_demo.string_local.ROOT", tmp_path)
    with pytest.raises(ValueError, match="任务要求本地 STRING 来源"):
        string_local_network(["TP53"], {**TASK, "string_source": "local_files"}, tmp_path / "out")


def test_header_mismatch_fails(data_dir, tmp_path):
    target = data_dir / "9606.protein.info.v12.0.txt"
    if not target.exists():
        pytest.skip("gz variant covered by txt case")
    target.write_text("wrong\theader\n", encoding="utf-8")
    with pytest.raises(ValueError, match="表头"):
        string_local_network(["TP53"], local_task(data_dir), tmp_path / "out")


def test_signature_tracks_local_files(data_dir):
    assert string_local_signature({**TASK, "string_source": "api"}) is None
    signature = string_local_signature(local_task(data_dir))
    assert set(signature) == {"info", "aliases", "links"}
    assert all(len(record["sha256"]) == 64 for record in signature.values())
    assert string_local_signature({**local_task(data_dir), "string_source": "api"}) is None


def test_threshold_is_inclusive_and_score_scaled(data_dir, tmp_path):
    links = data_dir / "9606.protein.links.detailed.v12.0.txt"
    if not links.exists():
        pytest.skip("gz variant covered by txt case")
    links.write_text(LINKS + "9606.ENSP00000000003 9606.ENSP00000000004 0 0 0 0 0 0 0 900\n", encoding="utf-8")
    result = string_local_network(["EGFR", "ALB"], local_task(data_dir), tmp_path / "out")
    assert result["edges"] == [{"source": "EGFR", "target": "ALB", "score": .9}]
