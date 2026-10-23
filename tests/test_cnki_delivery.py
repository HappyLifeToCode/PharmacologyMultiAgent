import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from pharm.literature import cnki, collector, dataset


def source_data(tmp_path):
    source = tmp_path / "old-project/data"
    directory = source / "cnki_温经汤"; directory.mkdir(parents=True)
    paper = {"rank": 1, "title": "温经汤治疗示例病", "abstract": "温经汤用于示例病患者研究。", "authors": "甲", "source": "示例期刊", "date": "2026-01-01", "is_modified": False, "url": "https://kns.cnki.net/one"}
    (directory / "papers.json").write_text(json.dumps({"keyword": "温经汤", "papers": [paper]}, ensure_ascii=False), encoding="utf-8")
    row = {"head": "温经汤", "head_type": "方剂", "tail": "示例病", "tail_type": "疾病", "relation": "主治", "count": 1,
           "sources": [{"rank": 1, "title": paper["title"], "url": paper["url"], "is_modified": False, "evidence": paper["abstract"]}]}
    (directory / "triples.jsonl").write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return source, paper


@pytest.fixture(autouse=True)
def independent_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("PHARM_CNKI_DATA_DIR", raising=False)
    monkeypatch.setenv("PHARM_CNKI_PROJECT_DIR", str(tmp_path / "does-not-exist"))


def test_import_is_readonly_idempotent_and_has_only_relative_manifest_paths(tmp_path):
    source, _ = source_data(tmp_path)
    before = {p.name: p.read_bytes() for p in (source / "cnki_温经汤").iterdir()}
    target = tmp_path / "project/data/cnki"
    first = dataset.import_data(source, target)
    manifest_bytes = (target / "manifest.json").read_bytes()
    assert dataset.import_data(source, target) == first
    assert (target / "manifest.json").read_bytes() == manifest_bytes
    assert before == {p.name: p.read_bytes() for p in (source / "cnki_温经汤").iterdir()}
    assert first["corpora"][0]["directory"] == "cnki_温经汤"
    assert str(tmp_path) not in json.dumps(first)


def test_managed_data_works_when_legacy_project_is_unavailable(tmp_path):
    source, _ = source_data(tmp_path)
    root = tmp_path / "project"
    dataset.import_data(source, root / "data/cnki")
    unavailable = tmp_path / "unavailable-legacy"
    assert source.parent.resolve().is_relative_to(tmp_path.resolve()) and unavailable.parent.resolve() == tmp_path.resolve()
    source.parent.rename(unavailable)
    result = cnki.analyze({"input": {"formula": "温经汤"}}, data_dir=cnki.data_directory(root), catalog=({}, {}))
    assert result["status"] == "ready" and result["candidates"][0]["raw_names"] == ["示例病"]
    assert str(root / "data/cnki") in result["source"]["directory"]


def test_package_can_be_restored_and_verifies_hashes_without_original_project(tmp_path):
    source, _ = source_data(tmp_path)
    target = tmp_path / "project/data/cnki"; dataset.import_data(source, target)
    output = tmp_path / "delivery.zip"
    summary = dataset.package(target, output)
    assert summary["corpora"] == summary["papers"] == 1
    restored = tmp_path / "other-machine/data/cnki"
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"manifest.json", "cnki_温经汤/papers.json", "cnki_温经汤/triples.jsonl"}
        assert all(not name.startswith(("/", "\\")) and ".." not in Path(name).parts for name in archive.namelist())
        archive.extractall(restored)
    assert dataset.manifest(restored)["corpora"] == dataset.manifest(target)["corpora"]


def test_conflict_and_tampering_are_not_silently_accepted(tmp_path):
    source, _ = source_data(tmp_path)
    target = tmp_path / "project/data/cnki"; dataset.import_data(source, target)
    data_path = source / "cnki_温经汤/papers.json"
    value = json.loads(data_path.read_text(encoding="utf-8")); value["papers"][0]["title"] += "新版"
    data_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="未覆盖"): dataset.import_data(source, target)
    target_path = target / "cnki_温经汤/papers.json"; target_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="校验失败"): dataset.manifest(target)


def test_manifest_rejects_traversal_and_unknown_files(tmp_path):
    source, _ = source_data(tmp_path)
    target = tmp_path / "project/data/cnki"; dataset.import_data(source, target)
    path = target / "manifest.json"; value = json.loads(path.read_text(encoding="utf-8"))
    value["corpora"][0]["directory"] = "../escape"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError): dataset.manifest(target)
    with pytest.raises(ValueError): dataset.safe_keyword("../escape")


