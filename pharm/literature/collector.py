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
from uuid import uuid4

from ..core.common import ROOT, now, write_json
from . import dataset, cnki

PROMPTS = Path(__file__).with_name("prompts")


def mcp_configuration(root=ROOT):
    root = Path(root).resolve()
    return {"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@0.0.64", "--headless", "--save-trace",
        "--output-dir", str(root / "local/cnki-browser"), "--config", str(root / "configs/cnki_browser.json")]}}}


@contextmanager
def collector_lock(root):
    path = Path(root) / "local/agent/cnki-collection/collector.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("已有文献采集任务或遗留锁；请先核对任务状态，不要并发启动")
    with os.fdopen(fd, "w") as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def prepare(keyword, limit=20, *, root=ROOT):
    keyword = dataset.safe_keyword(keyword)
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("篇数应为1到100")
    root = Path(root).resolve()
    job_id = "cnki_collect_" + uuid4().hex
    work = root / "local/agent/cnki-collection" / job_id
    papers_dir = work / "papers"; papers_dir.mkdir(parents=True)
    target = cnki.data_directory(root)
    existing, base = [], {}
    if (target / "manifest.json").is_file():
        listing = dataset.manifest(target)
        if any(c["keyword"] == keyword for c in listing["corpora"]):
            entry, contents = dataset.read_corpus(target, keyword)
            base = entry["files"]
            existing = json.loads(contents["papers.json"].decode("utf-8-sig"))["papers"]
            if "triples.jsonl" in contents:
                (work / "triples.jsonl").write_bytes(contents["triples.jsonl"])
    for paper in existing:
        write_json(papers_dir / ("paper_%04d.json" % paper["rank"]), paper)
    task = {"id": job_id, "keyword": keyword, "limit": limit, "created_at": now(), "base_files": base,
            "base_ranks": [p["rank"] for p in existing],
            "base_papers": {str(p["rank"]): dataset.digest(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()) for p in existing},
            "target": str(target.resolve()), "status": "prepared"}
    write_json(work / "task.json", task)
    (work / "prompt.md").write_text(render_prompt(task, work), encoding="utf-8")
    write_json(work / "mcp.example.json", mcp_configuration(root))
    return task, work


def render_prompt(task, work):
    papers = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(work) / "papers").glob("paper_*.json"))]
    offset = max((p["rank"] for p in papers), default=0)
    new_count = sum(p["rank"] not in task["base_ranks"] for p in papers)
    text = (PROMPTS / "cnki_collection.md").read_text(encoding="utf-8")
    values = {"TASK_ID": "cnki_" + task["keyword"], "KEYWORD": task["keyword"], "KEYWORD_JSON": json.dumps(task["keyword"], ensure_ascii=False),
              "LIMIT": str(max(0, task["limit"] - new_count)), "OUTPUT_DIR": str(Path(work).resolve()).replace("\\", "/"),
              "RANK_OFFSET": str(offset), "RANK_OFFSET_PADDED": f"{offset+1:02d}",
              "EXISTING_TITLES": "\n".join("- " + p["title"] for p in papers) or "（无，全新采集）"}
    for name, value in values.items():
        text = text.replace("{{" + name + "}}", value)
    return text


def run_model(prompt, log_path, *, root=ROOT, timeout=2400, command=None):
    executable = os.environ.get("KIMI_BIN") or shutil.which("kimi")
    if command is None and not executable:
        raise RuntimeError("未找到Kimi CLI；请配置KIMI_BIN。查看本项目CNKI交付说明配置浏览器工具")
    args = list(command or [executable]) + ["-p", prompt]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    log_path = Path(log_path); log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **options)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            raise
    if code:
        raise RuntimeError("采集/抽取进程未成功结束；日志已保存，未发布不完整数据")
    return log_path.read_text(encoding="utf-8", errors="replace")


