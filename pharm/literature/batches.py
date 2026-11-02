"""Bounded normal-UI partitions with explicit parent/child count coverage."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from ..core.common import write_json, now

LIMIT = 6000


def selection(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {"mode", "order", "limit"}: raise ValueError("文献采集范围参数无效")
    mode, order = value.get("mode", "all"), value.get("order", "relevance")
    limit = value.get("limit", LIMIT if mode == "quick" else None)
    if mode not in {"all", "quick"} or order not in {"relevance", "date"} or (mode == "quick" and (type(limit) is not int or not 1 <= limit <= LIMIT)) or (mode == "all" and limit is not None):
        raise ValueError("全量不设篇数上限；快速模式需指定1至6000条及排序方式")
    return {"mode": mode, "order": order, "limit": limit}


def capacity(page):
    pages = page.get("accessible_pages")
    size = page.get("page_size")
    return min(LIMIT, pages * size) if type(pages) is int and pages > 0 and type(size) is int and size > 0 else LIMIT


def prepare(task, work, site, cancel):
    from . import inventory
    from .browser_collector import PageBlocked
    from .collector import CollectionCancelled
    work = Path(work)
    mode = task.get("selection_mode", "all")
    order = task.get("selection_order", "relevance")
    requested = task.get("selection_limit", LIMIT)
    branches, leaves, flat = [], [], []
    progress = {"keyword": task["keyword"], "phase": "listing", "complete": False, "pages": [],
                "listed_count": 0, "unique_paper_count": 0, "duplicate_positions": [], "count_discrepancies": [],
                "batch_count": 0, "completed_batches": 0, "stop_reason": "running", "selection_mode": mode}
    def check():
        if cancel(): raise CollectionCancelled("文献采集已停止，已完成批次清单保留")
    def save(): write_json(work / "listing_progress.json", progress)

    class Scoped:
        def __init__(self, filters, first): self.filters, self.first = filters, first
        def search(self, keyword): pass  # Already opened and verified by visit.
        def listing(self): return site.listing()
        def next_page(self, n): site.next_page(n)
        def recheck_listing(self, expected, initial): return site.recheck_listing(expected, initial)
        def inventory_progress(self, current):
            from .cnki import paper_identity
            combined = flat + current['pages']
            progress.update(pages=combined, listed_count=sum(len(p['items']) for p in combined),
                            unique_paper_count=len({paper_identity(row) for p in combined for row in p['items']}))
            save()

    def visit(filters, expected=None, parent=None):
        check()
        first = site.open_scope(task["keyword"], filters, order)
        total = first["total_results"]
        if expected is not None and total != expected: raise PageBlocked("partition_total_changed")
        identifier = inventory.checksum(filters)[:24]
        bound = capacity(first)
        if mode == "all" and total > bound:
            choices = site.partition_options(total, filters)
            fields = {x["field"] for x in choices}
            if len(fields) != 1 or len(choices) < 2 or len({x['value'] for x in choices}) != len(choices) or sum(x["count"] for x in choices) != total or any(not 0 < x["count"] < total for x in choices):
                raise PageBlocked("partition_coverage_unavailable")
            children = [filters + [{"field": x["field"], "value": x["value"], "label": x["label"]}] for x in choices]
            branches.append({"id": identifier, "filters": filters, "total": total,
                             "children": [inventory.checksum(x)[:24] for x in children]})
            progress.update(batch_count=progress["batch_count"] + len(children), current_batch="拆分检索范围")
            save()
            for child, choice in zip(children, choices): visit(child, choice["count"], identifier)
            return
        # A changed count gets a new cache key; retain the previous scope
        # snapshot instead of getting stuck on or overwriting its old baseline.
        sub = work / "listing_batches" / inventory.checksum([filters, total, order])[:24]
        sub.mkdir(parents=True, exist_ok=True)
        progress.update(current_batch=" / ".join(x["label"] for x in filters) or "全部结果", batch_count=max(1, progress["batch_count"]))
        save()
        if mode == "quick":
            target = min(total, requested, bound)
            rows, pages, signatures = [], [], set()
            n = 1
            while len(rows) < target or not pages:
                check()
                page = site.listing()
                if page["number"] != n or page["total_results"] != total: raise PageBlocked("partition_total_changed")
                take = copy.deepcopy(page)
                take["items"] = take["items"][:target - len(rows)]
                if not take["items"] and target: raise PageBlocked("inventory_count_mismatch")
                signature = inventory.checksum(take["items"])
                if signature in signatures: raise PageBlocked("inventory_repeated_page")
                signatures.add(signature)
                rows.extend(take["items"]); pages.append(take)
                progress.update(pages=pages, listed_count=len(rows)); save()
                if len(rows) >= target: break
                if not page["has_next"]: raise PageBlocked("inventory_count_mismatch")
                n += 1; site.next_page(n)
            data = {"total_results": target, "pages": pages}
        else:
            value = inventory.prepare(task, sub, Scoped(filters, first), cancel, batching=False)
            data = value["data"]
            if data["total_results"] != total: raise PageBlocked("partition_total_changed")
        leaf = {"id": identifier, "filters": filters, "parent": parent, "total": data["total_results"],
                "source_total": total, "start_page": len(flat) + 1, "page_count": len(data["pages"])}
        leaves.append(leaf)
        for page in data["pages"]:
            flat.append({**copy.deepcopy(page), "number": len(flat) + 1, "batch_id": identifier,
                         "source_page": page["number"], "source_total_results": page["total_results"],
                         "source_has_next": page["has_next"]})
        progress.update(completed_batches=len(leaves), listed_count=sum(len(p["items"]) for p in flat), pages=flat)
        save()
    try:
        save()
        visit([])
        root_total = branches[0]["total"] if branches else leaves[0]["source_total"]
        seen, duplicates, count = {}, [], 0
        from .cnki import paper_identity
        for n, page in enumerate(flat, 1):
            page["has_next"] = n < len(flat)
            for row in page["items"]:
                count += 1; identity = paper_identity(row)
                if identity in seen: duplicates.append({"position": count, "first_position": seen[identity]})
                else: seen[identity] = count
        data = {"version": 3, "keyword": task["keyword"], "total_results": count, "source_total_results": root_total,
                "pages": flat, "branches": branches, "batches": leaves, "frozen_at": now(),
                "unique_paper_count": len(seen), "duplicate_positions": duplicates, "count_discrepancies": [],
                "selection": {"mode": mode, "order": order, "requested_limit": requested if mode == "quick" else None,
                              "complete": mode == "all", "page_size": 50}, "count_policy": "verified_partition_positions"}
        value = {"data": data, "sha256": inventory.checksum(data)}
        inventory.validate(value, task["keyword"])
        check(); write_json(work / "listing_inventory.json", value)
        progress.update(complete=True, phase="abstracts", stop_reason="inventory_frozen", total_results=count,
                        unique_paper_count=len(seen), duplicate_positions=duplicates)
        save(); return value
    except BaseException as exc:
        progress["stop_reason"] = exc.reason if isinstance(exc, PageBlocked) else "cancelled" if isinstance(exc, CollectionCancelled) else "inventory_error"
        save(); raise


def validate(data):
    """Every leaf covers its local pages and every split sums to its parent."""
    leaves, branches = data.get("batches", []), data.get("branches", [])
    nodes = {n["id"]: n for n in [*leaves, *branches]}
    if not leaves or len(nodes) != len(leaves) + len(branches): raise ValueError("分批清单节点重复或缺失")
    from .inventory import checksum
    for node in nodes.values():
        if node["id"] != checksum(node["filters"])[:24]: raise ValueError("分批范围身份不一致")
    policy = data.get("selection", {})
    selection({"mode": policy.get("mode"), "order": policy.get("order"), "limit": policy.get("requested_limit")})
    if policy.get("complete") is not (policy["mode"] == "all") or policy.get("page_size") != 50: raise ValueError("分批范围完成标记无效")
    quick = data["selection"]["mode"] == "quick"
    offset = 0
    for leaf in leaves:
        pages = data["pages"][offset:offset + leaf["page_count"]]
        if leaf["start_page"] != offset + 1 or len(pages) != leaf["page_count"]: raise ValueError("分批页面不连续")
        if sum(len(p["items"]) for p in pages) != leaf["total"]: raise ValueError("分批位置数量不足")
        for i, page in enumerate(pages, 1):
            if page["batch_id"] != leaf["id"] or page["source_page"] != i: raise ValueError("分批页面身份不一致")
            if not quick and page["source_has_next"] is not (i < len(pages)): raise ValueError("分批未到末页")
        if not quick and (pages[0]["source_total_results"] != leaf["total"] or pages[-1]["source_total_results"] != leaf["total"]): raise ValueError("分批首末计数不一致")
        offset += len(pages)
    if offset != len(data["pages"]): raise ValueError("存在未归属的分批页面")
    referenced = []
    for branch in branches:
        children = [nodes[key] for key in branch["children"]]
        if len(children) < 2 or sum(c["total"] for c in children) != branch["total"]: raise ValueError("分批范围覆盖不完整")
        if len({c["filters"][-1]["field"] for c in children}) != 1 or len({c["filters"][-1]["value"] for c in children}) != len(children):
            raise ValueError("分批条件不是同一字段的不重复分类")
        for child in children:
            if child["filters"][:-1] != branch["filters"]: raise ValueError("分批范围不属于父范围")
        referenced.extend(branch["children"])
    roots = set(nodes) - set(referenced)
    if len(roots) != 1 or len(referenced) != len(set(referenced)): raise ValueError("分批范围重复或孤立")
    root = nodes[roots.pop()]
    if root["filters"] != [] or root.get("source_total", root["total"]) != data["source_total_results"]: raise ValueError("分批根范围不一致")
    if not quick and sum(x["total"] for x in leaves) != data["source_total_results"]: raise ValueError("分批合计不能覆盖原总数")
    if quick and (branches or len(leaves) != 1 or data["total_results"] > min(data["selection"]["requested_limit"], data["source_total_results"], LIMIT)):
        raise ValueError("快速模式范围无效")
