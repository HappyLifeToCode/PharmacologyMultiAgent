import json

import pytest

from pharm.core.common import write_json
from pharm.literature import browser_collector as browse, collector


def item(n):
    return {"title": f"测试方与疾病{n}", "url": f"https://kns.cnki.net/item/{n}", "authors": "甲", "source": "来源", "date": "2026-01-01"}


class Site:
    def __init__(self, fail=(), changed=False):
        self.number, self.calls, self.fail, self.changed = 1, [], set(fail), changed
    def search(self, keyword): self.number = 1
    def listing(self):
        n = self.number
        return {"number": n, "total_results": 4 if n == 2 and self.changed else 3, "has_next": n == 1,
                "items": [item(1), item(2)] if n == 1 else [item(3)]}
    def next_page(self, n): self.number = n
    def refresh(self, keyword, n): self.number = n; return self.listing()
    def detail(self, row):
        self.calls.append(row["title"])
        n = int(row["title"][-1])
        if n in self.fail: raise browse.PageBlocked("http_404")
        return {"abstract": row["title"] + "的研究摘要", "keywords": []}


def test_browser_collection_covers_all_positions_and_resume_reuses_exact_identity(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    scan = browse.collect_pages(task, work, site)
    papers = [json.loads(p.read_text(encoding="utf-8")) for p in (work / "papers").glob("*.json")]
    assert scan["complete"] and len(site.calls) == 3
    collector.validate_scan(task, work, papers)
    original = {p.name: p.read_bytes() for p in (work / "papers").glob("*.json")}
    site = Site()
    browse.collect_pages(task, work, site)
    assert not site.calls
    assert original == {p.name: p.read_bytes() for p in (work / "papers").glob("*.json")}


def test_single_404_is_retried_once_but_does_not_prevent_later_papers(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site(fail={2})
    scan = browse.collect_pages(task, work, site)
    assert site.calls.count(item(2)["title"]) == 2
    assert item(3)["title"] in site.calls and not scan["complete"]
    assert scan["stop_reason"] == "failed_positions"
    assert len(list((work / "papers").glob("*.json"))) == 2
    assert not (tmp_path / "data/cnki").exists()


def test_visible_verification_stops_without_inventing_a_paper_or_full_scan(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    def blocked(row): raise browse.PageBlocked("captcha", {"blocking_verification": True, "verification_elements": [{"text": "安全验证", "top": 20}]})
    site.detail = blocked
    with pytest.raises(browse.PageBlocked): browse.collect_pages(task, work, site)
    scan = json.loads((work / "collection_progress.json").read_text(encoding="utf-8"))
    assert not scan["complete"] and scan["stop_reason"] == "captcha"
    with pytest.raises(collector.CollectionValidationError) as raised: collector.validate_scan(task, work, [])
    assert raised.value.failure_code == "cnki_verification_required"
    assert not list((work / "papers").glob("*.json"))


def test_total_change_preserves_completed_fragments_and_never_claims_complete(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    with pytest.raises(browse.PageBlocked, match="total_changed"):
        browse.collect_pages(task, work, Site(changed=True))
    assert len(list((work / "papers").glob("*.json"))) == 2
    scan = json.loads((work / "collection_progress.json").read_text(encoding="utf-8"))
    assert not scan["complete"]


def test_missing_abstract_cannot_be_misrepresented_as_no_abstract(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    site.detail = lambda row: {"abstract": None}
    with pytest.raises(browse.PageBlocked, match="abstract_not_confirmed"):
        browse.collect_pages(task, work, site)
    assert not list((work / "papers").glob("*.json"))


def test_explicitly_absent_abstract_is_retained_with_explanation(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    site.detail = lambda row: {"abstract": None, "no_abstract_confirmed": True}
    browse.collect_pages(task, work, site)
    papers = [json.loads(p.read_text(encoding="utf-8")) for p in (work / "papers").glob("*.json")]
    assert len(papers) == 3 and all(p["note"] for p in papers)
    collector.validate_scan(task, work, papers)


def test_cancelled_browser_collection_never_opens_a_page(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    site.search = lambda _: pytest.fail("must not visit")
    with pytest.raises(collector.CollectionCancelled): browse.collect_pages(task, work, site, cancel=lambda: True)


def test_same_title_with_different_authors_is_not_reused(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    write_json(work / "papers/paper_0001.json", {**item(1), "rank": 1, "authors": "不同作者", "abstract": "旧摘要"})
    site = Site()
    browse.collect_pages(task, work, site)
    assert item(1)["title"] in site.calls
    assert json.loads((work / "papers/paper_0001.json").read_text(encoding="utf-8"))["authors"] == "不同作者"


def test_single_unreadable_detail_is_recorded_and_other_pages_continue(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    good = site.detail
    def detail(row):
        if row["title"] == item(2)["title"]: raise browse.PageBlocked("detail_unavailable")
        return good(row)
    site.detail = detail
    scan = browse.collect_pages(task, work, site)
    assert not scan["complete"] and scan["pages"][0]["items"][1]["status"] == "failed"
    assert len(list((work / "papers").glob("*.json"))) == 2


def test_three_consecutive_unreadable_details_stop_without_retry_storm(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    calls = []
    def detail(row): calls.append(row); raise browse.PageBlocked("detail_unavailable")
    site.detail = detail
    with pytest.raises(browse.PageBlocked, match="consecutive_details_unavailable"):
        browse.collect_pages(task, work, site)
    assert len(calls) == 3


def test_cnki_bibliographic_badge_is_removed_only_with_matching_document_title():
    assert browse.title_matches("花椒研究", ["花椒研究 题录"], "花椒研究 - 中国知网")
    assert not browse.title_matches("花椒研究", ["花椒研究 题录"], "另一篇 - 中国知网")
    assert not browse.title_matches("花椒研究", ["另一篇 题录"], "花椒研究 - 中国知网")


def test_site_excerpt_is_distinct_from_full_abstract_and_only_title_is_analyzed(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    site.detail = lambda row: {"abstract": None, "abstract_excerpt": "网站仅有节选...", "abstract_status": "excerpt_only",
                              "title_only_confirmed": True, "note": "网站只提供节选；仅依据标题分析"}
    scan = browse.collect_pages(task, work, site)
    papers = [json.loads(p.read_text(encoding="utf-8")) for p in (work / "papers").glob("*.json")]
    assert scan["complete"] and all(i["status"] == "title_only" for page in scan["pages"] for i in page["items"])
    collector.validate_scan(task, work, papers)
    calls = []
    def run(prompt, log):
        assert "网站仅有节选" not in prompt
        calls.append(log)
        return '{"triples":[]}'
    collector.extract_papers(papers, work, run)
    assert len(calls) == 3
    papers[0]["abstract_excerpt"] = ""
    with pytest.raises(ValueError): collector.validate_scan(task, work, papers)


@pytest.mark.parametrize("blocked", [False, True])
def test_production_browser_uses_background_and_cleans_only_owned_pages(tmp_path, monkeypatch, blocked):
    from types import SimpleNamespace
    import playwright.sync_api
    pages=[SimpleNamespace(closed=False), SimpleNamespace(closed=False)]
    for page in pages:
        page.is_closed=lambda p=page:p.closed
        page.close=lambda p=page:setattr(p,'closed',True)
    context=SimpleNamespace(new_page=lambda:pages[0])
    browser=SimpleNamespace(contexts=[context])
    calls=[]
    class Runtime:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda address:calls.append(address) or browser))
        def __exit__(self,*args): pass
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',Runtime)
    monkeypatch.setattr(browse.browser_session,'background_endpoint',lambda root:'http://127.0.0.1:9222')
    monkeypatch.setattr(browse.browser_session,'endpoint',lambda *a:pytest.fail('must ensure background mode'))
    monkeypatch.setattr(browse,'CnkiSite',lambda *a,**k:SimpleNamespace(article=pages[1]))
    def collect(*a,**k):
        if blocked: raise browse.PageBlocked('captcha',{'blocking_verification':True})
        return {'complete':True}
    monkeypatch.setattr(browse,'collect_pages',collect)
    if blocked:
        with pytest.raises(browse.PageBlocked): browse.collect({},tmp_path,root=tmp_path)
    else: assert browse.collect({},tmp_path,root=tmp_path)['complete']
    assert calls==['http://127.0.0.1:9222'] and all(p.closed for p in pages)


def test_visible_login_pause_is_explicit_and_does_not_claim_no_results(tmp_path):
    task, work=collector.prepare('测试方',root=tmp_path)
    site=Site()
    def login(row): raise browse.PageBlocked('login_required',{'login_required':True})
    site.detail=login
    with pytest.raises(browse.PageBlocked): browse.collect_pages(task,work,site)
    with pytest.raises(collector.CollectionValidationError) as raised: collector.validate_scan(task,work,[])
    assert raised.value.failure_code=='cnki_login_required'
    assert not list((work/'papers').glob('*.json'))
