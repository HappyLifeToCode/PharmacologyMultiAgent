import json
import pytest
from pharm.core.archive import archive_run, import_directory


def make_run(tmp_path, mode='live'):
    run=tmp_path/'runs'/'r1'
    folder=run/'herb_targets'/'attempt_01'
    (folder/'input_snapshot/raw').mkdir(parents=True)
    (folder/'input_snapshot/raw/export.csv').write_text('gene\nTEST\n')
    (folder/'input_snapshot/provenance.json').write_text(json.dumps({'sources':{'batman':{'accessed_at':'2026-09-12','threshold':None}}}))
    (folder/'handoff.json').write_text('{}')
    (folder/'prompt.txt').write_text('PRIVATE')
    (folder/'browser/traces').mkdir(parents=True)
    (folder/'browser/traces/state.json').write_text('PRIVATE')
    manifest={'run_id':'r1','mode':mode,'status':'partial','task':{'task_id':'task1','formula':'温经汤'},'stages':{'herb_targets':{'status':'blocked','directory':'herb_targets/attempt_01','attempt':1,'artifacts':['herb_targets/attempt_01/handoff.json']}},'metrics':{}}
    (run/'manifest.json').write_text(json.dumps(manifest))
    return run


def test_partial_archive_preserves_frozen_raw_and_dates(tmp_path):
    run=make_run(tmp_path)
    result=archive_run(run)
    root=tmp_path/'data/pharm/温经汤'
    batch=root/'02_herb/run_r1/attempt_01'
    assert result['created']==['02_herb']
    assert (batch/'input_snapshot/raw/export.csv').read_text()=='gene\nTEST\n'
    assert not (batch/'prompt.txt').exists()
    assert not (batch/'browser/traces/state.json').exists()
    assert '2026-09-12' in (root/'_meta.md').read_text(encoding='utf8')
    assert json.loads((batch/'_archive.json').read_text(encoding='utf8'))['stage_status']=='blocked'
    assert (root/'04_review').is_dir()
    # New mutable imports cannot change a frozen attempt.
    imports=tmp_path/'data/imports/task1';imports.mkdir(parents=True)
    (imports/'export.csv').write_text('NEW DATA')
    before=(root/'_meta.md').read_bytes()
    assert archive_run(run)['reused']==['02_herb']
    assert before==(root/'_meta.md').read_bytes()
    (run/'herb_targets/attempt_01/input_snapshot/raw/export.csv').write_text('CHANGED')
    with pytest.raises(ValueError,match='refusing overwrite'): archive_run(run)
    assert (batch/'input_snapshot/raw/export.csv').read_text()=='gene\nTEST\n'


def test_fixture_and_path_escape_are_rejected(tmp_path):
    with pytest.raises(ValueError,match='only live'): archive_run(make_run(tmp_path,mode='fixture'))
    assert not (tmp_path/'data/pharm').exists()
    run=make_run(tmp_path/'escape')
    m=json.loads((run/'manifest.json').read_text());m['task']['formula']='..'
    (run/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='unsafe'): archive_run(run)


def test_free_herb_task_archives_under_custom_name(tmp_path):
    run=make_run(tmp_path)
    m=json.loads((run/'manifest.json').read_text());m['task']['formula']=None
    (run/'manifest.json').write_text(json.dumps(m))
    result=archive_run(run)
    assert (tmp_path/'data/pharm/custom_task1/02_herb/run_r1/attempt_01/_archive.json').is_file()
    assert result['created']==['02_herb']


def test_explicit_import_batch(tmp_path):
    task={'task_id':'t1','formula':'温经汤'}
    assert import_directory(tmp_path,task)==tmp_path/'data/imports/t1'
    task['import_batch']='20260913_01'
    assert import_directory(tmp_path,task)==tmp_path/'data/pharm/温经汤/imports/t1/20260913_01'
    task['import_batch']='../outside'
    with pytest.raises(ValueError,match='unsafe'): import_directory(tmp_path,task)


def test_all_stages_archive_without_collisions(tmp_path):
    run = make_run(tmp_path)
    manifest = json.loads((run / 'manifest.json').read_text())
    for role in ('preflight', 'disease_reverse', 'review'):
        folder = run / role / 'attempt_01'
        folder.mkdir(parents=True)
        (folder / 'handoff.json').write_text(json.dumps({'role': role}))
        manifest['stages'][role] = {'status': 'blocked', 'directory': role + '/attempt_01', 'attempt': 1}
    (run / 'manifest.json').write_text(json.dumps(manifest))
    archive_run(run)
    root = tmp_path / 'data/pharm/温经汤'
    for role, stage_dir in [('preflight', '01_preflight'), ('disease_reverse', '03_reverse'), ('review', '04_review')]:
        record = json.loads((root / stage_dir / 'run_r1/attempt_01/handoff.json').read_text())
        assert record['role'] == role
    assert len(archive_run(run)['reused']) == 4
