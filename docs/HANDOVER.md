# 项目交接说明

更新：2026-10-01。阅读顺序：本文件 → [项目进度](PROJECT_STATUS.md) → [数据契约](DATA_CONTRACT.md)。

## 最新决定与修改

用户暂停新数据库选型，要求删除不可用的 GeneCards、OMIM 及其在线采集代码。基于同门提交 `0e4abd0`，已移除两库采集器、HTML/官方导出转换、专用导入与中位数过滤、协助浏览器桥、验证码画布及 `/api/assist/*`、`/ws/assist` 接口。前端与新任务仅支持 Open Targets 数据查询。

保留 BATMAN 本地查询、Open Targets GraphQL API 与 Parquet 快照、通用 `associations.csv` 导入、双流水线、网络与富集功能。没有增加新数据库，也没有下载研究数据或改动正式研究阈值。

本地原始数据、登录资料、SQLite 索引与历史运行保持原样。旧 schema 1/2 索引仍可读，来源字段不会被改成 Open Targets；新建索引只走通用批次。旧任务若启用已删除的在线来源，会明确 blocked 并提示保存新任务，不静默切换数据来源。查看历史结果不需要恢复旧采集器。

清理移除了评分中的 GeneCards 专用归一分值组件；新计算标为 `heuristic_v2`，沿用原权重，`evidence_quality` 仅使用 BATMAN known 占比，无证据则 null 并剔除。Open Targets 原始分数保留展示，不自动变成疗效概率。历史 `heuristic_v1` 报告不重写。该启发式仍需研究评审，不是老师要求的临床治疗置信度。

## 当前流程

`discovery`：数据预检 → BATMAN 药材成分靶点解析 → 疾病查询 → 程序验收与报告。

- 未启用数据查询时读取已配置的本地 SQLite 疾病索引；缺索引明确 blocked。
- `online_collect=true` 启用 Open Targets 查询。疾病范围为空时输入 BATMAN 唯一靶点；填写范围时按疾病查询靶点。
- `PHARM_OPEN_TARGETS_MODE=local` 读取本地快照；默认 `online` 使用 GraphQL。字段名称 `online_collect` 为现有任务协议保留，本地模式不联网。
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

本次全量回归为 `192 passed, 3 skipped`，Open Targets 本地 Parquet 测试已实际运行；API 和模型采用测试替身，未执行真实科研分析。JavaScript 语法检查通过。浏览器工具的本机页面访问权限检查不可用，视觉实测未完成。

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
