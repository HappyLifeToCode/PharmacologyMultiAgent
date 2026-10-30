"""Immutable, separate literature snapshots; original research archives stay read-only."""
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from ..core.common import ROOT, safe_name, write_json, read_json
from . import cnki
from . import dataset
from . import source_ranking
from .pipeline import run_literature_pipeline


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def research_archive(run_id, root):
    safe_name(run_id)
    direction = "forward" if run_id.startswith("forward_") else "reverse" if run_id.startswith("reverse_") else None
    if not direction: raise ValueError("文献分析只接受正向或反向研究归档")
    root = Path(root).resolve()
    archive_root = (root / "local/agent" / direction).resolve()
    archive = (archive_root / run_id / "result.json").resolve()
    if not archive.is_relative_to(archive_root) or not archive.is_file():
        raise FileNotFoundError("研究归档不存在")
    return archive, direction


def collection_request_id(run_id):
    return "literature_" + hashlib.sha256(run_id.encode()).hexdigest()[:48]


def collection_keyword(run_id, *, root=ROOT):
    archive, direction = research_archive(run_id, root)
    research = read_json(archive)
    research["direction"] = direction
    keyword, _ = cnki.subject_of(research)
    if not keyword: raise ValueError("此输入无法确定单一文献关键词，多味药材组合不自动拆开采集")
    return dataset.safe_keyword(keyword)


def query(run_id, *, root=ROOT, snapshot=None):
    archive, direction = research_archive(run_id, root)
    root = Path(root).resolve()
    snapshot_root = (root / "local/agent/literature" / run_id).resolve()
    if not snapshot_root.is_relative_to(root / "local/agent/literature"):
        raise ValueError("文献快照路径无效")
    if snapshot:
        if not re.fullmatch(r"cnki_[a-f0-9]{32}", snapshot):
            raise ValueError("文献快照编号无效")
        snapshot_path = (snapshot_root / (snapshot + ".json")).resolve()
        if not snapshot_path.is_relative_to(snapshot_root):
            raise ValueError("文献快照路径无效")
        value = read_json(snapshot_path)
        if value.get("run_id") != run_id or value.get("snapshot_id") != snapshot or value.get("integrity") != checksum(value.get("result")):
            raise ValueError("文献快照内容校验失败")
        if value["result"].get("database_archive_sha256") != hashlib.sha256(archive.read_bytes()).hexdigest():
            raise ValueError("原研究归档已变化，不能沿用此文献快照")
        return value
    raw = archive.read_bytes()
    research = json.loads(raw.decode("utf-8-sig"))
    research["direction"] = direction
    value = cnki.analyze(research, data_dir=cnki.data_directory(root))
    if value.get("status") == "ready" and direction == "forward":
        source_path = Path(value["source"]["directory"]) / "papers.json"
        source_raw = source_path.read_bytes()
        source_payload = json.loads(source_raw.decode("utf-8-sig"))
        formula = value.get("subject")
        catalog_path = root / "local/literature/concept_maps" / (dataset.safe_keyword(formula) + ".json")
        catalog = read_json(catalog_path) if catalog_path.is_file() else None
        value["evidence_pipeline"] = run_literature_pipeline(
            source_payload,
            research,
            formula=formula,
            requested_limit=max(1, len(source_payload.get("papers", []))),
            concept_catalog=catalog,
            frequency_candidates=value.get("candidates", []),
            source_path=source_path,
            source_sha256=hashlib.sha256(source_raw).hexdigest(),
            concept_catalog_path=catalog_path if catalog is not None else None,
        )
    value = source_ranking.apply(value, root=root)
    payload = {"run_id": run_id, "snapshot_id": None, "result": value}
    if value["status"] == "ready":
        value["database_archive_sha256"] = hashlib.sha256(raw).hexdigest()
        value["fingerprint"] = checksum([value["fingerprint"], value["database_archive_sha256"], hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
        payload.update(snapshot_id="cnki_" + uuid4().hex, integrity=checksum(value))
        write_json(snapshot_root / (payload["snapshot_id"] + ".json"), payload)
    return payload
