"""Connect conversational requests to the existing CNKI collection manager."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from ..core.common import write_json, digest
from ..literature import cnki, dataset, jobs, source_ranking

MAX_WAIT_SECONDS = 8 * 60 * 60
POLL_SECONDS = 2


class NeedsAttention(Exception):
    pass


def resolve_scope(question, requested="auto", prior="all"):
    if requested == "cnki": return requested
    # This only enforces explicit source restrictions; the LLM still plans the query.
    for match in re.finditer(r"(?:只|仅)(?:(?:进行|使用|检索|查询|查|用|限于|限|在|从)|\s){0,8}(?:CNKI|中国知网|知网)", question, re.I):
        before = question[max(0, match.start()-3):match.start()]
        after = question[match.end():match.end()+35]
        if re.search(r"不|别|莫", before): continue
        if re.search(r"(?:和|及|、|与).*(?:PubMed|Europe\s*PMC|BATMAN|Open\s*Targets)", after, re.I): continue
        return "cnki"
    if requested == "all": return "all"
    return prior if prior in {"all", "cnki"} else "all"


def permits_evidence(evidence, scope):
    return scope != "cnki" or evidence.get("source") in {"本地 CNKI", "CNKI 文献候选汇总"}


def research_input(keyword, kind):
    if kind == "formula": return {"input": {"formula": keyword}}
    if kind == "herb": return {"input": {"herbs": [keyword]}}
    return {"direction": "reverse", "resolved_disease": {"disease_name": keyword}, "input": {"disease": keyword}}


def load_result(root, keyword, kind):
    # CNKI-only requests do not require or consult disease/target databases.
    directory = cnki.data_directory(root)
    if not (directory / "manifest.json").is_file():
        if any(directory.glob("cnki_*/papers.json")):
            raise ValueError("本地已有未登记的知网文件，请先校验导入，不能自动覆盖")
        return {"status": "not_configured", "message": "本地尚无已登记的知网语料"}
    return cnki.analyze(research_input(keyword, kind), data_dir=directory, catalog=({}, {}))


def start(root, record, args, network, selection):
    keyword = dataset.safe_keyword(args["keyword"])
    kind = args["kind"]
    local = load_result(root, keyword, kind)
    if local["status"] == "ready":
        return {"status": "cnki_ready", "keyword": keyword, "kind": kind, "local_reused": True}
    if local["status"] not in {"not_configured", "not_found", "needs_extraction"}:
        return {"status": "unavailable", "message": local.get("message", "本地文献不可用")}
    local_only = local["status"] == "needs_extraction"
    if not local_only and not network:
        return {"status": "disabled", "message": "本地没有对应 CNKI 文献，且本轮未允许联网。打开联网资料后可发起知网采集，不会改用其他数据库。"}
    request_id = "chat_cnki_" + hashlib.sha256((record["id"] + keyword).encode()).hexdigest()[:48]
    status = jobs.manager.start(root, keyword, request_id, local_only=local_only, limit=None, model_selection=selection)
    pending = {"collection_id": status["id"], "keyword": keyword, "kind": kind,
               "local_analysis": local_only, "request_id": request_id}
    return {"status": "pending_cnki", "pending": pending, "collection": status,
            "message": "已关联知网任务；页面将持续显示进度，完成后继续回答，不把任务启动当作已取得证据。"}


def current(root, pending):
    return jobs.manager.status(root, pending["collection_id"])


def resume(root, pending, selection):
    return jobs.manager.resume(root, pending["collection_id"], model_selection=selection)


def cancel(root, pending):
    return jobs.manager.cancel(root, pending["collection_id"])


def result(root, record, state, keyword, kind):
    from .tools import add_evidence
    value = load_result(root, keyword, kind)
    if value.get("status") != "ready":
        raise NeedsAttention("采集任务已结束，但对应文献尚未形成可读取的分析结果；请检查任务后重试")
    value = source_ranking.apply(value, root=root)
    all_candidates = value.get("source_candidates", value.get("candidates", [])) + value.get("source_formula_candidates", value.get("formula_candidates", []))
    items = [{"names": c["raw_names"], "paper_count": c["paper_count"], "direct_paper_count": c["direct_paper_count"],
              "candidate_type": c.get("candidate_type", "disease"), "rank": c["rank"]} for c in all_candidates]
    snapshot_id = uuid4().hex
    snapshot = Path(root) / "local/assistant/cnki" / record["id"] / (snapshot_id + ".json")
    write_json(snapshot, value)
    record["cnki_result"] = {"snapshot_id": snapshot_id, "sha256": digest(snapshot), "keyword": keyword, "kind": kind,
                             "candidate_count": len(items), "paper_count": value["source"]["unique_paper_count"], "items": items}
    summary = {"keyword": keyword, "kind": kind, "paper_count": value["source"]["unique_paper_count"],
               "candidate_count": len(items), "candidates_shown": items[:40], "candidates_truncated": len(items) > 40,
               "source_files": value["source"]["files"], "ranking": value.get("source_ranking", {}).get("note"),
               "scope": "当前已保存并通过出处检查的知网标题摘要关系，非疗效结论；完整候选见页面列表及文献明细"}
    records = [add_evidence(state, title=keyword + "：CNKI 文献候选汇总", text=json.dumps(summary, ensure_ascii=False),
        source="CNKI 文献候选汇总", metadata={"snapshot_id": snapshot_id, "sha256": record["cnki_result"]["sha256"], "scope": summary["scope"]})]
    seen = set()
    # Representative quotes are explicitly bounded; the full candidate list is retained in the snapshot.
    for candidate in all_candidates:
        for e in candidate.get("evidence", []):
            if e["paper_id"] in seen: continue
            seen.add(e["paper_id"])
            records.append(add_evidence(state, title=e["title"], text=e["title"] + "\n" + (e.get("abstract") or "未提供摘要") + "\n已核对的关系原句：" + e["quote"],
                source="本地 CNKI", url=e.get("url"), metadata={"paper_id": e["paper_id"], "raw_disease": e["raw_disease"],
                "quote": e["quote"], "quote_field": e["quote_field"], "study_type": e.get("study_type"),
                "intervention": e.get("intervention"), "source_files": value["source"]["files"], "scope": "知网标题/摘要与程序校验原句；研究类型仅初步识别"}))
            break
        if len(records) >= 7: break
    return {"status": "ok", "records": records, "candidate_count": len(items), "paper_count": value["source"]["unique_paper_count"],
            "message": "CNKI 资料已就绪。汇总证据若被截短，请调用 read_evidence 阅读完整可用文本；代表性论文只是抽样，完整候选和明细已保存。"}
