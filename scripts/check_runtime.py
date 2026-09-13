"""Read-only model/browser handshake. No scientific result is produced."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pharm_demo.codex_runtime import execute
from pharm_demo.common import ROOT
from datetime import datetime

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--site", choices=["string", "david"], default="string")
    options = parser.parse_args()
    prompt = "仅执行连通性检查。不要读取项目其他文件，不要修改项目代码。"
    if options.browser:
        url = "https://string-db.org/" if options.site == "string" else "https://davidbioinformatics.nih.gov/"
        prompt += "使用已提供的 playwright browser_run_code 打开 " + url + "，读取页面 title 和前 800 字正文。保存截图到工具输出目录。资源列表不是工具列表，不根据 resources 为空就断言没有工具。遇登录或验证码停止。不要因导航条上有 login 字样就认定需要登录。不要注册账号。返回 structured response，findings 填实际标题和访问结论，artifacts 填截图路径。不要编造。"
    else:
        prompt += "不要使用工具。返回 status=succeeded，summary=READY，其余数组为空。"
    output = ROOT / "runs/diagnostics" / ((options.site if options.browser else "isolated_model") + "_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    prompt += " 截图必须保存在 " + (output / "browser/connectivity.png").as_posix() + "，不要写项目根目录。"
    result, meta = execute(prompt, output, browser=options.browser, timeout=240)
    print(json.dumps({"result": result, "execution": meta}, ensure_ascii=False, indent=2))
