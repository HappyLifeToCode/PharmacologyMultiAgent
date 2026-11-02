import copy
import json

import pytest

from pharm.literature import batches, inventory, collector, browser_collector as browse
from test_cnki_browser_collector import item


class Site:
    def __init__(self):
        self.scope, self.number, self.details, self.opens = (), 1, [], []
        self.records = {(): list(range(1, 7)), ('A',): [1, 2, 3, 4], ('B',): [5, 6],
                        ('A', '2025'): [1, 2], ('A', '2024'): [3, 4]}

    def open_scope(self, keyword, filters, order):
        self.scope = tuple(x['value'] for x in filters); self.number = 1
        self.opens.append((self.scope, order))
        return self.listing()

    def partition_options(self, total, filters):
        if not filters:
            return [{'field': 'resource', 'value': 'A', 'label': '期刊', 'count': 4},
                    {'field': 'resource', 'value': 'B', 'label': '会议', 'count': 2}]
        return [{'field': 'year', 'value': '2025', 'label': '2025年', 'count': 2},
                {'field': 'year', 'value': '2024', 'label': '2024年', 'count': 2}]

    def listing(self):
        rows = self.records[self.scope]
        return {'number': self.number, 'total_results': len(rows), 'page_size': 50,
                'accessible_pages': 1, 'has_next': False, 'items': [item(n) for n in rows]}

    def next_page(self, number): raise AssertionError('one-page fixture')
    def detail(self, row):
        self.details.append(row['title']); return {'abstract': row['title'] + '摘要'}


def test_recursive_partitions_cover_root_and_preserve_scope_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(batches, 'LIMIT', 3)
    task, work = collector.prepare('测试方', root=tmp_path)
    site = Site()
    scan = inventory.collect(task, work, site)
    data = inventory.load(work, '测试方')['data']
    assert len(data['branches']) == 2 and len(data['batches']) == 3
    assert data['total_results'] == data['source_total_results'] == 6
    assert data['selection']['complete'] is True
    assert [p['source_page'] for p in data['pages']] == [1, 1, 1]
    assert scan['complete'] and len(site.details) == 6
    papers = [json.loads(p.read_text(encoding='utf-8')) for p in (work/'papers').glob('*.json')]
    collector.validate_scan(task, work, papers)
    # Once frozen, an explicit retry does not reopen the search scopes.
    site.open_scope = lambda *a: pytest.fail('frozen inventory must not be rebuilt')
    inventory.collect(task, work, site)
    assert len(site.details) == 6


def test_incomplete_partition_does_not_silently_skip_missing_years(tmp_path, monkeypatch):
    monkeypatch.setattr(batches, 'LIMIT', 3)
    task, work = collector.prepare('测试方', root=tmp_path)
    site = Site()
    site.partition_options = lambda *a: [{'field': 'year', 'value': '2025', 'label': '2025年', 'count': 2},
                                        {'field': 'year', 'value': '2024', 'label': '2024年', 'count': 3}]
    with pytest.raises(browse.PageBlocked, match='partition_coverage_unavailable'):
        inventory.collect(task, work, site)
    assert not (work/'listing_inventory.json').exists() and not site.details


def test_quick_mode_records_order_and_never_claims_all(tmp_path, monkeypatch):
    monkeypatch.setattr(batches, 'LIMIT', 3)
    task, work = collector.prepare('测试方', root=tmp_path)
    task.update(selection_mode='quick', selection_order='date', selection_limit=3)
    site = Site(); inventory.collect(task, work, site)
    data = inventory.load(work, '测试方')['data']
    assert data['selection'] == {'mode': 'quick', 'order': 'date', 'requested_limit': 3, 'complete': False, 'page_size': 50}
    assert data['total_results'] == 3 and data['source_total_results'] == 6
    assert not data['branches'] and len(site.details) == 3
    collector.execute(task, work, root=tmp_path, runner=lambda *a: pytest.fail('already collected'))
    published = json.loads((work/'publish/cnki_测试方/papers.json').read_text(encoding='utf-8'))
    assert published['collection']['complete'] is False
    assert published['collection']['selection']['order'] == 'date'


def test_batch_metadata_cannot_hide_missing_or_duplicate_children(tmp_path, monkeypatch):
    monkeypatch.setattr(batches, 'LIMIT', 3)
    task, work = collector.prepare('测试方', root=tmp_path)
    value = inventory.prepare(task, work, Site(), lambda: False)
    broken = copy.deepcopy(value)
    broken['data']['branches'][0]['children'].pop()
    broken['sha256'] = inventory.checksum(broken['data'])
    with pytest.raises(ValueError): inventory.validate(broken, '测试方')


@pytest.mark.parametrize('value', [{'mode':'quick','limit':True}, {'mode':'quick','limit':6001},
                                  {'mode':'all','limit':6000}, {'mode':'quick','order':'unknown'}])
def test_selection_is_strict(value):
    with pytest.raises(ValueError): batches.selection(value)


def test_capacity_uses_actual_page_count_not_300_pages():
    assert batches.capacity({'page_size':50,'accessible_pages':120}) == 6000
    assert batches.capacity({'page_size':50,'accessible_pages':80}) == 4000
    assert batches.capacity({'page_size':50,'accessible_pages':300}) == 6000


def test_completed_batch_is_reused_after_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr(batches, 'LIMIT', 3)
    task, work = collector.prepare('测试方', root=tmp_path)
    site = Site()
    original = site.open_scope
    def interrupted(keyword, filters, order):
        if tuple(x['value'] for x in filters) == ('B',): raise collector.CollectionCancelled('fixture')
        return original(keyword, filters, order)
    site.open_scope = interrupted
    with pytest.raises(collector.CollectionCancelled): inventory.prepare(task, work, site, lambda: False)
    cached = {p: p.read_bytes() for p in (work/'listing_batches').glob('*/listing_inventory.json')}
    assert len(cached) == 2
    value = inventory.prepare(task, work, Site(), lambda: False)
    assert value['data']['total_results'] == 6
    assert all(p.read_bytes() == raw for p, raw in cached.items())


def test_single_resource_falls_back_to_years_only_if_year_counts_cover_total():
    from types import SimpleNamespace
    site = object.__new__(browse.CnkiSite)
    site.page = SimpleNamespace(evaluate=lambda code: [{'field':'resource','value':'A','label':'期刊','count':7000}])
    years = [{'field':'year','value':'2025','label':'2025年','count':3500},
             {'field':'year','value':'2024','label':'2024年','count':3500}]
    site._year_rows = lambda: SimpleNamespace(locator=lambda selector: SimpleNamespace(evaluate_all=lambda code: years))
    assert site.partition_options(7000, []) == years
    years[0]['count'] = 3490
    with pytest.raises(browse.PageBlocked, match='partition_coverage_unavailable'):
        site.partition_options(7000, [])
