import json
from pathlib import Path
import subprocess
import sys
import threading
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


def full_scan(work, count=23):
    papers = []
    for rank in range(1, count + 1):
        paper = {"rank": rank, "title": f"温经汤标题{rank}", "abstract": f"温经汤摘要{rank}",
                 "url": f"https://kns.cnki.net/paper/{rank}", "is_modified": False}
        (work / f"papers/paper_{rank:04d}.json").write_text(json.dumps(paper), encoding="utf-8")
        papers.append(paper)
    pages = []
    for offset in range(0, max(1, count), 20):
        pages.append({"number": len(pages) + 1, "total_results": count, "has_next": offset + 20 < count,
                      "items": [{"position": p["rank"], "rank": p["rank"], "title": p["title"], "url": p["url"], "status": "collected"}
                                for p in papers[offset:offset + 20]]})
    scan = {"keyword": "温经汤", "total_results": count, "complete": True, "stop_reason": "exhausted", "pages": pages}
    (work / "collection_progress.json").write_text(json.dumps(scan), encoding="utf-8")
    (work / "meta.json").write_text(json.dumps({"keyword": "温经汤", "total_results": count}), encoding="utf-8")
    return scan


def test_default_is_codex_luna_and_all_keyword_results_without_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("prepare is offline"))
    task, work = collector.prepare("白芍", root=tmp_path)
    assert task["limit"] is None and task["collection_mode"] == "all"
    assert task["engine"] == "codex_cli" and task["model"] == "gpt-5.6-luna"
    text = (work / "prompt.md").read_text(encoding="utf-8")
    assert "{{" not in text and "不设篇数上限" in text and "不获取论文正文" in text
    assert "collection_progress.json" in text
    assert collector.prepare("白芍", 125, root=tmp_path)[0]["limit"] == 125
    with pytest.raises(ValueError): collector.prepare("白芍", True, root=tmp_path)


@pytest.mark.parametrize("count", [0, 23, 125])
def test_full_keyword_scan_publishes_all_pages_without_a_20_or_100_cap(tmp_path, count):
    task, work = collector.prepare("温经汤", root=tmp_path)
    def run(prompt, log):
        full_scan(work, count)
        return "已遍历"
    result = collector.execute(task, work, root=tmp_path, runner=run)
    assert result["status"] == "succeeded" and result["added"] == count
    entry, contents = dataset.read_corpus(tmp_path / "data/cnki", "温经汤")
    meta = json.loads(contents["papers.json"])
    assert entry["papers"] == count and len(meta["papers"]) == count
    assert meta["collection"]["mode"] == "all" and meta["collection"]["complete"] is True
    assert meta["collection_scan"]["total_results"] == count
    assert meta["collector"]["model"] == "gpt-5.6-luna"


@pytest.mark.parametrize("fault", ["missing_page", "still_has_next", "failed_paper", "changing_total", "wrong_position", "wrong_title", "no_progress", "unexplained_missing_abstract"])
def test_full_scan_does_not_publish_a_claimed_but_incomplete_result(tmp_path, fault):
    task, work = collector.prepare("温经汤", root=tmp_path)
    def run(prompt, log):
        scan = full_scan(work)
        if fault == "missing_page": scan["pages"].pop()
        elif fault == "still_has_next": scan["pages"][-1]["has_next"] = True
        elif fault == "failed_paper": scan["pages"][0]["items"][0]["status"] = "failed"
        elif fault == "changing_total": scan["pages"][-1]["total_results"] += 1
        elif fault == "wrong_position": scan["pages"][0]["items"][0]["position"] = 2
        elif fault == "wrong_title": scan["pages"][0]["items"][0]["title"] = "别的标题"
        elif fault == "unexplained_missing_abstract":
            paper_path = work / "papers/paper_0001.json"
            value = json.loads(paper_path.read_text(encoding="utf-8")); value["abstract"] = None
            paper_path.write_text(json.dumps(value), encoding="utf-8")
        if fault == "no_progress": (work / "collection_progress.json").unlink()
        else: (work / "collection_progress.json").write_text(json.dumps(scan), encoding="utf-8")
        return "已经全部完成"
    with pytest.raises(ValueError, match="未发布"):
        collector.execute(task, work, root=tmp_path, runner=run)
    assert task["status"] == "failed_or_partial"
    assert not (tmp_path / "data/cnki/manifest.json").exists()
    assert len(list((work / "papers").glob("*.json"))) == 23
    assert collector.execute(task, work, root=tmp_path, runner=lambda *a: full_scan(work))["status"] == "succeeded"


