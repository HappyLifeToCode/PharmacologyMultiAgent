"""Immutable, separate literature snapshots; original research archives stay read-only."""
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from ..core.common import ROOT, safe_name, write_json, read_json
from . import cnki


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def query(run_id, *, root=ROOT, snapshot=None):
    safe_name(run_id)
    if not run_id.startswith("forward_"):
        raise ValueError("文献验证只接受正向研究归档")
    root = Path(root).resolve()
    archive_root = (root / "local/agent/forward").resolve()
    archive = (archive_root / run_id / "result.json").resolve()
    if not archive.is_relative_to(archive_root) or not archive.is_file():
        raise FileNotFoundError("正向查询归档不存在")
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
    value = cnki.analyze(research, data_dir=cnki.data_directory(root))
    payload = {"run_id": run_id, "snapshot_id": None, "result": value}
    if value["status"] == "ready":
        value["database_archive_sha256"] = hashlib.sha256(raw).hexdigest()
        value["fingerprint"] = checksum([value["fingerprint"], value["database_archive_sha256"], hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
        payload.update(snapshot_id="cnki_" + uuid4().hex, integrity=checksum(value))
        write_json(snapshot_root / (payload["snapshot_id"] + ".json"), payload)
    return payload
