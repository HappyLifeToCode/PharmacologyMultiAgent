"""New traversals may rebase; one traversal must remain internally consistent."""
import json
from pathlib import Path

import pytest

from pharm.core.common import write_json
from pharm.literature import browser_collector as browse, collector, dataset, jobs
from test_cnki_browser_collector import Site, item


def test_status_read_retries_transient_json_but_not_permanent_damage(monkeypatch, tmp_path):
    calls = []
    def read(path, **kwargs):
        calls.append(path)
        return '' if len(calls) == 1 else '{"id":"test"}'
    monkeypatch.setattr(Path, "read_text", read)
    assert jobs.read(tmp_path / "task.json") == {"id": "test"}
    assert len(calls) == 2
    calls.clear()
    def broken(path, **kwargs):
        calls.append(path)
        return '{'
    monkeypatch.setattr(Path, "read_text", broken)
    assert jobs.read(tmp_path / "task.json", {}) == {}
    assert len(calls) == 3


class CurrentSite(Site):
    def __init__(self, numbers):
        super().__init__()
        self.numbers = numbers

    def listing(self):
        start = (self.number - 1) * 2
        return {"number": self.number, "total_results": len(self.numbers),
                "has_next": start + 2 < len(self.numbers),
                "items": [item(n) for n in self.numbers[start:start + 2]]}


def saved(work):
    return [json.loads(p.read_text(encoding="utf-8")) for p in (work / "papers").glob("*.json")]


@pytest.mark.parametrize("numbers", [[4, 1, 3, 2], [3, 1], [3, 2, 1], []])
def test_new_traversal_reuses_identity_and_preserves_old_files(tmp_path, numbers):
    task, work = collector.prepare("测试方", root=tmp_path)
    browse.collect_pages(task, work, Site())
    before = {p.name: p.read_bytes() for p in (work / "papers").glob("*.json")}
    old_scan = (work / "collection_progress.json").read_bytes()
    site = CurrentSite(numbers)
    scan = browse.collect_pages(task, work, site)
    assert scan["total_results"] == len(numbers) and scan["previous_total_results"] == 3
    assert scan["baseline_changed"] == (len(numbers) != 3)
    assert site.calls == ([item(4)["title"]] if 4 in numbers else [])
    assert all((work / "papers" / name).read_bytes() == raw for name, raw in before.items())
    assert any(p.read_bytes() == old_scan for p in (work / "scan_history").glob("*.json"))
    assert scan["excluded_saved_ranks"] == sorted(set([1, 2, 3]) - set(numbers))
    collector.validate_scan(task, work, saved(work))


