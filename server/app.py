from __future__ import annotations

import argparse
import json
import copy
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pharm.core.common import ROOT, read_json, safe_name, task_list, public_artifact, owned_run
from pharm.pipeline.engine import manifests, start
from pharm.pipeline.tasks import save_task
from pharm.discovery import query as discovery
from pharm.assist.bridge import AssistManager, AssistUnavailable
from pharm.batman.formulas import SOURCE, formula_candidates, list_formulas
from starlette.concurrency import run_in_threadpool
from uuid import uuid4
import asyncio
import sqlite3

app = FastAPI(title="Pharmacology Multi-Agent", docs_url=None, redoc_url=None)

assist_manager = AssistManager()


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.url.hostname not in ("localhost", "127.0.0.1"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
    if request.method == "POST":
        origin = request.headers.get("origin")
        expected = request.headers.get("host")
        if origin and urlparse(origin).netloc != expected:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return FileResponse(ROOT / "server/static/index.html")


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


@app.get("/discovery/artifacts/{run_id}/{filename}")
def discovery_artifact(run_id: str, filename: str):
    try:
        safe_name(run_id)
    except ValueError:
        raise HTTPException(404)
    if filename not in ("result.json", "candidates.csv", "evidence.csv", "report.md", "manifest.json"):
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


@app.post("/api/assist/start")
async def assist_start(request: Request):
    if len(await request.body()) > 10000:
        raise HTTPException(413, "请求内容过长")
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求必须是 JSON 对象")
        url, guidance, request_id = body.get("url"), body.get("guidance"), body.get("request_id")
        if url is not None and (not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://") or url.startswith("data:"))):
            raise ValueError("url 必须是 http(s) 或 data: URL")
        if guidance is not None and (not isinstance(guidance, str) or len(guidance) > 2000):
            raise ValueError("引导文本最多 2000 字")
        session = await run_in_threadpool(assist_manager.start, url, guidance, request_id)
        return {"state": session.state, "url": session.url}
    except AssistUnavailable as exc:
        raise HTTPException(503, "内嵌浏览器不可用：" + str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/assist/request")
async def assist_request(request: Request):
    """采集 Agent 登记遇到人机验证的当前页面，等待用户接管。"""
    if len(await request.body()) > 20000:
        raise HTTPException(413, "协助请求过长")
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {"url", "guidance", "context"}:
            raise ValueError("协助请求仅接受 url、guidance、context")
        url, guidance = body.get("url"), body.get("guidance")
        if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://") or url.startswith("data:")):
            raise ValueError("url 必须是 http(s) 或 data: URL")
        if guidance is not None and (not isinstance(guidance, str) or len(guidance) > 2000):
            raise ValueError("引导文本最多 2000 字")
        context = body.get("context")
        if context is not None and not isinstance(context, dict):
            raise ValueError("context 必须是对象")
        pending = await run_in_threadpool(assist_manager.request, url, guidance, context)
        return {"state": "pending", **pending}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/assist/stop")
def assist_stop():
    session = assist_manager.stop()
    return {"state": "closed" if session is not None else "idle"}


@app.get("/api/assist/status")
def assist_status():
    return assist_manager.status()


@app.websocket("/ws/assist")
async def assist_ws(websocket: WebSocket):
    host = websocket.headers.get("host") or websocket.url.netloc
    origin = websocket.headers.get("origin")
    # "testserver" 是 starlette TestClient 的 WS scope 默认主机名
    hostname = websocket.url.hostname or host.split(":")[0]
    if hostname not in ("localhost", "127.0.0.1", "testserver") or (origin and urlparse(origin).netloc != host):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    session = assist_manager.current()
    if session is None or session.state == "closed":
        await websocket.send_text(json.dumps({"type": "state", "state": "idle"}))
        await websocket.close()
        return
    subscriber = session.subscribe()

    async def pump():
        try:
            while True:
                item = await run_in_threadpool(subscriber.get)
                if item is None:
                    return
                if item[0] == "frame":
                    await websocket.send_text(json.dumps({"type": "frame", **item[1]}))
                    await websocket.send_bytes(item[2])
                else:
                    await websocket.send_text(json.dumps(item[1], ensure_ascii=False))
        except Exception:
            return

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                event = await websocket.receive_json()
            except json.JSONDecodeError:
                continue
            try:
                session.handle_input(event)
            except ValueError as exc:
                await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    finally:
        session.unsubscribe(subscriber)
        task.cancel()


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
    uvicorn.run(app, host="127.0.0.1", port=args.port)
