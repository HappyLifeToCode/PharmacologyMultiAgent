"""Sequential collection through normal CNKI pages; no model decides page state."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse
from uuid import uuid4

from ..core.common import now, write_json
from . import browser_session, cnki


class PageBlocked(RuntimeError):
    def __init__(self, reason, state=None):
        super().__init__(reason)
        self.reason, self.state = reason, state


def title_matches(expected, headings, page_title, *, heading_titles=()):
    target = cnki.normalise(expected)
    actual = [cnki.normalise(h) for h in headings]
    if target in actual: return True
    # Some CNKI headings append a UI badge. Require the document title too.
    return (cnki.normalise(re.sub(r"\s*-\s*中国知网\s*$", "", page_title)) == target
            and (target + "题录" in actual or target in {cnki.normalise(value) for value in heading_titles}))


def paper_status(paper, existing=False):
    if paper.get("abstract"): return "existing" if existing else "collected"
    return "title_only" if paper.get("abstract_status") == "excerpt_only" else "no_abstract"


def collect_pages(task, work, site, *, cancel=lambda: False):
    """Keep fragments immutable, record every position, never publish partial data."""
    from .collector import CollectionCancelled, cnki_url
    work = Path(work)
    stock = [json.loads(p.read_text(encoding="utf-8")) for p in (work / "papers").glob("paper_*.json")]
    by_title = {}
    for paper in stock: by_title.setdefault(cnki.normalise(paper["title"]), []).append(paper)
    next_rank = max((p["rank"] for p in stock), default=0) + 1
    scan = {"keyword": task["keyword"], "total_results": None, "complete": False, "stop_reason": "running", "pages": [], "browser_engine": "playwright"}
    old_path = work / "collection_progress.json"
    old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.is_file() else {}
    if old:
        history = work / "scan_history"
        history.mkdir(exist_ok=True)
        (history / ("scan_" + uuid4().hex + ".json")).write_bytes(old_path.read_bytes())
    meta_path = work / "meta.json"
    prior_meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    previous_total = old.get("total_results") if old.get("total_results") is not None else prior_meta.get("total_results")
    added = sum(p["rank"] not in task["base_ranks"] for p in stock)
    position, failed, consecutive_failures = 0, 0, 0

    def save(): write_json(old_path, scan)
    def check():
        if cancel(): raise CollectionCancelled("文献采集已停止，进度已保留")
    try:
        check()
        site.search(task["keyword"])
        while True:
            check()
            listing = site.listing()
            number = len(scan["pages"]) + 1
            total = listing["total_results"]
            expected_total = scan["total_results"] if scan["total_results"] is not None else previous_total
            if type(total) is not int or total < 0 or (expected_total is not None and total != expected_total):
                scan.update(expected_total_results=expected_total, observed_total_results=total, failed_page=number)
                raise PageBlocked("result_total_unreadable" if type(total) is not int or total < 0 else "result_total_changed")
            if listing["number"] != number or (not listing["items"] and total): raise PageBlocked("page_number_or_rows_unreadable")
            scan["total_results"] = total
            write_json(work / "meta.json", {"keyword": task["keyword"], "total_results": total, "sort": "中国知网默认相关度排序", "collected_at": now()})
            page = {"number": number, "total_results": total, "has_next": listing["has_next"], "items": []}
            scan["pages"].append(page)
            save()
            for offset, item in enumerate(listing["items"]):
                check()
                position += 1
                if not cnki_url(item.get("url")) or not item.get("title"): raise PageBlocked("invalid_list_item")
                # A title alone never merges distinct source/author/date identities.
                matches = [p for p in by_title.get(cnki.normalise(item["title"]), []) if cnki.paper_identity(p) == cnki.paper_identity(item)]
                saved = matches[0] if len(matches) == 1 else None
                record = {"position": position, "title": item["title"], "url": item["url"]}
                if saved:
                    record.update(rank=saved["rank"], status=paper_status(saved, existing=True))
                    if not saved.get("abstract"): record["note"] = saved.get("note") or "既有记录中网站未提供摘要"
                else:
                    try:
                        detail = site.detail(item)
                    except PageBlocked as exc:
                        if exc.reason in {"detail_unavailable", "detail_title_mismatch", "abstract_may_be_truncated", "abstract_empty_or_still_loading"}:
                            record.update(rank=next_rank, status="failed", reason=exc.reason)
                            page["items"].append(record); failed += 1; consecutive_failures += 1; save()
                            if consecutive_failures >= 3: raise PageBlocked("consecutive_details_unavailable")
                            continue
                        if exc.reason != "http_404": raise
                        # Refresh through the ordinary search UI once, never alter a token.
                        refreshed = site.refresh(task["keyword"], number)
                        if refreshed["total_results"] != total or len(refreshed["items"]) != len(listing["items"]) or any(cnki.paper_identity(a) != cnki.paper_identity(b) for a, b in zip(refreshed["items"], listing["items"])):
                            raise PageBlocked("results_changed_during_link_refresh")
                        listing = refreshed
                        item = listing["items"][offset]
                        record["url"] = item["url"]
                        try: detail = site.detail(item)
                        except PageBlocked as retry:
                            if retry.reason != "http_404": raise
                            record.update(rank=next_rank, status="failed", reason="详情页刷新一次后仍返回404")
                            page["items"].append(record); failed += 1; consecutive_failures += 1; save()
                            if consecutive_failures >= 3: raise PageBlocked("consecutive_details_unavailable")
                            continue
                    if not detail.get("abstract") and not (detail.get("no_abstract_confirmed") or detail.get("title_only_confirmed")): raise PageBlocked("abstract_not_confirmed")
                    saved = {**item, **{k: detail.get(k) for k in ("abstract", "abstract_excerpt", "abstract_status", "keywords", "journal_level", "note", "record_labels") if k in detail},
                             "rank": next_rank, "cif": None, "is_modified": bool(re.search(r"加味|加减|化裁|合方", item["title"])), "western_diseases": []}
                    if not saved["abstract"]: saved["note"] = detail.get("note") or "正常详情页未提供摘要，仅保留标题和题录"
                    write_json(work / "papers" / ("paper_%04d.json" % next_rank), saved)
                    by_title.setdefault(cnki.normalise(saved["title"]), []).append(saved)
                    next_rank += 1; added += 1
                    record.update(rank=saved["rank"], status=paper_status(saved))
                    if not saved["abstract"]: record["note"] = saved["note"]
                consecutive_failures = 0
                page["items"].append(record); save()
                if task["limit"] is not None and added >= task["limit"]:
                    scan["stop_reason"] = "incremental_limit_reached"; save(); return scan
            if not listing["has_next"]:
                if position != total: raise PageBlocked("position_count_mismatch")
                scan.update(complete=failed == 0, stop_reason="failed_positions" if failed else "exhausted")
                save(); return scan
            site.next_page(number + 1)
    except BaseException as exc:
        scan["stop_reason"] = exc.reason if isinstance(exc, PageBlocked) else "cancelled" if isinstance(exc, CollectionCancelled) else "collection_error"
        if isinstance(exc, PageBlocked) and exc.state: scan["verification"] = exc.state
        save()
        raise


class CnkiSite:
    SEARCH_WAIT_SECONDS = 30

    def __init__(self, context, list_page, work, *, root=None, cancel=lambda: False, timeout=2400):
        self.context, self.page, self.work = context, list_page, Path(work)
        self.article = None
        self.root = root
        self.blocked_page = None
        self.cancel, self.deadline = cancel, time.monotonic() + timeout
        self.guard = Path(__file__).with_name("browser_guards.js").read_text(encoding="utf-8")
        self.title_reader = Path(__file__).with_name("browser_titles.js").read_text(encoding="utf-8")
        self.listing_reader = Path(__file__).with_name("browser_listing.js").read_text(encoding="utf-8")
        self.expected_page = 1
        self.navigation_responses = {}
        self.observe_page(list_page)

    def observe_page(self, page):
        def response_received(response):
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                self.navigation_responses[page] = response
        page.on("response", response_received)

    def check(self, page, response=None):
        from .collector import CollectionCancelled
        if self.cancel(): raise CollectionCancelled("文献采集已停止，进度已保留")
        if time.monotonic() > self.deadline: raise PageBlocked("collection_timeout")
        response = response or self.navigation_responses.get(page)
        status = response.status if response else None
        if status in {403, 429}:
            self.blocked_page = page
            self.save_navigation_diagnostic(page, response, "http_403" if status == 403 else "rate_limited")
            # Chromium may replace a rejected page with chrome-error://chromewebdata/.
            # The actual navigation response retains the rejected CNKI URL/status.
            if self.root and status == 403: browser_session.remember_manual_page(self.root, response.url)
            raise PageBlocked("http_403" if status == 403 else "rate_limited")
        if status == 404: raise PageBlocked("http_404")
        state = page.evaluate("() => {" + self.guard + "; return cnkiPageState();}")
        if state["blocking_verification"]:
            self.blocked_page = page
            page.screenshot(path=str(self.work / "blocking-verification.png"))
            if self.root: browser_session.remember_manual_page(self.root, page.url)
            raise PageBlocked("captcha", state)
        if state.get("login_required"):
            self.blocked_page = page
            if self.root: browser_session.remember_manual_page(self.root, page.url)
            raise PageBlocked("login_required", state)
        return state

    def search(self, keyword):
        self.expected_page = 1
        response = self.page.goto("https://www.cnki.net/", wait_until="domcontentloaded", timeout=45000)
        self.check(self.page, response)
        field = self.page.locator("#txt_SearchText:visible,#txt_sug:not([type=hidden]):visible").first
        field.wait_for(timeout=20000)
        field.fill(keyword)
        self.page.locator(".search-btn").first.click()
        deadline = min(self.deadline, time.monotonic() + self.SEARCH_WAIT_SECONDS)
        from playwright.sync_api import Error
        while time.monotonic() < deadline:
            # Pump navigation events before inspecting the DOM, so a 403/429 is
            # diagnosed promptly even when Chromium shows its internal error page.
            try:
                self.page.wait_for_timeout(200)
                state = self.check(self.page)
                if state["result_rows"] or self.page.locator(".no-result:visible,.no-results:visible").count():
                    self.page.wait_for_timeout(1200)
                    self.check(self.page)
                    return
            except Error:
                if self.page.is_closed(): raise PageBlocked("search_page_closed")
                # A navigation can destroy an execution context between polls.
                continue
        self.check(self.page)
        self.blocked_page = self.page
        self.save_navigation_diagnostic(self.page, self.navigation_responses.get(self.page), "search_results_unavailable")
        if self.root: browser_session.remember_manual_page(self.root, "https://www.cnki.net/")
        raise PageBlocked("search_results_unavailable")

    def save_navigation_diagnostic(self, page, response, reason):
        """Retain only bounded technical state, never form values or page HTML."""
        from urllib.parse import urlparse
        url = urlparse(response.url if response else page.url)
        name = "search-diagnostic.json" if page == self.page else "article-diagnostic.json"
        write_json(self.work / name, {"host": url.hostname, "path": url.path,
                   "http_status": response.status if response else None, "reason": reason})

    def listing(self):
        deadline = min(self.deadline, time.monotonic() + 15)
        previous = None
        data = {}
        from playwright.sync_api import Error
        while time.monotonic() < deadline:
            try:
                self.check(self.page)
                data = self.page.evaluate("() => {" + self.listing_reader + "; return cnkiListingSnapshot();}")
                ready = (type(data.get("total_results")) is int and data["total_results"] >= 0
                         and data.get("number") == self.expected_page
                         and (bool(data.get("items")) or data["total_results"] == 0))
                if ready and data == previous:
                    return {key: data[key] for key in ("total_results", "number", "has_next", "items")}
                previous = data if ready else None
            except Error:
                if self.page.is_closed(): raise PageBlocked("search_page_closed")
                previous = None
            self.page.wait_for_timeout(200)
        write_json(self.work / "listing-diagnostic.json", {"expected_page": self.expected_page,
                   "observed_page": data.get("number"), "observed_total_results": data.get("total_results"),
                   "count_text": str(data.get("count_text") or "")[:80], "rows": len(data.get("items") or [])})
        raise PageBlocked("result_total_unreadable")

    def next_page(self, number):
        before = self.page.evaluate("() => {" + self.listing_reader + "; return cnkiListingSnapshot();}")
        identity = [{key: item.get(key, "") for key in ("title", "authors", "source", "date")} for item in before["items"]]
        self.expected_page = number
        self.page.locator("a:visible").filter(has_text=re.compile(r"^下一页$")).first.click()
        self.page.wait_for_function("args => {" + self.listing_reader + """
          const listing=cnkiListingSnapshot();
          const identity=listing.items.map(row=>({title:row.title,authors:row.authors,source:row.source,date:row.date}));
          return listing.number===args.number && listing.items.length>0 && JSON.stringify(identity)!==JSON.stringify(args.before);
        }""", arg={"before": identity, "number": number}, timeout=30000)
        self.check(self.page)

    def refresh(self, keyword, number):
        self.search(keyword)
        for page_number in range(2, number + 1): self.next_page(page_number)
        return self.listing()

    def detail(self, item):
        if self.article is None or self.article.is_closed():
            self.article = self.context.new_page()
            self.observe_page(self.article)
        response = self.article.goto(item["url"], wait_until="domcontentloaded", timeout=45000)
        self.article.wait_for_timeout(1200)
        self.check(self.article, response)
        if urlparse(self.article.url).hostname == "thinker.cnki.net" and urlparse(self.article.url).path == "/bookstore/book/bookdetail":
            # The search includes books. Their chapter text/introduction is not a paper abstract.
            if not self.article.get_by_text(item["title"], exact=True).first.is_visible(): raise PageBlocked("detail_title_mismatch")
            return {"abstract": None, "keywords": [], "journal_level": None, "no_abstract_confirmed": True,
                    "note": "图书记录，正常详情页未提供论文摘要；未将内容简介或章节正文当作摘要"}
        try: self.article.locator("#ChDivSummary,.abstract-text,.brief h1,.wx-tit h1").first.wait_for(timeout=15000)
        except Exception:
            self.check(self.article)
            raise PageBlocked("detail_unavailable")
        # Expand only the abstract control, never open HTML/full-text/PDF readers.
        self.article.evaluate("""() => {const more=[...document.querySelectorAll('a,span')].find(e=>e.children.length===0 && e.innerText.trim()==='更多' && e.checkVisibility());if(more)more.click();}""")
        self.article.wait_for_timeout(250)
        state = self.check(self.article)
        data = self.article.evaluate("() => {" + self.title_reader + """; return {...cnkiTitleInfo(),abstract:document.querySelector('#ChDivSummary')?.innerText.trim()||document.querySelector('.abstract-text')?.innerText.trim()||null,
          keywords:[...document.querySelectorAll('.keywords a')].map(a=>a.innerText.trim().replace(/;$/,'')),journal_level:null,
          more_visible:[...document.querySelectorAll('a,span')].some(e=>e.children.length===0&&e.innerText.trim()==='更多'&&e.checkVisibility())};}""")
        if not title_matches(item["title"], data["headings"], self.article.title(), heading_titles=data["heading_titles"]): raise PageBlocked("detail_title_mismatch")
        if data["record_labels"]: data["note"] = "知网详情题名附标：" + "、".join(data["record_labels"]) + "。"
        if data["more_visible"]: raise PageBlocked("abstract_may_be_truncated")
        if (data["abstract"] or "").endswith(("...", "…")):
            data.update(abstract_excerpt=data["abstract"], abstract=None, abstract_status="excerpt_only", title_only_confirmed=True,
                        note=(data.get("note") or "") + "正常详情页摘要区仅提供节选，且没有可展开的更多内容；节选单独保留，不当完整摘要，仅依据标题分析")
            return data
        if not data["abstract"] and self.article.locator("#ChDivSummary,.abstract-text").count(): raise PageBlocked("abstract_empty_or_still_loading")
        data["no_abstract_confirmed"] = not data["abstract"] and not state["blocking_verification"]
        if data["no_abstract_confirmed"]: data["note"] = (data.get("note") or "") + "正常详情页未提供论文摘要；仅保留题名和题录，不补写摘要。"
        return data


def collect_in_mode(task, work, *, root, mode, cancel, timeout):
    from playwright.sync_api import sync_playwright, Error
    address = browser_session.background_endpoint(root) if mode == "background" else browser_session.interactive_collection_endpoint(root)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(address)
        context = browser.contexts[0]
        page = context.new_page()
        site = CnkiSite(context, page, work, root=root, cancel=cancel, timeout=timeout)
        keep = None
        try: return collect_pages(task, work, site, cancel=cancel)
        except PageBlocked as exc:
            if mode == "interactive" and exc.reason in {"captcha", "login_required", "http_403", "search_results_unavailable"}:
                keep = getattr(site, "blocked_page", None)
            raise
        finally:
            # Preserve a real visible challenge for manual handling; never
            # operate it automatically or close unrelated project tabs.
            for owned_page in (page, site.article):
                try:
                    if owned_page and owned_page is not keep and not owned_page.is_closed(): owned_page.close()
                except Error: pass


def collect(task, work, *, root, cancel=lambda: False):
    from .collector import CollectionCancelled
    mode = task.get("browser_mode", "background")
    if mode not in {"background", "interactive"}: raise ValueError("采集浏览器模式无效")
    deadline = time.monotonic() + 2400
    for attempt in range(2):
        if cancel(): raise CollectionCancelled("文献采集已停止，进度已保留")
        try:
            return collect_in_mode(task, work, root=root, mode=mode, cancel=cancel, timeout=max(1, deadline-time.monotonic()))
        except PageBlocked as exc:
            if exc.reason != "http_403" or mode != "background" or attempt:
                raise
            # User policy: one normal headed retry after an actual main-frame
            # 403. The same task/profile/fragments are retained; no UA spoofing.
            mode = "interactive"
            task.update(browser_mode=mode, browser_fallback="http_403")
            write_json(Path(work) / "task.json", task)
