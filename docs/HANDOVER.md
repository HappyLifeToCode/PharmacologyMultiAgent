# 项目交接说明（给下一位协作者 / 大模型）

> 目的：让没有本项目上下文的人（或 AI）能快速接手。阅读顺序：本文件 → docs/PROJECT_STATUS.md → docs/DATA_CONTRACT.md。
> 更新日期：2026-09-19。项目根目录即本文件所在仓库。

## 1. 项目是什么

中药复方网络药理学多 Agent 系统。首轮案例：**芍药甘草汤（白芍、炙甘草）× 五个甲状腺疾病关键词**（Hyperthyroidism、Hypothyroidism、Thyroid cancer、Thyroid nodules、Thyroiditis），复现一篇网络药理学论文的流程：BATMAN 药材靶点 + GeneCards/OMIM 疾病靶点 → 合并 → Venny 交集 → STRING PPI 网络 + CytoNCA 拓扑 → DAVID 富集。另有独立的**甲状腺癌补充流程**（docs/THYROID_SUPPLEMENT.md）。长期方向：经典名方重定位智能体（见桌面《研究方案.docx》，提取件在 local/yanjiu_fangan.txt）。

## 2. 当前状态（2026-09-19 里程碑）

**五库数据全部到位，研究参数经医院方全部确认，首个真实数据运行已完成大部分：**

- 793 药材靶点（BATMAN 本地全量，阈值 score>0.84）
- 13,392 疾病靶点（GeneCards 五病按"单疾病分别中位数"筛选 13,381 + OMIM 176）
- Venny 2.1.0 实际执行交集 **659 个共同靶点**（Python 独立核对通过）
- STRING 本地网络 651 节点 / 2,350 边，**CytoNCA 2.1.6 经自研桥接算出真实 Degree**（NetworkX 独立核对一致）
- 待做：DAVID 正式富集（任务已配好 EASE+BH 确认配置，需**新建一次运行**——旧运行的任务快照冻结在确认前）

## 3. 关键资产位置

| 内容 | 位置 |
|---|---|
| 五病任务 | `tasks/tasks.local.jsonl` 中 `web_ddc19c70894f4140`（含 network_topology、david_enrichment 已确认配置） |
| 导入批次 | `data/pharm/芍药甘草汤/imports/web_ddc19c70894f4140/20260918_01/`（三库 CSV + provenance.json + raw/） |
| STRING 数据 | `data/string/v12.0/`（1.1GB，不入库；配置见 `configs/string_data.example.json`） |
| BATMAN 数据 | `data/batman/v2.0/`（不入库；配置 `configs/batman_data.example.json`） |
| 运行记录 | `runs/<run_id>/manifest.json`；最新真实运行 `runs/2026-09-19T110300_0000_121e66` |
| 研究归档 | `data/pharm/芍药甘草汤/`（live 运行自动归档） |

## 4. 架构速览

- `pharm_demo/engine.py`：Runner。DAG 调度（`scheduler.py`，依赖边在代码注册表），九阶段、六角色、七个 Codex 会话（`codex_runtime.py`，`codex exec`，独立会话或 shared resume）。
- 数据流：`imports.py` 契约校验（provenance 台账必须 complete/confirmed 才放行）→ `processing.py` 确定性计算 → `sources.py`/`string_local.py`（STRING 优先本地、API 回退）→ `venny.py`（官方 Venny 浏览器执行）→ `cytoscape.py`（CyREST + CytoNCA 桥接 `integrations/cytonca_bridge/`）→ `david.py`（DAVID 工作台 JSON 接口）。
- 工具：`batman_local.py`（BATMAN 全量→导入包）、`genecards_online.py`/`genecards_export.py`（在线采集/导出转换）、`omim_export.py`（Gene Map 转换）、`supplement.py`（甲状腺癌流程）、`benchmark.py`（对照实验）。
- 网页工作台：`server/app.py`（FastAPI）。测试：`pytest tests/`（170 项，约 20 秒）。

