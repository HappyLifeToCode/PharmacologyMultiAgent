# 项目交接说明（给下一位协作者 / 大模型）

> 目的：让没有本项目上下文的人（或 AI）能快速接手。阅读顺序：本文件 → docs/PROJECT_STATUS.md → docs/DATA_CONTRACT.md。
> 更新日期：2026-09-24（补充在线采集人机协助与静态验证页画布修复）。项目根目录即本文件所在仓库。

## 2026-09-23 济川煎反查口径修正

济川煎真实运行 `2026-09-23T103851_0000_82015e` 的疾病反查程序产物本身保留完整：输入靶点 2337 个，命中唯一靶点 2178 个，未命中 159 个，证据行 7472 行。此前疾病发现 Agent 将 `matched_count`（去重后的唯一匹配基因数）与 GeneCards/OMIM 的原始 `evidence_row_count` 当作必须相等，导致多个疾病被误报不一致并使阶段降为 partial。

现行口径在结果和 Agent 提示词中显式区分：`matched_count` 与 `unique_evidence_gene_count` 都是唯一基因数；`evidence_row_count` 是原始证据行数，同一基因对应多条来源记录时可以更大。Agent 只核对唯一基因数与程序记录，不再要求两种数量相等。历史运行不改写；修正后的新运行应重新生成。

本机 BATMAN 数据获取日期记录为 2026-09-18，Agent 默认超时 900 秒；STRING/CytoNCA/DAVID 已有真实工程冒烟证据，但仍不代表正式科研验收，`scientific_complete` 继续为 false。

## 2026-09-24 人机协助静态验证页修复

人机协助桥 `pharm/assist/bridge.py` 已修复静态 Cloudflare/OMIM 验证页黑屏问题。此前桥接只依赖 CDP screencast 的重绘事件，页面停留在静态验证页时可能没有首帧；现在在超过 1 秒未收到 CDP 帧时，自动调用 Playwright 截图发布 JPEG 兜底帧，并在收到正常 screencast 帧后恢复按帧推送。生产 headed Chromium 原始窗口移到屏幕外，用户只在工作台 canvas 中操作。该改动只影响画面显示，不绕过验证码或改变采集权限。

本次针对性验证：`tests/test_assist.py`、`tests/test_server.py`、`tests/test_genecards_online.py`、`tests/test_omim_online.py` 共 **22 passed**。提交为 `1927fb6`（`修复人机协助静态页面黑屏`），已推送 `main`。

当前边界保持不变：GeneCards/OMIM 采集器已经能够登记协助请求、等待用户完成验证并继续；用户必须在右侧内嵌协助画布中操作，完成后验证 cookies/storage state 会同步回原采集器，避免原页面重复验证。discovery 主流水线尚未自动编排在线采集、产物导入和 resume，仍需后续实现。

## 1. 项目是什么

中药复方反向疾病发现工具。输入方剂（内置四方：温经汤/半夏白术天麻汤/济川煎/桃核承气汤，或自由药材组合）→ BATMAN-TCM v2.0 本地全量文件解析药材—成分—靶点 → 本地 SQLite 疾病索引反查候选疾病关联（带启发式置信度 heuristic_v1）→ 程序验收。对候选疾病可发起**机制分析链路**（pipeline="analysis"）：共同靶点 → STRING/CytoNCA 网络 → DAVID 富集 → 验收。

