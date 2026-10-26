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


@pytest.mark.parametrize('badge',['全文替换','现行'])
def test_verified_dom_heading_without_ui_label_still_requires_matching_document_title(badge):
    assert browse.title_matches('研究题名',['研究题名'+badge],'研究题名 - 中国知网',heading_titles=['研究题名'])
    assert not browse.title_matches('研究题名',['研究题名'+badge],'另一篇 - 中国知网',heading_titles=['研究题名'])
    assert not browse.title_matches('研究题名',['研究题名'+badge],'研究题名 - 中国知网')
    assert not browse.title_matches('研究题名',['另一篇'+badge],'研究题名 - 中国知网',heading_titles=['另一篇'])


def test_saved_record_preserves_status_label_and_no_abstract_explanation(tmp_path):
    task,work=collector.prepare('测试方',root=tmp_path)
    site=Site()
    site.detail=lambda row:{'abstract':None,'no_abstract_confirmed':True,'record_labels':['现行'],
                           'note':'知网详情题名附标：现行。正常详情页未提供论文摘要。'}
    scan=browse.collect_pages(task,work,site)
    papers=[json.loads(p.read_text(encoding='utf-8')) for p in (work/'papers').glob('*.json')]
    collector.validate_scan(task,work,papers)
    assert all(p['record_labels']==['现行'] and '未提供论文摘要' in p['note'] for p in papers)


def test_failed_positions_report_actual_gap_and_never_publish_partial_scan(tmp_path):
    task,work=collector.prepare('测试方',root=tmp_path)
    browse.collect_pages(task,work,Site(fail={2}))
    papers=[json.loads(p.read_text(encoding='utf-8')) for p in (work/'papers').glob('*.json')]
    with pytest.raises(collector.CollectionValidationError) as raised:collector.validate_scan(task,work,papers)
    assert raised.value.failure_code=='cnki_incomplete_records'
    assert '保存2条题录' in str(raised.value) and '1条详情' in str(raised.value) and '位置2' in str(raised.value)
    assert not (tmp_path/'data/cnki').exists()


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


class NavigationPage:
    def __init__(self, target_status=403):
        self.main_frame=object();self.handlers={};self.rows=0;self.waits=[]
        self.url='about:blank';self.target_status=target_status
    def on(self,event,handler): self.handlers[event]=handler
    def emit(self,status,*,main=True,navigation=True):
        from types import SimpleNamespace
        response=SimpleNamespace(status=status,url='https://kns.cnki.net/kns8s/defaultresult/index?kw=fixture',
            frame=self.main_frame if main else object(),request=SimpleNamespace(is_navigation_request=lambda:navigation))
        self.handlers['response'](response)
        return response
    def goto(self,url,**kwargs): self.url=url;return self.emit(200)
    def evaluate(self,code): return {'blocking_verification':False,'login_required':False,'result_rows':self.rows}
    def wait_for_timeout(self,ms): self.waits.append(ms)
    def is_closed(self): return False
    def locator(self,selector):
        page=self
        class Locator:
            @property
            def first(self): return self
            def wait_for(self,**kwargs): pass
            def fill(self,text): page.keyword=text
            def count(self): return 0
            def click(self):
                page.emit(302);page.emit(page.target_status)
                page.rows=2 if page.target_status==200 else 0
                page.url='chrome-error://chromewebdata/' if page.target_status==403 else 'https://kns.cnki.net/kns8s/defaultresult/index'
        return Locator()


@pytest.mark.parametrize('status,reason',[(403,'http_403'),(429,'rate_limited')])
def test_search_classifies_redirect_navigation_rejection_without_waiting_30_seconds(tmp_path,status,reason):
    page=NavigationPage(status)
    site=browse.CnkiSite(None,page,tmp_path)
    with pytest.raises(browse.PageBlocked) as raised: site.search('测试方')
    assert raised.value.reason==reason and page.waits==[200]
    diagnostic=json.loads((tmp_path/'search-diagnostic.json').read_text(encoding='utf-8'))
    assert diagnostic['http_status']==status and diagnostic['host']=='kns.cnki.net'
    assert '?' not in diagnostic['path']


def test_search_success_and_subresource_denial_are_not_mistaken_for_main_page_denial(tmp_path):
    page=NavigationPage(200);site=browse.CnkiSite(None,page,tmp_path)
    site.search('测试方')
    assert page.keyword=='测试方' and page.waits==[200,1200]
    page.emit(403,main=False);page.emit(403,navigation=False)
    assert site.check(page)['result_rows']==2


def test_403_switches_once_to_headed_and_preserves_existing_fragments(tmp_path,monkeypatch):
    task,work=collector.prepare('测试方',root=tmp_path)
    saved=work/'papers/paper_0001.json';write_json(saved,{**item(1),'rank':1,'abstract':'已保存摘要'})
    before=saved.read_bytes();calls=[]
    def run(current,directory,*,mode,**kwargs):
        assert current['id']==task['id'] and directory==work and saved.read_bytes()==before
        calls.append(mode)
        if mode=='background': raise browse.PageBlocked('http_403')
        return {'complete':True}
    monkeypatch.setattr(browse,'collect_in_mode',run)
    assert browse.collect(task,work,root=tmp_path)['complete']
    assert calls==['background','interactive']
    assert json.loads((work/'task.json').read_text(encoding='utf-8'))['browser_fallback']=='http_403'


