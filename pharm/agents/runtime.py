from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from ..core.common import ROOT, read_json, write_json


def load_toml(path):
    try:
        import tomllib
    except ImportError:
        from pip._vendor import tomli as tomllib
    return tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))


def prepare_home():
    """Build a private minimal Codex home; never expose credentials in run outputs."""
    import toml
    runtime = read_json(ROOT / "configs/runtime.json")
    source = Path(os.environ.get("PHARM_CODEX_SOURCE_HOME", str(Path.home() / ".codex")))
    profile_path = source / (runtime["codex_profile"] + ".config.toml")
    if not profile_path.exists():
        raise RuntimeError("本机缺少指定 Codex profile；请配置后重试")
    base = load_toml(source / "config.toml") if (source / "config.toml").exists() else {}
    profile = load_toml(profile_path)
    provider = profile.get("model_provider", base.get("model_provider"))
    providers = dict(base.get("model_providers", {}))
    providers.update(profile.get("model_providers", {}))
    minimal = {"model": runtime["model"], "model_reasoning_effort": runtime["model_reasoning_effort"], "approval_policy": "on-request", "approvals_reviewer": "auto_review", "sandbox_mode": "workspace-write", "sandbox_workspace_write": {"network_access": True}}
    if provider:
        minimal["model_provider"] = provider
        if provider in providers:
            minimal["model_providers"] = {provider: providers[provider]}
    home = ROOT / "local/codex-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(toml.dumps(minimal), encoding="utf-8")
    (home / (runtime["codex_profile"] + ".config.toml")).write_text(toml.dumps(minimal), encoding="utf-8")
    auth = source / "auth.json"
    if auth.exists():
        shutil.copy2(str(auth), str(home / "auth.json"))
    return home


def browser_overrides(output_dir):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        chromium = p.chromium.executable_path
    if not Path(chromium).exists():
        raise RuntimeError("缺少 Playwright Chromium，先运行环境检查")
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx:
        raise RuntimeError("缺少 npx")
    args = ["/c", npx, "-y", "@playwright/mcp@0.0.64", "--headless", "--isolated", "--save-trace", "--image-responses", "omit", "--executable-path", chromium, "--output-dir", str(output_dir), "--config", str(ROOT / "configs/playwright.json")]
    values = {
        "mcp_servers.playwright.command": "cmd.exe",
        "mcp_servers.playwright.args": args,
        "mcp_servers.playwright.startup_timeout_sec": 120,
        "mcp_servers.playwright.required": True,
        "mcp_servers.playwright.tool_timeout_sec": 90,
        "mcp_servers.playwright.enabled_tools": ["browser_run_code", "browser_take_screenshot", "browser_wait_for"],
    }
    local_settings = ROOT / "configs/browser.local.json"
    if local_settings.exists():
        settings = read_json(local_settings)
        storage = settings.get("storage_state")
        if storage:
            storage = (ROOT / storage).resolve()
            if not storage.exists():
                raise RuntimeError("配置的浏览器登录态文件不存在")
            args.extend(["--storage-state", str(storage)])
        if settings.get("compatibility_init_script"):
            args.extend(["--init-script", str(ROOT / "scripts/browser_compat.js")])
    out = []
    for key, value in values.items():
        out.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=False)])
    return out


RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["succeeded", "partial", "blocked", "failed"]},
        "summary": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "graph": {"type": ["object", "null"], "additionalProperties": False, "properties": {
            "enabled": {"type": ["array", "null"], "items": {"type": "string"}}},
            "required": ["enabled"]},
        "rework": {"type": ["array", "null"], "items": {"type": "string"}},
        "confidence": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
    }, "required": ["status", "summary", "blockers", "findings", "artifacts", "graph", "rework", "confidence"],
}


def execute(prompt, directory, browser=False, on_event=None, timeout=360, home=None, resume_session=None, record_session=False):
    runtime = read_json(ROOT / "configs/runtime.json")
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if not executable:
        raise RuntimeError("找不到 Codex CLI")
    home = home or prepare_home()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "response.schema.json", RESULT_SCHEMA)
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    command = [executable, "exec"]
    ephemeral = [] if record_session else ["--ephemeral"]
    if resume_session:
        # resume 子命令没有 -p/--sandbox 选项；沙箱用 -c 传递，profile 由会话自身携带
        command += ["resume", resume_session, "-m", runtime["model"],
                    "-c", 'model_reasoning_effort="' + runtime["model_reasoning_effort"] + '"',
                    "-c", 'sandbox_mode="workspace-write"',
                    "--json", "--skip-git-repo-check"] + ephemeral + [
                    "--output-schema", str(directory / "response.schema.json"),
                    "--output-last-message", str(directory / "response.json")]
    else:
        command += ["-p", runtime["codex_profile"], "-m", runtime["model"], "-c", 'model_reasoning_effort="' + runtime["model_reasoning_effort"] + '"', "--json"] + ephemeral + ["--sandbox", "workspace-write", "--skip-git-repo-check", "--output-schema", str(directory / "response.schema.json"), "--output-last-message", str(directory / "response.json"), "-C", str(ROOT)]
    if browser:
        (directory / "browser").mkdir(exist_ok=True)
        command.extend(browser_overrides(directory / "browser"))
    command.append("-")
    environment = dict(os.environ, CODEX_HOME=str(home), PYTHONUTF8="1")
    start = time.monotonic()
    # Logs remain local. The UI consumes only curated events and verified artifacts.
    log_path = directory / "codex.events.jsonl"
    with log_path.open("w", encoding="utf-8") as logfile, (directory / "stderr.log").open("w", encoding="utf-8") as errfile:
        process = subprocess.Popen(command, cwd=str(ROOT), env=environment, stdin=subprocess.PIPE, stdout=logfile, stderr=errfile, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process.stdin.write(prompt)
        process.stdin.close()
        timed_out = False
        last_offset = 0
        session = None
        pending = ""
        tool_calls = []
        def drain():
            nonlocal last_offset, pending, session
            with log_path.open(encoding="utf-8", errors="replace") as stream:
                stream.seek(last_offset)
                content = stream.read()
                last_offset = stream.tell()
            lines = (pending + content).split("\n")
            pending = lines.pop()
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "thread.started":
                    session = event.get("thread_id")
                if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "mcp_tool_call":
                    item = event["item"]
                    tool_calls.append({"server": item.get("server"), "tool": item.get("tool"), "status": item.get("status")})
                if on_event:
                    on_event(event)
        while process.poll() is None:
            drain()
            if time.monotonic() - start > timeout:
                timed_out = True
                # Stop only this run's own child process tree on Windows.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    process.terminate()
                break
            time.sleep(.5)
        process.wait(timeout=20)
        drain()
    meta = {"exit_code": process.returncode, "elapsed_seconds": round(time.monotonic() - start, 2), "session_id": session, "model": runtime["model"], "reasoning_effort": runtime["model_reasoning_effort"], "browser": browser, "mcp_tool_calls": tool_calls, "timed_out": timed_out}
    write_json(directory / "execution.json", meta)
    if timed_out:
        raise RuntimeError("Agent 任务超时，已保留中间产物，可恢复重试")
    if process.returncode != 0 or not (directory / "response.json").exists():
        raise RuntimeError("Codex 会话未正常完成，诊断见该任务本地 stderr.log")
    result = read_json(directory / "response.json")
    for key in RESULT_SCHEMA["required"]:
        if key not in result:
            raise ValueError("Agent 交接缺少字段：" + key)
    return result, meta
