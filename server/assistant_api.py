"""Local-only model configuration and persistent research dialogue endpoints."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from pharm.core.common import ROOT
from pharm.chat import config, provider, engine

router = APIRouter()


async def body(request, limit=20000):
    if len(await request.body()) > limit: raise HTTPException(413, "请求过长")
    try:
        value = await request.json()
        if not isinstance(value, dict): raise ValueError()
        return value
    except ValueError:
        raise HTTPException(400, "请求必须是 JSON 对象")


@router.get("/api/assistant/config")
def model_settings():
    try: return config.read(ROOT)
    except (OSError, ValueError): raise HTTPException(503, "模型配置无法读取")


@router.post("/api/assistant/config")
async def save_settings(request: Request):
    try: return config.save(ROOT, await body(request, 10000))
    except ValueError as exc: raise HTTPException(400, str(exc))
    except OSError: raise HTTPException(503, "无法保存模型设置")


@router.post("/api/assistant/config/test")
async def test_settings(request: Request):
    if await body(request): raise HTTPException(400, "连接检查不接受额外参数")
    try:
        value = config.read(ROOT, secret=True)
        await provider.complete(value, [{"role": "user", "content": "Reply only OK."}], short=True)
        return {"ok": True, "message": "模型接口连接成功；工具调用能力将在实际问答时核验", "model": value["model"]}
    except (provider.ModelError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.post("/api/assistant/config/select")
async def select_settings(request: Request):
    value = await body(request)
    if set(value) != {"profile_id"}: raise HTTPException(400, "请选择一个已保存配置")
    try: return config.select(ROOT, value["profile_id"])
    except ValueError as exc: raise HTTPException(400, str(exc))
    except OSError: raise HTTPException(503, "配置切换未完成")


@router.post("/api/assistant/config/import-codex")
async def import_codex_settings(request: Request):
    if await body(request): raise HTTPException(400, "导入不接受路径或额外参数")
    try: return config.import_codex(ROOT)
    except ValueError as exc: raise HTTPException(400, str(exc))
    except OSError: raise HTTPException(503, "本机 Codex 的 icrc 配置不可读取")


@router.post("/api/assistant/runs", status_code=202)
async def start_dialogue(request: Request):
    try: return engine.manager.start(ROOT, await body(request, 50000))
    except ValueError as exc: raise HTTPException(400, str(exc))
    except OSError: raise HTTPException(503, "无法创建问答记录")


@router.get("/api/assistant/runs/{job_id}")
def dialogue_status(job_id: str):
    try: return engine.manager.status(ROOT, job_id)
    except FileNotFoundError: raise HTTPException(404, "问答记录不存在，请重新发送问题")
    except ValueError: raise HTTPException(400, "问答编号或记录无效")


@router.post("/api/assistant/runs/{job_id}/cancel")
async def cancel_dialogue(job_id: str, request: Request):
    if await body(request): raise HTTPException(400, "停止操作不接受额外参数")
    try: return await engine.manager.cancel(ROOT, job_id)
    except FileNotFoundError: raise HTTPException(404, "问答记录不存在")
    except ValueError: raise HTTPException(400, "问答编号无效")


@router.get("/assets/assistant-ui.js")
def assistant_script():
    return FileResponse(ROOT / "server/static/assistant-ui.js", media_type="text/javascript")


@router.get("/assets/assistant.css")
def assistant_style():
    return FileResponse(ROOT / "server/static/assistant.css", media_type="text/css")
