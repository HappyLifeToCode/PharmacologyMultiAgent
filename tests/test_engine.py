import gzip
import json

import pytest

from pharm.core import common
from pharm.batman import local as batman_local
from pharm.pipeline import engine
from pharm.pipeline.tasks import save_task


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "runtime.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(common, "ROOT", tmp_path)
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    monkeypatch.setattr(batman_local, "ROOT", tmp_path)
    return tmp_path


def run_task(root, body):
    task, _ = save_task(body, root)
    run_id = engine.start(task["task_id"], background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    return run_id, manifest


def _write_gz(path, text):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(text)


def _batman_data_dir(tmp_path):
    data = tmp_path / "batman_data"
    data.mkdir(parents=True)
    (data / "herb_browse.txt").write_text(
        "Pinyin.Name\tChinese.Name\tEnglish.Name\tLatin.Name\tIngredients\n"
        "BAI SHAO\t白芍\tCommon Peony\tPaeonia Albiflora\tcompoundA(1001)\n",
        encoding="utf-8")
    _write_gz(data / "known_browse_by_ingredients.txt.gz",
              "PubChem_CID\tIUPAC_name\tknown_target_proteins\n"
              "1001\tnameA\tTP53|EGFR\n")
    _write_gz(data / "known_browse_by_targets.txt.gz",
              "entrez_gene_id\tentrez_gene_symbol\tPubChem_CIDs\n"
              "7157\tTP53\t1001\n1956\tEGFR\t1001\n")
    return data


def test_fixture_end_to_end(root):
    run_id, manifest = run_task(root, {"formula": "桃核承气汤", "mode": "fixture"})
    assert manifest["status"] == "succeeded"
    assert manifest["scientific_complete"] is False
    for role in engine.scheduler.stages_for("discovery"):
        assert manifest["stages"][role]["status"] == "succeeded"
    assert manifest["metrics"]["unique_targets"] == 6
    assert manifest["metrics"]["candidate_diseases"] == 5
    run = root / "runs" / run_id
    result = json.loads((run / manifest["verified_targets"]["disease_reverse"]).read_text(encoding="utf-8"))
    assert result["evidence_type"] == "synthetic_engineering"
    assert result["matched_input_count"] == 6 and result["input_count"] == 6
    for candidate in result["candidates"]:
        assert candidate["confidence"]["formula_version"] == "heuristic_v1"
        assert 0.0 <= candidate["confidence"]["value"] <= 1.0
    assert 0.0 < manifest["metrics"]["max_confidence"] <= 1.0
    report = (run / "report.md").read_text(encoding="utf-8")
    assert "置信度" in report and "heuristic_v1" in report
    assert "scientific_complete：false" in report and "关联≠疗效" in report or "局限" in report
    assert manifest["report"] == "report.md"
    # fixture 不进归档
    assert not (root / "data" / "pharm").exists()


def test_live_blocked_without_local_data(root):
    run_id, manifest = run_task(root, {"herbs": ["白芍"], "agents": False})
    assert manifest["status"] == "partial"
    assert manifest["stages"]["preflight"]["status"] == "succeeded"
    availability = json.loads((root / "runs" / run_id / "preflight/attempt_01/availability.json").read_text(encoding="utf-8"))
    assert availability["batman"]["available"] is False
    assert availability["discovery_index"]["available"] is False
    herb = manifest["stages"]["herb_targets"]
    assert herb["status"] == "blocked"
    assert any("BATMAN" in blocker for blocker in herb["blockers"])
    assert manifest["stages"]["disease_reverse"]["status"] == "blocked"
    # 没有靶点产物顶替
    assert not (root / "runs" / run_id / "herb_targets/attempt_01/targets.json").exists()
    # blocked 携带人机协助升级点标记与事件
    handoff = json.loads((root / "runs" / run_id / "herb_targets/attempt_01/handoff.json").read_text(encoding="utf-8"))
    assert handoff["assist"]["available"] is True
    assert "协助会话" in handoff["assist"]["guidance"]
    events = (root / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8")
    assert "assist_requested" in events
    assert manifest["stages"]["review"]["status"] == "succeeded"
    assert (root / "runs" / run_id / "report.md").is_file()
    # live 的 blocked 证据也归档
    task_id = manifest["task"]["task_id"]
    assert (root / "data" / "pharm" / ("custom_" + task_id) / "02_herb").is_dir()


def test_resume_reuses_succeeded_stages(root):
    run_id, _ = run_task(root, {"formula": "济川煎", "mode": "fixture"})
    engine.start(resume=run_id, background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["status"] == "succeeded"
    for role in ("preflight", "herb_targets", "disease_reverse"):
        assert manifest["stages"][role]["attempt"] == 1
    # review 每次重新核对
    assert manifest["stages"]["review"]["attempt"] == 2
    events = (root / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8")
    assert "stage.reused" in events


def test_review_detects_tampered_artifact(root):
    run_id, manifest = run_task(root, {"formula": "温经汤", "mode": "fixture"})
    path = root / "runs" / run_id / manifest["verified_targets"]["disease_reverse"]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["input_count"] = 999
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    problems = engine.Runner(run_id)._verify()
    assert any("哈希不一致" in problem for problem in problems)


def test_live_herb_unmatched_recorded(root, tmp_path):
    data = _batman_data_dir(tmp_path)
    task = {"task_id": "web_testbatman", "formula": None, "herbs": ["白芍", "阿胶"],
            "research_notes": "", "batman_threshold": 0.84, "mode": "live", "agents": False,
            "batman_local_dir": str(data), "batman_accessed_at": "2026-09-17"}
    (root / "tasks").mkdir()
    (root / "tasks" / "tasks.local.jsonl").write_text(json.dumps(task, ensure_ascii=False) + "\n", encoding="utf-8")
    run_id = engine.start("web_testbatman", background=False)
    manifest = common.read_json(root / "runs" / run_id / "manifest.json")
    assert manifest["stages"]["herb_targets"]["status"] == "succeeded"
    targets = json.loads((root / "runs" / run_id / manifest["verified_targets"]["herb_targets"]).read_text(encoding="utf-8"))
    assert targets["genes"] == ["EGFR", "TP53"]
    assert targets["unmatched_herbs"] == ["阿胶"]  # 动物药查不到如实记录，不报错
    assert targets["provenance"]["include_predicted"] is False
    assert targets["provenance"]["accessed_at"] == "2026-09-17"
    assert manifest["stages"]["disease_reverse"]["status"] == "blocked"
    assert manifest["status"] == "partial"


def test_reverse_lookup_chunks_over_query_limit(root, tmp_path):
    database = tmp_path / "idx.sqlite"
    engine._write_fixture_index(database)
    merged, chunking = engine._reverse_lookup(database, ["G%d" % i for i in range(6500)])
    assert chunking == {"chunked": True, "chunk_size": 3000, "chunks": 3}
    assert merged["input_count"] == 6500
    assert merged["matched_input_count"] == 0
    assert len(merged["unmatched_genes"]) == 6500
    assert len(merged["candidates"]) == 5
    # 分块合并后按合并结果重算置信度
    assert all("confidence" in candidate for candidate in merged["candidates"])
    assert all(candidate["confidence"]["value"] == 0.0 for candidate in merged["candidates"])
    result, chunking = engine._reverse_lookup(database, ["TP53"])
    assert chunking == {"chunked": False}
    assert result["matched_input_count"] == 1


def test_fixture_reverse_uses_synthetic_index(root):
    _, manifest = run_task(root, {"herbs": ["白芍", "甘草"], "mode": "fixture"})
    result = json.loads((root / "runs" / _ / manifest["verified_targets"]["disease_reverse"]).read_text(encoding="utf-8"))
    assert result["dataset"]["batch_name"] == "synthetic_engineering"
    diseases = {candidate["disease"] for candidate in result["candidates"] if candidate["matched_count"]}
    # 两味药覆盖 TP53/EGFR/AKT1/TNF，其余疾病无命中如实保留
    assert diseases == {"Hyperthyroidism", "Hypothyroidism", "Thyroid cancer", "Thyroid nodules"}
    assert result["unmatched_genes"] == []
