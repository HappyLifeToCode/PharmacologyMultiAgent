# 启动与使用

## 当前能力

两条流水线：discovery（本地数据预检 → 药材靶点解析 → 疾病反向查询 → 程序验收与报告）与 analysis（候选疾病的机制分析：共同靶点 → 网络 → 富集 → 验收）。计算全程确定性程序；live 默认启用六个 Codex 核验会话（多 Agent 编排，程序计算·Agent 核验）；本地数据缺失时阶段 blocked 并给出指引，工作台可启动人机协助会话（内嵌浏览器）人工介入。

## 1. 启动页面

首次使用先完成 [README 的环境安装](../README.md#快速启动)。以下命令在项目根目录的 PowerShell 中执行，使用项目 `.venv`；已有环境可替换解释器路径。

```powershell
.\.venv\Scripts\python.exe server/app.py --port 8766
```

打开 [本机工作台](http://127.0.0.1:8766)。也可以使用 `scripts/start_demo.ps1`。服务仅监听本机。

## 2. 提交任务

左侧"新建任务"：选择内置方剂（自动填入组成，可增删编辑）或"自由药材组合"手工填写药材（每行一味）；阈值默认 0.84；选择运行方式；可选研究说明。

- **保存任务**：写入 `tasks/tasks.local.jsonl`（Git 忽略）；相同内容重复保存复用同一任务（task_id 为内容哈希）。
- **保存并启动**：保存后立即启动一次运行。保存与启动是两个步骤；启动失败时已保存内容保留。

| 方式 | 实际执行 | 能证明什么 |
|---|---|---|
| live（本地数据反查） | 读取本机 BATMAN 全量文件与疾病索引；默认启用 Agent 核验会话；缺失时对应阶段 blocked + guidance，不伪造 | 真实本地数据链路与真实缺口 |
| fixture（合成工程验证） | 固定合成靶点与合成索引，全程标注 synthetic_engineering，不访问外网、不调模型 | 工程流程可运行；不代表任何药理结果 |

**多 Agent 开关**：任务字段 `agents`（live 默认 true、fixture 强制 false）。live + agents=true 需要可用的 Codex CLI 与 profile（见 [Codex 执行配置](CODEX_RUNTIME.md)）；环境不可用时运行直接 failed 并提示改用 agents=false——纯程序 live 不需要模型服务，报告注明"无 Agent 核验"。

也可以从命令行运行：

```powershell
.\.venv\Scripts\python.exe scripts/run_tasks.py --task <task_id>
.\.venv\Scripts\python.exe scripts/run_tasks.py --resume <run_id>
```

同一时刻只允许一个完整运行（`runs/.runner.lock`）。

## 3. 查看与恢复

- 中间栏选择运行，阶段图按状态着色（succeeded 绿 / failed 红 / blocked·partial 橙 / running 蓝）；点击阶段节点，右栏显示状态、摘要、blockers、findings 与产物下载。analysis 运行的阶段图为共同靶点提取 → 网络分析 → 富集分析 → 验收与报告，运行项带"机制分析·疾病名"徽标。
- "疾病反向查询"阶段详情内含候选疾病表、未命中靶点与未命中药材清单；report.md 在运行级下载。
- **置信度阅读**：候选疾病表的"置信度"列是程序计算的透明启发式（heuristic_v1：log 匹配数、输入/疾病覆盖率、证据质量的加权和），**非统计检验，仅供排序参考；列表是固定顺序，不是疗效排名**。点击置信度数值展开四个组件明细（null 组件不参与加权）；零匹配疾病置信度为 0 并如实显示。
- **机制分析**：候选疾病表中匹配靶点 > 0 的行有"机制分析"按钮，确认后创建 analysis 运行（共同靶点→网络→富集→验收）并自动选中；零匹配行不显示按钮。
- **Agent 核验**：阶段详情显示各阶段 Agent 核验结论（角色、状态、把握自评高/中/低、理由、会话编号）；会话未完成时如实显示错误，阶段状态以程序结果为准。
- 事件列表滚动显示阶段与工具事件；页面每 5 秒轮询。
- blocked/failed 的运行显示"恢复运行"；恢复只复用输入、代码、数据与产物哈希全一致的成功阶段，其余阶段新建 attempt 重跑（验收每次重核）。补齐本地数据后恢复，受阻阶段会检测到签名变化而自动重跑；修改 `agents/*.md` 提示词也会使对应阶段重跑（预期）。
- 异常关闭留下运行锁时，先查看 `runs/.runner.lock` 中的 PID 并确认原进程已退出，再删除该锁。

## 4. 人机协助面板

某阶段 blocked 且 handoff 带 assist 标记时，右栏显示"可启动人机协助会话"：

1. 采集 Agent 通过协助请求登记当前页面后，右栏会显示页面地址；用户点击“接管当前采集页面”——后台启动 headed Chromium，原始窗口移到屏幕外，画面实时显示在页面内嵌 canvas 中；静态验证页没有重绘时，系统会自动截图补发首帧，避免画布黑屏。用户不需要手动复制 URL。
2. 点击 canvas 取得焦点后即可正常点击、滚动、键入（事件回传到远端浏览器）；旁侧为引导消息与状态。
3. 必须在右侧内嵌画布中完成人机验证/登录；左侧采集器原页面不属于协助会话。完成后点“验证完成，继续采集”，系统会同步验证状态并让原采集器继续执行；只有不再需要采集时才结束会话。

同一时刻只允许一个协助会话。采集端可调用 `POST /api/assist/request` 登记 `url`、`guidance` 和可选 `context`，前端再调用 `POST /api/assist/start`（只需 `request_id`）启动接管。在线采集 Agent 的完整采集编排仍需接入；当前桥接已经不要求用户手动输入 URL。

> 若浏览器控制台显示 `WebSocket connection ... /ws/assist ... 404`，画布会保持黑色。该问题 2026-09-24 已定位：服务环境缺 `websockets` 包（现已写入 requirements.txt，启动时缺包会直接报错）。请先 `pip install -r requirements.txt` 并重启服务；仍出现则核验运行进程加载的是否旧代码。

## 5. 环境检查与测试

```powershell
.\.venv\Scripts\python.exe scripts/doctor.py
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

`doctor.py` 检查依赖、命令与配置文件存在性（结果存 `runs/diagnostics/environment.json`），不验证网站权限或数据可用性。测试不需要研究数据或外网。

## 6. 离线报告

```powershell
.\.venv\Scripts\python.exe scripts/export_report.py --run <run_id>
```

生成 `reports/<run_id>.html`（图片内嵌，断网可打开，不重新调用任何东西）。`runs/`、`reports/` 默认不提交 Git；分享前检查具体内容。

## 7. 真实数据准备

- BATMAN v2.0 全量文件与疾病索引批次的准备见 [环境准备](ENVIRONMENT_SETUP.md) 与 [导入说明](IMPORTS.md)。
- 不自动注册账号，不绕过验证码；账号可用不等于已取得合格导出。
