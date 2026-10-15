from __future__ import annotations

import argparse
import asyncio
import json
import copy
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.datastructures import MutableHeaders
from pharm.core.common import ROOT, read_json, safe_name, task_list, public_artifact, owned_run
from pharm.pipeline.engine import manifests, start
from pharm.pipeline.tasks import save_task
from pharm.discovery import query as discovery
from pharm.agent import jobs as forward_jobs
from pharm.agent import progress as query_progress
from pharm.agent import disease_input
from pharm.agent.forward import _top_k
from pharm.batman.formulas import SOURCE, formula_candidates, list_formulas
from pharm.batman import formula_catalog
from starlette.concurrency import run_in_threadpool
from uuid import uuid4
import sqlite3

app = FastAPI(title="Pharmacology Multi-Agent", docs_url=None, redoc_url=None)


class LocalOnlyMiddleware:
    """Preserve ASGI disconnect messages while enforcing local access."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Cache-Control"] = "no-store"
            await send(message)

        if request.url.hostname not in ("localhost", "127.0.0.1"):
            return await JSONResponse({"detail": "仅允许本机访问"}, status_code=403)(scope, receive, send_with_headers)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return await JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)(scope, receive, send_with_headers)
        await self.app(scope, receive, send_with_headers)


app.add_middleware(LocalOnlyMiddleware)


@app.get("/")
def index():
    return FileResponse(ROOT / "server/static/index.html")


@app.get("/workbench")
def workbench():
    return FileResponse(ROOT / "server/static/workbench.html")


@app.get("/assets/chat.js")
def chat_script():
    return FileResponse(ROOT / "server/static/chat.js", media_type="text/javascript")


@app.get("/assets/chat.css")
def chat_style():
    return FileResponse(ROOT / "server/static/chat.css", media_type="text/css")


@app.get("/assets/disease-labels.js")
def disease_labels_script():
    return FileResponse(ROOT / "server/static/disease-labels.js", media_type="text/javascript")


@app.get("/assets/disease-labels.json")
def disease_labels_glossary():
    return FileResponse(ROOT / "server/static/disease-labels.json", media_type="application/json")


@app.get("/assets/query-presence.js")
def query_presence_script():
    return FileResponse(ROOT / "server/static/query-presence.js", media_type="text/javascript")


@app.get("/assets/query-progress.js")
def query_progress_script():
    return FileResponse(ROOT / "server/static/query-progress.js", media_type="text/javascript")


@app.get("/assets/formula-picker.js")
def formula_picker_script():
    return FileResponse(ROOT / "server/static/formula-picker.js", media_type="text/javascript")


@app.get("/assets/formula-picker.css")
def formula_picker_style():
    return FileResponse(ROOT / "server/static/formula-picker.css", media_type="text/css")


@app.get("/assets/icons/{filename}")
def chat_icon(filename: str):
    allowed = {"leaf.svg", "plus.svg", "search.svg", "arrow-up.svg", "settings.svg",
               "layout-sidebar.svg", "download.svg", "x.svg", "trash.svg", "favicon.svg"}
    if filename not in allowed:
        raise HTTPException(404)
    return FileResponse(ROOT / "server/static/icons" / filename, media_type="image/svg+xml")


@app.get("/assets/app.js")
def app_script():
    return FileResponse(ROOT / "server/static/app.js", media_type="text/javascript")


@app.get("/assets/style.css")
def app_style():
    return FileResponse(ROOT / "server/static/style.css", media_type="text/css")


@app.get("/api/formulas")
def formulas():
    return {"formulas": [{"name": name,
                          "herbs": [{"canonical": canonical, "candidates": candidates}
                                    for canonical, candidates in formula_candidates(name)],
                          "source": SOURCE} for name in list_formulas()]}


async def formula_body(request):
    if len(await request.body()) > 100000:
        raise HTTPException(413, "查询内容过长")
    body = await request.json()
    if not isinstance(body, dict) or set(body) - (formula_catalog.INPUT_KEYS | {"use_saved"}):
        raise ValueError("方剂组成只接受 text、formula、herbs、formula_variant_id、formula_herbs、use_saved")
    if "use_saved" in body and not isinstance(body["use_saved"], bool):
        raise ValueError("use_saved 必须是布尔值")
    return body


@app.post("/api/formulas/resolve")
async def resolve_formula(request: Request):
    try:
        body = await formula_body(request)
        return await run_in_threadpool(formula_catalog.resolve, **body, root=ROOT)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(503, "本地方剂数据不可用：" + str(exc))


@app.post("/api/formulas/preference")
async def formula_preference(request: Request):
    try:
        body = await formula_body(request)
        body.pop("use_saved", None)
        if not body.get("formula_variant_id"):
            raise ValueError("保存默认组成前必须明确选择组成候选")
        return await run_in_threadpool(formula_catalog.save_preference, body, root=ROOT)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(503, "无法保存本地方剂组成：" + str(exc))


@app.get("/api/discovery/catalog")
def discovery_catalog():
    try:
        return discovery.catalog(discovery.database_path(ROOT))
    except (ValueError, OSError, sqlite3.Error):
        raise HTTPException(503, "本地疾病索引不可用，请先按照反向查询说明准备索引")


def execute_discovery(body):
    if not isinstance(body, dict) or set(body) - {"herbs", "genes"}:
        raise ValueError("仅接受 herbs 或 genes 输入，不需要指定疾病")
    result = discovery.query(discovery.database_path(ROOT), **body)
    run_id = "lookup_" + uuid4().hex
    discovery.save_result(result, ROOT / "local/discovery/runs" / run_id)
    return {"run_id": run_id, "result": result}


@app.post("/api/discovery/query")
async def discovery_query(request: Request):
    if len(await request.body()) > 100000:
        raise HTTPException(413, "查询内容过长")
    try:
        body = await request.json()
        return await run_in_threadpool(execute_discovery, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "本地查询或归档不可用，请检查索引与目录权限")


@app.post("/api/agent/forward")
async def agent_forward(request: Request):
    """对话框正向查询：方剂/药材 → BATMAN 靶点 → 候选疾病。"""
    if len(await request.body()) > 100000:
        raise HTTPException(413, "查询内容过长")
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求必须是 JSON 对象")
        allowed = formula_catalog.INPUT_KEYS | {"top_k", "batman_threshold", "request_id", "supersedes", "owner_id"}
        if set(body) - allowed:
            raise ValueError("正向查询包含不支持的字段")
        request_id = body.pop("request_id", uuid4().hex)
        supersedes = body.pop("supersedes", None)
        owner_id = body.pop("owner_id", None)
        # Resolve before spawning so composition choices do not start a query.
        resolved = await run_in_threadpool(formula_catalog.resolve,
                                          **{key: value for key, value in body.items() if key in formula_catalog.INPUT_KEYS},
                                          root=ROOT)
        if resolved["status"] != "ready":
            return JSONResponse({"detail": resolved}, status_code=409)
        body.update({key: value for key, value in resolved["query_input"].items()
                     if key in {"formula_variant_id", "formula_herbs", "formula_fingerprint"}})
        return await forward_jobs.manager.run(request, body, ROOT, request_id, supersedes, owner_id)
    except forward_jobs.QueryCancelled as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(503, "本地正向查询不可用：" + str(exc))


@app.post("/api/agent/reverse/resolve")
async def resolve_reverse(request: Request):
    if len(await request.body()) > 10000:
        raise HTTPException(413, "查询内容过长")
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {"text", "disease", "disease_id"}:
            raise ValueError("疾病解析只接受 text、disease、disease_id")
        return await run_in_threadpool(disease_input.resolve, **body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/agent/reverse")
async def agent_reverse(request: Request):
    if len(await request.body()) > 10000:
        raise HTTPException(413, "查询内容过长")
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {"text", "disease", "disease_id", "top_k", "request_id", "supersedes", "owner_id"}:
            raise ValueError("反向查询只接受疾病输入、top_k 与请求跟踪字段；当前采用 known-only")
        _top_k(body.get("top_k"))
        request_id = body.pop("request_id", uuid4().hex)
        supersedes, owner_id = body.pop("supersedes", None), body.pop("owner_id", None)
        resolved = await run_in_threadpool(disease_input.resolve,
                                          **{key: value for key, value in body.items() if key in {"text", "disease", "disease_id"}})
        if resolved["status"] != "ready":
            return JSONResponse({"detail": resolved}, status_code=409 if resolved["status"] == "needs_selection" else 400)
        body["disease_id"] = resolved["disease"]["disease_id"]
        return await forward_jobs.manager.run(request, body, ROOT, request_id, supersedes, owner_id, direction="reverse")
    except forward_jobs.QueryCancelled as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(503, "本地反向查询不可用：" + str(exc))


@app.get("/api/agent/reverse/{run_id}")
def reverse_result(run_id: str):
    try:
        safe_name(run_id)
    except ValueError:
        raise HTTPException(404, "查询记录不存在")
    if not run_id.startswith("reverse_"):
        raise HTTPException(404, "查询记录不存在")
    root = (ROOT / "local/agent/reverse").resolve()
    path = (root / run_id / "result.json").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "查询结果不存在，请重新读取查询状态")
    return FileResponse(path, media_type="application/json")


@app.get("/assets/reverse-chat.js")
def reverse_chat_script():
    return FileResponse(ROOT / "server/static/reverse-chat.js", media_type="text/javascript")


@app.post("/api/agent/reverse/cancel")
@app.post("/api/agent/forward/cancel")
async def cancel_forward(request: Request):
    if len(await request.body()) > 1000:
        raise HTTPException(413, "查询内容过长")
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"request_id"}:
            raise ValueError("停止查询只接受 request_id")
        return await forward_jobs.manager.stop(body["request_id"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/agent/forward/{run_id}")
def forward_result(run_id: str):
    try:
        safe_name(run_id)
    except ValueError:
        raise HTTPException(404, "查询记录不存在")
    if not run_id.startswith("forward_"):
        raise HTTPException(404, "查询记录不存在")
    root = (ROOT / "local/agent/forward").resolve()
    path = (root / run_id / "result.json").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "查询结果不存在，请重新查询")
    return FileResponse(path, media_type="application/json")


@app.get("/api/agent/reverse/presence/{owner_id}")
@app.get("/api/agent/forward/presence/{owner_id}")
async def forward_presence(owner_id: str):
    try:
        forward_jobs.request_identifier(owner_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    async def events():
        forward_jobs.manager.connect_tab(owner_id)
        try:
            while True:
                yield ": connected\n\n"
                await asyncio.sleep(3)
        finally:
            forward_jobs.manager.disconnect_tab(owner_id)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.get("/api/agent/reverse/request/{request_id}")
@app.get("/api/agent/forward/request/{request_id}")
def forward_request_status(request_id: str):
    try:
        forward_jobs.request_identifier(request_id)
    except ValueError:
        raise HTTPException(404, "查询记录不存在")
    path = ROOT / "local/agent/jobs" / (request_id + ".json")
    if not path.is_file():
        raise HTTPException(404, "尚未找到本次查询记录，可以重新提交")
    status = read_json(path)
    result = {key: status.get(key) for key in (
        "request_id", "run_id", "status", "owner_id", "created_at", "finished_at", "reason", "error", "direction",
    )}
    if status.get("status") == "running" and status.get("service_instance_id") not in (None, forward_jobs.manager.instance_id):
        result.update(status="interrupted", error="原查询服务已重启或当前端口不是原服务，请核对后重新查询。")
    result["progress"] = query_progress.snapshot(ROOT, status.get("run_id"))
    return result


@app.get("/discovery/artifacts/{run_id}/{filename}")
def discovery_artifact(run_id: str, filename: str):
    try:
        safe_name(run_id)
    except ValueError:
        raise HTTPException(404)
    if filename not in ("result.json", "candidates.csv", "evidence.csv", "report.md", "manifest.json",
                        "candidates_complete.csv", "candidates_base.csv", "candidates_strict.csv",
                        "candidates_top50.csv", "candidate_hierarchy.json",
                        "summary.json", "candidates_topk.csv", "topk_target_evidence.csv",
                        "topk_hierarchy.json", "result_full.json",
                        "monarch_gene_disease.csv", "monarch_hpo.csv", "monarch_inheritance.csv",
                        "monarch_publications.csv", "monarch_unresolved.csv", "monarch_provenance.json"):
        raise HTTPException(404)
    root = (ROOT / "local/discovery/runs").resolve()
    path = (root / run_id / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


@app.get("/api/tasks")
def tasks():
    return {"tasks": task_list()}


@app.post("/api/tasks")
async def task_create(request: Request):
    if len(await request.body()) > 20000:
        raise HTTPException(413, "任务内容过长")
    try:
        task, created = save_task(await request.json(), ROOT)
        return {"task": task, "created": created}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/runs")
def runs():
    return {"runs": [public_manifest(m) for m in manifests()]}


def public_manifest(value):
    value = copy.deepcopy(value)
    for stage in value.get("stages", {}).values():
        stage["artifacts"] = [p for p in stage.get("artifacts", []) if public_artifact(p)]
    return value


def manifest_for(run_id):
    try:
        safe_name(run_id)
        value = read_json(ROOT / "runs" / run_id / "manifest.json")
        if not owned_run(value, ROOT):
            raise HTTPException(404, "运行不存在")
        return value
    except (ValueError, OSError):
        raise HTTPException(404, "运行不存在")


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    return public_manifest(manifest_for(run_id))


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str):
    manifest_for(run_id)
    path = ROOT / "runs" / run_id / "events.jsonl"
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    return {"events": events[-400:]}


@app.post("/api/run")
async def run_new(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求必须是 JSON 对象")
        return {"run_id": start(body.get("task_id"), body.get("mode"))}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/runs/{run_id}/resume")
def run_resume(run_id: str):
    manifest_for(run_id)
    try:
        return {"run_id": start(resume=run_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/analysis")
async def analysis_new(request: Request):
    if len(await request.body()) > 20000:
        raise HTTPException(413, "请求内容过长")
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求必须是 JSON 对象")
        return {"run_id": start(pipeline="analysis", analysis=body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.get("/artifacts/{run_id}/{filename:path}")
def artifact(run_id: str, filename: str):
    manifest = manifest_for(run_id)
    allowed = {"report.md"}
    for stage in manifest["stages"].values():
        allowed.update(stage.get("artifacts", []))
    if filename not in allowed or not public_artifact(filename):
        raise HTTPException(404, "未登记的产物")
    root = (ROOT / "runs" / run_id).resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(404)
    if not path.is_file() or path.suffix.lower() not in (".png", ".json", ".csv", ".tsv", ".txt", ".md"):
        raise HTTPException(404)
    response = FileResponse(path, media_type="image/png" if path.suffix == ".png" else "text/plain; charset=utf-8")
    if filename == "report.md":
        response.headers["Content-Disposition"] = 'attachment; filename="report.md"'
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, timeout_graceful_shutdown=5)