def test_prepare_only_uses_owned_prompts_and_never_launches_a_model(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("no model during prepare"))
    task, work = collector.prepare("温经汤", 1, root=tmp_path)
    prompt = (work / "prompt.md").read_text(encoding="utf-8")
    assert "{{" not in prompt and str(work).replace("\\", "/") in prompt
    assert "D:/UI-Agent" not in prompt and "D:\\UI-Agent" not in prompt
    assert task["status"] == "prepared"
    config = json.dumps(collector.mcp_configuration(tmp_path))
    assert str(tmp_path / "configs/cnki_browser.json").replace("\\", "\\\\") in config


def fake_runner(work, *, corrupt_quote=False):
    def run(prompt, log):
        if log.name == "collection.log":
            paper = {"rank": 1, "title": "温经汤治疗示例病", "abstract": "温经汤用于示例病患者研究。", "is_modified": False, "url": "https://kns.cnki.net/example"}
            (work / "papers/paper_01.json").write_text(json.dumps(paper), encoding="utf-8")
            (work / "meta.json").write_text(json.dumps({"keyword": "温经汤"}), encoding="utf-8")
            return "采集完成"
        return json.dumps({"triples": [{"head": "温经汤", "head_type": "方剂", "relation": "主治", "tail": "示例病", "tail_type": "疾病", "evidence": "错误引用" if corrupt_quote else "温经汤用于示例病患者研究。"}]})
    return run


def test_simulated_collection_and_extraction_publish_a_valid_owned_dataset(tmp_path):
    task, work = collector.prepare("温经汤", 1, root=tmp_path)
    result = collector.execute(task, work, root=tmp_path, extract=True, runner=fake_runner(work))
    assert result["status"] == "succeeded"
    value = dataset.manifest(tmp_path / "data/cnki")
    assert value["corpora"][0]["papers"] == 1 and "triples.jsonl" in value["corpora"][0]["files"]
    report = cnki.analyze({"input": {"formula": "温经汤"}}, data_dir=tmp_path / "data/cnki", catalog=({}, {}))
    assert report["candidates"][0]["paper_count"] == 1
    assert not (tmp_path / "local/agent/cnki-collection/collector.lock").exists()


def test_partial_or_unverifiable_collection_does_not_publish(tmp_path):
    task, work = collector.prepare("温经汤", 1, root=tmp_path)
    with pytest.raises(RuntimeError, match="未发布"):
        collector.execute(task, work, root=tmp_path, runner=lambda *a: "没有采到")
    assert not (tmp_path / "data/cnki/manifest.json").exists()
    with pytest.raises(ValueError, match="原句"):
        collector.execute(task, work, root=tmp_path, extract=True, runner=fake_runner(work, corrupt_quote=True))
    assert not (tmp_path / "data/cnki/manifest.json").exists()


def test_existing_stock_cannot_count_as_new_or_be_rewritten(tmp_path):
    source, _ = source_data(tmp_path)
    dataset.import_data(source, tmp_path / "data/cnki")
    task, work = collector.prepare("温经汤", 1, root=tmp_path)
    before = (tmp_path / "data/cnki/cnki_温经汤/papers.json").read_bytes()
    with pytest.raises(RuntimeError, match="新增0篇"):
        collector.execute(task, work, root=tmp_path, runner=lambda *a: "没有新增")
    assert before == (tmp_path / "data/cnki/cnki_温经汤/papers.json").read_bytes()


def test_collector_lock_is_exclusive_and_failed_contender_does_not_remove_it(tmp_path):
    with collector.collector_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with collector.collector_lock(tmp_path): pass
        assert (tmp_path / "local/agent/cnki-collection/collector.lock").exists()
    assert not (tmp_path / "local/agent/cnki-collection/collector.lock").exists()


def test_collection_cannot_replace_existing_paper_in_staging(tmp_path):
    source, _ = source_data(tmp_path)
    target = tmp_path / "data/cnki"; dataset.import_data(source, target)
    task, work = collector.prepare("温经汤", 1, root=tmp_path)
    original = (target / "cnki_温经汤/papers.json").read_bytes()
    def corrupt(prompt, log):
        p = json.loads((work / "papers/paper_0001.json").read_text(encoding="utf-8"))
        p["title"] = "被替换的旧论文"
        (work / "papers/paper_0001.json").write_text(json.dumps(p), encoding="utf-8")
        return ""
    with pytest.raises(ValueError, match="既有论文被改动"):
        collector.execute(task, work, root=tmp_path, runner=corrupt)
    assert original == (target / "cnki_温经汤/papers.json").read_bytes()


def test_model_runner_timeout_stops_its_own_process(tmp_path, monkeypatch):
    real = subprocess.Popen
    processes = []
    def tracked(*args, **kwargs):
        process = real(*args, **kwargs); processes.append(process); return process
    monkeypatch.setattr(subprocess, "Popen", tracked)
    with pytest.raises(subprocess.TimeoutExpired):
        collector.run_model("test", tmp_path / "model.log", root=tmp_path, timeout=.15,
                            command=[sys.executable, "-c", "import time;time.sleep(10)"])
    assert processes and processes[0].poll() is not None
