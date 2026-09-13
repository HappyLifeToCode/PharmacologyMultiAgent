import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm_demo.common import ROOT, read_json, write_json

if __name__ == "__main__":
    libraries = ["fastapi", "uvicorn", "playwright", "requests", "scipy", "networkx", "matplotlib", "toml"]
    profile = read_json(ROOT / "configs/runtime.json")["codex_profile"]
    report = {"python": sys.executable, "python_version": sys.version.split()[0], "libraries": {name: bool(importlib.util.find_spec(name)) for name in libraries}, "codex_found": bool(shutil.which("codex.exe") or shutil.which("codex")), "npx_found": bool(shutil.which("npx.cmd") or shutil.which("npx")), "profile_exists": (Path.home() / ".codex" / (profile + ".config.toml")).exists(), "playwright_mcp_version": "0.0.64", "mcp_startup_timeout_seconds": 120}
    if report["libraries"]["playwright"]:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            report["chromium_installed"] = Path(p.chromium.executable_path).exists()
    write_json(ROOT / "runs/diagnostics/environment.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
