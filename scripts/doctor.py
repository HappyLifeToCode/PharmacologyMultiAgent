import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm.core.common import ROOT, read_json, write_json
from pharm import llm

if __name__ == "__main__":
    libraries = ["fastapi", "uvicorn", "playwright", "requests", "networkx", "toml", "pyarrow"]
    libraries.append("httpx")
    report = {"python": sys.executable, "python_version": sys.version.split()[0], "libraries": {name: bool(importlib.util.find_spec(name)) for name in libraries}, "selected_api": llm.describe(ROOT), "llm_transport": "configured_api"}
    if report["libraries"]["playwright"]:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            report["chromium_installed"] = Path(p.chromium.executable_path).exists()
    write_json(ROOT / "runs/diagnostics/environment.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
