"""GeneCards 在线采集：运行时经 Playwright（headed Chromium）查询检索结果页。

Cloudflare 拦截无头浏览器，因此采集必须使用有窗口的 headed 模式；遇到人机验证
明确失败并保留截图证据，不绕过。导出走分页逐页读取，每关键词核对解析行数与
页面声明总条数，不一致即失败——不允许第一页冒充全表。匿名账号无批量导出
（下载菜单仅提供登录入口），逐页采集是当前唯一在线途径。
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..core.common import ROOT, digest, now, write_json
from ..core.symbols import _valid_symbol

SEARCH_URL = "https://www.genecards.org/search/results?q="
_CARD_LINK = re.compile(r"/card/([A-Za-z0-9.\-_]+)\?")
_TOTAL_PATTERNS = [
    re.compile(r"of\s+([\d,]+)\b"),
    re.compile(r"([\d,]+)\D{0,60}?results by", re.I),
]
_VERSION_RE = re.compile(r"Version [\d.]+\s*Build [A-Za-z0-9 ,]+")
CHALLENGE_TITLES = ("Just a moment", "Cloudflare", "Attention Required", "Verify Your Access", "Datacenter")


class _RowsParser(HTMLParser):
    """GeneCards 结果表：行内 /card/ 链接给 Symbol，第 6 列是 Relevance Score。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._in_tr = False
        self._cells = []
        self._cell_text = None
        self._gene = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_tr, self._cells, self._gene = True, [], None
        elif self._in_tr and tag in ("td", "th"):
            self._cell_text = ""
        elif self._in_tr and tag == "a":
            match = _CARD_LINK.search(dict(attrs).get("href", ""))
            if match:
                self._gene = match.group(1)

    def handle_data(self, data):
        if self._cell_text is not None:
            self._cell_text += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell_text is not None:
            self._cells.append(self._cell_text.strip())
            self._cell_text = None
        elif tag == "tr" and self._in_tr:
            if self._gene:
                self.rows.append((self._gene, list(self._cells)))
            self._in_tr = False


def parse_rows_from_html(html):
    """Parse one results page -> [{gene_symbol, relevance_score, gene_type}]; strict columns."""
    parser = _RowsParser()
    parser.feed(html)
    rows = []
    for gene, cells in parser.rows:
        if len(cells) < 6:
            raise ValueError("结果行列数不足（结构可能变化）：%s -> %r" % (gene, cells[:6]))
        try:
            score = float(cells[5].replace(",", ""))
        except ValueError as exc:
            raise ValueError("结果行 Relevance Score 列无法解析：%s -> %r" % (gene, cells[5])) from exc
        if not _valid_symbol(gene):
            raise ValueError("无效基因符号：%r" % gene)
        rows.append({"gene_symbol": gene, "relevance_score": score, "gene_type": cells[4]})
    return rows


def parse_declared_total(html):
    for pattern in _TOTAL_PATTERNS:
        match = pattern.search(html)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def parse_site_version(html):
    match = _VERSION_RE.search(html)
    return match.group(0).strip() if match else None


class _RetryableCollection(Exception):
    """间歇性 API 403 / 表格未刷新 / 翻页超时——整词重载后再试。"""


