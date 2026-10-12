# 项目交接说明

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