## 5. 硬性约定（违反会被程序拦截或评审打回）

1. **不编造、不顶替**：缺数据 = blocked + 证据保留；禁止用合成数据、本地绘图、旧结果冒充。
2. **完整性**：GeneCards 行数必须与页面声明总数一致；分页不完整不得宣称完成。
3. **来源留痕**：文件哈希、下载日期、版本必填；`accessed_at` 用真实下载日期。
4. **非标准符号**（lncRNA 等小写命名）剔除但**留档**；零关联/零显著结果如实保留。
5. **参数只经任务 JSON 配置**，未知值不猜测；confirmed 必须对应真实确认记录。
6. 直推 `main`（无分支流程），**push 前跑 `pytest tests/ -q`**。

## 6. 验收闭环（2026-09-20 新增）

```powershell
# 生成运行验收清单（程序核对产物存在性/哈希/计数，输出 audit.json + audit_report.md）
.\.venv\Scripts\python.exe -m pharm_demo.audit --run <run_id>
# 清单全部通过后，记录人工复核签字（run 级覆盖记录，阶段状态不回写）
.\.venv\Scripts\python.exe -m pharm_demo.audit --run <run_id> --signoff 姓名 --note "复核范围与结论"
```

设计：工程核对（存在性/哈希/计数/交叉验证）由程序完成；人工复核是 run 级覆盖记录（`human_review.json` + manifest + report.md 追加段），阶段执行状态永远不回写。验收 Agent 的证据已含各阶段产物索引（路径+哈希），已登记产物视为已提供，不再要求补交。

## 6. 常用命令

```powershell
# 测试（必过再提交）
.\.venv\Scripts\python.exe -m pytest tests/ -q
# 新建真实运行（五病任务）
.\.venv\Scripts\python.exe scripts/run_tasks.py --task web_ddc19c70894f4140 --mode live
# 断点续跑（成功阶段复用，失败/未完成阶段重跑）
.\.venv\Scripts\python.exe scripts/run_tasks.py --resume <run_id>
# 启动 Cytoscape（网络阶段需要 CyREST 在 127.0.0.1:1234）
local\tools\Cytoscape-3.10.0\Cytoscape.exe
```

## 7. 已知坑（都踩过，别再踩）

- **Cloudflare 拦无头浏览器**（GeneCards headless 403；OMIM 连环人机验证）：在线采集必须 headed 有窗口；人机验证留人工窗口，不绕过。
- **Agent 会话输入上限 1MB**：大结果集不能整个塞进 prompt——引擎已有 `_bounded_evidence` 裁剪（计数+样例）。
- **API 严格 schema**：结构化输出所有字段必须 required（可选语义用 `["type","null"]`）。
- **`codex exec resume`**：无 `-p`/`--sandbox` 参数；首会话不能用 `--ephemeral`（否则无档可续）。
- **Venny 大名单**：>500 基因用 JS 赋值（`fill()` 会被页面轮询阻塞）。
- **任务快照冻结**：`--resume` 用创建时的任务内容；改配置要新建运行。
- Windows 下杀毒可能造成文件占用：写 JSON/归档已带重试。

## 8. 待办（按优先级）

1. ~~DAVID 正式富集~~（2026-09-20 已完成：659/659 识别，7,558 条，显著 1,232）
2. 甲状腺癌补充流程真实运行：`python -m pharm_demo.supplement_smoke` 参考，正式输入=五病批次的 Thyroid cancer 子集 + 主交集 659
3. 原文 995/434 与现结果（13,392/待算癌症子集）的数量级差异：组内/医院讨论（数据版本 5.26→6.1）
4. OMIM morbidmap（邮件申请中）到位后升级 OMIM 数据
5. 核心七药/跨朝代聚类输入（补充流程可选输入）
6. 组会汇报：桌面《项目进展汇报-2026-09-19.md》
