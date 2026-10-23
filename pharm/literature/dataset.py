"""Import and package only the CNKI data required by this project."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
import zipfile

from ..core.common import now, write_json

FORMAT = "pharm_cnki_dataset_v1"
FILES = ("papers.json", "triples.jsonl")


def safe_keyword(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 150 or re.search(r'[\\/:*?"<>|\x00-\x1f]', value) or value.strip() in {".", ".."}:
        raise ValueError("文献检索名不能包含路径分隔符或控制字符")
    return value.strip()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("数据文件超出文献数据目录")
    return path


def manifest(data_root, *, verify=True):
    data_root = Path(data_root).resolve()
    path = checked_path(data_root, "manifest.json")
    if not path.is_file():
        raise ValueError("本地论文数据缺少清单文件，请重新导入文献数据包")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("format") != FORMAT or not isinstance(value.get("corpora"), list):
        raise ValueError("文献数据清单格式无效，请重新导入数据包")
    seen = set()
    for corpus in value["corpora"]:
        keyword = safe_keyword(corpus.get("keyword"))
        if keyword in seen or corpus.get("directory") != "cnki_" + keyword:
            raise ValueError("文献数据清单的检索名或目录无效")
        seen.add(keyword)
        files = corpus.get("files")
        if not isinstance(files, dict) or "papers.json" not in files or set(files) - set(FILES):
            raise ValueError("文献数据清单包含不支持的文件")
        for name, record in files.items():
            file_path = checked_path(data_root, corpus["directory"] + "/" + name)
            if verify:
                raw = file_path.read_bytes()
                if record.get("sha256") != digest(raw) or record.get("size") != len(raw):
                    raise ValueError("文献数据校验失败：" + corpus["directory"] + "/" + name)
    return value


def read_corpus(source, keyword):
    source = Path(source).resolve()
    keyword = safe_keyword(keyword)
    directory = "cnki_" + keyword
    paths = {name: checked_path(source, directory + "/" + name) for name in FILES}
    contents = {name: path.read_bytes() for name, path in paths.items() if path.is_file()}
    if "papers.json" not in contents:
        raise ValueError("缺少论文数据：" + keyword)
    data = json.loads(contents["papers.json"].decode("utf-8-sig"))
    if data.get("keyword") != keyword or not isinstance(data.get("papers"), list):
        raise ValueError("论文检索名或数据结构不一致：" + keyword)
    ranks = set()
    for paper in data["papers"]:
        rank = paper.get("rank") if isinstance(paper, dict) else None
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1 or rank in ranks or not isinstance(paper.get("title"), str) or not paper["title"].strip():
            raise ValueError("论文编号或标题无效：" + keyword)
        if paper.get("abstract") is not None and not isinstance(paper["abstract"], str):
            raise ValueError("论文摘要格式无效：" + keyword)
        ranks.add(rank)
    if "triples.jsonl" in contents:
        for line in contents["triples.jsonl"].decode("utf-8-sig").splitlines():
            if line.strip() and not isinstance(json.loads(line), dict):
                raise ValueError("关系记录格式无效：" + keyword)
    if any(path.is_file() != (name in contents) or (name in contents and path.read_bytes() != contents[name]) for name, path in paths.items()):
        raise ValueError("来源文献正在变化，请完成采集后重试")
    entry = {"keyword": keyword, "directory": directory,
             "files": {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in contents.items()},
             "papers": len(data["papers"]), "abstracts": sum(bool(p.get("abstract")) for p in data["papers"])}
    return entry, contents


def import_data(source, destination, *, keywords=None, update=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("来源目录与目标目录必须不同")
    if (source / "manifest.json").is_file():
        manifest(source)
    keywords = keywords or [p.name[5:] for p in sorted(source.glob("cnki_*")) if p.is_dir() and (p / "papers.json").is_file()]
    if not keywords:
        raise ValueError("来源目录中没有可导入的文献")
    staged = [read_corpus(source, keyword) for keyword in keywords]
    prior = manifest(destination) if (destination / "manifest.json").is_file() else {"format": FORMAT, "created_at": now(), "corpora": []}
    entries = {entry["keyword"]: entry for entry in prior["corpora"]}
    # Check every conflict before touching destination files. Never overwrite silently.
    for entry, contents in staged:
        if entry["keyword"] in entries and set(entries[entry["keyword"]]["files"]) - set(entry["files"]):
            raise ValueError("新版本缺少原有文件，拒绝丢失关系记录")
        for name in set(FILES) - set(contents):
            if checked_path(destination, entry["directory"] + "/" + name).exists():
                raise ValueError("目标已有来源未提供的文件，拒绝丢失记录")
        for name, raw in contents.items():
            path = checked_path(destination, entry["directory"] + "/" + name)
            if path.exists() and path.read_bytes() != raw and not update:
                raise ValueError("已有不同版本的文献，未覆盖：" + str(path))
    changed = False
    for entry, contents in staged:
        for name, raw in contents.items():
            path = checked_path(destination, entry["directory"] + "/" + name)
            if not path.exists() or path.read_bytes() != raw:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
                temporary.write_bytes(raw); temporary.replace(path)
                changed = True
        changed = changed or entries.get(entry["keyword"]) != entry
        entries[entry["keyword"]] = entry
    if changed or not (destination / "manifest.json").exists():
        prior.update(corpora=[entries[k] for k in sorted(entries)], updated_at=now())
        write_json(destination / "manifest.json", prior)
    return manifest(destination)


def package(data_root, output):
    data_root, output = Path(data_root).resolve(), Path(output).resolve()
    value = manifest(data_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(data_root / "manifest.json", "manifest.json")
        for entry in value["corpora"]:
            for name in entry["files"]:
                relative = entry["directory"] + "/" + name
                archive.write(checked_path(data_root, relative), relative)
    # Detect a source change rather than handing off a mixed-version bundle.
    manifest(data_root)
    with zipfile.ZipFile(temporary) as archive:
        for entry in value["corpora"]:
            for name, record in entry["files"].items():
                if digest(archive.read(entry["directory"] + "/" + name)) != record["sha256"]:
                    raise ValueError("打包期间文献发生变化，请重试")
    temporary.replace(output)
    return {"path": str(output), "sha256": digest(output.read_bytes()), "corpora": len(value["corpora"]), "papers": sum(e["papers"] for e in value["corpora"])}
