# 项目交接说明

2026-10-14 中文显示补充：用户截图中的拼音分别来自未匹配拼写、含糊名称、原药材表缺中文名。修复 `BAI SHAO YAO` 有登记别名但精确命中 NA 中文条目后漏显示中文的问题，新增仅展示字段，不换原检索条目（真实 BAI SHAO YAO 为 30 个成分，BAI SHAO 为 301 个，不能为显示中文而静默替换）。未命中/中文名缺失分开标记；SHAO YAO 及剂量说明混入项仍不猜测。最终全量 284 passed / 3 skipped，新的显示字段不改变组成 ID 或数据指纹。

## 2026-10-14 方剂组成表接入

最终全量回归 **282 passed, 3 skipped**，四份 JS 语法检查通过；浏览器验证说明见下文。

基于 main/`0d48f54`，接入用户补充的 `data/batman/v2.0/formula_browse.txt`：54,832 行，字段为方剂拼音名、中文名、药材拼音组成，没有出处/剂量。新 `pharm/batman/formula_catalog.py` 按明确名称及组成合并重复记录，保留不同组成和原表行号，不取第一条为标准方；真实四物汤 19 行合并为 14 个组成候选。原四方及旧任务面板候选规则保留。首页和工作台顶部共用组成选择器，支持编辑、恢复原候选、保存默认、取消不启动计算及数据变更后重新选择。

新增 `/api/formulas/resolve`、`/api/formulas/preference`；正向接口可携带组成 ID、编辑清单及数据指纹，歧义在启动子进程前返回 409。归档 `input.formula_resolution` 保存原始组成、映射/未匹配、编辑类型、行号和文件哈希；`reference:null` 如实表示无出处。默认选择只写本机偏好，不是科学复核。明确列出的项目别名处理熟地/生地/白芍药/麦门冬及对应拼音，不自动修正 `CHUAN QIONG` 等拼写、不判定不明确的芍药、不替代炮制形式。缺失匹配允许编辑或明确按匹配部分继续；至少一味明确匹配后才进入新方剂查询。

验证目录 `local/checks/formula-catalog-20261014/`：真实全表解析及哈希核对；选定的四味编辑清单在真实 BATMAN 命中 973 个 known 靶点、2,158 条关系，无未命中。浏览器桌面/移动/工作台通过选择、编辑、保存复用、刷新、取消及部分匹配确认；偏好使用真实 API 但写隔离测试目录，查询结果是明确标注的界面合成响应，不写研究归档、不代表新方剂疾病查询完成。最终全量回归和当前服务 PID 见 AGENTS.md；不修改数据源、评分或科学验收状态。

## 2026-10-14 最新接续

最新起点 main/`be2409c`，默认首页是会话，研究工作台在 `/workbench`；自然问句方剂识别与 BATMAN 本地优先回退已经合入，不能把下方 10-12 页面状态当最新。10-14 新增疾病中文名称展示与正向请求取消，不改变研究数据和排序。

首页/工作台共用 `server/static/disease-labels.js` 与提交的本地 `disease-labels.json`：2,620 个 MONDO ID、66 个精确英文名/别名，来源 Wikidata CC0 标签与 `pharm/diseases/chinese_labels_overrides.json` 的项目译名。旧归档读取后直接显示中文，无需重算、不回写旧文件；未知译名明确标注。维护与来源见 DATA_CONTRACT，更新脚本不在用户查询时运行。

用户最后指令为“关闭页签才自动停止”，覆盖先前的页面离开/连接报错即取消策略。`pharm/agent/jobs.py` 管理独立子进程，`query-presence.js` 用 sessionStorage 页签编号 + SSE 持续连接跟踪 owner_id；项目内导航/刷新重连同页签、切换标签继续，断开超过 10 秒才停止该页签的查询。POST 断开/前端报错可恢复原查询，状态接口 `/api/agent/forward/request/<request_id>` 支持接续；服务实例变更的运行记录返回 interrupted。主动重试 supersedes 先等待旧任务退出，显式 cancel 入口仍保留；无 owner_id 的旧 API 仍在 HTTP 断开时停止。任务记录见 local/agent/jobs/ 与查询目录 job.json，重试从头运行、不自动续算。纯 ASGI 保留访问校验/断开信号；工作台新标签页打开，归档 GET 可只重读。

验证使用自行启动的 8771 服务和真实既有温经汤归档，保存于 `local/checks/chinese-disease-20261014/`；自动测试包含独立进程被终止、先停旧任务再重试、请求编号重复与提前取消。更新后仍用原命令启动服务，实时端口/PID看本地 AGENTS.md。三个此前重叠的真实查询均已保存 succeeded 结果（第二条 12:08，第一/第三 12:09），并非进程自动停止；新策略只适用于更新后的服务。

