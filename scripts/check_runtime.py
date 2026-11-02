"""Read-only model/browser handshake. No scientific result is produced."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm.agents.runtime import execute
from pharm.core.common import ROOT
from datetime import datetime

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--site", choices=["string", "david"], default="string")
    options = parser.parse_args()
    output = ROOT / "runs/diagnostics" / ((options.site if options.browser else "isolated_model") + "_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    prompt = "仅执行连通性检查。不要读取项目其他文件，不要修改项目代码。"
    if options.browser:
        url = "https://string-db.org/" if options.site == "string" else "https://davidbioinformatics.nih.gov/"
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                observed = {"url": url, "http_status": response.status if response else None,
                            "title": page.title(), "text": page.locator("body").inner_text()[:800]}
                page.screenshot(path=str(output / "connectivity.png"))
            finally: browser.close()
        prompt += "以下是程序实际访问的页面记录，只核对访问状态，不声称已完成研究分析：" + json.dumps(observed, ensure_ascii=False)
    else:
        prompt += "不要使用工具。返回 status=succeeded，summary=READY，其余数组为空。"
    result, meta = execute(prompt, output, timeout=240)
    print(json.dumps({"result": result, "execution": meta}, ensure_ascii=False, indent=2))
