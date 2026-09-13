# 最小 Demo 启动与演示

## 当前能力

五个角色由独立 Codex CLI 会话执行，默认使用 `icrc`、`gpt-5.6-luna`、`medium`，执行设置见 `configs/runtime.json`。协调角色分别进行规划和验收，因此一次完整运行包含六个模型会话；标准化与交集是确定性程序，不伪装成额外 Agent。

页面展示任务依赖、会话编号、真实工具事件、交接结果、截图、统计图及运行报告。真实来源核验与合成工程验证分别保存，不能混用。

## 1. 启动页面

首次使用先完成 [README 的环境安装与执行配置](../README.md#快速启动)。以下命令在项目根目录的 PowerShell 中执行，使用项目 `.venv`；已有环境可替换解释器路径。

```powershell
.\.venv\Scripts\python.exe server/app.py --port 8766
```

打开 [本机工作台](http://127.0.0.1:8766)。也可以使用 `scripts/start_demo.ps1`。服务仅监听本机，不自动对外开放。

## 2. 选择运行方式

右侧“新建任务”可直接填写研究名称、药材、疾病关键词和研究说明。点击“保存并启动”会将任务写入 `tasks/tasks.jsonl` 并启动现有多 Agent 执行器；“保存任务”仅加入清单。相同内容重复保存会复用原任务，改动已有内容保存为新的任务，不改写历史运行快照。

点击流程节点会切换到“阶段详情”，可查看执行状态和交接。返回“新建任务”可继续输入，页面刷新前保留当前表单内容。数据库不可达或真实输入不足时显示受限状态，不能把提交成功当作研究完成。

| 方式 | 实际执行 | 能证明什么 |
|---|---|---|
| 实时运行 | 独立 Agent 使用 Playwright 核验站点；存在合格导入时继续处理和分析 | 真实访问、真实缺口、证据交接；受限步骤保持 blocked |
| 合成工程验证 | 独立 Agent 审核显式给定的合成集合；程序计算交集、测试网络及本地超几何检验/BH 校正 | 多 Agent 分工、接口、计算及交接可运行；不代表五库数据或药理结果 |

合成输入来自 `examples/fixture.json`；图表和页面均标记合成。合成富集不是 DAVID 调用，合成网络不是 STRING 查询。

也可以从命令行运行：

```powershell
.\.venv\Scripts\python.exe scripts/run_tasks.py --mode live
.\.venv\Scripts\python.exe scripts/run_tasks.py --mode fixture
```

两条命令是不同选择，按需执行其中一条。同一时刻只允许一个完整运行，内部的独立角色可以并行。

## 3. 查看与恢复

- 从运行下拉框选择历史运行，不需要组会现场重新等待远端模型或网站。
- 点击流程节点查看该角色的独立会话、交接与产物；报告链接位于详情面板。
- 原始模型日志、浏览器 traces 和提示词留在本地，不通过产物接口公开。
- 网络恢复或补充账号/导出后，选择运行并恢复；也可执行 `scripts/run_tasks.py --resume <run_id>`。
- 恢复只复用输入、参数和产物哈希均一致的成功步骤；失败或变化步骤创建新的 attempt，旧资产保留。
- 异常关闭留下运行锁时，先查看 `runs/.runner.lock` 中的 PID 并确认原进程已退出，再删除该锁；不能在任务仍执行时删除。

## 4. 账号与真实导出

不自动注册账号，不绕过验证码。待人工登录或完成机构验证后，按 [真实数据导入说明](IMPORTS.md) 提供结果并接续。单纯注册不一定能解决机构授权或网络限制。

浏览器登录态可在本地 `configs/browser.local.json` 中指定 `storage_state`，路径指向项目内 `.auth/` 下的 Playwright storage state 文件。该本地配置和登录态均不上传仓库；使用时每个 Agent 获得独立浏览器上下文。

## 5. 环境检查

```powershell
.\.venv\Scripts\python.exe scripts/doctor.py
.\.venv\Scripts\python.exe -m pytest -q tests
```

`doctor.py` 检查本地依赖、命令和配置文件是否存在，结果保存在 `runs/diagnostics/environment.json`；它不验证模型服务认证或网站权限。测试验证代码与合成输入，不需要真实数据库账号，也不代表真实研究分析完成。

Playwright MCP 固定 `0.0.64`，保留 `--save-trace`；Codex 的 MCP 启动超时使用 `startup_timeout_sec=120`（Kimi 同义设置为 120000 毫秒）。启动前缺少浏览器/MCP 会报错，不以“没有工具”的假定跳过科学步骤。

原 Scholar 的兼容性初始化脚本保留为 `scripts/browser_compat.js`，默认不启用；需要测试时可在本地浏览器配置中设 `compatibility_init_script: true`。默认使用当前 Chromium 的真实 UA，不复制旧 Chrome/124 指纹和关闭浏览器沙箱的参数。遇挑战仍停止。

## 尚未完成的研究环节

完整 BATMAN、GeneCards、OMIM 真实数据获取及阈值/映射复核；DAVID 的正式导出和统计口径确认；真实研究网络的 Cytoscape/CytoNCA 自动交接。软件已安装并完成合成三节点插件计算验证，详见 [环境准备](ENVIRONMENT_SETUP.md)。当前 NetworkX 度值只标为 NetworkX，不冒充 CytoNCA。


## 离线报告

通过 scripts/export_report.py --run <run_id> 可生成 reports/<run_id>.html，图片嵌入文件，断网也能打开，不会重新调用模型。合成报告始终标记为工程验证。分享前检查选定运行的内容；reports/ 默认不提交到 Git。


实时运行每步归档到 `data/pharm/<方名>/01_batman/` 至 `05_enrich/`，状态与原始导出一同保存，断点重试不覆盖旧批次。批次导入方式见 [IMPORTS.md](IMPORTS.md)。