**多 Agent 编排**：live 运行默认六个独立 Codex 会话（agents/*.md）核验程序产物；程序做全部计算，Agent 只核验与解释，结论不改程序产物。旧方向（药物×疾病交集的多 Agent 主流程）已于 2026-09-22 重构废弃，见 Git 历史（commit `6779567` 之前）。

## 2. 当前状态

- 双流水线均完成并经端到端工程验证（fixture 全链、合成数据 live、mock Agent 会话）；`pytest tests/ -q`：**209 passed, 3 skipped**。
- 如实未做：六会话**完整**真实 live 运行（单次真实 Codex 会话冒烟已于 2026-09-22 通过，gpt-5.6-luna，证据 runs/diagnostics/model_smoke_20260922.json）；STRING 桥接/CytoNCA 计算/DAVID 正式提交未验证（可达性 2026-09-22 实测：STRING API 200、DAVID 首页 200、Cytoscape 在 D:/Tools，runs/diagnostics/dependency_smoke_20260922.json）；本机无研究数据。
- 未实现：在线采集编排（agents/runtime.py 的浏览器采集路径预留）；四方组成出处待用户确认；疾病库覆盖取决于批次。

## 3. 关键资产位置

| 内容 | 位置 |
|---|---|
| 双流水线 DAG | `pharm/pipeline/scheduler.py`（GRAPHS：discovery / analysis） |
| 执行引擎 | `pharm/pipeline/engine.py`（Runner：签名/reuse/finish/八阶段函数/Agent 编排） |
| 核验角色提示词 | `agents/*.md`（六角色；内容哈希进阶段签名） |
| Codex 适配器 | `pharm/agents/runtime.py`（execute/prepare_home；RESULT_SCHEMA 含可选 confidence） |
| 置信度 | `pharm/discovery/query.py`（CONFIDENCE_WEIGHTS/_evidence_maps/_confidence/apply_confidence） |
| 网络模块 | `pharm/network/`（string_local 选路、cytoscape 桥、metrics 度值核对）、`integrations/cytonca_bridge/` |
| 富集模块 | `pharm/enrich/david.py`（validate_config 门禁 + run_david） |
| 四方组成 | `pharm/batman/formulas.py`（source 待确认） |
| 人机协助 | `pharm/assist/bridge.py`（WS 协议见模块文档字符串） |
| Web 工作台 | `server/app.py` + `server/static/{index.html,app.js,style.css}` |
| BATMAN 数据（本机无） | 团队机器 `data/batman/v2.0/`；配置 `configs/batman_data.local.json` |
| 疾病索引（本机无） | 默认 `local/discovery/disease_index.sqlite`；配置 `configs/discovery_data.local.json` |
| STRING 本地数据（可选） | 配置 `configs/string_data.local.json`（模板 string_data.example.json）；未配置时 API 回退（触网） |

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
# 直接反查（不经过 pipeline）
.\.venv\Scripts\python.exe -m pharm.discovery.query query --genes TP53 EGFR --output local/discovery/my-lookup
# 机制分析（编程入口；Web 上点候选疾病行的"机制分析"按钮）
#   engine.start(pipeline="analysis", analysis={"discovery_run_id": <run_id>, "disease": <候选疾病>})
# 构建 CytoNCA 桥接插件（需要本机 Cytoscape 与 JDK）
.\.venv\Scripts\python.exe scripts/build_cytonca_bridge.py --cytoscape-home <Cytoscape目录> --jdk-home <JDK目录> --output local/cytonca-bridge.jar
# 离线 HTML 报告
.\.venv\Scripts\python.exe scripts/export_report.py --run <run_id>
# 环境检查（依赖/浏览器/profile 存在性，不验证权限）
.\.venv\Scripts\python.exe scripts/doctor.py
```

## 5. 硬性约定（违反会被程序拦截或评审打回）

1. **不编造、不顶替**：缺数据 = blocked + guidance + 证据保留；fixture 全程标注 synthetic_engineering、不进归档、强制不调模型。
2. **参数即身份**（task_id 哈希）；**代码即签名**：input_signature 含任务、runtime、`pharm/**/*.py`、数据文件与 `agents/*.md` 提示词哈希——改代码或改提示词后 resume 重跑属预期。
3. **程序计算，Agent 核验**：Agent 结论不改程序产物；failed 只降级 partial；会话错误记 agent_review.error。
4. **门禁**：STRING 拓扑（network_topology）与 DAVID（david_enrichment）未显式确认即 blocked/partial，不冒充完成；CytoNCA 未成功时度值来源如实标 NetworkX。
5. **scientific_complete 恒 false**；置信度是启发式（heuristic_v1），非统计检验；固定顺序非疗效排名。
6. 直推 `main`，**push 前跑 `pytest tests/ -q`**。

## 6. 已知坑（都踩过，别再踩）

- **sync Playwright 不允许跨线程**；CDP screencast 只在重绘时出帧且必须逐帧 ack；输入回传用 page.mouse/keyboard（裸 CDP 键码不可靠）。
- **execute_graph 就绪顺序是注册表声明序**（曾按字母序导致 enrichment 先于 network）——新增流水线时注意节点声明顺序即调度顺序。
- **analysis 缺省继承来源运行模式**：fixture 来源 → fixture 分析（证据类型一致，且演示不触网）；要 live 分析须显式 `mode="live"`。
- **Agent 证据裁剪**：`_bounded` 计数+样例，大结果集不进 prompt；Agent 会话文件写 `<attempt>/agent/`，仅 execution.json 公开。
- **fixture 强制 agents=false**：任务传 agents=true 也会被压为 false（测试断言）。
- **DAVID max_list_size 默认 400**：交集大的分析需显式配置并接受分批/另行适配。
- Windows 下杀毒可能造成文件占用：写 JSON/归档已带重试。
- Agent 核验默认超时为 900 秒（15 分钟），可用环境变量 `PHARM_AGENT_TIMEOUT` 临时覆盖；超时会保留程序产物并将阶段标记为 `partial`。

## 7. 待办（按优先级）

1. **数据同步**：BATMAN v2.0 全量文件与疾病索引批次到本机 → 六会话完整真实 live 运行 + 人工抽检（单次会话冒烟 2026-09-22 已通过）。
2. **STRING/CytoNCA/DAVID 正式冒烟**：可达性 2026-09-22 已核验（200/200/软件在）；小样本桥接与正式提交留证仍待做。
3. **数据同步**：BATMAN v2.0 全量文件与疾病索引批次到本机 → 真实 live 验证 + 人工抽检。
4. **四方组成确认**：formulas.py 的 source 标注 pending_user_confirmation，待用户/文献确认。
5. **在线采集编排**（预留）：Agent 采集 → 人机协助接管 → 产物导入 → resume。
6. push 到远程前全量 pytest。