def extract_papers(papers, work, runner):
    path = Path(work) / "triples.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    instructions = (PROMPTS / "cnki_extract.md").read_text(encoding="utf-8")
    for paper in papers:
        fragment = Path(work) / "extracted" / ("paper_%04d.json" % paper["rank"])
        if fragment.is_file():
            raw = json.loads(fragment.read_text(encoding="utf-8"))
        else:
            prompt = instructions + "\n标题：" + paper["title"] + "\n摘要：" + (paper.get("abstract") or "未提供，仅依据标题")
            output = runner(prompt, Path(work) / "logs" / ("extract_%04d.log" % paper["rank"]))
            start, end = output.find("{"), output.rfind("}")
            raw = json.loads(output[start:end+1])
            if not isinstance(raw, dict) or not isinstance(raw.get("triples"), list):
                raise ValueError("关系抽取输出格式不正确，未发布")
        for triple in raw["triples"]:
            if not isinstance(triple, dict) or any(not isinstance(triple.get(k), str) or not triple[k].strip() for k in ("head", "head_type", "relation", "tail", "tail_type", "evidence")):
                raise ValueError("关系抽取缺少必要字段，未发布")
            if triple["head_type"] not in {"方剂", "中药", "症状", "证候", "功效", "疾病"} or triple["tail_type"] not in {"方剂", "中药", "症状", "证候", "功效", "疾病"} or triple["relation"] not in {"组成", "主治", "治疗", "具有功效", "加减"}:
                raise ValueError("关系抽取类型不正确，未发布")
            quote = cnki.normalise(triple["evidence"])
            if quote not in cnki.normalise(paper["title"]) and quote not in cnki.normalise(paper.get("abstract")):
                raise ValueError("抽取的引用原句不在标题摘要中，未发布")
            row = {k: triple[k] for k in ("head", "head_type", "relation", "tail", "tail_type")}
            row.update(count=1, sources=[{"rank": paper["rank"], "title": paper["title"], "url": paper.get("url"),
                                        "is_modified": paper.get("is_modified"), "evidence": triple["evidence"]}])
            if row not in rows: rows.append(row)
        if not fragment.exists(): write_json(fragment, raw)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def execute(task, work, *, root=ROOT, extract=False, runner=None):
    root, work = Path(root).resolve(), Path(work).resolve()
    if not work.is_relative_to(root / "local/agent/cnki-collection"):
        raise ValueError("任务目录不属于本项目")
    dataset.safe_keyword(task["keyword"])
    if task["id"] != work.name or Path(task["target"]).resolve() != cnki.data_directory(root).resolve() or not isinstance(task["limit"], int) or not 1 <= task["limit"] <= 100:
        raise ValueError("任务定义或目标文献目录已变化，请重新准备任务")
    if task.get("status") == "succeeded": return task
    runner = runner or (lambda prompt, log: run_model(prompt, log, root=root))
    with collector_lock(root):
        task["status"] = "running"; write_json(work / "task.json", task)
        try:
            stock = [json.loads(path.read_text(encoding="utf-8")) for path in (work / "papers").glob("paper_*.json")]
            if len({cnki.paper_identity(p) for p in stock if p["rank"] not in task["base_ranks"]}) < task["limit"]:
                runner(render_prompt(task, work), work / "logs/collection.log")
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
            if len(new) < task["limit"]:
                raise RuntimeError(f"本次新增{len(new)}篇，目标{task['limit']}篇；保留任务供续跑，未发布")
            if extract: extract_papers(new, work, runner)
            meta_path = work / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("keyword") != task["keyword"] or any(not isinstance(p.get("url"), str) or not p["url"].startswith("https://") for p in new):
                raise ValueError("采集关键词或新论文来源链接无效，未发布")
            meta.update(keyword=task["keyword"], source="中国知网（CNKI）", papers=papers, last_updated_at=now(),
                        extraction_status="completed_for_new_papers" if extract else "not_requested_for_new_papers")
            target = Path(task["target"])
            current = next((e["files"] for e in dataset.manifest(target)["corpora"] if e["keyword"] == task["keyword"]), {}) if (target / "manifest.json").is_file() else {}
            if current != task["base_files"]:
                raise RuntimeError("目标文献已由其他任务更新，未覆盖，请重新准备任务")
            publication = work / "publish" / ("cnki_" + task["keyword"]); publication.mkdir(parents=True, exist_ok=True)
            write_json(publication / "papers.json", meta)
            if (work / "triples.jsonl").exists(): shutil.copyfile(work / "triples.jsonl", publication / "triples.jsonl")
            dataset.import_data(publication.parent, target, keywords=[task["keyword"]], update=True)
            task.update(status="succeeded", finished_at=now(), added=len(new), extracted=extract)
        except BaseException as exc:
            task.update(status="failed_or_partial", error=str(exc), finished_at=now())
            raise
        finally:
            write_json(work / "task.json", task)
    return task