最新浏览器验证通过：真实白芍查询进入工作台超过缓冲期、返回/刷新保持同一 PID，仅一个 POST；关闭查询页签后才 cancelled/tab_closed，另一工作台页签继续可用。桌面/移动端真实温经汤归档双语候选/证据/评分、GET 故障重读通过，原 43MB result.json SHA-256 完全不变。网络故障/合成错误响应验证保留原请求编号和显式重试 supersedes，不把错误响应当研究结果；`scientific_complete=false`。最终全量结果记录在 AGENTS.md/PROJECT_STATUS。

最终全量 **269 passed, 3 skipped**；四份 JS 语法检查通过，本已中文的名称不重复追加、未收录/异常键不误匹配的浏览器核对通过。

## 2026-10-12 最新接续

当前优先链路是“方剂/药材 → BATMAN 靶点 → 候选疾病”。顶部对话框支持直接输入“温经汤”等内置方剂名，后端展开多味药材后查询本地 BATMAN、Open Targets 26.09 和 Monarch 2026-10-07，展示药材组成与 BATMAN 实际命中、候选疾病和逐条来源证据。

`POST /api/agent/forward` 接受 `text`、`formula` 或 `herbs`（只能选择一种），前端直接发送 `text`，不依赖下方任务面板的方剂目录。当前已收录温经汤、半夏白术天麻汤、济川煎、桃核承气汤；未收录方剂或加减方需手工提供组成。内置组成与 BATMAN 名称候选沿用 `pharm/batman/formulas.py`，来源尚待研究者确认，不由程序确认正式研究口径。下方旧任务面板本轮仍保留；仅对话框页面的初始化不再访问旧面板或任务 API。

启动命令仍为：

```powershell
.\.venv\Scripts\python.exe server/app.py --port 8766
```

本次相关文件为 `pharm/agent/forward.py`、`server/app.py`、`server/static/`、`tests/test_forward_agent.py` 与 `tests/test_server.py`。新增方剂展开、甘草候选回退、未命中与混合输入校验；全量回归 **222 passed, 3 skipped**。温经汤真实查询暴露原聚合将约 200 万条记录整体驻留内存的问题，已改为两次流式读取：第一次汇总所有候选并按原规则排序，第二次仅加载 `top_k` 候选完整证据；完整采集 CSV 保留，评分与来源规则未变。

真实验证保存于 `local/agent/formula-validation-20261012/`：温经汤 12/12 味 BATMAN 命中，2670 个唯一靶点、34571 条成分—靶点关系。首次查询完成 Open Targets 采集后因内存压力主动停止；修复后的验证复用该真实采集批次，核对输入靶点、选择参数与 CSV SHA-256，继续执行真实 Monarch 与 API 聚合，不伪造疾病证据；原未完成目录保留。成功结果为 `local/agent/forward/forward_9ed3b61f9e3b4f34acf40d9ffd7808d1/`：Open Targets 1996649 条采集关联，Monarch 95 条 ClinGen / 2113 条 OMIM 匹配，过滤后 13177 个候选，按用户设置返回 10 个，两个来源工程状态均 succeeded，`scientific_complete=false`。

浏览器检查通过：现有工作台和移除下方面板的仅对话框页面，桌面与移动端均可提交方剂、显示 12 味组成、查看 10 个候选与逐条证据，无 JS 错误或移动端页面溢出；仅对话框模式未请求旧任务 API。成功响应使用上述真实 API 归档回放，混合输入与未知显式方剂通过真实 API 校验并返回 400。截图及 `browser_summary.json` 保存于验证目录；临时服务 8771 验证后关闭，原 8766 服务未改动，用户需按原命令重启后刷新。当前结果是候选关联，不提供治疗有效率或临床疗效证据。

本轮开始时 `main` 与 `origin/main` 同步到 `f8a713a`。接续前先检查 `git status`，保留用户未提交修改；GitHub 推送需显式使用本机代理 `127.0.0.1:7890`，除非用户另行配置 Git 代理。

更新：2026-10-02。阅读顺序：本文件 → [项目进度](PROJECT_STATUS.md) → [数据契约](DATA_CONTRACT.md)。

## 最新决定与修改

用户暂停新数据库选型，要求删除不可用的 GeneCards、OMIM 及其在线采集代码。基于同门提交 `0e4abd0`，已移除两库采集器、HTML/官方导出转换、专用导入与中位数过滤、协助浏览器桥、验证码画布及 `/api/assist/*`、`/ws/assist` 接口。前端与新任务仅支持 Open Targets 数据查询。

保留 BATMAN 本地查询、Open Targets GraphQL API 与 Parquet 快照、通用 `associations.csv` 导入、双流水线、网络与富集功能。没有增加新数据库，也没有下载研究数据或改动正式研究阈值。

