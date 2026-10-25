import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from pharm.agent import disease_input
from pharm.core.common import write_json
from pharm.literature import cnki, collector, dataset, flows, jobs, service
from server import app as backend


def wait_for(fn):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = fn()
        if value: return value
        time.sleep(.025)
    raise AssertionError("isolated literature worker did not finish")


def archive(root, run_id="forward_case", keyword="测试方", reverse=False):
    direction = "reverse" if reverse else "forward"
    value = {"direction": direction, "input": {"disease_id": "MONDO_1"} if reverse else {"formula": keyword},
             "candidates": [{"sentinel": "original target ranking"}]}
    if reverse: value["resolved_disease"] = {"disease_id": "MONDO_1", "disease_name": "example", "chinese_name": keyword}
    path = root / "local/agent" / direction / run_id / "result.json"
    write_json(path, value)
    return path, path.read_bytes()


def paper_and_row(keyword="测试方", rank=1, head=None, head_type="方剂", term="示例病"):
    head = head or keyword
    title = head + "研究" + term
    paper = {"rank": rank, "title": title, "abstract": head + "用于" + term + "患者研究。", "is_modified": False,
             "url": f"https://kns.cnki.net/fixture/{rank}"}
    row = {"head": head, "head_type": head_type, "relation": "治疗" if head_type == "中药" else "主治", "tail": term, "tail_type": "疾病",
           "sources": [{"rank": rank, "title": title, "url": paper["url"], "is_modified": False, "evidence": paper["abstract"]}]}
    return paper, row


