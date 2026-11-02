"""Narrow, read-only research tools with real provenance and bounded payloads."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote

import httpx

from ..core.common import now
from .cnki_bridge import permits_evidence


def definition(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


TEXT = {"type": "string", "minLength": 1, "maxLength": 500}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 5}
DEFINITIONS = [
    definition("list_local_resources", "查看本地已保存的 CNKI 关键词和最近的研究归档，不联网。", {}, []),
    definition("search_local_literature", "检索本地 CNKI 标题摘要。query 用方剂、疾病等关键词，以空格分隔；返回的原句可作为引文，标题命中不代表疗效。", {"query": TEXT, "limit": LIMIT}, ["query"]),
    definition("collect_cnki_literature", "CNKI 专用：输入一个明确方剂、药材或疾病关键词。已有文献直接汇总候选；缺少时启动知网正常网页全量标题摘要采集，已有题录则仅补分析。页面显示进度，完成后继续回答；只查CNKI时不改用其他来源。", {"keyword": {"type": "string", "minLength": 1, "maxLength": 150}, "kind": {"type": "string", "enum": ["formula", "herb", "disease"]}}, ["keyword", "kind"]),
    definition("search_online_literature", "联网查询 Europe PMC 文献及可用摘要。用精准中英文关键词；缺少摘要时不能推断研究结果。不会采集 CNKI。", {"query": TEXT, "limit": LIMIT}, ["query"]),
    definition("read_evidence", "读取已取得证据的完整可用文本，核对引文；不自动获取未收录全文。", {"evidence_id": TEXT}, ["evidence_id"]),
    definition("read_research", "读取已保存的正向/反向研究结果及部分来源证据。run_id 来自资源列表或用户当前结果。", {"run_id": TEXT}, ["run_id"]),
    definition("query_pharmacology", "用现有本地 BATMAN、Open Targets、Monarch 查询候选关联。方向 forward 为方剂/药材查疾病，reverse 为疾病查药材。缺数据或输入歧义返回说明，不猜组成、不下载数据；最多返回5项。", {"direction": {"type": "string", "enum": ["forward", "reverse"]}, "text": TEXT}, ["direction", "text"]),
]
LABELS = {"list_local_resources": "查看本地资料", "search_local_literature": "检索本地文献",
          "collect_cnki_literature": "获取 CNKI 文献与候选",
          "search_online_literature": "检索联网文献", "read_evidence": "核对证据原文",
          "read_research": "读取研究结果", "query_pharmacology": "查询靶点关联"}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = PlainText(); parser.feed(str(value or ""))
    return " ".join(parser.parts).strip()


def validate(name, args):
    schema = next((d["function"]["parameters"] for d in DEFINITIONS if d["function"]["name"] == name), None)
    if schema is None or not isinstance(args, dict) or set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
        raise ValueError("工具或参数无效")
    for k, value in args.items():
        if k == "limit":
            if type(value) is not int or not 1 <= value <= 5: raise ValueError("返回条数须为1到5")
        elif not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ValueError("工具文字参数无效")
    if name == "query_pharmacology" and args["direction"] not in {"forward", "reverse"}:
        raise ValueError("查询方向无效")
    if name == "collect_cnki_literature" and args["kind"] not in {"formula", "herb", "disease"}:
        raise ValueError("知网关键词类型无效")


def permitted_tool(name, scope):
    return scope != "cnki" or name in {"list_local_resources", "search_local_literature", "collect_cnki_literature", "read_evidence"}


def definitions(scope):
    return [d for d in DEFINITIONS if permitted_tool(d["function"]["name"], scope)]


def add_evidence(state, *, title, text, source, url=None, metadata=None):
    full = str(text or "")
    row = {"title": str(title)[:500], "text": full[:16000], "source": source, "url": url,
           "metadata": metadata or {}, "retrieved_at": now(), "truncated": len(full) > 16000}
    identity = hashlib.sha256(json.dumps([title, full, source, url], ensure_ascii=False).encode()).hexdigest()
    old = next((e for e in state["evidence"] if e["identity"] == identity), None)
    if old is None:
        if len(state["evidence"]) >= 80:
            return {"status": "limit", "message": "本会话已达到80条证据上限，请开始新对话"}
        row.update(id="E" + str(len(state["evidence"]) + 1), identity=identity)
        state["evidence"].append(row)
        old = row
    return {"evidence_id": old["id"], "title": old["title"], "text": old["text"][:2400],
            "source": old["source"], "url": old["url"], "metadata": old["metadata"],
            "excerpt_truncated": len(full) > 2400, "full_available_via": "read_evidence"}


def local_resources(root, cnki_only=False):
    from ..literature import cnki, dataset
    directory = cnki.data_directory(root)
    listing = dataset.manifest(directory, verify=False) if (directory / "manifest.json").exists() else {"corpora": []}
    if cnki_only:
        return {"cnki_corpora": [{"keyword": e["keyword"], "papers": e.get("papers")} for e in listing["corpora"][:100]], "scope": "仅本地 CNKI；未命中时可使用 collect_cnki_literature"}
    paths = []
    for direction in ("forward", "reverse"):
        paths.extend((Path(root) / "local/agent" / direction).glob(direction + "_*/result.json"))
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return {"cnki_corpora": [{"keyword": e["keyword"], "papers": e.get("papers")} for e in listing["corpora"][:100]],
            "research_runs": [p.parent.name for p in paths[:20]],
            "scope": "当前项目已有数据；未列出不代表数据库或文献中不存在"}


def search_local(root, args, state):
    from ..literature import cnki, dataset
    directory = cnki.data_directory(root)
    if not (directory / "manifest.json").is_file():
        return {"status": "missing_data", "message": "本地没有 CNKI 文献包。可调用 collect_cnki_literature 获取指定关键词的知网文献；需要联网权限，不要自动替换为其他来源。"}
    listing = dataset.manifest(directory, verify=False)
    tokens = [t.casefold() for t in re.split(r"\s+", args["query"].strip()) if t]
    corpora = sorted(listing["corpora"], key=lambda e: -sum(t in e["keyword"].casefold() for t in tokens))
    hits, scanned, used_bytes, skipped = [], 0, 0, 0
    for entry in corpora[:20]:
        path = dataset.checked_path(directory, entry["directory"] + "/papers.json")
        size = path.stat().st_size
        if size > 10_000_000 or used_bytes + size > 30_000_000:
            skipped += 1; continue
        raw = path.read_bytes(); used_bytes += len(raw)
        if hashlib.sha256(raw).hexdigest() != entry["files"]["papers.json"]["sha256"]:
            raise ValueError("本地文献校验失败，请完成采集后重试")
        payload = json.loads(raw.decode("utf-8-sig")); scanned += 1
        for paper in payload.get("papers", []):
            content = str(paper.get("title") or "") + "\n" + str(paper.get("abstract") or "")
            score = sum(t in content.casefold() for t in tokens)
            if score:
                hits.append((score, paper, entry))
    hits.sort(key=lambda item: (-item[0], str(item[1].get("title"))))
    records, seen = [], set()
    for _, paper, entry in hits:
        pid = cnki.paper_identity(paper)
        if pid in seen: continue
        seen.add(pid)
        records.append(add_evidence(state, title=paper["title"], text=paper["title"] + "\n" + (paper.get("abstract") or "未提供摘要；仅可核对标题"),
            source="本地 CNKI", url=paper.get("url"), metadata={"paper_id": pid, "keyword": entry["keyword"],
            "source_sha256": entry["files"]["papers.json"]["sha256"], "authors": paper.get("authors"), "date": paper.get("date"), "journal": paper.get("source"), "scope": "标题与摘要，未读全文"}))
        if len(records) >= args.get("limit", 5): break
    return {"status": "ok", "records": records, "scanned_corpora": scanned,
            "partial_scan": len(corpora) > 20 or skipped > 0, "matching": "关键词匹配，按命中关键词数排序；不是疗效或证据等级排序"}


async def search_online(args, state):
    params = {"query": args["query"], "format": "json", "resultType": "core", "pageSize": args.get("limit", 5)}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        async with client.stream("GET", "https://www.ebi.ac.uk/europepmc/webservices/rest/search", params=params) as response:
            if response.status_code != 200:
                return {"status": "unavailable", "message": "Europe PMC 返回 HTTP " + str(response.status_code)}
            parts, size = [], 0
            async for part in response.aiter_bytes():
                size += len(part)
                if size > 3_000_000: raise ValueError("文献响应过大，请缩小检索范围")
                parts.append(part)
    data = json.loads(b"".join(parts))
    rows = []
    for paper in data.get("resultList", {}).get("result", [])[:args.get("limit", 5)]:
        pmid, identifier = str(paper.get("pmid") or ""), str(paper.get("id") or "")
        url = "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/" if pmid.isdigit() else None
        if not url and identifier and paper.get("source"):
            url = "https://europepmc.org/article/" + quote(str(paper["source"]), safe="") + "/" + quote(identifier, safe="")
        abstract = plain(paper.get("abstractText"))
        rows.append(add_evidence(state, title=plain(paper.get("title")), text=plain(paper.get("title")) + "\n" + (abstract or "来源未返回摘要，仅为题录，不能据此判断疗效"), source="Europe PMC",
            url=url, metadata={"pmid": pmid or None, "source_id": identifier, "doi": paper.get("doi"),
            "authors": paper.get("authorString"), "year": paper.get("pubYear"), "journal": ((paper.get("journalInfo") or {}).get("journal") or {}).get("title"), "query": args["query"], "scope": "题录与来源返回的摘要，未读全文"}))
    return {"status": "ok", "records": rows, "hit_count": data.get("hitCount"), "note": "检索命中不等于研究支持；零命中不等于不存在证据"}


def read_research(root, run_id, state):
    from ..literature.service import research_archive
    if not re.fullmatch(r"(?:forward|reverse)_[a-f0-9]{32}", run_id):
        raise ValueError("研究编号无效")
    path, direction = research_archive(run_id, root)
    if path.stat().st_size > 100_000_000:
        return {"status": "too_large", "message": "归档过大，请在研究工作台查看"}
    raw = path.read_bytes(); data = json.loads(raw.decode("utf-8-sig"))
    rows = []
    for item in data.get("candidates", [])[:5]:
        rows.append({k: item[k] for k in ("disease_id", "disease", "disease_name", "canonical_name", "herb", "herb_name", "herb_id", "herb_record", "display_name", "matched_count", "input_coverage", "disease_coverage", "herb_coverage", "confidence", "compound_count", "target_count") if k in item})
        rows[-1]["matched_genes_excerpt"] = item.get("matched_genes", [])[:12]
        rows[-1]["evidence_excerpt"] = item.get("evidence", [])[:2]
    summary = {"run_id": run_id, "direction": direction, "input": data.get("input"), "candidates": rows,
               "candidate_count": data.get("candidate_count"), "status": data.get("status"),
               "source_status": {k: {"status": v.get("status")} for k, v in (data.get("source_status") or {}).items() if isinstance(v, dict)},
               "scope": "前5个候选与部分逐条记录；数据库关联不代表疗效", "scientific_complete": False}
    evidence = add_evidence(state, title="研究归档 " + run_id, text=json.dumps(summary, ensure_ascii=False),
        source="本地药理计算", metadata={"run_id": run_id, "sha256": hashlib.sha256(raw).hexdigest(), "scope": "部分候选与来源记录"})
    return {"status": "ok", "records": [evidence], "run_id": run_id}


async def execute(name, args, state, root, network):
    validate(name, args)
    scope = state.get("source_scope", "all")
    if not permitted_tool(name, scope):
        return {"status": "disabled", "message": "本轮仅允许 CNKI，不执行其他数据库或文献源查询"}
    if name == "list_local_resources":
        result = await asyncio.to_thread(local_resources, root, scope == "cnki")
        if scope == "cnki": result.pop("research_runs", None)
        return result
    if name == "search_local_literature":
        return await asyncio.to_thread(search_local, root, args, state)
    if name == "search_online_literature":
        if not network:
            return {"status": "disabled", "message": "本轮未开启联网资料检索，请使用本地资料或告知用户可打开联网开关"}
        return await search_online(args, state)
    if name == "read_evidence":
        row = next((e for e in state["evidence"] if e["id"] == args["evidence_id"] and permits_evidence(e, scope)), None)
        return {"status": "ok", "record": row} if row else {"status": "not_found", "message": "该证据编号不存在，不可引用"}
    if name == "collect_cnki_literature":
        return {"status": "requires_context", "message": "知网采集需要由问答任务关联后执行"}
    if name == "read_research":
        return await asyncio.to_thread(read_research, root, args["run_id"], state)
    from ..batman import local, formula_catalog
    from ..agent import jobs, disease_input
    # Require local files even when web literature is allowed: never download BATMAN implicitly.
    try:
        local.batman_local_files({})
    except local.MissingLocalData:
        return {"status": "missing_data", "message": "本机 BATMAN 数据缺失；未启动药理计算，也未下载数据。可检索文献，但不能编造靶点结果。"}
    body = {"text": args["text"], "top_k": 5}
    if args["direction"] == "forward":
        resolution = await asyncio.to_thread(formula_catalog.resolve, text=args["text"], root=root)
        if resolution["status"] != "ready":
            return {"status": "needs_selection", "message": "方剂组成需要用户选择；请让用户切换数据库查询模式完成组成确认后继续。", "choices": resolution.get("choices", [])[:6]}
        body.update({k: v for k, v in resolution["query_input"].items() if k in {"formula_variant_id", "formula_herbs", "formula_fingerprint"}})
    else:
        resolution = await asyncio.to_thread(disease_input.resolve, args["text"])
        if resolution["status"] != "ready":
            return {"status": "needs_selection", "message": resolution.get("message"), "choices": resolution.get("choices", [])[:6]}
    class Connected:
        async def is_disconnected(self): return False
    result = await jobs.manager.run(Connected(), body, root, "assistant_" + uuid4().hex, direction=args["direction"])
    return await asyncio.to_thread(read_research, root, result["run_id"], state)
