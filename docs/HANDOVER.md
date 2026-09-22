# 项目交接说明（给下一位协作者 / 大模型）

> 目的：让没有本项目上下文的人（或 AI）能快速接手。阅读顺序：本文件 → docs/PROJECT_STATUS.md → docs/DATA_CONTRACT.md。
> 更新日期：2026-09-22（阶段G，与重构后代码严格对齐）。项目根目录即本文件所在仓库。

## 1. 项目是什么

中药复方反向疾病发现工具。输入方剂（内置四方：温经汤/半夏白术天麻汤/济川煎/桃核承气汤，或自由药材组合）→ BATMAN-TCM v2.0 本地全量文件解析药材—成分—靶点 → 本地 SQLite 疾病索引反查候选疾病关联 → 程序验收并出报告。

主链路是**零模型会话的确定性程序**（四阶段 DAG）。旧方向（药物×疾病交集：Venny/STRING/CytoNCA/DAVID/甲状腺补充流程/多Agent九阶段）已于 2026-09-22 重构废弃，实现与真实数据记录见 Git 历史（commit `6779567` 之前）。

## 2. 当前状态

- 四阶段 pipeline（preflight→herb_targets→disease_reverse→review，workflow_version=3）已完成并经端到端验证（fixture 合成与带合成数据的 live 路径）。
- 疾病索引已泛化为可插拔批次（GeneCards+OMIM 三源 / 通用 associations.csv，自动识别；疾病集合来自批次实际值，上限 500）。
- 人机协助桥已完成：/api/assist/* + WS /ws/assist，headed Chromium 画面推流 + 输入回传，工作台内嵌 canvas。
- Web 单页三栏工作台已完成，真实浏览器验证通过。
- `pytest tests/ -q`：**119 passed, 1 skipped**。
- **未实现**：Agent 在线采集流程（预留 `pharm/agents/runtime.py` 与 blocked 阶段的 assist 升级标记）；四方组成出处待用户确认；疾病库覆盖范围取决于批次；本机无研究数据，真实数据验证待数据同步。

## 3. 关键资产位置

| 内容 | 位置 |
|---|---|
| 四阶段引擎 | `pharm/pipeline/engine.py`（Runner：begin/signature/reuse/finish/四阶段/review/run） |
| DAG 调度 | `pharm/pipeline/scheduler.py`（注册表即依赖图，拓扑序，依赖终态放行） |
| 任务模型 | `pharm/pipeline/tasks.py`（formula/herbs/research_notes/batman_threshold/mode） |
| 四方组成 | `pharm/batman/formulas.py`（canonical + BATMAN 候选名，source 待确认） |
| BATMAN 本地查询 | `pharm/batman/local.py`（`query_local_targets`，未命中药材记 unmatched 不报错） |
| 疾病索引 | `pharm/discovery/query.py`（build/prepare/query/catalog）、`pharm/diseases/associations.py`（通用批次） |
| 人机协助 | `pharm/assist/bridge.py`（AssistSession/AssistManager，WS 协议见模块文档字符串） |
| Web 工作台 | `server/app.py` + `server/static/{index.html,app.js,style.css}` |
| BATMAN 数据（本机无） | 团队机器 `data/batman/v2.0/`；配置 `configs/batman_data.local.json` |
| 疾病索引（本机无） | 默认 `local/discovery/disease_index.sqlite`；配置 `configs/discovery_data.local.json` |

## 4. 常用命令

```powershell
# 测试（必过再提交）
.\.venv\Scripts\python.exe -m pytest tests/ -q
# 启动工作台（默认 127.0.0.1:8766）
.\.venv\Scripts\python.exe server/app.py --port 8766
# 命令行运行 / 断点续跑
.\.venv\Scripts\python.exe scripts/run_tasks.py --task <task_id>
.\.venv\Scripts\python.exe scripts/run_tasks.py --resume <run_id>
# 建疾病索引（自动识别批次类型；--db 在子命令之前）
.\.venv\Scripts\python.exe -m pharm.discovery.query --db local/discovery/disease_index.sqlite prepare --batch <批次目录>
# 全药材目录扩展（从 v1 索引生成 v2）
.\.venv\Scripts\python.exe -m pharm.discovery.query --db <v1.sqlite> expand-batman --data-dir <BATMAN目录> --manifest <批次清单.json> --output <新索引.sqlite>
# 直接反查（不经过 pipeline）
.\.venv\Scripts\python.exe -m pharm.discovery.query query --genes TP53 EGFR --output local/discovery/my-lookup
# 离线 HTML 报告
.\.venv\Scripts\python.exe scripts/export_report.py --run <run_id>
# 环境检查（依赖/浏览器/profile 存在性，不验证权限）
.\.venv\Scripts\python.exe scripts/doctor.py
```

## 5. 硬性约定（违反会被程序拦截或评审打回）

1. **不编造、不顶替**：缺数据 = blocked + guidance + 证据保留；禁止合成数据冒充（fixture 除外且全程标注 synthetic_engineering、不进归档）。
2. **参数即身份**：task_id = 内容哈希；**代码即签名**：input_signature 含任务、runtime、`pharm/**/*.py` 哈希与数据文件哈希——改代码后 resume 会重跑全部阶段，这是特性不是 bug。
3. **manifest.json 是唯一状态源**；attempt_NN 不可变；归档（`data/pharm/<名>/01_preflight..04_review/`）拒绝覆盖，archive 不等于验收。
4. **scientific_complete 恒 false**；空交集/零匹配/未命中药材如实保留。
5. 直推 `main`，**push 前跑 `pytest tests/ -q`**。
6. 不绕过站点人机验证；账号、密码、验证码不进任务、不进仓库。

## 6. 已知坑（都踩过，别再踩）

- **sync Playwright 不允许跨线程**：AssistSession 的全部 Playwright 操作收敛在专属浏览器线程，外部经任务队列交互；CDP screencast 事件在 Playwright 内部线程回调，只做 ack + 线程安全分发。跨线程调用会报 `greenlet.error`。
- **screencast 只在重绘时出帧**：DOM evaluate 不一定出帧，真实输入（Tab 焦点切换、点击聚焦后光标闪烁） reliably 出帧；首帧可能是预绘黑帧。每帧必须回 `Page.screencastFrameAck`，否则流停。
- **裸 CDP Input.dispatchKeyEvent 不带 windowsVirtualKeyCode 不可靠**：输入回传用 Playwright 的 page.mouse/page.keyboard（内部即 CDP 输入管线且自动映射键码）。
- **starlette TestClient 的 WS scope 主机名恒为 testserver**：/ws/assist 的本机校验白名单含此值（代码有注释）。
- **headless 测试环境 headed 起不来**：AssistSession 的 headless=True 仅限测试；生产路径 headed 失败抛 AssistUnavailable，不静默降级。
- **herb_browse.txt 表头/格式变更会建库失败**：BATMAN 文件核验严格按 v2.0 表头；predicted 文件名部分镜像是双下划线（`predicted__browse_by_targets.txt.gz`），代码兼容两种。
- Windows 下杀毒可能造成文件占用：写 JSON/归档已带重试。

## 7. 待办（按优先级）

1. **在线采集接入**：用 agents/runtime.py + assist 桥实现"Agent 采集遇人机验证 → 用户内嵌接管 → 继续"的完整编排（当前只有 blocked + assist 标记，无人在环等待）。
2. **四方组成确认**：formulas.py 的组成与 canonical→候选名映射标注 `standard_reference_pending_user_confirmation`，待用户/文献确认；阿胶、芒硝等动物/矿物药 BATMAN 可能无记录（如实 unmatched）。
3. **疾病库来源**：当前索引覆盖取决于批次；需要宽覆盖疾病-基因关联批次（通用 associations.csv 通道已就绪）。
4. **数据同步**：BATMAN v2.0 全量文件与疾病索引批次在团队机器上，本机同步后再做真实数据验证。
5. push 到远程前全量 pytest。