def install(root, keyword="测试方", *, missing_extraction=False, entries=None):
    entries = entries if entries is not None else [paper_and_row(keyword)]
    source = root / "source" / ("cnki_" + keyword)
    write_json(source / "papers.json", {"keyword": keyword, "papers": [p for p, _ in entries]})
    if not missing_extraction:
        (source / "triples.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for _, r in entries), encoding="utf-8")
    dataset.import_data(source.parent, root / "data/cnki")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv("PHARM_CNKI_DATA_DIR", raising=False)
    monkeypatch.setattr(disease_input, "catalog", lambda: ({}, {}, {}, {}))


class Harness:
    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = []
        self.fail = False
        self.collections = jobs.CollectionManager(execute=self.execute)
        self.flows = flows.LiteratureFlowManager(self.collections)

    def execute(self, task, work, **kwargs):
        self.entered.set()
        while not self.release.wait(.025):
            if (work / "cancel.requested.json").exists(): raise collector.CollectionCancelled("已停止")
        if self.fail: raise RuntimeError("模拟采集失败")
        keyword = task["keyword"]
        paper, row = paper_and_row(keyword, head="白芍", head_type="中药", term=keyword) if keyword == "示例病" else paper_and_row(keyword)
        def runner(prompt, log):
            self.calls.append((keyword, log.name))
            if log.name == "collection.log":
                write_json(work / "papers/paper_0001.json", paper)
                write_json(work / "meta.json", {"keyword": keyword, "total_results": 1})
                write_json(work / "collection_progress.json", {"keyword": keyword, "total_results": 1, "complete": True, "stop_reason": "exhausted",
                    "pages": [{"number": 1, "total_results": 1, "has_next": False, "items": [
                        {"position": 1, "rank": 1, "title": paper["title"], "url": paper["url"], "status": "collected"}]}]})
                return "模拟采集完成"
            return json.dumps({"triples": [{k: row[k] for k in ("head", "head_type", "relation", "tail", "tail_type")} | {"evidence": paper["abstract"]}]})
        return collector.execute(task, work, runner=runner, **kwargs)

    def close(self):
        self.collections.shutdown()
        self.flows.shutdown()


@pytest.fixture
def harness():
    value = Harness()
    try: yield value
    finally: value.close()


def test_cached_literature_is_ranked_without_any_model_call(tmp_path, harness):
    path, before = archive(tmp_path)
    install(tmp_path)
    result = harness.flows.resolve(tmp_path, "forward_case")
    assert result["state"] == "ready" and result["collection"] is None
    assert result["literature"]["result"]["candidates"][0]["raw_names"] == ["示例病"]
    assert harness.flows.resolve(tmp_path, "forward_case")["snapshot_id"] == result["snapshot_id"]
    assert not harness.entered.is_set() and not harness.calls
    assert path.read_bytes() == before


def test_missing_literature_returns_targets_first_then_generates_ranking_in_background(tmp_path, harness):
    path, before = archive(tmp_path)
    state = harness.flows.resolve(tmp_path, "forward_case")
    assert state["state"] == "collecting" and state["literature"] is None
    assert path.read_bytes() == before
    collection_id = state["collection_id"]
    assert harness.flows.resolve(tmp_path, "forward_case")["collection_id"] == collection_id
    assert len(harness.collections.list(tmp_path)) == 1
    harness.release.set()
    result = wait_for(lambda: (s if (s := harness.flows.status(tmp_path, "forward_case"))["state"] == "ready" else None))
    assert result["literature"]["result"]["candidates"][0]["paper_count"] == 1
    assert result["collection"]["model"] == "gpt-5.6-luna"
    assert path.read_bytes() == before


def test_local_titles_without_extraction_are_analyzed_without_cnki_collection(tmp_path, harness):
    archive(tmp_path)
    install(tmp_path, missing_extraction=True)
    harness.release.set()
    state = harness.flows.resolve(tmp_path, "forward_case")
    assert state["collection"]["collection_mode"] == "local_analysis"
    ready = wait_for(lambda: (s if (s := harness.flows.status(tmp_path, "forward_case"))["state"] == "ready" else None))
    assert ready["literature"]["result"]["candidates"]
    assert harness.calls == [("测试方", "extract_0001.log")]


def test_disabled_and_empty_valid_local_corpus_do_not_collect(tmp_path, harness):
    archive(tmp_path)
    assert harness.flows.resolve(tmp_path, "forward_case", enabled=False)["state"] == "disabled"
    assert harness.flows.status(tmp_path, "forward_case")["state"] == "not_started"
    install(tmp_path, entries=[])
    result = harness.flows.resolve(tmp_path, "forward_case")
    assert result["state"] == "ready" and not result["literature"]["result"]["candidates"]
    assert not harness.entered.is_set()


def test_failed_flow_does_not_retry_until_explicit_resume(tmp_path, harness):
    archive(tmp_path)
    harness.fail = True; harness.release.set()
    first = harness.flows.resolve(tmp_path, "forward_case")
    wait_for(lambda: harness.flows.status(tmp_path, "forward_case")["state"] == "failed")
    assert harness.flows.resolve(tmp_path, "forward_case")["collection_id"] == first["collection_id"]
    assert not harness.calls
    wait_for(lambda: not harness.collections.active)
    harness.fail = False
    harness.flows.resume(tmp_path, "forward_case")
    wait_for(lambda: harness.flows.status(tmp_path, "forward_case")["state"] == "ready")
    assert len(harness.collections.list(tmp_path)) == 1


def test_queue_serializes_keywords_and_shares_a_live_keyword_between_queries(tmp_path, harness):
    archive(tmp_path, "forward_first", "第一方")
    archive(tmp_path, "forward_second", "第二方")
    archive(tmp_path, "forward_shared", "第一方")
    first = harness.flows.resolve(tmp_path, "forward_first")
    assert harness.entered.wait(2)
    second = harness.flows.resolve(tmp_path, "forward_second")
    shared = harness.flows.resolve(tmp_path, "forward_shared")
    assert first["collection_id"] == shared["collection_id"]
    assert second["collection"]["status"] == "queued"
    assert len(harness.collections.running) == 1
    harness.release.set()
    for name in ("forward_first", "forward_second", "forward_shared"):
        wait_for(lambda: harness.flows.status(tmp_path, name)["state"] == "ready")
    assert len([c for c in harness.calls if c == ("第一方", "collection.log")]) == 1


def test_cancel_preserves_progress_and_explicit_resume_uses_same_task(tmp_path, harness):
    archive(tmp_path)
    state = harness.flows.resolve(tmp_path, "forward_case")
    assert harness.entered.wait(2)
    harness.flows.cancel(tmp_path, "forward_case")
    wait_for(lambda: harness.flows.status(tmp_path, "forward_case")["state"] == "cancelled")
    wait_for(lambda: not harness.collections.active)
    harness.release.set()
    assert harness.flows.resume(tmp_path, "forward_case")["collection_id"] == state["collection_id"]
    wait_for(lambda: harness.flows.status(tmp_path, "forward_case")["state"] == "ready")


def test_read_only_status_recovers_after_manager_restart_without_model_restart(tmp_path, harness):
    archive(tmp_path)
    state = harness.flows.resolve(tmp_path, "forward_case")
    harness.flows.shutdown()
    replacement = flows.LiteratureFlowManager(harness.collections)
    try:
        assert replacement.status(tmp_path, "forward_case")["collection_id"] == state["collection_id"]
        harness.release.set()
        wait_for(lambda: replacement.status(tmp_path, "forward_case")["state"] == "ready")
        assert len(harness.collections.list(tmp_path)) == 1
    finally: replacement.shutdown()


def test_original_archive_change_invalidates_literature_flow(tmp_path, harness):
    path, _ = archive(tmp_path)
    install(tmp_path)
    harness.flows.resolve(tmp_path, "forward_case")
    path.write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="归档已变化"): harness.flows.status(tmp_path, "forward_case")


