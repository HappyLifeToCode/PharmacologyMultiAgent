from __future__ import annotations

import argparse
import json
import copy
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pharm.core.common import ROOT, read_json, safe_name, task_list, public_artifact
from pharm.pipeline.engine import manifests, start
from pharm.pipeline.tasks import save_task
from pharm.discovery import query as discovery
from starlette.concurrency import run_in_threadpool
from uuid import uuid4
import sqlite3

app = FastAPI(title="Pharmacology Multi-Agent", docs_url=None, redoc_url=None)


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
    return FileResponse(ROOT / "server/static/discovery.html")


@app.get("/legacy")
def legacy_index():
    return FileResponse(ROOT / "server/static/index.html")


@app.get("/assets/discovery.js")
def discovery_script():
    return FileResponse(ROOT / "server/static/discovery.js", media_type="text/javascript")


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


@app.get("/assets/workbench.js")
def workbench_script():
    return FileResponse(ROOT / "server/static/workbench.js", media_type="text/javascript")


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
        return read_json(ROOT / "runs" / run_id / "manifest.json")
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
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
