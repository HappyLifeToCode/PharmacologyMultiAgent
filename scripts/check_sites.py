"""Date-stamped, read-only browser connectivity evidence (no scientific exports)."""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm_demo.common import ROOT, write_json
from pharm_demo.sources import SOURCES, probe_all
from playwright.sync_api import sync_playwright


def main():
    target = ROOT / "runs/diagnostics" / ("sites_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    target.mkdir(parents=True, exist_ok=False)
    records = probe_all(target / "http")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for name, url in SOURCES.items():
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="en-US", timezone_id="Asia/Shanghai")
            page = context.new_page()
            evidence = {"source": name, "requested_url": url, "accessed_at": datetime.now().astimezone().isoformat(), "scope": "homepage_connectivity_only"}
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                evidence["http_status"] = response.status if response else None
                page.wait_for_timeout(2500)
            except Exception as exc:
                evidence["navigation_error"] = str(exc)[:1200]
            try:
                evidence.update(final_url=page.url, title=page.title(), text=page.locator("body").inner_text(timeout=6000)[:14000])
                evidence["links"] = page.locator("a[href]").evaluate_all("els => els.map(a=>({text:a.innerText,url:a.href})).filter(a=>/register|account|download|api|sign|start analysis/i.test(a.text)).slice(0,30)")
                page.screenshot(path=str(target / (name + ".png")), full_page=False)
                (target / (name + ".html")).write_text(page.content(), encoding="utf-8")
            except Exception as exc:
                evidence["capture_error"] = str(exc)[:1200]
            write_json(target / (name + ".json"), evidence)
            records[name]["browser"] = evidence
            print(json.dumps(evidence, ensure_ascii=False), flush=True)
            context.close()
        browser.close()
    write_json(target / "report.json", records)
    print("Evidence: " + str(target), flush=True)


if __name__ == "__main__":
    main()
