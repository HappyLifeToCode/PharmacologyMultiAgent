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

**confidence 字段**（result.json 每候选）：`{value, components: {match_score, input_coverage, disease_coverage, evidence_quality}, formula_version: "heuristic_v2", note}`——程序计算的透明启发式，固定权重 0.3/0.3/0.2/0.2，组件 null 剔除归一，零匹配 value=0.0；非统计检验，固定顺序非排名。candidates.csv 与 report.md 含同名列与声明。

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
| handoff.json（每 attempt） | run_id、task_id、agent_role、recorded_at、status、summary、blockers、findings、artifacts 及哈希；可选 agent_review、guidance、confidence 相关产物索引 |
| events.jsonl | timestamp、role、type、message；含 stage.*、agent.started/completed/error、data.unavailable、agents.unavailable、run.completed |

同一时刻一个完整运行（`runs/.runner.lock`）；异常留下的锁先核验进程再处理。

## 任务输入与 API

- `POST /api/tasks`：formula/herbs/research_notes/batman_threshold/mode/**agents**（布尔；live 默认 true、fixture 强制 false）。
- `POST /api/run`：task_id + 可选 mode。
- `POST /api/analysis`：`{discovery_run_id, disease, research_notes?, agents?, string_source?, string_local_dir?, network_topology?, david_enrichment?}`；校验来源 run 存在、是 discovery、disease_reverse succeeded、disease 在候选清单内。
- `GET /api/formulas`：内置方剂组成与 BATMAN 候选名。
- `POST /api/agent/forward`：正向 Agent 对话查询，`text`、`formula` 与 `herbs` 三种输入只能选择一种。对话框使用 `{"text":"温经汤","top_k":10}`；也支持显式 `{"formula":"温经汤"}` 或原接口 `{"herbs":["白芍"],"top_k":10,"batman_threshold":0.84}`。`text` 为非空字符串（最多 10000 字符），后端按换行、逗号、顿号、分号拆分药材，识别单个内置方剂名并展开；已识别方剂与其他名称混合时拒绝。`formula` 必须为已收录名称，未知方剂拒绝，不猜测组成。`herbs` 为 1—30 个名称，`top_k` 为 1—100。结果写入 `local/agent/forward/<run_id>/`，不进入旧 discovery manifest。
- 正向结果的 `input` 保留原始 `text`、识别出的 `formula`、`formula_source`、展开后的 `herbs` 与实际参数。内置方剂来源标记为 `standard_reference_pending_user_confirmation`。`resolved_herbs` 逐味记录 `canonical`、有序 `batman_candidates`、实际 `batman_name`、`matched`、`compound_count` 和 `relation_count`；`matched` 表示 BATMAN 成分目录命中，不等于临床证据。未命中项继续记录于 `unmatched_herbs`；返回本次 `run_id`、BATMAN 靶点、候选疾病、来源证据和 `heuristic_v2` 组件。
- 正向聚合逐行读取冻结的来源文件，先汇总全部合格候选并排序，再仅加载返回候选的逐条完整证据。`candidate_count` 是截断前的候选数，`candidates` 仍按用户 `top_k` 截断；`evidence_row_count` 保留重复来源行计数，`matched_count` 仍按基因去重。完整 Open Targets 采集文件继续保存于本次目录 `open_targets/associations.csv`，不因只展示前 K 项而删减。
- 产物下载 `GET /artifacts/{run_id}/{path}`：仅 manifest 登记且通过 public_artifact 白名单的文件；提示词、模型原始响应、私有日志不公开（Agent 会话仅 agent/execution.json 公开）。

### 正向 Agent 结果边界

- Open Targets 的 `associationScore`、Monarch 的 ClinGen/OMIM 证据和 BATMAN 的 known/predicted 关系分别保留，不跨来源相加。
- `confidence` 是透明启发式候选排序分数，包含匹配靶点数、输入覆盖率和 BATMAN 证据质量；它不是统计检验、治疗有效率或疗效概率。
- 正向查询当前不接入临床治疗证据、网络分析或富集分析；`scientific_complete` 保持 `false`。

## Open Targets 查询任务与兼容

`online_collect`（布尔，默认 false）启用第三阶段数据查询；`online_sources` 仅支持 `["open_targets"]`；`online_diseases` 为可选范围，空列表使用 BATMAN 靶点。默认 local 模式读取快照，只有显式选择 online 才调用 GraphQL。原字段名称保留，local 不联网。

### Open Targets 疾病实体与候选分层（2026-10 确认）

- `disease_id`（Open Targets 原始 ID）是疾病实体主键；`disease_name` 仅用于展示；`exactSynonyms` 仅用于别名搜索与展示，不改变实体身份。
- MONDO 只作为可选 `mondo_id` 映射字段（direct / dbxref / conflict / unmapped），不设为所有疾病的主键；没有 MONDO 映射的 EFO、HP、OBA、Orphanet 等实体必须保留。26.09 审计：45,896 条记录无 MONDO 冲突、无多记录共指（见 `local/open_targets_mondo_audit_20261009/`）。
- 不按英文名称相似度自动合并疾病（heart failure 与 congestive heart failure、breast cancer 与 breast carcinoma 为不同 ID，必须并存）。
- 父子疾病仅标记关系（`parent_candidate_ids` / `child_candidate_ids`），不合并分数，不删除任一实体。
- 候选分层：完整层（全部候选与全部原始 direct association）→ 基础层（associationScore > 0.2）→ 严格层（>= 0.6）→ 展示层（entity_type == disease 后排序取 Top-50）；排序字段依次为 strict_supported、strict_support_target_count、support_target_count、max_association_score、score_sum、disease_id；Top-50 在 disease_id 聚合与实体过滤之后生成。
- `top_k_per_target` / `top_k_per_disease` / `min_score` 只用于候选展示与排序，不删除完整层原始关联；启用这些过滤的批次 provenance 必须标记 `complete=false` 且 `raw_rows_complete=false`，不得称为完整层。
- `associationScore` 是 Open Targets 关联强度分数，不是疗效概率，也不是临床适应症证据。

旧数据库采集与 `/api/assist/*`、`/ws/assist` 已移除。旧任务启用不支持来源时明确 blocked；历史任务与运行不回写。

### Monarch Initiative（OMIM 功能替代来源，2026-10-12 接入）

- 定位：Monarch KG 是本项目 OMIM 所需功能（基因—疾病、HPO 表型、遗传方式、PMID/ECO 证据）的替代来源；Open Targets 仍是主疾病关联来源，associationScore 与 Top-50 排序不受 Monarch 影响。
- 默认关闭：`PHARM_MONARCH_ENABLED=true` 或任务 `monarch_enabled=true` 才启用；数据目录解析顺序为任务 `monarch_data_dir` > `PHARM_MONARCH_DATA_DIR` > `configs/monarch_data.local.json` > `data/monarch/<最新版本目录>`。无在线回退，缺文件明确报错。
- 匹配规则：基因为 gene_id 命名空间 > HGNC symbol > UniProt > 记录 unresolved；疾病为 candidate.mondo_id > Monarch 明确 MONDO ID > 其他明确本体 ID > 记录 unresolved。禁止按英文名称模糊合并；MONDO 是跨库映射字段，不是所有疾病的强制主键；无 MONDO 映射的记录保留。
- 当前只对 Top-50 候选做证据补充；产物独立保存：`monarch_gene_disease.csv`、`monarch_hpo.csv`、`monarch_inheritance.csv`、`monarch_publications.csv`、`monarch_unresolved.csv`、`monarch_provenance.json`（每条 HPO/遗传方式/PMID 单独一行），登记进运行 manifest 与下载白名单。
- Monarch 证据不参与 Open Targets 分数计算与候选排序，不能解释为临床疗效或诊断结论。

`heuristic_v2` 沿用权重，evidence_quality 仅取 BATMAN known 占比；原始关联分值不参与评分，缺失组件剔除。历史 v1 报告保留原版本。

## 归档布局

live 运行逐阶段归档到 `data/pharm/<归档名>/`（方名或 `custom_<task_id>`，analysis 继承来源任务）：

```text
01_preflight/  02_herb/  03_reverse/  04_review/
05_analysis/shared/  05_analysis/network/  05_analysis/enrich/  05_analysis/review/
  run_<run_id>/attempt_<nn>/
    _archive.json    批次哈希、任务参数、阶段状态、计数、来源访问日期记录
```

每批不可变：相同哈希复用，不同哈希拒绝覆盖；`_meta.md` 追加流水账；归档时间不可冒充数据库访问时间；partial/blocked 尝试同样归档；fixture 禁止进入；归档≠验收。