def _collect_one_disease(page, disease, declared_hint, directory, pages_root,
                         attempt, page_delay_ms, api_state, meta):
    """Collect one keyword's complete rows; returns (rows, declared_total)."""
    ROWS = "table tbody tr:has(a[href*='/card/'])"
    try:
        page.wait_for_selector(ROWS, timeout=45000)
    except Exception as exc:
        raise _RetryableCollection("结果表未出现：" + str(exc))
    html = page.content()
    declared = parse_declared_total(html)
    if declared is None:
        raise ValueError("页面未给出结果总数，无法核验完整性：" + disease)
    size_select = None
    for i in range(page.locator("select").count()):
        candidate = page.locator("select").nth(i)
        if candidate.locator("option").all_inner_texts() == ["10", "20", "50", "100"]:
            size_select = candidate
            break
    if size_select is None:
        raise ValueError("未找到每页条数选择器（结构可能变化）")
    blocked_before = api_state["blocked"]
    size_select.select_option("100")
    expected_rows = min(100, declared)
    deadline = time.monotonic() + 60
    while page.locator(ROWS).count() != expected_rows:
        if api_state["blocked"] > blocked_before:
            raise _RetryableCollection("切换条数时 SearchApi 返回 403")
        if time.monotonic() > deadline:
            raise _RetryableCollection("切换每页 100 条后结果表未刷新")
        page.wait_for_timeout(500)
    page.wait_for_timeout(page_delay_ms)
    rows, seen, page_no = [], set(), 0
    first_no = ROWS + " td:nth-child(2)"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", disease)
    while True:
        page_no += 1
        html = page.content()
        (pages_root / ("%s_try%d_page%02d.html" % (slug, attempt, page_no))).write_text(html, encoding="utf-8")
        for row in parse_rows_from_html(html):
            key = row["gene_symbol"]
            if key in seen:
                raise ValueError("重复分页或重复行：%s 在 %s 第 %d 页重复出现" % (key, disease, page_no))
            seen.add(key)
            rows.append(row)
        meta["actions"].append({"action": "read_page", "disease": disease, "page": page_no,
                                "rows_so_far": len(rows), "at": now()})
        next_link = page.get_by_role("link", name="Next")
        next_disabled = (next_link.count() == 0
                         or next_link.get_attribute("disabled") is not None
                         or "disabled" in (next_link.first.get_attribute("class") or "")
                         or "disabled" in (next_link.first.evaluate(
                             "e => e.closest('li') ? e.closest('li').className : ''") or ""))
        if next_disabled:
            break
        marker = page.locator(first_no).first.text_content()
        blocked_before = api_state["blocked"]
        next_link.click()
        deadline = time.monotonic() + 45
        while page.locator(first_no).first.text_content() == marker:
            if api_state["blocked"] > blocked_before:
                raise _RetryableCollection("翻页时 SearchApi 返回 403（第 %d 页后）" % page_no)
            if time.monotonic() > deadline:
                raise _RetryableCollection("翻页后结果表未更新（第 %d 页后）" % page_no)
            page.wait_for_timeout(300)
        page.wait_for_timeout(page_delay_ms)
        if page_no > declared / 100 + 2:
            raise ValueError("页数超出总数预期，分页异常：" + disease)
    if len(rows) != declared:
        raise ValueError("%s 解析行数 %d 与页面声明总数 %d 不一致，不得按完整结果使用"
                         % (disease, len(rows), declared))
    return rows, declared


