# 启动与使用

## 当前能力

四阶段固定流程：本地数据预检 → 药材靶点解析 → 疾病反向查询 → 程序验收与报告。全程确定性程序，无模型会话；本地数据缺失时阶段 blocked 并给出指引，工作台可启动人机协助会话（内嵌浏览器）人工介入。

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
| live（本地数据反查） | 读取本机 BATMAN 全量文件与疾病索引；缺失时对应阶段 blocked + guidance，不伪造 | 真实本地数据链路与真实缺口 |
| fixture（合成工程验证） | 固定合成靶点与合成索引，全程标注 synthetic_engineering，不访问外网 | 工程流程可运行；不代表任何药理结果 |

也可以从命令行运行：

```powershell
.\.venv\Scripts\python.exe scripts/run_tasks.py --task <task_id>
.\.venv\Scripts\python.exe scripts/run_tasks.py --resume <run_id>
```

同一时刻只允许一个完整运行（`runs/.runner.lock`）。

## 3. 查看与恢复

- 中间栏选择运行，阶段图按状态着色（succeeded 绿 / failed 红 / blocked·partial 橙 / running 蓝）；点击阶段节点，右栏显示状态、摘要、blockers、findings 与产物下载。
- "疾病反向查询"阶段详情内含候选疾病表（点击行展开逐条证据）、未命中靶点与未命中药材清单；report.md 在运行级下载。
- 事件列表滚动显示阶段与工具事件；页面每 5 秒轮询。
- blocked/failed 的运行显示"恢复运行"；恢复只复用输入、代码、数据与产物哈希全一致的成功阶段，其余阶段新建 attempt 重跑（review 每次重新核对）。补齐本地数据（BATMAN 文件、疾病索引）后恢复，受阻阶段会检测到签名变化而自动重跑。
- 异常关闭留下运行锁时，先查看 `runs/.runner.lock` 中的 PID 并确认原进程已退出，再删除该锁。

## 4. 人机协助面板

某阶段 blocked 且 handoff 带 assist 标记时，右栏显示"可启动人机协助会话"：

1. 填入采集入口 URL，点击"启动协助会话"——本机弹出一个 headed Chromium，画面实时显示在页面内嵌 canvas 中。
2. 点击 canvas 取得焦点后即可正常点击、滚动、键入（事件回传到远端浏览器）；旁侧为引导消息与状态。
3. 完成人机验证/操作后点"结束会话"。

同一时刻只允许一个协助会话。在线采集 Agent 本身尚未实现：当前会话是人工工具，engine 不会等待其完成；采集产物请按 [导入说明](IMPORTS.md) 整理为批次后恢复运行。

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
