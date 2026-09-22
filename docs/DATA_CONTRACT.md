# 数据交接约定

双流水线（workflow_version=4）的产物、状态语义与归档布局。旧九阶段契约已随重构废弃（Git 历史可查）。

## 流水线与 manifest

- `pipeline`：`discovery`（四阶段反向发现）或 `analysis`（候选疾病机制分析，经 POST /api/analysis {discovery_run_id, disease, ...} 创建；任务快照继承来源任务的方剂/药材/阈值，记录 `analysis: {source_run_id, disease}`，缺省继承来源运行模式）。
- manifest.json：任务快照、mode、pipeline、workflow_version=4、各阶段状态/attempt/input_signature/产物索引与 SHA-256、verified_targets、metrics、scientific_complete（恒 false）、report 路径；analysis 另含 analysis 来源信息。v3 及更早 manifest 拒绝 resume。
- **参数即身份**（task_id 哈希）；**代码即签名**：input_signature 含任务、`configs/runtime.json`、`pharm/**/*.py`、相关数据文件哈希，以及（agents 启用时）`agents/<角色>.md` 提示词哈希。resume 只复用签名与产物哈希全一致的成功阶段；验收阶段每次重核。

## discovery 四阶段产物

| 阶段 | 产物 | 内容 |
|---|---|---|
| preflight | availability.json | 每项本地数据 available=true/false、细节、缺失时 guidance；含疾病索引疾病数与样例 |
| herb_targets | targets.json | herbs、threshold、relations、genes、unmatched_herbs、rejected_symbols、per_herb、source_counts、provenance |
| disease_reverse | reverse/result.json、candidates.csv、evidence.csv、report.md、manifest.json | 逐疾病 matched/coverage/**confidence**/逐条证据、未命中靶点、索引 metadata、database 路径与 SHA-256；超 3000 靶点分块记录 chunking |
| review | verification.json + 运行根 report.md | 程序核对结果；报告含候选疾病概览（含置信度）与局限声明 |

**confidence 字段**（result.json 每候选）：`{value, components: {match_score, input_coverage, disease_coverage, evidence_quality}, formula_version: "heuristic_v1", note}`——程序计算的透明启发式，固定权重 0.3/0.3/0.2/0.2，组件 null 剔除归一，零匹配 value=0.0；非统计检验，固定顺序非排名。candidates.csv 与 report.md 含同名列与声明。

## analysis 四阶段产物

| 阶段 | 产物 | 内容 |
|---|---|---|
| shared_targets | shared_targets.json | disease、genes（来源靶点∩疾病关联基因）、counts、source（来源 run_id、targets/索引路径与 SHA-256）；空交集如实 succeeded |
| network | network.json、degrees.csv（live 可选 cytoscape/*） | 节点/边/度值表、method（NetworkX degree 或 CytoNCA 桥，未成功如实标 NetworkX）、provenance（映射/未映射/歧义/孤立）；空输入 blocked；CytoNCA 未成功 partial |
| enrichment | fixture：enrichment_fixture.json（非 DAVID 声明）；live：david/*（执行记录、全量与显著表、enrichment_david.json） | 未配置 david_enrichment 即 blocked + limitation；零显著如实 |
| analysis_review | verification.json + 运行根 report.md | 共同靶点数、网络规模与方法、Degree Top、富集显著数、局限声明 |

## 状态语义

- 阶段状态：pending / running / succeeded / blocked / failed / partial。blocked/failed 也是终态；调度器在依赖到达终态后放行下游，是否继续由阶段自行判断。
- 运行状态：running / succeeded / partial（存在 blocked/partial）/ failed。
- **多 Agent 核验**：live + `agents=true`（live 默认；fixture 强制 false）时，程序计算成功后启动对应 Codex 会话（六角色，agents/*.md）。环境不可用（无 CLI/profile）→ 整个运行 failed 并提示 `agents=false` 调试开关；agents=false 的 live 为纯程序运行，报告注明"无 Agent 核验"。

**agent_review 字段**（阶段 handoff.json 新增）：

```json
{"agent_role": "batman_targets", "session_id": "...", "status": "succeeded",
 "summary": "...", "confidence": "high|medium|low", "findings": ["..."],
 "model": "...", "elapsed_seconds": 0.1}
```

Agent 报 failed → 阶段降 partial + blocker（程序产物保留）；会话错误时为 `{"agent_role": ..., "error": "..."}` 并在 findings 注明"Agent 核验未完成"，阶段状态由程序结果决定。RESULT_SCHEMA 的 confidence 为可选自评字段。

## 交接与运行记录

| 文件 | 内容 |
|---|---|
| manifest.json | 见上"流水线与 manifest" |
| handoff.json（每 attempt） | run_id、task_id、agent_role、recorded_at、status、summary、blockers、findings、artifacts 及哈希；可选 agent_review、assist 标记、confidence 相关产物索引 |
| events.jsonl | timestamp、role、type、message；含 stage.*、agent.started/completed/error、assist_requested、agents.unavailable、run.completed |

同一时刻一个完整运行（`runs/.runner.lock`）；异常留下的锁先核验进程再处理。

## 任务输入与 API

- `POST /api/tasks`：formula/herbs/research_notes/batman_threshold/mode/**agents**（布尔；live 默认 true、fixture 强制 false）。
- `POST /api/run`：task_id + 可选 mode。
- `POST /api/analysis`：`{discovery_run_id, disease, research_notes?, agents?, string_source?, string_local_dir?, network_topology?, david_enrichment?}`；校验来源 run 存在、是 discovery、disease_reverse succeeded、disease 在候选清单内。
- `GET /api/formulas`：内置方剂组成与 BATMAN 候选名。
- 产物下载 `GET /artifacts/{run_id}/{path}`：仅 manifest 登记且通过 public_artifact 白名单的文件；提示词、模型原始响应、私有日志不公开（Agent 会话仅 agent/execution.json 公开）。

## 人机协助桥（WS /ws/assist）

协议全文见 `pharm/assist/bridge.py` 模块文档字符串。要点：下行 frame 文本帧+二进制 JPEG、guidance、state、error；上行相对坐标鼠标事件、wheel、key、text。状态机 running→waiting_human→done→closed；单会话（重复启动 409）；生产路径 headed，失败抛 AssistUnavailable 不降级。

## 归档布局

live 运行逐阶段归档到 `data/pharm/<归档名>/`（方名或 `custom_<task_id>`，analysis 继承来源任务）：

```text
01_preflight/  02_herb/  03_reverse/  04_review/
05_analysis/shared/  05_analysis/network/  05_analysis/enrich/  05_analysis/review/
  run_<run_id>/attempt_<nn>/
    _archive.json    批次哈希、任务参数、阶段状态、计数、来源访问日期记录
```

每批不可变：相同哈希复用，不同哈希拒绝覆盖；`_meta.md` 追加流水账；归档时间不可冒充数据库访问时间；partial/blocked 尝试同样归档；fixture 禁止进入；归档≠验收。
