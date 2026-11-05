"""Review regressions: isolated fixtures only, no real sites or model calls."""
import asyncio
import itertools
import json
import shutil
import threading
from uuid import uuid4

import httpx
import pytest

from pharm.core import common
from pharm.core.archive import archive_run
from pharm.chat import cnki_bridge, config, provider
from pharm.literature import collector, dataset, jobs
from pharm.network import string_local
from pharm.pipeline import engine
from pharm import llm
from test_analysis import root, fixture_discovery_run, _craft_source_run
from test_archive import make_run
from test_chat_cnki import publish
from test_literature_flow import wait_for


def test_fixture_source_cannot_be_promoted_to_live(root):
    source_id = fixture_discovery_run(root)
    with pytest.raises(ValueError, match="fixture|合成"):
        engine._analysis_task({"discovery_run_id": source_id, "disease": "Hyperthyroidism"}, 'live')
    assert not (root / "data/pharm").exists()


def test_legacy_live_analysis_of_fixture_cannot_resume(root, monkeypatch):
    source_id = fixture_discovery_run(root)
    run_id = engine.start(pipeline='analysis', mode='fixture', background=False,
                          analysis={'discovery_run_id': source_id, 'disease': 'Hyperthyroidism'})
    path = root / 'runs' / run_id / 'manifest.json'
    manifest = common.read_json(path); manifest['mode'] = 'live'
    common.write_json(path, manifest)
    monkeypatch.setattr(engine.Runner, 'run', lambda self: pytest.fail('unsafe resume executed'))
    with pytest.raises(ValueError, match='fixture|合成'):
        engine.start(resume=run_id, background=False)


def test_archive_rejects_synthetic_verified_output_even_with_live_label(tmp_path):
    run = make_run(tmp_path)
    path = run / 'herb_targets/attempt_01/targets.json'
    common.write_json(path, {'evidence_type': 'synthetic_engineering', 'genes': ['TP53']})
    manifest = common.read_json(run / 'manifest.json')
    manifest['verified_targets'] = {'herb_targets': 'herb_targets/attempt_01/targets.json'}
    common.write_json(run / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='synthetic|合成'):
        archive_run(run)
    assert not (tmp_path / 'data/pharm').exists()


@pytest.mark.parametrize('same_version', [False, True])
def test_relocated_source_index_requires_original_hash(root, monkeypatch, same_version):
    monkeypatch.setattr(engine.string_local, 'string_network_auto', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('external network disabled for this test')))
    source_id = _craft_source_run(root, ['TP53'], 'Example', ['TP53'])
    source = common.read_json(root / 'runs' / source_id / 'manifest.json')
    result = common.read_json(root / 'runs' / source_id / source['verified_targets']['disease_reverse'])
    original = root / (source_id + '.sqlite')
    relocated = root / 'replacement.sqlite'
    shutil.copyfile(original, relocated)
    if not same_version:
        import sqlite3
        with sqlite3.connect(relocated) as connection:
            connection.execute("UPDATE associations SET gene='BRCA1'")
    original.unlink()
    monkeypatch.setattr(engine.discovery, 'database_path', lambda *a: relocated)
    run_id = engine.start(pipeline='analysis', background=False,
                         analysis={'discovery_run_id': source_id, 'disease': 'Example', 'agents': False})
    manifest = common.read_json(root / 'runs' / run_id / 'manifest.json')
    assert (manifest['stages']['shared_targets']['status'] == 'succeeded') is same_version
    if same_version:
        shared = common.read_json(root / 'runs' / run_id / manifest['verified_targets']['shared_targets'])
        assert shared['genes'] == ['TP53'] and shared['source']['database_sha256'] == result['database_sha256']


@pytest.mark.parametrize('with_preferred', [False, True])
def test_string_three_alias_collisions_are_order_independent(tmp_path, with_preferred):
    info, aliases = tmp_path / 'info.tsv', tmp_path / 'aliases.tsv'
    info.write_text('#string_protein_id\tpreferred_name\tprotein_size\tannotation\n9606.ENSP1\tNATIVE\t10\tx\n', encoding='utf-8')
    aliases.write_text('#string_protein_id\talias\tsource\n' + ''.join('9606.ENSP1\t' + gene + '\ttest\n' for gene in ['A', 'B', 'C']), encoding='utf-8')
    genes = ['A', 'B', 'C'] + (['NATIVE'] if with_preferred else [])
    for order in itertools.permutations(genes):
        mapped, records, ambiguous, unmapped = string_local._map_symbols(order, {'taxon_id': 9606}, {'info': info, 'aliases': aliases})
        assert mapped == ({'9606.ENSP1': 'NATIVE'} if with_preferred else {})
        assert set(ambiguous) == {'A', 'B', 'C'} and not unmapped