def test_all_scan_with_existing_papers_can_complete_with_no_new_papers(tmp_path):
    source, paper = source_data(tmp_path)
    dataset.import_data(source, tmp_path / "data/cnki")
    task, work = collector.prepare("温经汤", root=tmp_path)
    def run(prompt, log):
        scan = {"keyword": "温经汤", "total_results": 1, "complete": True, "stop_reason": "exhausted",
                "pages": [{"number": 1, "total_results": 1, "has_next": False, "items": [
                    {"position": 1, "rank": 1, "title": paper["title"], "url": paper["url"], "status": "existing"}]}]}
        (work / "collection_progress.json").write_text(json.dumps(scan), encoding="utf-8")
        (work / "meta.json").write_text(json.dumps({"keyword": "温经汤", "total_results": 1}), encoding="utf-8")
    result = collector.execute(task, work, root=tmp_path, runner=run)
    assert result["status"] == "succeeded" and result["added"] == 0
    assert dataset.read_corpus(tmp_path / "data/cnki", "温经汤")[0]["papers"] == 1


def test_codex_runner_uses_pinned_model_stdin_and_separate_final_output(tmp_path):
    stub = tmp_path / "fake_codex.py"
    stub.write_text('''import json, sys
from pathlib import Path
args = sys.argv[1:]
prompt = sys.stdin.buffer.read().decode("utf-8")
Path("invocation.json").write_text(json.dumps({"args":args,"prompt":prompt}), encoding="utf-8")
Path(args[args.index("--output-last-message")+1]).write_text('{"triples": []}', encoding="utf-8")
print('{"event": "log with unrelated braces"}')
''', encoding="utf-8")
    for browser in (True, False):
        prompt = "只读标题与摘要：" + "中文" * 20000
        output = collector.run_model(prompt, tmp_path / "logs/model.log", root=tmp_path, browser=browser,
                                     command=[sys.executable, str(stub)])
        assert json.loads(output) == {"triples": []}
        invocation = json.loads((tmp_path / "invocation.json").read_text(encoding="utf-8"))
        args = invocation["args"]
        assert invocation["prompt"] == prompt and prompt not in args
        assert args[:3] == ["exec", "--model", "gpt-5.6-luna"]
        assert args[args.index("--sandbox") + 1] == ("workspace-write" if browser else "read-only")
        assert ("mcp_servers.playwright.command=" in " ".join(args)) == browser
        assert "--dangerously-bypass-approvals-and-sandbox" not in args
        assert not (tmp_path / ".codex/config.toml").exists()


def test_extraction_only_sends_title_and_abstract_not_saved_fulltext(tmp_path):
    p = {"rank": 1, "title": "温经汤标题", "abstract": "原摘要", "full_text": "不得进入模型的论文正文"}
    def run(prompt, log):
        assert "温经汤标题" in prompt and "原摘要" in prompt
        assert p["full_text"] not in prompt
        return '{"triples": []}'
    collector.extract_papers([p], tmp_path, run)


def test_full_scan_keeps_a_paper_when_cnki_explicitly_provides_no_abstract(tmp_path):
    task, work = collector.prepare("温经汤", root=tmp_path)
    def run(prompt, log):
        scan = full_scan(work, 1)
        path = work / "papers/paper_0001.json"
        paper = json.loads(path.read_text(encoding="utf-8")); paper["abstract"] = None
        path.write_text(json.dumps(paper), encoding="utf-8")
        scan["pages"][0]["items"][0].update(status="no_abstract", note="知网未提供摘要")
        (work / "collection_progress.json").write_text(json.dumps(scan), encoding="utf-8")
    assert collector.execute(task, work, root=tmp_path, runner=run)["status"] == "succeeded"
    entry, _ = dataset.read_corpus(tmp_path / "data/cnki", "温经汤")
    assert entry["papers"] == 1 and entry["abstracts"] == 0


