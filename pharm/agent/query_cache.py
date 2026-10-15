"""Reuse successful queries only when inputs, code and source bytes agree.

Cached requests get a new archive and an explicit reference to the original
calculation. Original CSVs and result files remain untouched and are checked
before reuse. Missing sources, partial runs and damaged caches are never hits.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from ..core.common import ROOT, now, read_json, write_json
from .progress import report

VERSION = 1
MAX_ENTRIES = 16
MAX_BYTES = 1024 ** 3


def digest(path):
    # Source snapshots and original CSVs can be several GiB.
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _sources():
    from ..batman import local, formula_catalog
    from ..diseases import open_targets_local as ot, monarch_local as monarch
    files = list(local.batman_local_files({}).values())
    batman_dir = local._data_root({})
    for names in local.PREDICTED_FILES.values():
        files.extend(batman_dir / name for name in names if (batman_dir / name).is_file())
    formula_file = batman_dir / "formula_browse.txt"
    if formula_file.is_file():
        files.append(formula_file)
    ot_dir = ot.resolve_data_dir()
    _, grouped = ot._load_manifest(ot_dir)
    files.append(ot_dir / "manifest.json")
    for entries in grouped.values():
        files.extend(ot_dir / item["dataset"] / item["file"] for item in entries)
    monarch_dir = monarch.resolve_data_dir()
    files.extend(monarch_dir / name for name in monarch.REQUIRED_FILES)
    files.append(formula_catalog.ALIAS_FILE)
    files.extend(path for path in (monarch_dir / "metadata.yaml", ROOT / "server/static/disease-labels.json",
                                  ROOT / "configs/batman_data.local.json", ROOT / "configs/open_targets_data.local.json")
                 if path.is_file())
    preference = ROOT / "local/agent/formula_preferences.json"
    if preference.is_file():
        files.append(preference)
    for directory in ("pharm/agent", "pharm/batman", "pharm/diseases", "pharm/core", "pharm/discovery"):
        files.extend((ROOT / directory).glob("*.py"))
    return sorted(set(path.resolve() for path in files), key=str)


def fingerprint(body, direction):
    if os.environ.get("PHARM_AGENT_QUERY_CACHE", "true").lower() in {"0", "false", "off"}:
        return None
    if not any(body.get(key) for key in ("text", "formula", "herbs", "disease", "disease_id")):
        return None
    try:
        paths = _sources()
        hashes = []
        for index, path in enumerate(paths, 1):
            report("source_validation", "核对查询数据版本", current=index - 1, total=len(paths), unit="个文件")
            before = path.stat()
            checksum = digest(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                return None
            hashes.append((str(path), checksum))
        report("source_validation", "核对查询数据版本", current=len(paths), total=len(paths), unit="个文件")
        value = json.dumps({"version": VERSION, "direction": direction, "input": body, "sources": hashes,
                            "environment": {key: value for key, value in os.environ.items() if key.startswith("PHARM_")}},
                           sort_keys=True, ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return None


def _inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("缓存归档路径越界")
    return path


def restore(root, key, directory, direction):
    entry = root / key
    try:
        manifest = read_json(entry / "manifest.json")
        if not isinstance(manifest, dict) or manifest.get("version") != VERSION or manifest.get("key") != key or manifest.get("direction") != direction:
            return None
        origin = Path(manifest["origin"]).resolve()
        if origin.parent != directory.parent.resolve() or origin.name != manifest["origin_run_id"] or origin == directory.resolve():
            return None
        if not isinstance(manifest.get("origin_files"), dict) or "result.json" not in manifest["origin_files"]:
            return None
        report("cache_validation", "核对可复用查询与原始归档")
        for name, expected in manifest["origin_files"].items():
            if digest(_inside(origin, name)) != expected:
                return None
        payload_path = entry / "result.json.gz"
        if digest(payload_path) != manifest["payload_sha256"]:
            return None
        with gzip.open(payload_path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        result = payload["result"]
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            return None
        report("cache_hit", "已找到相同数据版本的完成结果", detail="复用原计算，不重复采集关联")
        result["created_at"] = now()
        result["execution"] = {"cache_hit": True, "source_fingerprint": key,
                               "reused_from": manifest["origin_run_id"], "computed_at": manifest["computed_at"]}
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return None
    # Once a new archive is created, write failures must propagate. Falling
    # through to compute would try to reuse a partially written directory.
    directory.mkdir(parents=True, exist_ok=False)
    report("writing", "保存本次复用结果与来源引用")
    write_json(directory / "result.json", result)
    if payload.get("herb_targets") is not None:
        write_json(directory / "herb_targets.json", payload["herb_targets"])
    return {"run_id": directory.name, "output_dir": str(directory), "result": result}


def _remove_entry(root, entry):
    # Only derived cache entries, never a research archive or an arbitrary path.
    if entry.resolve().parent != root.resolve():
        return
    for name in ("manifest.json", "manifest.json.tmp", "result.json.gz"):
        (entry / name).unlink(missing_ok=True)
    try:
        entry.rmdir()
    except OSError:
        pass


def store(root, key, directory, result, direction):
    if result.get("status") != "succeeded":
        return
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / (key + "." + uuid4().hex + ".tmp")
    temporary.mkdir()
    try:
        files = [path for path in directory.rglob("*") if path.is_file()
                 and path.name not in {"job.json", "worker_outcome.json"} and not path.name.endswith(".tmp")]
        report("cache_store", "保存可复用查询记录", detail="原始研究归档继续保留")
        origin_files = {str(path.relative_to(directory)): digest(path) for path in files}
        payload = {"result": result}
        targets = directory / "herb_targets.json"
        if targets.is_file():
            payload["herb_targets"] = read_json(targets)
        with gzip.open(temporary / "result.json.gz", "wt", encoding="utf-8", compresslevel=1) as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        write_json(temporary / "manifest.json", {
            "version": VERSION, "key": key, "direction": direction,
            "origin": str(directory.resolve()), "origin_run_id": directory.name,
            "computed_at": result.get("created_at"), "origin_files": origin_files,
            "payload_sha256": digest(temporary / "result.json.gz"),
        })
        if sum(path.stat().st_size for path in temporary.iterdir() if path.is_file()) > MAX_BYTES:
            return
        destination = root / key
        if destination.exists():
            _remove_entry(root, destination)
        temporary.rename(destination)
        # Bound this expendable cache to sixteen entries / one GiB. Research
        # directories referenced by manifests are never removed.
        entries = sorted((path for path in root.iterdir() if path.is_dir() and len(path.name) == 64),
                         key=lambda path: path.stat().st_mtime, reverse=True)
        size, kept = 0, 0
        for entry in entries:
            entry_size = sum(path.stat().st_size for path in entry.iterdir() if path.is_file())
            if kept >= MAX_ENTRIES or size + entry_size > MAX_BYTES:
                _remove_entry(root, entry)
            else:
                size += entry_size
                kept += 1
    finally:
        _remove_entry(root, temporary)


def execute(body, directory, direction, compute):
    directory = Path(directory)
    key = fingerprint(body, direction)
    root = directory.parent.parent / "query-cache"
    if key:
        cached = restore(root, key, directory, direction)
        if cached is not None:
            return cached
    response = compute()
    result = response["result"]
    result["execution"] = {"cache_hit": False, "source_fingerprint": key}
    write_json(directory / "result.json", result)
    if key and result.get("status") == "succeeded":
        try:
            # Do not cache a calculation across a live source/config change.
            if fingerprint(body, direction) == key:
                store(root, key, directory, result, direction)
        except (OSError, ValueError, TypeError, KeyError):
            # Cache availability never downgrades a completed calculation.
            pass
    return response