def test_reverse_ranks_raw_herbs_and_keeps_formulas_separate_without_parent_substitution(tmp_path):
    path, before = archive(tmp_path, "reverse_case", "示例病", reverse=True)
    entries = [paper_and_row("示例病", 1, head="白芍", head_type="中药"),
               paper_and_row("示例病", 2, head="测试方", head_type="方剂"),
               paper_and_row("示例病", 3, head="甘草", head_type="中药", term="示例病亚型")]
    install(tmp_path, "示例病", entries=entries)
    payload = service.query("reverse_case", root=tmp_path)
    result = payload["result"]
    assert result["direction"] == "reverse"
    assert [c["raw_names"] for c in result["candidates"]] == [["白芍"]]
    assert [c["raw_names"] for c in result["formula_candidates"]] == [["测试方"]]
    assert result["candidates"][0]["mapping"]["status"] == "literature_name_only"
    assert any(r["reason"] == "other_disease" for r in result["rejected"])
    assert path.read_bytes() == before
    assert service.query("reverse_case", root=tmp_path, snapshot=payload["snapshot_id"]) == payload


def test_reverse_exact_alias_is_allowed_but_ambiguous_alias_is_not(tmp_path, monkeypatch):
    archive(tmp_path, "reverse_case", "示例病", reverse=True)
    entities = {"MONDO_1": {"disease_name": "example"}, "MONDO_2": {"disease_name": "another"}}
    monkeypatch.setattr(disease_input, "catalog", lambda: (entities, {"别名": {"MONDO_1"}, "歧义名": {"MONDO_1", "MONDO_2"}}, {}, {}))
    install(tmp_path, "示例病", entries=[paper_and_row("示例病", 1, head="白芍", head_type="中药", term="别名"),
                                       paper_and_row("示例病", 2, head="甘草", head_type="中药", term="歧义名")])
    result = service.query("reverse_case", root=tmp_path)["result"]
    assert [c["raw_names"] for c in result["candidates"]] == [["白芍"]]