def test_codex_zero_exit_without_a_new_final_message_cannot_reuse_stale_output(tmp_path):
    log = tmp_path / "model.log"
    log.with_suffix(".response.txt").write_text('{"triples": []}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="未生成最终响应"):
        collector.run_model("标题和摘要", log, root=tmp_path, browser=False, command=[sys.executable, "-c", "pass"])
    assert not log.with_suffix(".response.txt").exists()


def test_model_cancellation_stops_only_its_own_child_process(tmp_path, monkeypatch):
    processes = []
    real = subprocess.Popen
    def tracked(*args, **kwargs):
        process = real(*args, **kwargs); processes.append(process); return process
    monkeypatch.setattr(subprocess, "Popen", tracked)
    cancel = threading.Event()
    timer = threading.Timer(.15, cancel.set); timer.start()
    try:
        with pytest.raises(collector.CollectionCancelled):
            collector.run_model("test", tmp_path / "cancel.log", root=tmp_path, cancel=cancel.is_set,
                                command=[sys.executable, "-c", "import time; time.sleep(10)"])
    finally: timer.cancel()
    assert processes and processes[0].poll() is not None


def test_collection_browser_authorization_is_scoped_to_the_three_enabled_tools(tmp_path):
    server = collector.mcp_configuration(tmp_path)["mcp_servers"]["playwright"]
    assert server["default_tools_approval_mode"] == "approve"
    assert server["enabled_tools"] == ["browser_run_code", "browser_take_screenshot", "browser_wait_for"]
    assert "approval_policy" not in collector.mcp_configuration(tmp_path)


def test_structured_browser_approval_error_is_not_reported_as_incomplete_papers(tmp_path):
    stub = tmp_path / "blocked_codex.py"
    stub.write_text('''import json, sys
from pathlib import Path
args=sys.argv[1:]
sys.stdin.buffer.read()
Path(args[args.index("--output-last-message")+1]).write_text("Browser tool denied",encoding="utf-8")
print(json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call","tool":"browser_run_code","status":"failed","error":{"message":"MCP tool call requires approval, but approval policy is never"}}}))
''', encoding="utf-8")
    with pytest.raises(collector.CollectionRuntimeError, match="尚未进入知网") as raised:
        collector.run_model("test", tmp_path / "denied.log", root=tmp_path, command=[sys.executable, str(stub)])
    assert raised.value.failure_code == "browser_approval_required"


def test_recovered_browser_error_does_not_discard_later_success(tmp_path):
    log = tmp_path / "events.jsonl"
    events = [{"type":"item.completed","item":{"type":"mcp_tool_call","tool":"browser_run_code","status":"failed","error":{"message":"requires approval; policy never"}}},
              {"type":"item.completed","item":{"type":"mcp_tool_call","tool":"browser_run_code","status":"completed","error":None}}]
    log.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    assert collector.browser_failure(log) is None


def test_explicit_cnki_access_denial_is_not_disguised_as_a_pagination_error(tmp_path):
    (tmp_path / "collection_progress.json").write_text(json.dumps({"keyword":"测试方","total_results":None,"complete":False,
        "stop_reason":"homepage_search_result_http_403_after_retry","pages":[]}),encoding="utf-8")
    with pytest.raises(collector.CollectionValidationError, match="HTTP 403") as raised:
        collector.validate_scan({"keyword":"测试方","base_ranks":[]},tmp_path,[])
    assert raised.value.failure_code == "cnki_access_denied"


def test_batched_extraction_keeps_paper_identity_and_omits_fulltext(tmp_path):
    papers=[{"rank":i,"title":f"测试方研究病{i}","abstract":f"测试方用于病{i}研究。","full_text":"不允许进入模型的正文"} for i in range(1,5)]
    calls=[]
    def run(prompt,log):
        calls.append(log.name)
        assert '不允许进入模型的正文' not in prompt
        inputs=json.loads(prompt.split('输入：')[-1])
        return json.dumps({'papers':[{'rank':p['rank'],'triples':[{'head':'测试方','head_type':'方剂','relation':'主治',
            'tail':f"病{p['rank']}",'tail_type':'疾病','evidence':p['abstract']}]} for p in inputs]})
    collector.extract_papers(papers,tmp_path,run,batch_size=2)
    assert len(calls)==2 and len(list((tmp_path/'extracted').glob('*.json')))==4
    assert len((tmp_path/'triples.jsonl').read_text(encoding='utf-8').splitlines())==4
    collector.extract_papers(papers,tmp_path,lambda *a:pytest.fail('completed extraction must be reused'),batch_size=2)


def test_batched_extraction_rejects_quotes_borrowed_from_another_paper(tmp_path):
    papers=[{'rank':1,'title':'第一篇','abstract':'甲摘要'},{'rank':2,'title':'第二篇','abstract':'乙摘要'}]
    def run(prompt,log):
        return json.dumps({'papers':[{'rank':1,'triples':[{'head':'测试方','head_type':'方剂','relation':'主治','tail':'病','tail_type':'疾病','evidence':'乙摘要'}]},
                                    {'rank':2,'triples':[]}]})
    with pytest.raises(ValueError,match='原句'): collector.extract_papers(papers,tmp_path,run,batch_size=2)
    assert not list((tmp_path/'extracted').glob('*.json'))


def test_batched_extraction_corrects_once_and_never_weakens_quote_validation(tmp_path):
    paper = {"rank": 1, "title": "测试方与疾病研究", "abstract": "测试方用于疾病（英文名）的研究。"}
    calls = []
    def run(prompt, log):
        calls.append(log.name)
        quote = "测试方用于疾病的研究。" if len(calls) == 1 else paper["abstract"]
        return json.dumps({"papers":[{"rank":1,"triples":[{"head":"测试方","head_type":"方剂","relation":"主治", "tail":"疾病","tail_type":"疾病","evidence":quote}]}]})
    collector.extract_papers([paper], tmp_path, run, batch_size=8)
    assert calls == ["extract_batch_0001_0001.log", "extract_retry_batch_0001_0001.log"]
    saved = json.loads((tmp_path / "extracted/paper_0001.json").read_text(encoding="utf-8"))
    assert saved["triples"][0]["evidence"] == paper["abstract"]


def test_indexed_source_quotes_are_materialized_from_each_original_paper(tmp_path):
    papers = [{"rank": i, "title": f"测试方与病{i}", "abstract": f"测试方用于病{i}（英文原名）研究。", "full_text": "禁止正文"} for i in (1, 2)]
    def run(prompt, log):
        assert "禁止正文" not in prompt
        inputs = json.loads(prompt.split('输入：')[-1])
        assert inputs[0]["quote_choices"]["A1"] == papers[0]["abstract"]
        return json.dumps({"papers": [{"rank": p["rank"], "triples": [{"head": "测试方", "head_type": "方剂", "relation": "主治",
            "tail": f"病{p['rank']}", "tail_type": "疾病", "evidence_ref": "A1"}]} for p in papers]})
    collector.extract_papers(papers, tmp_path, run, batch_size=8, indexed_quotes=True)
    for paper in papers:
        raw = json.loads((tmp_path / ('extracted/paper_%04d.json' % paper['rank'])).read_text(encoding='utf-8'))
        assert raw['triples'][0]['evidence'] == paper['abstract'] and 'evidence_ref' not in raw['triples'][0]


def test_unknown_quote_reference_cannot_be_published_or_retried_forever(tmp_path):
    calls=[]
    def run(prompt, log):
        calls.append(log)
        return json.dumps({"papers":[{"rank":1,"triples":[{"head":"测试方","head_type":"方剂","relation":"主治","tail":"病","tail_type":"疾病","evidence_ref":"OTHER_PAPER_A1"}]}]})
    with pytest.raises(ValueError, match="原句编号"):
        collector.extract_papers([{"rank":1,"title":"测试方与病","abstract":None}],tmp_path,run,batch_size=8,indexed_quotes=True)
    assert len(calls)==2 and not list((tmp_path/'extracted').glob('*.json'))


def test_abstract_modification_is_marked_and_unclear_mentions_do_not_count_as_direct():
    assert collector.modification_flag({"title":"大建中汤临床研究","abstract":"以大建中汤为基础方，随症加减治疗。"}, "大建中汤") is True
    assert collector.modification_flag({"title":"大建中汤研究","abstract":"加味大建中汤用于本次研究。"}, "大建中汤") is True
    assert collector.modification_flag({"title":"经方综述","abstract":"谈及其他方剂的加减运用，未明确本次方剂。"}, "大建中汤") is None
    assert collector.modification_flag({"title":"大建中汤临床研究","abstract":"本研究使用大建中汤。"}, "大建中汤") is False


@pytest.mark.parametrize("state", ["cancelled", "failed_or_partial"])
def test_explicit_cli_resume_clears_a_pause_after_terminal_failure(tmp_path, monkeypatch, state):
    from scripts import collect_cnki as cli
    task, work = collector.prepare("测试方", root=tmp_path)
    task["status"] = state
    (work / "task.json").write_text(json.dumps(task), encoding="utf-8")
    marker = work / "cancel.requested.json"
    marker.write_text('{}', encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["collect_cnki.py", "--resume", task["id"]])
    cli.main()
    assert marker.is_file()  # Inspecting a task never resumes it.
    def execute(saved, directory, **kwargs):
        assert directory == work and not marker.exists()
        return saved
    monkeypatch.setattr(collector, "execute", execute)
    monkeypatch.setattr(sys, "argv", ["collect_cnki.py", "--resume", task["id"], "--execute"])
    cli.main()


def test_cli_resume_keeps_pause_when_another_collector_holds_the_lock(tmp_path, monkeypatch):
    from scripts import collect_cnki as cli
    task, work = collector.prepare("测试方", root=tmp_path)
    task["status"] = "failed_or_partial"
    (work / "task.json").write_text(json.dumps(task), encoding="utf-8")
    marker = work / "cancel.requested.json"
    marker.write_text('{}', encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["collect_cnki.py", "--resume", task["id"], "--execute"])
    with collector.collector_lock(tmp_path):
        with pytest.raises(SystemExit): cli.main()
        assert marker.is_file()