def _assist_json(base_url, path, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(base_url.rstrip("/") + path, data=body,
                      headers={"Content-Type": "application/json"} if body else {})
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError("无法连接人机协助工作台（127.0.0.1:8766）。请先启动 server/app.py：" + str(exc)) from exc


def _wait_for_assist(base_url, url, disease, meta, timeout=900):
    """将当前页面交给工作台，等待用户完成验证后再返回采集器。"""
    request = _assist_json(base_url, "/api/assist/request", {
        "url": url,
        "guidance": "请完成 GeneCards 人机验证；完成后点击‘验证完成，继续采集’。",
        "context": {"source": "genecards", "disease": disease},
    })
    request_id = request["request_id"]
    meta["actions"].append({"action": "assist_requested", "disease": disease,
                            "request_id": request_id, "at": now()})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _assist_json(base_url, "/api/assist/status")
        pending = status.get("pending") or {}
        if pending.get("request_id") == request_id:
            time.sleep(2)
            continue
        if status.get("state") in ("done", "closed"):
            meta["actions"].append({"action": "assist_completed", "disease": disease,
                                    "request_id": request_id, "at": now()})
            return
        time.sleep(2)
    raise RuntimeError("等待用户完成人机验证超时（15 分钟）：" + disease)


def collect_online(diseases, directory, headless=False, page_delay_ms=1800,
                   assist_base_url=None):
    """Query each disease keyword online and emit genecards.csv + provenance.json.

    Returns collection metadata. Any challenge, pagination mismatch or structural
    surprise raises with evidence preserved under directory/.
    """
    if not diseases or any(not isinstance(d, str) or not d.strip() for d in diseases):
        raise ValueError("diseases 必须是非空关键词列表")
    assist_base_url = assist_base_url or os.environ.get("PHARM_ASSIST_URL", "http://127.0.0.1:8766")
    from playwright.sync_api import sync_playwright

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / "raw"
    pages_root = raw / "pages"
    pages_root.mkdir(parents=True, exist_ok=True)
    meta = {"source": "GeneCards online search results", "started_at": now(),
            "driver": "Python Playwright headed Chromium", "headless": headless,
            "queries": [], "actions": []}
    all_rows = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        # 登录态仅存本机 gitignore 目录，供下次免登录复用；不含密码
        state_path = ROOT / "local" / "browser-auth" / "genecards_storage_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        context_kwargs = {"viewport": {"width": 1440, "height": 1000}, "locale": "en-US"}
        if state_path.is_file():
            context_kwargs["storage_state"] = str(state_path)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(30000)
        site_version = None
        page = context.new_page()
        page.set_default_timeout(30000)
        site_version = None
        api_state = {"blocked": 0}
        def watch_api(resp):
            if "SearchApi" in resp.url and resp.status == 403:
                api_state["blocked"] += 1
                meta["actions"].append({"action": "search_api_403", "url": resp.url[:80], "at": now()})
        page.on("response", watch_api)
        try:
            for disease in diseases:
                url = SEARCH_URL + quote(disease)
                query_rows, page_no = None, 0
                for attempt in (1, 2, 3):  # 间歇性 API 403/加载慢：整词重载重试；人机验证不重试
                    response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2500)
                    title = page.title()
                    if response is None or any(c in title for c in CHALLENGE_TITLES) \
                            or (response.status is not None and response.status != 200):
                        page.screenshot(path=str(directory / "challenge.png"))
                        meta["actions"].append({"action": "await_human_verification",
                                                "disease": disease, "title": title, "at": now()})
                        _wait_for_assist(assist_base_url, page.url or url, disease, meta)
                        # 登录流程结束后页面可能不在结果页，主动回到检索 URL
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(2500)
                        meta["actions"].append({"action": "human_verification_completed",
                                                "disease": disease, "at": now()})
                    dismiss = page.get_by_role("button", name="Dismiss for this session")
                    if dismiss.count():
                        dismiss.click()
                    try:
                        query_rows, page_no = _collect_one_disease(
                            page, disease, declared_hint=None, directory=directory,
                            pages_root=pages_root, attempt=attempt, page_delay_ms=page_delay_ms,
                            api_state=api_state, meta=meta)
                        break
                    except _RetryableCollection as exc:
                        meta["actions"].append({"action": "retry_disease", "disease": disease,
                                                "attempt": attempt, "reason": str(exc), "at": now()})
                        if attempt == 3:
                            page.screenshot(path=str(directory / ("failed_%s.png" % disease)))
                            raise RuntimeError("%s 三次采集均未完成（最后原因：%s）" % (disease, exc))
                site_version = site_version or parse_site_version(page.content())
                meta["queries"].append({"disease": disease, "declared_total": query_rows[1],
                                        "collected_rows": len(query_rows[0]), "pages": page_no})
                all_rows.extend({"disease": disease, "gene_symbol": r["gene_symbol"],
                                 "relevance_score": r["relevance_score"]} for r in query_rows[0])
                page.wait_for_timeout(page_delay_ms)
        finally:
            try:
                context.storage_state(path=str(state_path))  # 仅本机复用登录态，不入库
            except Exception:
                pass
            browser.close()
    meta.update(finished_at=now(), site_version=site_version,
                total_rows=len(all_rows))
    write_json(raw / "genecards_online.json", meta)
    with (directory / "genecards.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["disease", "gene_symbol", "relevance_score"])
        writer.writeheader()
        writer.writerows(all_rows)
    page_files = sorted(p.relative_to(directory).as_posix() for p in pages_root.rglob("*.html"))
    provenance = {"sources": {"genecards": {
        "complete": True,
        "source_url": "https://www.genecards.org/",
        "accessed_at": now()[:10],
        "diseases": list(diseases),
        "raw_files": page_files + ["raw/genecards_online.json"],
        "version": site_version,
        "collection": "online search results via headed Playwright; rows == declared total per keyword",
    }}, "mapping": {
        "confirmed": True,
        "method": "GeneCards 检索结果 symbol 与 relevance score 按页面发布原样使用，无标识映射",
        "version": site_version or "unknown",
        "raw_files": ["raw/genecards_online.json"],
    }}
    write_json(directory / "provenance.json", provenance)
    return meta
