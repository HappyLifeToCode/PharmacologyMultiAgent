"""Project-owned CNKI runner, adapted from UI-Agent's fragment pipeline.

No collection happens on import or through the literature-reading API. The CLI
requires --execute. Browser/model/runtime installation remains an external dependency.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from urllib.parse import urlparse
from uuid import uuid4

from ..core.common import ROOT, now, write_json
from . import dataset, cnki
from . import browser_session

PROMPTS = Path(__file__).with_name("prompts")
DEFAULT_MODEL = "gpt-5.6-luna"
RUNTIME_VERSION = "20261026-phased-analysis"


class CollectionCancelled(RuntimeError):
    pass


class CollectionRuntimeError(RuntimeError):
    def __init__(self, message, code):
        super().__init__(message)
        self.failure_code = code


class CollectionValidationError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.failure_code = code


def mcp_configuration(root=ROOT, *, output=None, connect_session=True):
    root = Path(root).resolve()
    address = browser_session.endpoint(root) if connect_session else None
    headed = os.environ.get("PHARM_CNKI_HEADED", "").strip().lower() in {"1", "true", "yes", "on"}
    browser_args = ["--cdp-endpoint", address] if address else ([] if headed else ["--headless"]) + ["--config", str(root / "configs/cnki_browser.json")]
    return {"mcp_servers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@0.0.64", *browser_args, "--save-trace",
        "--output-dir", str(output or root / "local/cnki-browser")],
        "required": True, "startup_timeout_sec": 60, "tool_timeout_sec": 120,
        "default_tools_approval_mode": "approve",
        "enabled_tools": ["browser_run_code", "browser_take_screenshot", "browser_wait_for"]}}}


def browser_failure(log_path):
    """Read structured errors, never expose prompts, tool arguments, or credentials."""
    failure = None
    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        try: event = json.loads(line)
        except ValueError: continue
        if not isinstance(event, dict): continue
        item = event.get("item")
        if not isinstance(item, dict) or event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call" or item.get("tool") != "browser_run_code": continue
        error = item.get("error")
        message = str(error.get("message", "") if isinstance(error, dict) else error or "").lower()
        if "approval" in message and ("requires" in message or "never" in message):
            failure = CollectionRuntimeError("采集浏览器工具未获调用授权，尚未进入知网；请加载修复后的采集配置再续跑。", "browser_approval_required")
        elif not error and item.get("status") in {"completed", "succeeded"}:
            failure = None
    return failure


def validate_model(model):
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,149}", model):
        raise ValueError("Codex模型名称无效")
    return model


def valid_limit(limit):
    return limit is None or (type(limit) is int and limit > 0)


class _OwnedLock:
    def __init__(self, path, message):
        self.path, self.message, self.held = path, message, False

    def acquire(self):
        if self.held: return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(self.message)
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        self.held = True

    def release(self):
        if not self.held: return
        try:
            self.path.unlink(missing_ok=True)
        finally:
            self.held = False


@contextmanager
def _file_lock(path, message):
    lock = _OwnedLock(path, message)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def collector_lock(root):
    with _file_lock(Path(root) / "local/agent/cnki-collection/collector.lock",
                    "已有文献采集任务或遗留锁；请先核对任务状态，不要并发启动"):
        yield


@contextmanager
def analysis_lock(root):
    with _file_lock(Path(root) / "local/agent/cnki-collection/analysis.lock",
                    "已有文献分析任务或遗留锁；请先核对任务状态，不要并发启动"):
        yield


@contextmanager
def execution_lock(root, phased):
    collection = _OwnedLock(Path(root) / "local/agent/cnki-collection/collector.lock",
                            "已有文献采集任务或遗留锁；请先核对任务状态，不要并发启动")
    analysis = _OwnedLock(Path(root) / "local/agent/cnki-collection/analysis.lock",
                          "已有文献分析任务或遗留锁；请先核对任务状态，不要并发启动")
    collection.acquire()
    try:
        if not phased: analysis.acquire()
        yield collection.release, analysis.acquire
    finally:
        analysis.release()
        collection.release()


def prepare(keyword, limit=None, *, root=ROOT, model=DEFAULT_MODEL, local_only=False):
    keyword = dataset.safe_keyword(keyword)
    if not valid_limit(limit):
        raise ValueError("新增篇数应为正整数；不指定时按关键词采集全部结果")
    validate_model(model)
    root = Path(root).resolve()
    job_id = "cnki_collect_" + uuid4().hex
    work = root / "local/agent/cnki-collection" / job_id
    papers_dir = work / "papers"; papers_dir.mkdir(parents=True)
    target = cnki.data_directory(root)
    existing, base = [], {}
    original_meta = {}
    if (target / "manifest.json").is_file():
        listing = dataset.manifest(target)
        if any(c["keyword"] == keyword for c in listing["corpora"]):
            entry, contents = dataset.read_corpus(target, keyword)
            base = entry["files"]
            original_meta = json.loads(contents["papers.json"].decode("utf-8-sig"))
            existing = original_meta["papers"]
            if "triples.jsonl" in contents:
                (work / "triples.jsonl").write_bytes(contents["triples.jsonl"])
    for paper in existing:
        write_json(papers_dir / ("paper_%04d.json" % paper["rank"]), paper)
    if local_only and not existing: raise ValueError("没有可分析的本地题录")
    if local_only: write_json(work / "meta.json", original_meta)
    task = {"id": job_id, "keyword": keyword, "limit": limit, "created_at": now(), "base_files": base,
            "collection_mode": "local_analysis" if local_only else "all" if limit is None else "incremental", "engine": "codex_cli", "model": model,
            "base_extracted_ranks": original_meta.get("extracted_ranks", []),
            "base_ranks": [p["rank"] for p in existing],
            "base_papers": {str(p["rank"]): dataset.digest(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()) for p in existing},
            "target": str(target.resolve()), "status": "prepared"}
    write_json(work / "task.json", task)
    (work / "prompt.md").write_text(render_prompt(task, work), encoding="utf-8")
    write_json(work / "mcp.example.json", mcp_configuration(root, connect_session=False))
    return task, work


def render_prompt(task, work):
    papers = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(work) / "papers").glob("paper_*.json"))]
    offset = max((p["rank"] for p in papers), default=0)
    new_count = sum(p["rank"] not in task["base_ranks"] for p in papers)
    text = (PROMPTS / "cnki_collection.md").read_text(encoding="utf-8")
    text = text.replace("{{BROWSER_GUARDS}}", Path(__file__).with_name("browser_guards.js").read_text(encoding="utf-8"))
    scope = "关键词全部检索结果，逐页遍历至末页，不设篇数上限" if task["limit"] is None else f"本次再新增{max(0, task['limit'] - new_count)}篇"
    values = {"TASK_ID": "cnki_" + task["keyword"], "KEYWORD": task["keyword"], "KEYWORD_JSON": json.dumps(task["keyword"], ensure_ascii=False),
              "COLLECTION_SCOPE": scope, "COLLECTION_MODE": "all" if task["limit"] is None else "incremental",
              "OUTPUT_DIR": str(Path(work).resolve()).replace("\\", "/"),
              "RANK_OFFSET": str(offset), "RANK_OFFSET_PADDED": f"{offset+1:02d}",
              "EXISTING_TITLES": "\n".join("- " + p["title"] for p in papers) or "（无，全新采集）"}
    for name, value in values.items():
        text = text.replace("{{" + name + "}}", value)
    return text


def run_model(prompt, log_path, *, root=ROOT, workspace=None, model=DEFAULT_MODEL, browser=True, timeout=2400, command=None, cancel=None):
    validate_model(model)
    if cancel and cancel(): raise CollectionCancelled("文献采集已停止，进度已保留")
    executable = os.environ.get("CODEX_BIN") or shutil.which("codex")
    if command is None and not executable:
        raise RuntimeError("未找到Codex CLI；请安装Codex或配置CODEX_BIN。默认模型为" + DEFAULT_MODEL)
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    log_path = Path(log_path); log_path.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(workspace or root).resolve()
    response_path = log_path.resolve().with_suffix(".response.txt")
    response_path.unlink(missing_ok=True)  # Never accept a stale final message after a failed retry.
    args = list(command or [executable]) + ["exec", "--model", model, "--cd", str(workspace)]
    if browser:
        # Browser MCP calls can require approval. Collection runs without an
        # interactive terminal, so route those requests through Codex's
        # workspace-scoped automatic reviewer. This option supplies its own
        # workspace-write sandbox and is mutually exclusive with --sandbox.
        args.append("--approve-for-me")
    else:
        args.extend(["--sandbox", "read-only"])
    args.extend(["--skip-git-repo-check", "--ephemeral", "--json", "--output-last-message", str(response_path), "-c", 'model_reasoning_effort="low"'])
    if browser:
        server = mcp_configuration(root, output=workspace / "browser")["mcp_servers"]["playwright"]
        for key, value in server.items():
            args.extend(["-c", "mcp_servers.playwright." + key + "=" + json.dumps(value, ensure_ascii=False)])
    args.append("-")  # UTF-8 stdin avoids command-line length limits for titles/abstracts.
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(args, cwd=workspace, stdin=subprocess.PIPE, text=True, encoding="utf-8",
                                   stdout=log, stderr=subprocess.STDOUT, **options)
        try:
            deadline, sent = time.monotonic() + timeout, False
            while True:
                if cancel and cancel(): raise CollectionCancelled("文献采集已停止，进度已保留")
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise subprocess.TimeoutExpired(args, timeout)
                try:
                    process.communicate(prompt if not sent else None, timeout=min(1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    sent = True
            code = process.returncode
        except BaseException:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            raise
    blocked = browser_failure(log_path)
    if blocked: raise blocked
    if code:
        raise RuntimeError("采集/抽取进程未成功结束；日志已保存，未发布不完整数据")
    if not response_path.is_file():
        raise RuntimeError("Codex未生成最终响应；日志已保存，未发布数据")
    return response_path.read_text(encoding="utf-8")


def cnki_url(value):
    if not isinstance(value, str): return False
    try:
        url = urlparse(value)
        return url.scheme == "https" and bool(url.hostname) and (url.hostname == "cnki.net" or url.hostname.endswith(".cnki.net"))
    except ValueError:
        return False


def validate_scan(task, work, papers):
    """Check the saved browser page inventory; a model saying 'done' is insufficient."""
    error = "关键词全量遍历记录不完整或与题录不一致；保留任务供续跑，未发布"
    path = Path(work) / "collection_progress.json"
    if not path.is_file(): raise ValueError(error)
    scan = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(scan, dict) and scan.get("complete") is False:
        reason = str(scan.get("stop_reason") or "").lower()
        if "http_403" in reason or reason in {"http403", "access_denied"}:
            raise CollectionValidationError("知网返回HTTP 403，当前访问被拒绝。进度已保留；请在专用窗口正常处理后再续跑。", "cnki_access_denied")
        if reason == "rate_limited":
            raise CollectionValidationError("知网返回HTTP 429，当前访问受到限流。进度已保留，请稍后显式续跑。", "cnki_rate_limited")
        if reason in {"captcha", "verification_required"}:
            if scan.get("verification", {}).get("blocking_verification") is not True:
                raise CollectionValidationError("验证码提示缺少可见性核验，可能是页面隐藏模板；请检查实际页面后再续跑，未发布不完整数据。", "cnki_verification_unconfirmed")
            raise CollectionValidationError("知网要求人工验证；已保留进度，完成正常页面验证后才能续跑。", "cnki_verification_required")
        if reason == "login_required":
            raise CollectionValidationError("知网要求登录；已保留进度，请打开登录/验证窗口手动完成后续跑，任务会自动切回后台。", "cnki_login_required")
        if reason == "search_results_unavailable":
            raise CollectionValidationError("等待知网检索结果超时，未读到可核对的结果列表。进度已保留，可打开登录/验证窗口检查页面后续跑。", "cnki_search_unavailable")
        if reason == "result_total_changed":
            raise CollectionValidationError(f"检索总数发生变化：第{scan.get('failed_page', '?')}页应为{scan.get('expected_total_results', '?')}条，实际读到{scan.get('observed_total_results', '?')}条。已存题录保留，本轮未作为全量结果发布，需重新核对检索清单。", "cnki_result_total_changed")
        if reason == "result_total_unreadable":
            try: diagnostic = json.loads((Path(work) / "listing-diagnostic.json").read_text(encoding="utf-8"))
            except (OSError, ValueError): diagnostic = {}
            page = scan.get("failed_page") or diagnostic.get("expected_page") or "待核对"
            raise CollectionValidationError(f"第{page}页的总数或页码未能稳定读取，采集已暂停。已存题录保留，续跑会复用；本次不将未读到总数误称为总数变化。", "cnki_result_total_unreadable")
        if reason == "failed_positions":
            pages = scan.get("pages")
            if isinstance(pages, list):
                failed = [item for page in pages if isinstance(page, dict) and isinstance(page.get("items"), list)
                          for item in page["items"] if isinstance(item, dict) and item.get("status") == "failed"]
                if failed:
                    sample = "、".join(str(item.get("position", "?")) for item in failed[:5])
                    suffix = "等" if len(failed) > 5 else ""
                    raise CollectionValidationError(f"采集尚不完整：已记录{len(pages)}页、保存{len(papers)}条题录，另有{len(failed)}条详情未通过核对（检索位置{sample}{suffix}）。已存记录保留，续跑会复用并补查失败项；完成前不发布全量结果。", "cnki_incomplete_records")
    if not isinstance(scan, dict) or scan.get("keyword") != task["keyword"] or scan.get("complete") is not True or scan.get("stop_reason") != "exhausted":
        raise ValueError(error)
    total, pages = scan.get("total_results"), scan.get("pages")
    if type(total) is not int or total < 0 or not isinstance(pages, list) or not pages:
        raise ValueError(error)
    by_rank = {p["rank"]: p for p in papers}
    if len(by_rank) != len(papers): raise ValueError(error)
    position, referenced = 0, set()
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or type(page.get("number")) is not int or page["number"] != number or type(page.get("total_results")) is not int or page["total_results"] != total or page.get("has_next") is not (number < len(pages)):
            raise ValueError(error)
        items = page.get("items")
        if not isinstance(items, list) or (not items and (total != 0 or len(pages) != 1)):
            raise ValueError(error)
        for item in items:
            position += 1
            if not isinstance(item, dict) or type(item.get("rank")) is not int or type(item.get("position")) is not int or item["position"] != position or item.get("status") not in {"collected", "existing", "no_abstract", "title_only"}:
                raise ValueError(error)
            paper = by_rank.get(item["rank"])
            if not paper or not isinstance(item.get("title"), str) or cnki.normalise(item["title"]) != cnki.normalise(paper.get("title")) or not cnki_url(item.get("url")) or not cnki_url(paper.get("url")):
                raise ValueError(error)
            if item["status"] == "no_abstract" and (paper.get("abstract") or not isinstance(item.get("note"), str) or not item["note"].strip()):
                raise ValueError(error)
            if item["status"] == "title_only" and (paper.get("abstract") or paper.get("abstract_status") != "excerpt_only" or not isinstance(paper.get("abstract_excerpt"), str) or not paper["abstract_excerpt"].strip() or not isinstance(item.get("note"), str) or not item["note"].strip()):
                raise ValueError(error)
            if not paper.get("abstract") and item["status"] not in {"no_abstract", "title_only"}:
                raise ValueError(error)
            referenced.add(item["rank"])
    if position != total or any(p["rank"] not in referenced for p in papers if p["rank"] not in task["base_ranks"]):
        raise ValueError(error)
    return scan


def validate_extraction(raw, paper):
    if not isinstance(raw, dict) or not isinstance(raw.get("triples"), list):
        raise ValueError("关系抽取输出格式不正确，未发布")
    for triple in raw["triples"]:
        if not isinstance(triple, dict) or any(not isinstance(triple.get(k), str) or not triple[k].strip() for k in ("head", "head_type", "relation", "tail", "tail_type", "evidence")):
            raise ValueError("关系抽取缺少必要字段，未发布")
        for key in ("head_type", "tail_type"):
            if triple[key] == "疾病（西医病名）": triple[key] = "疾病"
        if triple["head_type"] not in {"方剂", "中药", "症状", "证候", "功效", "疾病"} or triple["tail_type"] not in {"方剂", "中药", "症状", "证候", "功效", "疾病"} or triple["relation"] not in {"组成", "主治", "治疗", "具有功效", "加减"}:
            raise ValueError("关系抽取类型不正确，未发布")
        for prefix, source in (("标题：", paper["title"]), ("摘要：", paper.get("abstract") or "")):
            if triple["evidence"].startswith(prefix):
                candidate = triple["evidence"][len(prefix):].strip()
                if cnki.normalise(candidate) and cnki.normalise(candidate) in cnki.normalise(source): triple["evidence"] = candidate
                break
        quote = cnki.normalise(triple["evidence"])
        if quote not in cnki.normalise(paper["title"]) and quote not in cnki.normalise(paper.get("abstract")):
            raise ValueError("抽取的引用原句不在标题摘要中，未发布")


def source_quotes(paper):
    quotes = {"T": paper["title"]}
    sentences = re.findall(r"[^。！？；\n]+[。！？；\n]?", paper.get("abstract") or "")
    quotes.update({"A" + str(i): sentence.strip() for i, sentence in enumerate(sentences, 1) if sentence.strip()})
    return quotes


def modification_flag(paper, keyword):
    """Mark explicit modification text; uncertain references never count as unmodified."""
    marker = r"加味|加减|化裁|合方"
    if re.search(marker, paper["title"]): return True
    abstract = cnki.normalise(paper.get("abstract"))
    subject = re.escape(cnki.normalise(keyword))
    if re.search(subject + r"[^。；;！？\n]{0,18}(?:" + marker + r")|(?:" + marker + r")[^。；;！？\n]{0,6}" + subject, abstract): return True
    if re.search(marker, abstract): return None
    return False


def extract_papers(papers, work, runner, *, batch_size=1, indexed_quotes=False):
    path = Path(work) / "triples.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    instructions = (PROMPTS / "cnki_extract.md").read_text(encoding="utf-8")
    if batch_size > 1:
        missing = [paper for paper in papers if not (Path(work) / "extracted" / ("paper_%04d.json" % paper["rank"])).is_file()]
        for offset in range(0, len(missing), batch_size):
            batch = missing[offset:offset + batch_size]
            inputs = [{"rank": p["rank"], "title": p["title"], "abstract": p.get("abstract")} for p in batch]
            quotes = {p["rank"]: source_quotes(p) for p in batch}
            if indexed_quotes:
                for item in inputs: item["quote_choices"] = quotes[item["rank"]]
            correction = ""
            for attempt in range(2):
                quote_instruction = ('\n本次使用原句编号：每条关系不要生成evidence文本，必须改为evidence_ref字段，值仅能选择该篇quote_choices中的T或A编号。选择能支撑关系的原句；编号按篇独立，不能跨篇。程序会原样保存对应句子（可以超过80字），不需要改写、截短或重复输出原句。输出示例：{"papers":[{"rank":1,"triples":[{"head":"方名","head_type":"方剂","relation":"主治","tail":"病名","tail_type":"疾病","evidence_ref":"T"}]}]}。' if indexed_quotes else '')
                prompt = instructions + correction + '\n本次为小批量处理：每篇只依据自己的标题摘要，禁止跨篇借用信息。必须返回全部输入rank，每篇无关系则triples为空。evidence必须逐字连续复制自该篇标题或摘要；不可省略括号内的英文、改写或拼接片段。可以选短的连续片段；无法原样引用则不要该关系。最终只输出此格式：{"papers":[{"rank":1,"triples":[...]}]}。' + quote_instruction + '\n输入：' + json.dumps(inputs, ensure_ascii=False)
                name = "extract_retry_batch" if attempt else "extract_batch"
                output = runner(prompt, Path(work) / "logs" / (name + "_%04d_%04d.log" % (batch[0]["rank"], batch[-1]["rank"])))
                try:
                    start, end = output.find("{"), output.rfind("}")
                    raw = json.loads(output[start:end+1])
                    results = raw.get("papers") if isinstance(raw, dict) else None
                    if not isinstance(results, list) or len(results) != len(batch): raise ValueError("批量抽取缺少论文记录，未发布")
                    by_rank = {}
                    for item in results:
                        if not isinstance(item, dict) or type(item.get("rank")) is not int or item["rank"] in by_rank: raise ValueError("批量抽取论文编号无效，未发布")
                        by_rank[item["rank"]] = item
                    if set(by_rank) != {p["rank"] for p in batch}: raise ValueError("批量抽取论文编号不一致，未发布")
                    if indexed_quotes:
                        for rank, item in by_rank.items():
                            if not isinstance(item.get("triples"), list): raise ValueError("关系抽取输出格式不正确，未发布")
                            for triple in item["triples"]:
                                if not isinstance(triple, dict) or not isinstance(triple.get("evidence_ref"), str) or triple["evidence_ref"] not in quotes[rank]:
                                    raise ValueError("关系引用原句编号无效，未发布")
                                triple["evidence"] = quotes[rank][triple.pop("evidence_ref")]
                    for paper in batch: validate_extraction(by_rank[paper["rank"]], paper)
                    break
                except ValueError as exc:
                    if attempt: raise
                    correction = "\n上次输出未通过程序核对（" + str(exc) + "）。请重新检查下列原始标题摘要，严格逐字复制支撑片段；不要为了凑关系改写原文。只纠正本批，最多重试这一次。\n"
            for paper in batch:
                write_json(Path(work) / "extracted" / ("paper_%04d.json" % paper["rank"]), {"triples": by_rank[paper["rank"]]["triples"]})
    for paper in papers:
        fragment = Path(work) / "extracted" / ("paper_%04d.json" % paper["rank"])
        if fragment.is_file():
            raw = json.loads(fragment.read_text(encoding="utf-8"))
        else:
            prompt = instructions + "\n标题：" + paper["title"] + "\n摘要：" + (paper.get("abstract") or "未提供，仅依据标题")
            output = runner(prompt, Path(work) / "logs" / ("extract_%04d.log" % paper["rank"]))
            start, end = output.find("{"), output.rfind("}")
            raw = json.loads(output[start:end+1])
        validate_extraction(raw, paper)
        for triple in raw["triples"]:
            row = {k: triple[k] for k in ("head", "head_type", "relation", "tail", "tail_type")}
            row.update(count=1, sources=[{"rank": paper["rank"], "title": paper["title"], "url": paper.get("url"),
                                        "is_modified": paper.get("is_modified"), "evidence": triple["evidence"]}])
            if row not in rows: rows.append(row)
        if not fragment.exists(): write_json(fragment, raw)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def execute(task, work, *, root=ROOT, extract=False, runner=None, phased=False, before_analysis=None):
    root, work = Path(root).resolve(), Path(work).resolve()
    if not work.is_relative_to(root / "local/agent/cnki-collection"):
        raise ValueError("任务目录不属于本项目")
    dataset.safe_keyword(task["keyword"])
    if task["id"] != work.name or Path(task["target"]).resolve() != cnki.data_directory(root).resolve() or not valid_limit(task["limit"]):
        raise ValueError("任务定义或目标文献目录已变化，请重新准备任务")
    model = validate_model(task.get("model", DEFAULT_MODEL))
    local_only = task.get("collection_mode") == "local_analysis"
    if local_only: extract = True
    all_results = task["limit"] is None and not local_only
    if task.get("status") == "succeeded": return task
    cancelled = lambda: (work / "cancel.requested.json").is_file()
    production_runner = runner is None
    runner = runner or (lambda prompt, log: run_model(prompt, log, root=root, workspace=work, model=model, browser=log.name == "collection.log", cancel=cancelled))
    with execution_lock(root, phased) as (release_collection, acquire_analysis):
        task.update(status="running", engine="codex_cli", model=model, started_at=now())
        for key in ("error", "failure_code", "finished_at"): task.pop(key, None)
        write_json(work / "task.json", task)
        try:
            if cancelled(): raise CollectionCancelled("文献采集已停止，进度已保留")
            stock = [json.loads(path.read_text(encoding="utf-8")) for path in (work / "papers").glob("paper_*.json")]
            if local_only:
                collect = False
            elif all_results:
                try:
                    validate_scan(task, work, stock)
                    collect = False
                except (ValueError, KeyError, TypeError):
                    collect = True
            else:
                collect = len({cnki.paper_identity(p) for p in stock if p["rank"] not in task["base_ranks"]}) < task["limit"]
            if collect:
                prompt = render_prompt(task, work)
                (work / "prompt.md").write_text(prompt, encoding="utf-8")
                if production_runner:
                    from . import browser_collector
                    task["browser_engine"] = "playwright"
                    write_json(work / "task.json", task)
                    try:
                        browser_collector.collect(task, work, root=root, cancel=cancelled)
                    except browser_collector.PageBlocked as exc:
                        if exc.reason in {"http_403", "captcha", "rate_limited", "login_required", "search_results_unavailable"}:
                            current = [json.loads(p.read_text(encoding="utf-8")) for p in (work / "papers").glob("paper_*.json")]
                            validate_scan(task, work, current)
                        raise CollectionValidationError("知网正常页面采集尚未完成（" + exc.reason + "），进度已保留，未发布。", "cnki_page_unavailable") from exc
                else:
                    runner(prompt, work / "logs/collection.log")
            if cancelled(): raise CollectionCancelled("文献采集已停止，进度已保留")
            files = sorted((work / "papers").glob("paper_*.json"))
            papers = [json.loads(path.read_text(encoding="utf-8")) for path in files]
            papers.sort(key=lambda p: p["rank"])
            for rank, expected in task.get("base_papers", {}).items():
                matching = [p for p in papers if p["rank"] == int(rank)]
                if len(matching) != 1 or dataset.digest(json.dumps(matching[0], sort_keys=True, ensure_ascii=False).encode()) != expected:
                    raise ValueError("任务中的既有论文被改动，未发布")
            unique = {}
            for paper in papers: unique.setdefault(cnki.paper_identity(paper), paper)
            papers = list(unique.values())
            new = [p for p in papers if p["rank"] not in task["base_ranks"]]
            for paper in new:
                paper["is_modified"] = modification_flag(paper, task["keyword"])
                paper["modification_basis"] = "title_abstract_text_markers_v1"
            if not all_results and not local_only and len(new) < task["limit"]:
                raise RuntimeError(f"本次新增{len(new)}篇，目标{task['limit']}篇；保留任务供续跑，未发布")
            scan = validate_scan(task, work, papers) if all_results else None
            meta_path = work / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("keyword") != task["keyword"] or any(not cnki_url(p.get("url")) for p in new):
                raise ValueError("采集关键词或新论文来源链接无效，未发布")
            if scan and meta.get("total_results") != scan["total_results"]:
                raise ValueError("检索总数与翻页记录不一致，未发布")
            if phased:
                release_collection()
                if before_analysis: before_analysis()
                acquire_analysis()
            analyzed = []
            if extract:
                triples_path = work / "triples.jsonl"
                prior_rows = [json.loads(line) for line in triples_path.read_text(encoding="utf-8").splitlines() if line.strip()] if triples_path.exists() else []
                done_ranks = set(task.get("base_extracted_ranks", [])) | {s.get("rank") for row in prior_rows for s in row.get("sources", [])}
                analyzed = [p for p in papers if p["rank"] not in done_ranks]
                extract_papers(analyzed, work, runner, batch_size=8 if production_runner else 1, indexed_quotes=production_runner)
                meta["extracted_ranks"] = sorted(done_ranks | {p["rank"] for p in analyzed})
            if cancelled(): raise CollectionCancelled("文献采集已停止，进度已保留")
            meta.update(keyword=task["keyword"], source="中国知网（CNKI）", papers=papers, last_updated_at=now(),
                        collector={"engine": "codex_cli", "model": model, "task_id": task["id"]},
                        extraction_status="completed_for_available_papers" if extract and local_only else "completed_for_new_papers" if extract else "not_requested_for_new_papers")
            if not local_only:
                meta["collection"] = {"mode": "all" if all_results else "incremental", "complete": all_results,
                                      "scope": "keyword_search_results_at_collection_time", "limit": task["limit"]}
            if scan is not None:
                meta["collection_scan"] = scan
            elif not local_only:
                meta.pop("collection_scan", None)
            target = Path(task["target"])
            current = next((e["files"] for e in dataset.manifest(target)["corpora"] if e["keyword"] == task["keyword"]), {}) if (target / "manifest.json").is_file() else {}
            if current != task["base_files"]:
                raise RuntimeError("目标文献已由其他任务更新，未覆盖，请重新准备任务")
            publication = work / "publish" / ("cnki_" + task["keyword"]); publication.mkdir(parents=True, exist_ok=True)
            write_json(publication / "papers.json", meta)
            if (work / "triples.jsonl").exists(): shutil.copyfile(work / "triples.jsonl", publication / "triples.jsonl")
            dataset.import_data(publication.parent, target, keywords=[task["keyword"]], update=True)
            task.update(status="succeeded", finished_at=now(), added=len(new), extracted=extract, analyzed=len(analyzed))
        except CollectionCancelled as exc:
            task.update(status="cancelled", error=str(exc), finished_at=now())
            raise
        except BaseException as exc:
            task.update(status="failed_or_partial", error=str(exc), finished_at=now())
            if getattr(exc, "failure_code", None): task["failure_code"] = exc.failure_code
            raise
        finally:
            write_json(work / "task.json", task)
    return task