def test_retry_after_old_total_change_establishes_new_page_one_baseline(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    write_json(work / "meta.json", {"keyword": "测试方", "total_results": 6940})
    write_json(work / "collection_progress.json", {"keyword": "测试方", "total_results": None,
               "complete": False, "stop_reason": "result_total_changed", "pages": [],
               "expected_total_results": 6940, "observed_total_results": 6941, "failed_page": 1})
    scan = browse.collect_pages(task, work, CurrentSite([1, 2, 3, 4]))
    assert scan["previous_total_results"] == 6940 and scan["total_results"] == 4 and scan["complete"]
    collector.validate_scan(task, work, saved(work))


def test_legacy_generic_error_is_explained_without_mutating_task(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    task.update(status="failed_or_partial", failure_code="cnki_page_unavailable", error="generic error")
    write_json(work / "task.json", task)
    write_json(work / "collection_progress.json", {"complete": False, "stop_reason": "result_total_changed",
               "expected_total_results": 6940, "observed_total_results": 6941, "failed_page": 1})
    before = (work / "task.json").read_bytes()
    result = jobs.CollectionManager().status(tmp_path, task["id"])
    assert result["failure_code"] == "cnki_result_total_changed"
    assert "6940" in result["error"] and "6941" in result["error"]
    assert (work / "task.json").read_bytes() == before


@pytest.mark.parametrize("reason", ["result_total_changed", "result_total_unreadable"])
def test_real_execute_keeps_specific_failure_code_and_does_not_publish(tmp_path, monkeypatch, reason):
    task, work = collector.prepare("测试方", root=tmp_path)
    def collect(*args, **kwargs):
        write_json(work / "collection_progress.json", {"keyword": "测试方", "complete": False,
                   "stop_reason": reason, "expected_total_results": 6940, "observed_total_results": 6941,
                   "failed_page": 2})
        raise browse.PageBlocked(reason)
    monkeypatch.setattr(browse, "collect", collect)
    with pytest.raises(collector.CollectionValidationError) as raised:
        collector.execute(task, work, root=tmp_path)
    assert raised.value.failure_code == "cnki_" + reason
    if reason == "result_total_changed": assert "6940" in str(raised.value) and "6941" in str(raised.value)
    assert json.loads((work / "task.json").read_text(encoding="utf-8"))["failure_code"] == "cnki_" + reason
    assert not (tmp_path / "data/cnki/manifest.json").exists()


@pytest.mark.parametrize("fault", ["missing_exclusion", "exclude_active", "missing_position", "duplicate_position", "in_run_change"])
def test_rescan_does_not_relax_completeness_validation(tmp_path, fault):
    task, work = collector.prepare("测试方", root=tmp_path)
    browse.collect_pages(task, work, Site())
    if fault == "in_run_change":
        with pytest.raises(browse.PageBlocked): browse.collect_pages(task, work, Site(changed=True))
    else:
        scan = browse.collect_pages(task, work, CurrentSite([3, 1]))
        if fault == "missing_exclusion": scan.pop("excluded_saved_ranks")
        if fault == "exclude_active": scan["excluded_saved_ranks"] = [1, 2]
        if fault == "missing_position": scan["pages"][0]["items"].pop()
        if fault == "duplicate_position":
            scan["pages"][0]["items"][1] = {**scan["pages"][0]["items"][0], "position": 2}
            scan["excluded_saved_ranks"] = [1, 2]
        write_json(work / "collection_progress.json", scan)
    with pytest.raises(ValueError): collector.validate_scan(task, work, saved(work))


def test_execute_publishes_only_current_papers_and_sources_without_deleting_cache(tmp_path, monkeypatch):
    task, work = collector.prepare("测试方", root=tmp_path)
    browse.collect_pages(task, work, Site())
    # Simulate an incomplete prior run with extracted source caches.
    scan = json.loads((work / "collection_progress.json").read_text(encoding="utf-8"))
    scan.update(complete=False, stop_reason="cancelled")
    write_json(work / "collection_progress.json", scan)
    before = {p.name: p.read_bytes() for p in (work / "papers").glob("*.json")}
    rows = [{"head": "测试方", "head_type": "方剂", "relation": "治疗", "tail": "疾病", "tail_type": "疾病",
             "count": 2, "sources": [{"rank": n, "title": item(n)["title"], "evidence": item(n)["title"]} for n in [1, 2]]}]
    cache = work / "triples.jsonl"
    cache.write_text(json.dumps(rows[0], ensure_ascii=False), encoding="utf-8")
    cache_before = cache.read_bytes()
    monkeypatch.setattr(browse, "collect", lambda *a, **k: browse.collect_pages(task, work, CurrentSite([2])))
    # No real model: rank 2 was already extracted, rank 1 must not be analyzed.
    monkeypatch.setattr(collector, "extract_papers", lambda papers, *a, **k: pytest.fail("no new selected papers") if papers else None)
    result = collector.execute(task, work, root=tmp_path, extract=True)
    assert result["status"] == "succeeded"
    _, contents = dataset.read_corpus(tmp_path / "data/cnki", "测试方")
    published = json.loads(contents["papers.json"])
    assert [p["rank"] for p in published["papers"]] == [2]
    assert published["extracted_ranks"] == [2]
    sources = json.loads(contents["triples.jsonl"])
    assert sources["count"] == 1 and [s["rank"] for s in sources["sources"]] == [2]
    assert cache.read_bytes() == cache_before
    assert all((work / "papers" / name).read_bytes() == raw for name, raw in before.items())
    status = jobs.CollectionManager().status(tmp_path, task["id"])
    assert status["baseline_changed"] and status["excluded_saved_count"] == 2
    assert status["previous_total_results"] == 3 and status["total_results"] == 1
