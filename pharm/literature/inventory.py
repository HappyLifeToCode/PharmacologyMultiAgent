"""Freeze a verified listing before opening abstracts; never freeze a partial list."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..core.common import now, write_json
from . import cnki


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate(value, keyword):
    from .collector import cnki_url
    if not isinstance(value, dict): raise ValueError("固定题录清单格式错误")
    data = value.get("data")
    if not isinstance(data, dict) or value.get("sha256") != checksum(data):
        raise ValueError("固定题录清单校验失败；保留文件，请核对后处理")
    total, pages = data.get("total_results"), data.get("pages")
    if data.get("version") not in {1, 2, 3} or data.get("keyword") != keyword or type(total) is not int or total < 0 or not isinstance(pages, list) or not pages:
        raise ValueError("固定题录清单定义无效")
    seen, count, page_signatures, duplicates = {}, 0, set(), []
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or page.get("number") != number or type(page.get("total_results")) is not int or page["total_results"] < 0 or page.get("has_next") is not (number < len(pages)):
            raise ValueError("固定题录清单页码或总数不完整")
        items = page.get("items")
        if not isinstance(items, list) or (not items and (total != 0 or len(pages) != 1)):
            raise ValueError("固定题录清单缺少页面题录")
        signature = tuple(cnki.paper_identity(item) for item in items if isinstance(item, dict))
        if (page.get("batch_id"), signature) in page_signatures: raise ValueError("固定清单包含整页重复，无法确认翻页成功")
        page_signatures.add((page.get("batch_id"), signature))
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip() or not cnki_url(item.get("url")):
                raise ValueError("固定题录清单题名或来源链接无效")
            identity = cnki.paper_identity(item)
            count += 1
            if identity in seen:
                if data["version"] == 1: raise ValueError("旧版固定清单包含重复论文位置")
                duplicates.append({"position": count, "first_position": seen[identity]})
            else: seen[identity] = count
    if count != total: raise ValueError("固定题录清单未覆盖全部检索位置")
    if data["version"] != 3 and (pages[0]["total_results"] != total or pages[-1]["total_results"] != total):
        raise ValueError("固定题录清单首末页计数与实际覆盖不一致")
    if data["version"] >= 2 and (data.get("unique_paper_count") != len(seen) or data.get("duplicate_positions") != duplicates):
        raise ValueError("清单去重计数或重复位置记录不一致")
    if data["version"] == 3:
        from . import batches
        batches.validate(data)
    return data


def load(work, keyword):
    value = json.loads((Path(work) / "listing_inventory.json").read_text(encoding="utf-8"))
    validate(value, keyword)
    return value


def prepare(task, work, site, cancel, *, batching=True):
    from .browser_collector import PageBlocked
    from .collector import CollectionCancelled, cnki_url
    work = Path(work)
    path = work / "listing_inventory.json"
    if path.exists(): return load(work, task["keyword"])
    if batching and hasattr(site, "open_scope"):
        from . import batches
        return batches.prepare(task, work, site, cancel)
    progress_path = work / "listing_progress.json"
    if progress_path.exists():
        directory = work / "listing_history"
        directory.mkdir(exist_ok=True)
        (directory / (uuid4().hex + ".json")).write_bytes(progress_path.read_bytes())
    progress = {"keyword": task["keyword"], "phase": "listing", "complete": False,
                "total_results": None, "pages": [], "listed_count": 0, "count_discrepancies": [], "unique_paper_count": 0, "duplicate_positions": [], "stop_reason": "running"}
    seen, page_signatures = {}, set()
    def save():
        write_json(progress_path, progress)
        if hasattr(site, "inventory_progress"): site.inventory_progress(progress)
    def check():
        if cancel(): raise CollectionCancelled("文献采集已停止，题录及摘要进度已保留")
    try:
        check()
        save()
        site.search(task["keyword"])
        while True:
            check()
            page = site.listing()
            number = len(progress["pages"]) + 1
            total, expected = page.get("total_results"), progress["total_results"]
            if expected is not None and total != expected and hasattr(site, "recheck_listing"):
                progress.update(expected_total_results=expected, observed_total_results=total, failed_page=number)
                try:
                    page = site.recheck_listing(expected, page)
                except PageBlocked as exc:
                    # All observations had the same page/record identities.
                    # A fluctuating counter alone is advisory inside a listing.
                    if exc.reason != "result_total_unstable": raise
                total = page.get("total_results")
            if type(total) is not int or total < 0:
                progress.update(expected_total_results=expected, observed_total_results=total, failed_page=number)
                raise PageBlocked("result_total_unreadable")
            if expected is not None and total != expected:
                progress["count_discrepancies"].append({"page": number, "baseline": expected, "observed": total})
            if page.get("number") != number or (not page.get("items") and total):
                raise PageBlocked("page_number_or_rows_unreadable")
            for row in page["items"]:
                if not row.get("title") or not cnki_url(row.get("url")): raise PageBlocked("invalid_list_item")
            signature = tuple(cnki.paper_identity(row) for row in page["items"])
            if (page.get("batch_id"), signature) in page_signatures:
                write_json(work / "repeated-page-diagnostic.json", {"page": number, "rows": len(signature), "identity_sha256": checksum(signature)})
                raise PageBlocked("inventory_repeated_page")
            page_signatures.add(signature)
            for offset, row in enumerate(page["items"], 1):
                identity = cnki.paper_identity(row)
                position = progress["listed_count"] + offset
                if identity in seen:
                    progress["duplicate_positions"].append({"position": position, "first_position": seen[identity]})
                else: seen[identity] = position
            progress["unique_paper_count"] = len(seen)
            if expected is None: progress["total_results"] = total
            progress["pages"].append(copy.deepcopy(page))
            progress["listed_count"] += len(page["items"])
            if progress["listed_count"] > progress["total_results"]:
                raise PageBlocked("inventory_count_mismatch")
            for key in ("expected_total_results", "observed_total_results", "failed_page"):
                progress.pop(key, None)
            save()
            if not page["has_next"]:
                if progress["listed_count"] != progress["total_results"] or total != progress["total_results"]:
                    progress.update(expected_total_results=progress["total_results"], observed_total_results=total, failed_page=number)
                    raise PageBlocked("inventory_count_mismatch")
                data = {"version": 2, "keyword": task["keyword"], "total_results": progress["total_results"],
                        "pages": progress["pages"], "count_discrepancies": progress["count_discrepancies"],
                        "unique_paper_count": len(seen), "duplicate_positions": progress["duplicate_positions"],
                        "count_policy": "listing_positions_and_first_last_total", "frozen_at": now()}
                value = {"data": data, "sha256": checksum(data)}
                validate(value, task["keyword"])
                check()
                write_json(path, value)
                progress.update(complete=True, phase="abstracts", stop_reason="inventory_frozen")
                save()
                return value
            site.next_page(number + 1)
    except BaseException as exc:
        progress["stop_reason"] = exc.reason if isinstance(exc, PageBlocked) else "cancelled" if isinstance(exc, CollectionCancelled) else "inventory_error"
        progress["failed_page"] = progress.get("failed_page", len(progress["pages"]) + 1)
        if isinstance(exc, PageBlocked) and exc.state: progress["verification"] = exc.state
        save()
        raise


class FrozenSite:
    """Use immutable list pages, refreshing expired links only by exact identity."""
    def __init__(self, site, value):
        self.site, self.data = site, value["data"]
        self.inventory_digest = value["sha256"]
        self.number = 1

    def search(self, keyword):
        if keyword != self.data["keyword"]: raise ValueError("固定清单关键词不一致")
        self.number = 1

    def listing(self):
        page = copy.deepcopy(self.data["pages"][self.number - 1])
        page["observed_total_results"] = page["total_results"]
        page["total_results"] = self.data["total_results"]
        return page

    def next_page(self, number): self.number = number

    def detail(self, item): return self.site.detail(item)

    def refresh(self, keyword, number):
        # The real normal-page refresh may show different totals/order. Never
        # rebuild the frozen list; replace only uniquely matched record URLs.
        page = self.data["pages"][number - 1]
        if self.data["version"] == 3:
            batch = next(b for b in self.data["batches"] if b["id"] == page["batch_id"])
            current = self.site.refresh_scope(keyword, batch["filters"], page["source_page"], self.data["selection"]["order"])
        else:
            current = self.site.refresh(keyword, number)
        frozen = copy.deepcopy(self.data["pages"][number - 1])
        frozen["observed_total_results"] = frozen["total_results"]
        frozen["total_results"] = self.data["total_results"]
        for row in frozen["items"]:
            matches = [item for item in current["items"] if cnki.paper_identity(item) == cnki.paper_identity(row)]
            if len(matches) == 1: row["url"] = matches[0]["url"]
        return frozen


def collect(task, work, site, *, cancel=lambda: False):
    from .browser_collector import collect_pages
    value = prepare(task, work, site, cancel)
    return collect_pages(task, work, FrozenSite(site, value), cancel=cancel)
