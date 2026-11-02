import json

import pytest

from pharm.literature import browser_collector as browse, collector
from test_cnki_browser_collector import NavigationPage, Site, item


def test_status_read_retries_windows_sharing_violation(monkeypatch, tmp_path):
    from pathlib import Path
    from pharm.literature import jobs
    calls = []
    def read(path, **kwargs):
        calls.append(path)
        if len(calls) == 1: raise PermissionError("file is being replaced")
        return '{"id":"fixture"}'
    monkeypatch.setattr(Path, "read_text", read)
    assert jobs.read(tmp_path / "task.json") == {"id": "fixture"}
    assert len(calls) == 2


def listing(total=6781):
    return {"total_results": total, "number": 37, "has_next": True, "items": [item(1)]}


@pytest.mark.parametrize("totals,outcome", [([6941, 6941], "recovered"),
    ([6781, 6941, 6941], "recovered"), ([6781, 6781, 6781], "confirmed_total_changed")])
def test_recheck_recovers_only_after_two_matches_or_confirms_persistent_change(tmp_path, totals, outcome):
    page = NavigationPage(200)
    site = browse.CnkiSite(None, page, tmp_path)
    calls = []
    def read():
        calls.append(1)
        return listing(totals[len(calls) - 1])
    site.listing = read
    result = site.recheck_listing(6941, listing())
    assert result["total_results"] == totals[-1] and len(calls) <= 3
    assert page.waits == [1000, 2000, 3000][:len(calls)]
    report = json.loads(next((tmp_path / "count_rechecks").glob("*.json")).read_text(encoding="utf-8"))
    assert report["outcome"] == outcome and len(report["observations"]) == len(calls) + 1
    assert all("url" not in row and "title" not in row for row in report["observations"])


@pytest.mark.parametrize("totals", [[6781, 6782, 6783], [6781, 6781, 6941], [6941, 6781, 6941]])
def test_oscillation_or_single_late_recovery_does_not_prove_consistency(tmp_path, totals):
    site = browse.CnkiSite(None, NavigationPage(200), tmp_path)
    values = iter(totals)
    site.listing = lambda: listing(next(values))
    with pytest.raises(browse.PageBlocked, match="result_total_unstable"):
        site.recheck_listing(6941, listing())


@pytest.mark.parametrize("field,value", [("number", 38), ("items", [item(2)]), ("has_next", False)])
def test_changed_page_or_bibliography_cannot_be_accepted_as_count_recovery(tmp_path, field, value):
    site = browse.CnkiSite(None, NavigationPage(200), tmp_path)
    site.listing = lambda: {**listing(6941), field: value}
    with pytest.raises(browse.PageBlocked, match="result_list_changed"):
        site.recheck_listing(6941, listing())


def test_cancel_and_access_denial_interrupt_recheck_without_retrying(tmp_path):
    site = browse.CnkiSite(None, NavigationPage(200), tmp_path, cancel=lambda: True)
    site.listing = lambda: pytest.fail("cancelled must not read")
    with pytest.raises(collector.CollectionCancelled): site.recheck_listing(6941, listing())
    site.cancel = lambda: False
    def blocked(page): raise browse.PageBlocked("http_403")
    site.check = blocked
    with pytest.raises(browse.PageBlocked, match="http_403"): site.recheck_listing(6941, listing())


def test_collect_pages_rechecks_before_reading_details_and_keeps_baseline(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site(changed=True)
    calls = []
    def recheck(expected, current):
        calls.append((expected, current["total_results"]))
        return {**current, "total_results": expected}
    site.recheck_listing = recheck
    scan = browse.collect_pages(task, work, site)
    assert calls == [(3, 4)] and scan["complete"] and scan["total_results"] == 3
    assert "failed_page" not in scan


@pytest.mark.parametrize("reason", ["result_total_unstable", "result_list_changed"])
def test_recheck_errors_remain_clear_in_real_execute(tmp_path, monkeypatch, reason):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site(changed=True)
    def recheck(*args): raise browse.PageBlocked(reason)
    site.recheck_listing = recheck
    monkeypatch.setattr(browse, "collect", lambda *a, **k: browse.collect_pages(task, work, site))
    with pytest.raises(collector.CollectionValidationError) as exc:
        collector.execute(task, work, root=tmp_path)
    assert exc.value.failure_code == "cnki_" + reason
    assert not (tmp_path / "data/cnki/manifest.json").exists()
    assert len(list((work / "papers").glob("*.json"))) == 2
