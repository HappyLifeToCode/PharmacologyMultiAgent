"""Execute the official Venny UI; local set arithmetic only verifies its output."""
from __future__ import annotations

import base64
import re
from pathlib import Path

from .common import digest, now, write_json
from .processing import intersection

URL = "https://bioinfogp.cnb.csic.es/tools/venny/"
VERSION = "2.1.0"


def parse_region(text, labels, common=False):
    lines = text.strip().splitlines()
    suffix = ('common elements? in "' + re.escape(labels[0]) + '" and "' + re.escape(labels[1]) + '":') if common else ('elements? included exclusively in "' + re.escape(labels[0]) + '":')
    match = re.fullmatch(r"(\d+) " + suffix, lines[0] if lines else "")
    genes = [line.strip() for line in lines[1:] if line.strip()]
    if not match or int(match[1]) != len(genes) or len(set(genes)) != len(genes):
        raise ValueError("Venny result header/count/unique genes could not be verified")
    return sorted(genes)


def verify_result(result, herb, disease):
    if result != intersection(herb, disease):
        raise ValueError("Venny results differ from independent Python set verification")


def run_venny(herb, disease, directory, synthetic=False):
    from playwright.sync_api import sync_playwright, expect

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "sources"
    source.mkdir(exist_ok=True)
    traces = directory / "browser" / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    herb, disease = sorted(set(herb)), sorted(set(disease))
    labels = ("Herb SYNTHETIC", "Disease SYNTH") if synthetic else ("Herb targets", "Disease targets")
    metadata = {"tool": "Venny", "version": VERSION, "requested_url": URL,
                "started_at": now(), "status": "running", "synthetic": synthetic,
                "driver": "Python Playwright (deterministic UI tool)", "actions": []}
    for name, genes in (("herb", herb), ("disease", disease)):
        path = directory / (name + "_input.txt")
        path.write_text("\n".join(genes) + ("\n" if genes else ""), encoding="utf-8")
        metadata[name + "_input_sha256"] = digest(path)
    try:
        # An absent list does not yield a two-set figure in the official UI.
        if not herb or not disease:
            raise ValueError("Venny two-list execution requires two nonempty inputs; an upstream list is empty")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="en-US")
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page.set_default_timeout(15000)
            try:
                response = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                metadata.update(final_url=page.url, accessed_at=now(), http_status=response.status if response else None, title=page.title())
                if not response or not response.ok or metadata["title"] != "Venny 2.1.0":
                    raise ValueError("Official Venny 2.1.0 page/version unavailable")
                (source / "official_page.html").write_bytes(response.body())
                metadata["source_sha256"] = digest(source / "official_page.html")
                for index, (label, genes) in enumerate(zip(labels, (herb, disease)), 1):
                    name = page.locator("#name%d" % index)
                    name.fill(label)
                    # Official UI commits list names via its 500 ms focus timer.
                    page.wait_for_timeout(650)
                    name.press("Tab")
                    if len(genes) > 500:
                        # 大名单直接 JS 赋值并触发 change；fill 会被页面 500ms 轮询的
                        # compareLists 反复阻塞而超时
                        page.evaluate("""([selector, text]) => {
                            const el = document.querySelector(selector);
                            el.value = text;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            el.blur();
                        }""", ["#area%d" % index, "\n".join(genes)])
                    else:
                        area = page.locator("#area%d" % index)
                        area.fill("\n".join(genes))
                        area.press("End")
                        area.press("Enter")
                        area.press("Tab")
                    expect(page.locator("#elements%d" % index)).to_have_text(str(len(genes)), timeout=120000)
                    metadata["actions"].append({"action": "fill_list_and_blur", "list": index, "label": label, "count": len(genes), "at": now()})
                results, raw = {}, []
                for key, selector, region_labels in (("herb_only", "#resultC1000", (labels[0],)), ("disease_only", "#resultC0100", (labels[1],)), ("genes", "#resultC1100", labels)):
                    page.locator(selector).click()
                    value = page.locator("#names").input_value()
                    raw.append(value)
                    (directory / "venny_results.txt").write_text("\n\n".join(raw), encoding="utf-8")
                    results[key] = parse_region(value, region_labels, common=key == "genes")
                    metadata["actions"].append({"action": "read_region", "selector": selector, "count": len(results[key]), "at": now()})
                results.update(herb_count=len(herb), disease_count=len(disease), intersection_count=len(results["genes"]))
                png_url = page.locator("#image").get_attribute("src") or ""
                if not png_url.startswith("data:image/png;base64,"):
                    raise ValueError("Venny original PNG is missing")
                png = base64.b64decode(png_url.split(",", 1)[1], validate=True)
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Invalid Venny PNG")
                (directory / "venny.png").write_bytes(png)
                page.screenshot(path=str(directory / "venny_page.png"), full_page=True)
                verify_result(results, herb, disease)
                metadata.update(status="succeeded", python_crosscheck=True, counts={k: results[k] for k in ("herb_count", "disease_count", "intersection_count")})
                return results
            except Exception:
                try:
                    page.screenshot(path=str(directory / "venny_failure.png"), full_page=True, timeout=5000)
                except Exception:
                    pass
                raise
            finally:
                try:
                    context.tracing.stop(path=str(traces / "venny.zip"))
                finally:
                    browser.close()
    except Exception as exc:
        metadata.update(status="failed", error=str(exc), python_crosscheck=False)
        raise
    finally:
        metadata["finished_at"] = now()
        metadata["artifacts_sha256"] = {p.name: digest(p) for p in directory.iterdir() if p.is_file() and p.name != "venny_execution.json"}
        write_json(directory / "venny_execution.json", metadata)