Open Targets 本地结果会记录疾病/表型实体类型；工作台候选疾病列表使用独立可展开、可滚动区域，仅展示疾病实体。EFO/OBA 等表型或测量性状仍保留在运行证据中，不冒充疾病显示。

本地原始数据、登录资料、SQLite 索引与历史运行保持原样。旧 schema 1/2 索引仍可读，来源字段不会被改成 Open Targets；新建索引只走通用批次。旧任务若启用已删除的在线来源，会明确 blocked 并提示保存新任务，不静默切换数据来源。查看历史结果不需要恢复旧采集器。

清理移除了评分中的 GeneCards 专用归一分值组件；新计算标为 `heuristic_v2`，沿用原权重，`evidence_quality` 仅使用 BATMAN known 占比，无证据则 null 并剔除。Open Targets 原始分数保留展示，不自动变成疗效概率。历史 `heuristic_v1` 报告不重写。该启发式仍需研究评审，不是老师要求的临床治疗置信度。

## 当前流程

`discovery`：数据预检 → BATMAN 药材成分靶点解析 → 疾病查询 → 程序验收与报告。

- 未启用数据查询时读取已配置的本地 SQLite 疾病索引；缺索引明确 blocked。
- `online_collect=true` 启用 Open Targets 查询。疾病范围为空时输入 BATMAN 唯一靶点；填写范围时按疾病查询靶点。
- `PHARM_OPEN_TARGETS_MODE=local` 读取本地快照；当前默认也是 `local`，只有显式指定 `online` 才使用 GraphQL。字段名称 `online_collect` 为现有任务协议保留，本地模式不联网。
- 查询结果生成本次运行专用索引；存在基础库时复制扩展，不覆盖基础库。查询失败保留中间产物，提示检查连接或快照配置后恢复。
- 现有试验选择规则为 Top-100、score≥0.2；这是同门试验默认，不能宣称已确认的正式研究口径。

`analysis`：选择候选疾病 → 共同靶点 → STRING/CytoNCA 网络与 DAVID 富集 → 验收。数据库关联、网络指标、富集与启发式分数均不等于临床疗效，`scientific_complete` 恒 false。

## 关键位置

| 能力 | 入口 |
|---|---|
| 任务与双流水线 | `pharm/pipeline/tasks.py`、`scheduler.py`、`engine.py` |
| Open Targets API | `pharm/diseases/open_targets.py` |
| Open Targets 本地快照 | `pharm/diseases/open_targets_local.py` |
| 第三阶段数据查询编排 | `pharm/diseases/online_pipeline.py`（仅 Open Targets） |
| 通用批次导入 | `pharm/diseases/associations.py`、`pharm/core/imports.py` |
| SQLite 查询与证据 | `pharm/discovery/query.py` |
| Agent 核验 | `pharm/agents/runtime.py`、`agents/*.md` |
| 网络与富集 | `pharm/network/`、`pharm/enrich/`、`integrations/cytonca_bridge/` |
| 工作台 | `server/app.py`、`server/static/` |

Python `playwright` 仍供独立 Agent 核验运行时配置浏览器 MCP；疾病数据查询与工作台不再启动浏览器。`websockets` 已从工作台必需依赖中删除。

本次全量回归为 `197 passed, 3 skipped`，Open Targets 本地 Parquet 测试已实际运行；API 和模型采用测试替身，未执行真实科研分析。JavaScript 语法检查通过。浏览器工具的本机页面访问权限检查不可用，视觉实测未完成。

## 启动与配置

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server/app.py --port 8766
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe scripts/run_tasks.py --task <task_id>
.\.venv\Scripts\python.exe scripts/run_tasks.py --resume <run_id>
```

BATMAN：`configs/batman_data.local.json`；已建疾病索引：`configs/discovery_data.local.json`；Open Targets：`configs/open_targets_data.local.json`。模板均在 `configs/`。数据、个人任务、运行与认证目录不上传 Git。

## 接续事项

1. 数据库选型目前暂停；待用户决定后再接入治疗证据源，不继续 GeneCards/OMIM 登录与验证码排查。
2. 对 Open Targets 做真实快照或 API 的小样本抽检，记录 ID 映射覆盖、direct/indirect 范围与试验选择规则。工程测试不能代替科学验收。
3. 多 Agent 完整真实运行、STRING/CytoNCA/DAVID 本机正式验证与四方组成来源确认仍需继续，不能因本次清理而宣布完成。
4. 旧阶段记录可从 Git 历史与本地运行查阅，保留原始日期、计数和 partial/blocked 状态。

遵循用户后续指令在 main 工作，中文提交；推送前全量 pytest 通过。当前阶段不自行确定正式研究参数或签署人工复核。