@pytest.mark.parametrize('reason',['captcha','login_required','rate_limited','search_results_unavailable'])
def test_non_403_failure_never_automatically_opens_a_headed_window(tmp_path,monkeypatch,reason):
    task,work=collector.prepare('测试方',root=tmp_path);calls=[]
    def run(*a,mode,**kwargs):calls.append(mode);raise browse.PageBlocked(reason)
    monkeypatch.setattr(browse,'collect_in_mode',run)
    with pytest.raises(browse.PageBlocked):browse.collect(task,work,root=tmp_path)
    assert calls==['background'] and 'browser_mode' not in task


def test_headed_denial_stops_and_resume_reuses_headed_mode(tmp_path,monkeypatch):
    task,work=collector.prepare('测试方',root=tmp_path);calls=[]
    def run(*a,mode,**kwargs):calls.append(mode);raise browse.PageBlocked('http_403')
    monkeypatch.setattr(browse,'collect_in_mode',run)
    with pytest.raises(browse.PageBlocked):browse.collect(task,work,root=tmp_path)
    assert calls==['background','interactive']
    calls.clear()
    with pytest.raises(browse.PageBlocked):browse.collect(task,work,root=tmp_path)
    assert calls==['interactive']


def test_cancelled_collection_opens_neither_browser_mode(tmp_path,monkeypatch):
    monkeypatch.setattr(browse,'collect_in_mode',lambda *a,**k:pytest.fail('must remain stopped'))
    with pytest.raises(collector.CollectionCancelled):browse.collect({},tmp_path,root=tmp_path,cancel=lambda:True)


def test_real_headed_challenge_page_remains_open_for_manual_handling(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import playwright.sync_api
    pages=[SimpleNamespace(closed=False),SimpleNamespace(closed=False)]
    for page in pages:
        page.is_closed=lambda p=page:p.closed
        page.close=lambda p=page:setattr(p,'closed',True)
    browser=SimpleNamespace(contexts=[SimpleNamespace(new_page=lambda:pages[0])])
    class Runtime:
        def __enter__(self):return SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda _:browser))
        def __exit__(self,*args):pass
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',Runtime)
    monkeypatch.setattr(browse.browser_session,'interactive_collection_endpoint',lambda root:'http://127.0.0.1:9222')
    monkeypatch.setattr(browse,'CnkiSite',lambda *a,**k:SimpleNamespace(article=pages[1],blocked_page=pages[1]))
    def blocked(*a,**k):raise browse.PageBlocked('captcha',{'blocking_verification':True})
    monkeypatch.setattr(browse,'collect_pages',blocked)
    with pytest.raises(browse.PageBlocked):browse.collect_in_mode({},tmp_path,root=tmp_path,mode='interactive',cancel=lambda:False,timeout=60)
    assert pages[0].closed and not pages[1].closed


def test_listing_waits_for_current_page_and_readable_stable_count(tmp_path):
    import copy
    class Page(NavigationPage):
        def __init__(self):
            super().__init__(200)
            good={'total_results':1377,'number':61,'has_next':True,'items':[item(1)],'count_text':'1,377'}
            self.snapshots=[{**good,'total_results':None},{**good,'number':60},good,good]
        def evaluate(self,code):
            if 'return cnkiListingSnapshot()' in code:return copy.deepcopy(self.snapshots.pop(0))
            return super().evaluate(code)
    page=Page();site=browse.CnkiSite(None,page,tmp_path);site.expected_page=61
    value=site.listing()
    assert value['total_results']==1377 and value['number']==61
    assert page.waits==[200,200,200]


def test_unreadable_count_is_not_reported_as_a_real_total_change(tmp_path,monkeypatch):
    task,work=collector.prepare('测试方',root=tmp_path)
    clock=[0.0];monkeypatch.setattr(browse.time,'monotonic',lambda:clock[0])
    class Page(NavigationPage):
        def evaluate(self,code):
            if 'return cnkiListingSnapshot()' in code:return {'total_results':None,'number':61,'has_next':True,'items':[item(1)],'count_text':None}
            return super().evaluate(code)
        def wait_for_timeout(self,ms):clock[0]+=ms/1000
    site=browse.CnkiSite(None,Page(200),work);site.expected_page=61
    with pytest.raises(browse.PageBlocked,match='result_total_unreadable'):site.listing()
    write_json(work/'collection_progress.json',{'keyword':'测试方','total_results':1377,'complete':False,'stop_reason':'result_total_unreadable','pages':[]})
    with pytest.raises(collector.CollectionValidationError) as raised:collector.validate_scan(task,work,[])
    assert raised.value.failure_code=='cnki_result_total_unreadable' and '第61页' in str(raised.value)


def test_actual_total_change_records_both_values_without_publishing(tmp_path):
    task,work=collector.prepare('测试方',root=tmp_path)
    with pytest.raises(browse.PageBlocked,match='result_total_changed'):browse.collect_pages(task,work,Site(changed=True))
    scan=json.loads((work/'collection_progress.json').read_text(encoding='utf-8'))
    assert scan['expected_total_results']==3 and scan['observed_total_results']==4 and scan['failed_page']==2
    with pytest.raises(collector.CollectionValidationError) as raised:collector.validate_scan(task,work,[])
    assert raised.value.failure_code=='cnki_result_total_changed'
    assert not (tmp_path/'data/cnki').exists()


def test_resume_preserves_the_previous_scan_before_writing_new_progress(tmp_path):
    task,work=collector.prepare('测试方',root=tmp_path)
    browse.collect_pages(task,work,Site())
    before=(work/'collection_progress.json').read_bytes()
    browse.collect_pages(task,work,Site())
    history=list((work/'scan_history').glob('scan_*.json'))
    assert len(history)==1 and history[0].read_bytes()==before