def test_api_flow_start_is_explicit_local_first_and_rejects_scope_overrides(tmp_path, monkeypatch, harness):
    archive(tmp_path)
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    monkeypatch.setattr(backend.cnki_flows, "manager", harness.flows)
    monkeypatch.setattr(backend.cnki_collection, "manager", harness.collections)
    with TestClient(backend.app, base_url="http://localhost") as client:
        url = "/api/agent/forward/forward_case/literature/flow"
        assert client.get(url).json()["state"] == "not_started"
        assert not harness.entered.is_set()
        assert client.post(url + "/resolve", json={"keyword": "wrong"}).status_code == 400
        assert client.post(url + "/resolve", json={"enabled": "yes"}).status_code == 400
        assert client.post(url + "/resolve", json={"enabled": False}).json()["state"] == "disabled"
        assert client.post(url + "/resolve", json={}, headers={"Origin": "https://other.invalid"}).status_code == 403
        state = client.post(url + "/resolve", json={}).json()
        assert state["state"] == "collecting"
        assert client.post(url + "/resolve", json={}).json()["collection_id"] == state["collection_id"]
        harness.release.set()
        wait_for(lambda: client.get(url).json()["state"] == "ready")
        assert client.get("/api/literature/collections/../../escape").status_code in {400, 404}


def test_reverse_missing_disease_literature_uses_resolved_name_and_returns_herbs(tmp_path, harness):
    path, before = archive(tmp_path, "reverse_case", "示例病", reverse=True)
    state = harness.flows.resolve(tmp_path, "reverse_case")
    assert state["keyword"] == "示例病" and state["state"] == "collecting"
    harness.release.set()
    ready = wait_for(lambda: (s if (s := harness.flows.status(tmp_path, "reverse_case"))["state"] == "ready" else None))
    assert ready["literature"]["result"]["candidates"][0]["raw_names"] == ["白芍"]
    assert path.read_bytes() == before


def test_completed_collection_generates_a_snapshot_without_client_polling(tmp_path, harness):
    archive(tmp_path)
    harness.flows.resolve(tmp_path, "forward_case")
    harness.release.set()
    path = tmp_path / "local/agent/literature-flows/forward_case.json"
    ready = wait_for(lambda: (s if (s := jobs.read(path, {})).get("state") == "ready" else None))
    assert ready["snapshot_id"]


def test_corrupt_local_data_does_not_trigger_collection_or_overwrite(tmp_path, harness):
    archive(tmp_path)
    install(tmp_path)
    path = tmp_path / "data/cnki/cnki_测试方/papers.json"
    path.write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="校验失败"): harness.flows.resolve(tmp_path, "forward_case")
    assert not harness.entered.is_set() and path.read_text(encoding="utf-8") == '{}'


def test_reverse_literature_returns_all_raw_herb_names_without_top_n(tmp_path):
    archive(tmp_path, "reverse_case", "示例病", reverse=True)
    install(tmp_path, "示例病", entries=[paper_and_row("示例病", rank, head=f"工程药材{rank:02d}", head_type="中药") for rank in range(1, 38)])
    result = service.query("reverse_case", root=tmp_path)["result"]
    assert len(result["candidates"]) == 37 and not result["formula_candidates"]
    assert result["candidates"][-1]["raw_names"] == ["工程药材37"]


def test_external_collector_lock_is_not_removed_or_adopted(tmp_path, harness):
    archive(tmp_path)
    path = tmp_path / "local/agent/cnki-collection/collector.lock"
    path.parent.mkdir(parents=True)
    path.write_text("external-owner", encoding="utf-8")
    with pytest.raises(RuntimeError, match="遗留锁"): harness.flows.resolve(tmp_path, "forward_case")
    assert path.read_text(encoding="utf-8") == "external-owner" and not harness.entered.is_set()


def test_flow_refreshes_the_actual_failure_diagnostic_without_restarting_collection(tmp_path, harness):
    archive(tmp_path)
    harness.fail=True; harness.release.set()
    state=harness.flows.resolve(tmp_path,"forward_case")
    wait_for(lambda: harness.flows.status(tmp_path,"forward_case")["state"] == "failed")
    path=jobs.task_directory(tmp_path,state["collection_id"])/"task.json"
    task=jobs.read(path); task.update(error="知网HTTP 403访问被拒绝",failure_code="cnki_access_denied"); write_json(path,task)
    updated=harness.flows.status(tmp_path,"forward_case")
    assert updated["error"] == "知网HTTP 403访问被拒绝" and updated["failure_code"] == "cnki_access_denied"
    assert not harness.calls