@pytest.mark.parametrize('style', ['chat_completions', 'responses', 'anthropic'])
def test_provider_preserves_complete_long_answers(style):
    content = 'A' * 25000 + 'END'
    response = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
    if style == 'responses':
        response = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': content}]}]}
    elif style == 'anthropic':
        response = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': content}]}
    selection = {**config.DEFAULT, 'model': 'engineering', 'api_style': style, 'configured': True, 'api_key': 'fake'}
    result = asyncio.run(provider.complete(selection, [{'role': 'user', 'content': 'test'}], transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    assert result['content'] == content and result['finish_reason'] == 'stop'


def quick_corpus(root):
    publish(root)
    source = root / 'fixture/cnki_示例方'
    papers = common.read_json(source / 'papers.json')
    papers['total_results'] = 1
    papers['collection'] = {'mode': 'quick', 'complete': False, 'source_total_results': 6,
                            'selection': {'mode': 'quick', 'order': 'date', 'requested_limit': 1}}
    common.write_json(source / 'papers.json', papers)
    dataset.import_data(source.parent, root / 'data/cnki', keywords=['示例方'], update=True)


def test_chat_does_not_reuse_quick_corpus_for_full_collection(tmp_path, monkeypatch):
    monkeypatch.delenv('PHARM_CNKI_DATA_DIR', raising=False)
    quick_corpus(tmp_path)
    starts = []
    def start(*args, **kwargs):
        starts.append(kwargs)
        return {'id': 'cnki_collect_' + 'a' * 32, 'status': 'queued'}
    monkeypatch.setattr(jobs.manager, 'start', start)
    args = {'keyword': '示例方', 'kind': 'formula'}
    value = cnki_bridge.start(tmp_path, {'id': uuid4().hex}, args, True, {})
    assert value['status'] == 'pending_cnki' and len(starts) == 1
    assert starts[0]['local_only'] is False
    offline = cnki_bridge.start(tmp_path, {'id': uuid4().hex}, args, False, {})
    assert offline['status'] != 'cnki_ready' and len(starts) == 1


def test_chat_snapshot_and_model_summary_expose_collection_scope(tmp_path, monkeypatch):
    monkeypatch.delenv('PHARM_CNKI_DATA_DIR', raising=False)
    quick_corpus(tmp_path)
    record, state = {'id': uuid4().hex}, {'evidence': []}
    cnki_bridge.result(tmp_path, record, state, '示例方', 'formula')
    assert record['cnki_result']['collection']['complete'] is False
    summary = json.loads(state['evidence'][0]['text'])
    assert summary['collection']['mode'] == 'quick'
    assert summary['collection']['source_total_results'] == 6


def test_quick_unextracted_corpus_still_requires_full_collection(tmp_path, monkeypatch):
    monkeypatch.delenv('PHARM_CNKI_DATA_DIR', raising=False)
    source = tmp_path / 'fixture/cnki_示例方'; source.mkdir(parents=True)
    common.write_json(source / 'papers.json', {'keyword': '示例方', 'collection': {'mode': 'quick', 'complete': False},
        'papers': [{'rank': 1, 'title': '工程样本', 'abstract': None}]})
    dataset.import_data(source.parent, tmp_path / 'data/cnki', keywords=['示例方'])
    starts = []
    def start(*a, **kwargs):
        starts.append(kwargs)
        return {'id': 'cnki_collect_' + 'b' * 32}
    monkeypatch.setattr(jobs.manager, 'start', start)
    value = cnki_bridge.start(tmp_path, {'id': uuid4().hex}, {'keyword': '示例方', 'kind': 'formula'}, True, {})
    assert value['status'] == 'pending_cnki' and starts[0]['local_only'] is False


@pytest.mark.parametrize('resume', [False, True])
def test_queued_collection_keeps_model_selected_at_acceptance(tmp_path, monkeypatch, resume):
    monkeypatch.delenv('PHARM_CNKI_DATA_DIR', raising=False)
    config.save(tmp_path, {'model': 'model-A', 'base_url': 'http://127.0.0.1:9/v1', 'storage': 'session'})
    # Real local analysis, with only provider output mocked.
    source = tmp_path / 'fixture/cnki_示例方'; source.mkdir(parents=True)
    common.write_json(source / 'papers.json', {'keyword': '示例方', 'papers': [{'rank': 1, 'title': '工程样本', 'abstract': '工程摘要', 'url': 'https://kns.cnki.net/fixture/1'}]})
    dataset.import_data(source.parent, tmp_path / 'data/cnki', keywords=['示例方'])
    calls, entered, release = [], threading.Event(), threading.Event()
    async def complete(selection, messages, tools=None, **kwargs):
        calls.append(selection['model'])
        return {'content': '{"papers":[{"rank":1,"triples":[]}]}', 'tool_calls': [], 'finish_reason': 'stop'}
    monkeypatch.setattr(llm, 'complete', complete)
    def execute(task, work, **kwargs):
        if task['keyword'] == '占位方':
            entered.set()
            assert release.wait(5)
            task['status'] = 'succeeded'; common.write_json(work / 'task.json', task)
        else:
            collector.execute(task, work, **kwargs)
    manager = jobs.CollectionManager(execute=execute)
    try:
        manager.start(tmp_path, '占位方', 'blocker')
        assert entered.wait(2)
        if resume:
            task, work = collector.prepare('示例方', root=tmp_path, local_only=True)
            task['status'] = 'failed_or_partial'; common.write_json(work / 'task.json', task)
            queued = manager.resume(tmp_path, task['id'])
        else:
            queued = manager.start(tmp_path, '示例方', 'queued', local_only=True)
        assert queued['status'] == 'queued'
        config.save(tmp_path, {'model': 'model-B'})
        release.set()
        value = wait_for(lambda: (s if (s := manager.status(tmp_path, queued['id']))['status'] in {'succeeded', 'failed_or_partial'} else None))
        assert value['status'] == 'succeeded', value.get('error')
        assert calls == ['model-A']
    finally:
        release.set(); manager.shutdown()
