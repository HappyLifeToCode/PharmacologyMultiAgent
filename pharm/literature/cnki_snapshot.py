"""Normalise cached CNKI Agent ``papers.json`` snapshots.

The external UI Agent owns browser automation, sessions and credentials.  This
module only reads its exported JSON and converts it into a stable, versioned
contract for PharmacologyMultiAgent.  It deliberately does not infer treatment
relationships or modify candidate-disease ranking.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..core.common import digest, read_json, write_json


SCHEMA_VERSION = "literature-search-v1"
ADAPTER_VERSION = "cnki-snapshot-adapter-v1"
SOURCE_SCHEMA = "cnki-papers-json-unversioned"


def _text(value):
    return str(value or "").strip()


def _items(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def _authors(value):
    if isinstance(value, (list, tuple)):
        return _items(value)
    return [item.strip() for item in re.split(r"[;；]", _text(value)) if item.strip()]


def _normalised_title(value):
    return re.sub(r"[\s\-—–:：,，.。;；'\"“”‘’()（）《》<>]+", "", _text(value).casefold())


def _record_id(title, authors, publication_date):
    identity = json.dumps({
        "source": "cnki",
        "title": _normalised_title(title),
        "authors": [_text(item).casefold() for item in authors],
        "publication_date": _text(publication_date),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _accessed_at(payload):
    for key in ("collected_at", "last_updated_at", "first_collected_at"):
        value = _text(payload.get(key))
        if value:
            return value
    return None


def _status(record_count, requested_limit):
    if not record_count:
        return "failed"
    return "succeeded" if record_count >= requested_limit else "partial"


def normalise_cnki_snapshot(payload, *, formula=None, formula_aliases=None,
                            requested_limit=20, source_path=None, source_sha256=None):
    """Convert an external CNKI ``papers.json`` payload to our v1 contract."""
    if not isinstance(payload, dict):
        raise ValueError("CNKI papers.json 顶层必须是对象")
    if isinstance(requested_limit, bool) or not isinstance(requested_limit, int) or requested_limit < 1:
        raise ValueError("requested_limit 必须是正整数")
    papers = payload.get("papers")
    if not isinstance(papers, list):
        raise ValueError("CNKI papers.json 缺少 papers 数组")
    keyword = _text(payload.get("keyword"))
    formula = _text(formula) or keyword
    if not formula:
        raise ValueError("CNKI 快照缺少方剂/检索关键词")
    warnings = []
    if keyword and keyword != formula:
        warnings.append(f"外部检索词“{keyword}”与指定方剂“{formula}”不同")

    records, seen = [], set()
    for position, paper in enumerate(papers, 1):
        if not isinstance(paper, dict):
            warnings.append(f"第 {position} 条记录不是对象，已跳过")
            continue
        title = _text(paper.get("title"))
        if not title:
            warnings.append(f"第 {position} 条记录缺少标题，已跳过")
            continue
        authors = _authors(paper.get("authors"))
        publication_date = _text(paper.get("date")) or None
        record_id = _record_id(title, authors, publication_date)
        if record_id in seen:
            warnings.append(f"重复文献已去重：{title}")
            continue
        seen.add(record_id)
        rank = paper.get("rank")
        try:
            rank = int(rank) if rank is not None else position
        except (TypeError, ValueError):
            rank = position
            warnings.append(f"文献排名无法解析，已按出现顺序处理：{title}")
        records.append({
            "record_id": record_id,
            "source": "cnki",
            "source_id": None,
            "rank": rank,
            "title": title,
            "abstract": _text(paper.get("abstract")),
            "authors": authors,
            "journal": _text(paper.get("source")) or None,
            "publication_date": publication_date,
            "publication_year": int(publication_date[:4]) if publication_date and publication_date[:4].isdigit() else None,
            "pmid": None,
            "doi": None,
            "url": _text(paper.get("url")) or None,
            "keywords": _items(paper.get("keywords")),
            "query": keyword or formula,
            "accessed_at": _accessed_at(payload),
            "source_metadata": {
                "journal_level": paper.get("journal_level"),
                "cif": paper.get("cif"),
                "is_modified": paper.get("is_modified"),
                "western_diseases": _items(paper.get("western_diseases")),
                "note": paper.get("note"),
            },
        })

    records.sort(key=lambda item: (item["rank"], item["record_id"]))
    status = _status(len(records), requested_limit)
    if len(records) < requested_limit:
        warnings.append(f"实际获得 {len(records)} 篇，少于请求的 {requested_limit} 篇")
    missing_urls = sum(not item["url"] for item in records)
    if missing_urls:
        warnings.append(f"{missing_urls} 篇文献缺少稳定详情 URL；记录 ID 不依赖 URL")
    missing_abstracts = sum(not item["abstract"] for item in records)
    if missing_abstracts:
        warnings.append(f"{missing_abstracts} 篇文献缺少摘要")

    source_result = {
        "source": "cnki",
        "status": status,
        "query": keyword or formula,
        "job_id": "cnki_" + (keyword or formula),
        "cached": True,
        "accessed_at": _accessed_at(payload),
        "requested_count": requested_limit,
        "collected_count": len(records),
        "reported_total_results": payload.get("total_results"),
        "sort": payload.get("sort"),
        "records": records,
        "warnings": warnings,
        "provenance": {
            "adapter_version": ADAPTER_VERSION,
            "source_schema": SOURCE_SCHEMA,
            "source_path": str(source_path) if source_path else None,
            "source_sha256": source_sha256,
            "external_first_collected_at": payload.get("first_collected_at"),
            "external_last_updated_at": payload.get("last_updated_at"),
            "external_last_run_new": payload.get("last_run_new"),
        },
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "request": {
            "formula": formula,
            "formula_aliases": _items(formula_aliases),
            "disease_terms": [],
            "sources": ["cnki"],
            "max_results": requested_limit,
        },
        "source_results": [source_result],
        "ranking_effect": "none",
        "note": "离线文献规范化结果；尚未抽取方剂-疾病关系或评定证据等级，不参与候选疾病排序。",
    }


def _safe_directory_name(value):
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", _text(value)).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("方剂名不能用于本地快照目录")
    return cleaned


def _snapshot_date(payload):
    value = _accessed_at(payload)
    match = re.match(r"\d{4}-\d{2}-\d{2}", value or "")
    return match.group(0) if match else datetime.now(timezone.utc).date().isoformat()


def import_cnki_snapshot(source_path, output_root, *, formula=None, formula_aliases=None,
                         requested_limit=20):
    """Copy and normalise one external snapshot into an ignored local bundle."""
    source_path = Path(source_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    raw = source_path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode("utf-8-sig"))
    formula = _text(formula) or _text(payload.get("keyword"))
    snapshot_id = f"{_snapshot_date(payload)}_{source_sha256[:12]}"
    directory = Path(output_root).resolve() / _safe_directory_name(formula) / snapshot_id
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        if manifest.get("source_sha256") == source_sha256:
            return directory, read_json(directory / "normalized_papers.json")

    directory.mkdir(parents=True, exist_ok=True)
    raw_path = directory / "raw_papers.json"
    raw_path.write_bytes(raw)
    result = normalise_cnki_snapshot(
        payload, formula=formula, formula_aliases=formula_aliases,
        requested_limit=requested_limit, source_path=source_path,
        source_sha256=source_sha256)
    request_path = directory / "request.json"
    normalised_path = directory / "normalized_papers.json"
    write_json(request_path, result["request"])
    write_json(normalised_path, result)
    write_json(manifest_path, {
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "formula": formula,
        "source": "cnki",
        "status": result["status"],
        "requested_count": requested_limit,
        "collected_count": result["source_results"][0]["collected_count"],
        "accessed_at": result["source_results"][0]["accessed_at"],
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "files": {
            "raw_papers.json": digest(raw_path),
            "request.json": digest(request_path),
            "normalized_papers.json": digest(normalised_path),
        },
        "review_status": "collected_unreviewed",
        "ranking_effect": "none",
    })
    return directory, result
