import copy
import json

import pytest

from pharm.core.common import write_json
from pharm.literature import browser_collector as browse, collector, inventory, jobs, dataset
from test_cnki_browser_collector import Site, item


def papers(work):
    return [json.loads(p.read_text(encoding="utf-8")) for p in (work / "papers").glob("*.json")]


def test_all_listing_pages_are_frozen_before_first_detail(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    class Ordered(Site):
        def detail(self, row):
            assert self.number == 2
            value = inventory.load(work, task["keyword"])
            assert value["data"]["total_results"] == 3
            return super().detail(row)
    site = Ordered()
    scan = inventory.collect(task, work, site)
    assert scan["complete"] and len(site.calls) == 3
    assert scan["inventory_sha256"] == inventory.load(work, task["keyword"])["sha256"]
    collector.validate_scan(task, work, papers(work))


def test_resume_after_detail_cancellation_does_not_search_again_or_change_inventory(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    detail = site.detail
    def stop(row):
        if row["title"] == item(2)["title"]: raise collector.CollectionCancelled("fixture")
        return detail(row)
    site.detail = stop
    with pytest.raises(collector.CollectionCancelled): inventory.collect(task, work, site)
    original = (work / "listing_inventory.json").read_bytes()
    first = (work / "papers/paper_0001.json").read_bytes()
    resumed = Site(changed=True)
    resumed.search = lambda *a: pytest.fail("frozen resume must not search")
    resumed.listing = lambda *a: pytest.fail("frozen resume must not read live list")
    resumed.next_page = lambda *a: pytest.fail("frozen resume must not navigate")
    scan = inventory.collect(task, work, resumed)
    assert scan["complete"] and resumed.calls == [item(2)["title"], item(3)["title"]]
    assert (work / "listing_inventory.json").read_bytes() == original
    assert (work / "papers/paper_0001.json").read_bytes() == first
    collector.validate_scan(task, work, papers(work))


def test_listing_mismatch_never_opens_details_or_freezes_partial_inventory(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site(changed=True)
    with pytest.raises(browse.PageBlocked, match="inventory_count_mismatch"):
        inventory.collect(task, work, site)
    assert site.calls == [] and not (work / "listing_inventory.json").exists()
    assert papers(work) == []
    with pytest.raises(collector.CollectionValidationError) as exc: collector.validate_scan(task, work, [])
    assert exc.value.failure_code == "cnki_inventory_count_mismatch"
    state = jobs.CollectionManager().status(tmp_path, task["id"])
    assert state["collection_stage"] == "listing" and state["listed_count"] == 3
    assert state["pages_recorded"] == 2


def test_legacy_partial_papers_reused_only_after_complete_new_inventory(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    write_json(work / "papers/paper_0007.json", {**item(2), "abstract": "旧摘要", "rank": 7})
    old = (work / "papers/paper_0007.json").read_bytes()
    site = Site()
    scan = inventory.collect(task, work, site)
    assert item(2)["title"] not in site.calls and scan["complete"]
    assert (work / "papers/paper_0007.json").read_bytes() == old
    collector.validate_scan(task, work, papers(work))


def test_corrupt_inventory_is_not_silently_replaced(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    inventory.prepare(task, work, Site(), lambda: False)
    path = work / "listing_inventory.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["data"]["pages"][0]["items"][0]["title"] = "corrupted"
    write_json(path, value)
    site = Site()
    site.search = lambda *a: pytest.fail("must not discard frozen inventory")
    with pytest.raises(ValueError, match="校验失败"): inventory.collect(task, work, site)


@pytest.mark.parametrize("fault", ["repeated_page", "wrong_page", "missing_positions"])
def test_invalid_listing_cannot_be_frozen(tmp_path, fault):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    original = site.listing
    def listing():
        value = original()
        if site.number == 2:
            if fault == "repeated_page": value["items"] = [item(1), item(2)]
            if fault == "wrong_page": value["number"] = 7
            if fault == "missing_positions": value["items"] = [item(3), item(4)]
        return value
    site.listing = listing
    with pytest.raises(browse.PageBlocked): inventory.collect(task, work, site)
    assert not (work / "listing_inventory.json").exists() and not site.calls


def test_completed_scan_checks_fixed_identity_not_just_title(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    inventory.collect(task, work, Site())
    values = papers(work)
    values[0]["authors"] = "wrong author"
    with pytest.raises(ValueError, match="身份"): collector.validate_scan(task, work, values)


def test_expired_link_refresh_keeps_frozen_rows_when_live_results_change(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    value = inventory.prepare(task, work, Site(), lambda: False)
    site = Site()
    def refresh(keyword, number):
        return {"number": 1, "total_results": 999, "has_next": True,
                "items": [{**item(2), "url": "https://kns.cnki.net/fresh/2"}, {**item(1), "authors": "another"}]}
    site.refresh = refresh
    frozen = inventory.FrozenSite(site, value)
    result = frozen.refresh("测试方", 1)
    assert result["total_results"] == 3 and result["items"][0] == item(1)
    assert result["items"][1]["url"] == "https://kns.cnki.net/fresh/2"
    assert inventory.load(work, "测试方") == value


def test_execute_publishes_verified_inventory_scope_without_real_model(tmp_path, monkeypatch):
    task, work = collector.prepare("测试方", root=tmp_path)
    monkeypatch.setattr(browse, "collect", lambda *a, **k: inventory.collect(task, work, Site()))
    result = collector.execute(task, work, root=tmp_path)
    assert result["status"] == "succeeded"
    _, contents = dataset.read_corpus(tmp_path / "data/cnki", "测试方")
    data = json.loads(contents["papers.json"])
    assert data["collection"]["scope"] == "frozen_keyword_listing"
    assert len(data["papers"]) == 3 and data["collection"]["inventory_frozen_at"]
    assert jobs.CollectionManager().status(tmp_path, task["id"])["inventory_ready"]


def test_zero_results_freezes_explicit_empty_page(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    site.listing = lambda: {"number": 1, "total_results": 0, "has_next": False, "items": []}
    scan = inventory.collect(task, work, site)
    assert scan["complete"] and scan["total_results"] == 0
    collector.validate_scan(task, work, [])


@pytest.mark.parametrize("unstable", [False, True])
def test_middle_count_is_advisory_when_unique_coverage_and_endpoints_agree(tmp_path, unstable):
    task, work = collector.prepare("测试方", root=tmp_path)
    class Changing(Site):
        def listing(self):
            return {"number": self.number, "total_results": 4 if self.number == 2 else 5,
                    "has_next": self.number < 3,
                    "items": [item(n) for n in ({1: [1, 2], 2: [3, 4], 3: [5]}[self.number])]}
        def recheck_listing(self, expected, initial):
            if unstable: raise browse.PageBlocked("result_total_unstable")
            return initial
    scan = inventory.collect(task, work, Changing())
    assert scan["complete"]
    value = inventory.load(work, "测试方")
    assert value["data"]["pages"][1]["total_results"] == 4
    assert value["data"]["count_discrepancies"] == [{"page": 2, "baseline": 5, "observed": 4}]
    assert scan["pages"][1]["total_results"] == 5  # frozen count, not a live reading
    assert scan["pages"][1]["observed_listing_total_results"] == 4
    collector.validate_scan(task, work, papers(work))


def test_freezing_inventory_does_not_accept_shortened_last_page(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    def listing():
        return {"number": 1, "total_results": 6941, "has_next": False, "items": [item(1)]}
    site.listing = listing
    with pytest.raises(browse.PageBlocked, match="inventory_count_mismatch"):
        inventory.collect(task, work, site)
    assert site.calls == [] and not (work / "listing_inventory.json").exists()


def test_overlapping_page_boundary_preserves_positions_but_publishes_each_paper_once(tmp_path, monkeypatch):
    task, work = collector.prepare("测试方", root=tmp_path)
    class Overlap(Site):
        def listing(self):
            return {"number": self.number, "total_results": 4, "has_next": self.number == 1,
                    "items": [item(1), item(2)] if self.number == 1 else [item(2), item(3)]}
    site = Overlap()
    scan = inventory.collect(task, work, site)
    assert scan["complete"] and len(site.calls) == 3
    data = inventory.load(work, "测试方")["data"]
    assert data["version"] == 2 and data["total_results"] == 4 and data["unique_paper_count"] == 3
    assert data["duplicate_positions"] == [{"position": 3, "first_position": 2}]
    assert scan["pages"][0]["items"][1]["rank"] == scan["pages"][1]["items"][0]["rank"]
    collector.validate_scan(task, work, papers(work))
    monkeypatch.setattr(browse, "collect", lambda *a, **k: pytest.fail("complete scan must be reused"))
    collector.execute(task, work, root=tmp_path)
    _, contents = dataset.read_corpus(tmp_path / "data/cnki", "测试方")
    assert len(json.loads(contents["papers.json"])["papers"]) == 3
    state = jobs.CollectionManager().status(tmp_path, task["id"])
    assert state["listed_count"] == 4 and state["listed_unique_papers"] == 3 and state["duplicate_listing_positions"] == 1


def test_duplicate_metadata_is_verified_and_same_title_different_author_stays_distinct(tmp_path):
    task, work = collector.prepare("测试方", root=tmp_path)
    site = Site()
    site.listing = lambda: {"number": 1, "total_results": 2, "has_next": False,
                            "items": [item(1), {**item(1), "authors": "乙"}]}
    value = inventory.prepare(task, work, site, lambda: False)
    assert value["data"]["unique_paper_count"] == 2
    value["data"]["unique_paper_count"] = 1
    value["sha256"] = inventory.checksum(value["data"])
    with pytest.raises(ValueError, match="去重计数"): inventory.validate(value, "测试方")
