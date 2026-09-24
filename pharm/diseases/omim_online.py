"""OMIM Gene Map 在线采集：授权页面/导出链接 + 人机验证交接。

该模块只负责打开用户有权访问的 OMIM 页面并保存原始导出文件；不绕过
登录、验证码或下载权限。导出的 xlsx/zip 仍由 omim_export.py 严格转换。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..core.common import now, write_json

SEARCH_URL = os.environ.get("PHARM_OMIM_SEARCH_URL", "https://www.omim.org/search?search={query}")
CHALLENGE_TITLES = ("Just a moment", "Cloudflare", "Attention Required", "Verify", "Sign in")


def _assist_json(base_url, path, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(base_url.rstrip("/") + path, data=body,
                      headers={"Content-Type": "application/json"} if body else {})
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError("无法连接人机协助工作台（127.0.0.1:8766）。请先启动 server/app.py：" + str(exc)) from exc


def wait_for_assist(base_url, url, disease, meta, timeout=900):
    request = _assist_json(base_url, "/api/assist/request", {
        "url": url,
        "guidance": "请完成 OMIM 登录/人机验证；完成后点击‘验证完成，继续采集’。",
        "context": {"source": "omim", "disease": disease},
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
    raise RuntimeError("等待用户完成 OMIM 人机验证超时：" + disease)


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "omim"


def collect_online(diseases, directory, headless=False, assist_base_url=None, timeout=900):
    """采集每个疾病的 OMIM Gene Map 导出链接，返回台账。

    页面结构或权限变化时严格失败并保留 HTML；不会把搜索摘要当作 Gene Map 结果。
    """
    if not diseases or any(not isinstance(d, str) or not d.strip() for d in diseases):
        raise ValueError("diseases 必须是非空关键词列表")
    assist_base_url = assist_base_url or os.environ.get("PHARM_ASSIST_URL", "http://127.0.0.1:8766")
    directory = Path(directory)
    raw = directory / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    meta = {"source": "OMIM online Gene Map", "started_at": now(), "headless": headless,
            "queries": [], "actions": []}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        try:
            for disease in diseases:
                url = SEARCH_URL.format(query=quote(disease))
                response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                title = page.title()
                if response is None or (response.status and response.status >= 400) or any(x.lower() in title.lower() for x in CHALLENGE_TITLES):
                    page.screenshot(path=str(raw / ("challenge_" + _safe_name(disease) + ".png")))
                    meta["actions"].append({"action": "await_human_verification", "disease": disease, "url": page.url, "at": now()})
                    wait_for_assist(assist_base_url, page.url or url, disease, meta, timeout=timeout)
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2000)
                html_path = raw / ("search_" + _safe_name(disease) + ".html")
                html_path.write_text(page.content(), encoding="utf-8")
                links = page.locator("a").evaluate_all("els => els.map(a => ({text:a.innerText, href:a.href}))")
                candidates = [item for item in links if re.search(r"(\.xlsx|\.zip|download|gene.?map)", (item.get("href") or "") + " " + (item.get("text") or ""), re.I)]
                if not candidates:
                    raise RuntimeError("未找到 OMIM Gene Map 导出链接，可能需要授权或页面结构已变化：" + disease)
                downloaded = None
                for item in candidates:
                    try:
                        with page.expect_download(timeout=15000) as info:
                            page.goto(item["href"], wait_until="domcontentloaded", timeout=30000)
                        downloaded = info.value
                        break
                    except Exception:
                        continue
                if downloaded is None:
                    raise RuntimeError("找到 OMIM 链接但未能下载 Gene Map 文件：" + disease)
                target = raw / (_safe_name(disease) + "_genemap." + (downloaded.suggested_filename.split(".")[-1] or "xlsx"))
                downloaded.save_as(str(target))
                meta["queries"].append({"disease": disease, "source_url": url,
                                        "export_file": target.relative_to(directory).as_posix()})
        finally:
            browser.close()
    meta["finished_at"] = now()
    write_json(raw / "omim_online.json", meta)
    return meta
