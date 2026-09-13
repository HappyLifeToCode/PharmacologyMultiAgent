"""Immutable per-attempt source and analysis archive; archiving is not acceptance."""
from __future__ import annotations
import json
import re
import shutil
import threading
import uuid
from pathlib import Path
from .common import read_json, write_json, digest, now, public_artifact

STAGE_DIRS = {"herb_targets": "01_batman", "disease_targets": "02_disease", "genecards_targets": "02_disease/genecards", "omim_targets": "02_disease/omim", "intersection": "03_intersect", "network_analysis": "04_ppi", "enrichment_analysis": "05_enrich"}
_ARCHIVE_LOCK = threading.RLock()


def _component(value):
    if not isinstance(value, str) or not value or value != value.strip() or value.endswith('.') or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value in ('.', '..') or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I):
        raise ValueError('unsafe archive path component')
    return value


def init_archive(task, archive_root):
    canonical = Path(archive_root).resolve() / _component(task['formula'])
    for name in STAGE_DIRS.values():
        (canonical / name).mkdir(parents=True, exist_ok=True)
    meta = canonical / '_meta.md'
    with _ARCHIVE_LOCK:
        if not meta.exists():
            meta.write_text('# ' + task['formula'] + ' 数据流水账\n\n建目录不代表研究完成。原始文件、访问受限证据和分析产物按运行/尝试分批保存；以每批 status、参数和来源判断用途。合成数据禁止写入。归档时间不是数据库访问日期；未知访问日期保留空值。\n', encoding='utf-8')
    return canonical


def import_directory(project_root, task):
    """Explicit canonical import batch wins; legacy task import remains supported."""
    root = Path(project_root).resolve()
    batch = task.get('import_batch')
    if batch:
        return root / 'data/pharm' / _component(task['formula']) / 'imports' / _component(task['task_id']) / _component(batch)
    return root / 'data/imports' / _component(task['task_id'])


def _eligible(relative):
    # Raw source exports and frozen inputs are local-only, but belong in the archive.
    if relative.parts[0] in ('input_snapshot', 'sources', 'raw'):
        return True
    return public_artifact(relative)


def archive_run(run_directory, archive_root=None):
    """Archive finished live attempts, including partial access evidence. Idempotent.

    Copies the inputs frozen inside the attempt, never today's mutable imports.
    Existing batches are hash-checked and reused; changed data cannot overwrite them.
    """
    with _ARCHIVE_LOCK:
        run = Path(run_directory).resolve()
        manifest = read_json(run / 'manifest.json')
        if manifest.get('mode') != 'live':
            raise ValueError('only live runs can enter this archive')
        run_id = _component(manifest['run_id'])
        canonical = init_archive(manifest['task'], archive_root or run.parent.parent / 'data/pharm')
        created, reused = [], []
        for role, stage_name in STAGE_DIRS.items():
            stage = manifest.get('stages', {}).get(role, {})
            if stage.get('status') in (None, 'pending', 'running'):
                continue
            attempt = int(stage.get('attempt', 0))
            if attempt < 1:
                continue
            source = (run / stage.get('directory', role + '/attempt_%02d' % attempt)).resolve()
            if not source.is_relative_to(run):
                raise ValueError('stage directory escapes run')
            if not source.is_dir():
                raise ValueError('missing stage directory: ' + role)
            files = {}
            for p in sorted(source.rglob('*')):
                if p.is_file() and _eligible(p.relative_to(source)):
                    if not p.resolve().is_relative_to(source):
                        raise ValueError('archive source escapes stage')
                    files[p.relative_to(source).as_posix()] = p
            if not files:
                continue
            hashes = {name: digest(p) for name, p in files.items()}
            destination = canonical / stage_name / ('run_' + run_id) / ('attempt_%02d' % attempt)
            if destination.exists():
                existing = read_json(destination / '_archive.json')
                if existing['sha256'] != hashes or existing['stage_status'] != stage['status'] or any(not (destination / n).is_file() or digest(destination / n) != h for n,h in hashes.items()):
                    raise ValueError('existing archive differs; refusing overwrite: ' + str(destination))
                reused.append(stage_name)
                continue
            # Stage writes finish before this call. Keep partial copies separate until published.
            pending = destination.with_name(destination.name + '.pending_' + uuid.uuid4().hex[:8])
            pending.mkdir(parents=True, exist_ok=False)
            for name,p in files.items():
                target = pending / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)
                if digest(target) != hashes[name]:
                    raise ValueError('source changed while archiving: ' + name)
            provenance = []
            for name,p in files.items():
                if p.suffix.lower() == '.json':
                    try:
                        obj=read_json(p)
                    except (ValueError,OSError):
                        continue
                    def visit(v):
                        if isinstance(v,dict):
                            if 'accessed_at' in v: provenance.append({k:v[k] for k in ('source','source_url','requested_url','accessed_at','version','threshold','threshold_confirmed') if k in v})
                            for child in v.values(): visit(child)
                        elif isinstance(v,list):
                            for child in v: visit(child)
                    visit(obj)
            record = {'run_id':run_id, 'task_id':manifest['task']['task_id'], 'role':role, 'attempt':attempt, 'stage_status':stage['status'], 'archived_at':now(), 'stage_finished_at':stage.get('finished_at'), 'source_directory':source.relative_to(run).as_posix(), 'task_parameters':manifest['task'], 'metrics_at_archive':manifest.get('metrics',{}), 'source_records':provenance, 'note':'Archive preserves evidence; not a declaration of scientific completion.', 'sha256':hashes}
            write_json(pending / '_archive.json', record)
            pending.rename(destination)
            with (canonical / '_meta.md').open('a',encoding='utf-8') as f:
                f.write('\n## ' + run_id + ' / ' + stage_name + ' / attempt_%02d\n\n' % attempt)
                f.write('归档时间：' + record['archived_at'] + '\n\n状态：' + stage['status'] + '；文件数：' + str(len(files)) + '\n\n')
                f.write('计数：' + json.dumps(record['metrics_at_archive'],ensure_ascii=False) + '\n\n')
                dates=sorted({str(x['accessed_at']) for x in provenance})
                f.write('原始记录访问时间：' + ('；'.join(dates) if dates else '本步未记录；参见上游来源，不能用归档时间替代') + '\n')
            created.append(stage_name)
        return {'archive_root':str(canonical), 'created':created, 'reused':reused, 'meta':str(canonical/'_meta.md')}
