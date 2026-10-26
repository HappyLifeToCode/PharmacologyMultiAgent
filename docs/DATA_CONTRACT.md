# 数据交接约定

2026-10-26：文献任务状态新增browser_mode（background/interactive）及browser_fallback（http_403或null）。真实主文档403可在同一任务内自动切换一次有头模式，不新增研究POST、采集ID或修改范围/模型；续跑沿用该模式。子资源、iframe、429或DOM等待超时不会触发该切换。私有search/article-diagnostic仅记录主机、路径、HTTP状态与原因，URL查询参数不进入诊断文件或公开状态。原失败任务兼容保留，不重新生成旧成功快照。

双流水线（workflow_version=4）的产物、状态语义与归档布局。旧九阶段契约已随重构废弃（Git 历史可查）。

## 疾病到药材会话查询

正反向 HTTP 查询共用实际进度与成功结果缓存。状态接口增加可空 `progress`，研究结果增加 `execution`（`cache_hit`、`source_fingerprint`，命中时还有 `reused_from`、`computed_at`）。缓存命中使用新的运行编号，保留原候选与证据；旧来源 CSV 继续留在被引用的原运行，新正向流式结果保留源Parquet元数据及证据，不伪装为重新采集。完整字段及失效规则见 [查询执行说明](QUERY_EXECUTION.md)。

独立于旧 discovery 的疾病到药材入口为 `POST /api/agent/reverse`，返回 `workflow=reverse_disease_target_herb_v1`、`direction=reverse`、`scientific_complete=false`，归档于 `local/agent/reverse/reverse_<id>/result.json`。`/reverse/resolve` 只解析名称，歧义需明确选择；原始 Open Targets ID 与 MONDO 对应保留，父子疾病不自动合并。

BATMAN 派生索引由 herbs、herb_compounds、compound_targets 构成，只纳入 known，保留原始药材行号作为身份、文件哈希、拒绝记录和版本指纹。显示别名不替换查询条目。独立构建 CLI 和 HTTP 字段见 [疾病到药材](DISEASE_TO_HERB.md)。

结果 `disease_evidence` 保存完整已解析疾病靶点的逐来源记录，候选 `evidence` 保存靶点—成分—药材路径。`disease_target_count` 和 `matched_input_count` 不因 Top-K 改变；`disease_coverage` 分母为本次可用来源的完整已解析靶点，`herb_coverage` 分母为该药材通过项目符号校验的全部 BATMAN known 原符号。多对一基因映射保留批准符号/原符号两种计数；未接受原符号在 `excluded_known_symbols` 单独展示。

来源缺失标 partial、`target_scope_complete=false`，零匹配返回空候选，两源不可用则失败。工程排序为唯一匹配靶点数降序、药材 ID 升序，不使用正向 confidence，不提供治疗概率。请求跟踪、页签接续和关闭停止复用现有任务管理器；反向与正向归档接口分别校验运行编号前缀。

## 流水线与 manifest

- `pipeline`：`discovery`（四阶段反向发现）或 `analysis`（候选疾病机制分析，经 POST /api/analysis {discovery_run_id, disease, ...} 创建；任务快照继承来源任务的方剂/药材/阈值，记录 `analysis: {source_run_id, disease}`，缺省继承来源运行模式）。
- manifest.json：任务快照、mode、pipeline、workflow_version=4、各阶段状态/attempt/input_signature/产物索引与 SHA-256、verified_targets、metrics、scientific_complete（恒 false）、report 路径；analysis 另含 analysis 来源信息。v3 及更早 manifest 拒绝 resume。
- **参数即身份**（task_id 哈希）；**代码即签名**：input_signature 含任务、`configs/runtime.json`、`pharm/**/*.py`、相关数据文件哈希，以及（agents 启用时）`agents/<角色>.md` 提示词哈希。resume 只复用签名与产物哈希全一致的成功阶段；验收阶段每次重核。

## discovery 四阶段产物

| 阶段 | 产物 | 内容 |
|---|---|---|
| preflight | availability.json | 每项本地数据 available=true/false、细节、缺失时 guidance；含疾病索引疾病数与样例 |
| herb_targets | targets.json | herbs、threshold、relations、genes、unmatched_herbs、rejected_symbols、per_herb、source_counts、provenance |
| disease_reverse | reverse/result.json、summary.json、candidates_topk.csv、topk_target_evidence.csv、topk_hierarchy.json、report.md、manifest.json | 默认 compact：保存可配置 Top-K 候选及其逐条支持证据、完整/基础/严格层计数、未命中靶点、索引 metadata、database 路径与 SHA-256；超 3000 靶点分块记录 chunking |
| review | verification.json + 运行根 report.md | 程序核对结果；报告含候选疾病概览（含置信度）与局限声明 |

**confidence 字段**（result.json 每个 Top-K 候选）：`{value, components: {match_score, input_coverage, disease_coverage, evidence_quality}, formula_version: "heuristic_v2", note}`——程序计算的透明启发式，固定权重 0.3/0.3/0.2/0.2，组件 null 剔除归一，零匹配 value=0.0；非统计检验。`candidates_topk.csv` 与 report.md 含同名列与声明。

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
- `GET /api/formulas`：保留旧任务面板的四个内置方剂组成与 BATMAN 候选名。
- `POST /api/formulas/resolve`：读取本地方剂表并核对组成，不启动药理计算。接受 `text` / `formula` / `herbs`（三选一）、可选 `formula_variant_id`、`formula_herbs`、`formula_fingerprint` 和 `use_saved`。返回 `ready` 或 `selection_required`、组成候选、原表行号、原名/药材映射、未匹配项和待提交的 `query_input`。组成 ID 由归一名称及药材组成生成，行顺序不决定版本身份；同名同组成合并，不同组成保留。
- `POST /api/formulas/preference`：明确选择组成 ID 后，将默认组成及可选编辑清单保存到本机 `local/agent/formula_preferences.json`；选择是输入偏好，不是科学签字。保存数据指纹（方剂/药材/别名表哈希及内置表）；数据变化后重新核对。新方剂没有任何明确匹配的药材时必须先修改清单。中文/拼音及原表明确给出的括号别名共用已选版本。
- `POST /api/agent/forward`：正向 Agent 对话查询，`text`、`formula` 与 `herbs` 三种输入只能选择一种。对话框使用 `{"text":"温经汤","top_k":10}`；也支持显式 `{"formula":"温经汤"}` 或原接口 `{"herbs":["白芍"],"top_k":10,"batman_threshold":0.84}`。`text` 最多 10000 字符，后端识别单个本地收录方剂名并展开，或按换行、逗号、顿号、分号拆分药材；混合方剂/药材拒绝。可追加经组成核对返回的 `formula_variant_id`、`formula_herbs`（编辑清单，1—100 个名称）和 `formula_fingerprint`；有歧义且未选组成时返回 HTTP 409 的结构化 `detail`，不创建计算进程；数据指纹变化时阻止旧选择进入计算。未知方剂拒绝，不猜测组成。直接 `herbs` 为 1—30 个名称，`top_k` 为 1—100。结果写入 `local/agent/forward/<run_id>/`，不进入旧 discovery manifest。
- 正向结果的 `input` 保留原始 `text`、识别出的 `formula`、`formula_source`、展开后的 `herbs` 与实际参数。内置来源保持 `standard_reference_pending_user_confirmation`；本地方剂表为 `batman_local_formula_table`。新增 `formula_resolution` 保留组成 ID、原记录组成/行号、实际原名映射、编辑类型、未匹配项、数据文件哈希及 `reference:null`（原表无出处）。编辑清单不覆盖原始组成。项目显式别名表仅列常用简称，规则随结果保留，不猜测 `SHAO YAO` 的芍药类别、不合并炮制形式；原四方既有候选规则保留。`resolved_herbs` 逐味记录 `canonical`、候选名、实际命中名、成分与关系数；`matched` 仅表示目录命中。未匹配项不被丢弃，`unmatched_herbs` 继续保留；历史结果不回写。
- 正向交互查询直接流式读取本地 Open Targets Parquet：第一遍汇总全部合格候选并排序，第二遍只读取返回候选的逐条完整证据，不再为每次运行生成 GB 级 `open_targets/associations.csv`。`candidate_count` 是截断前的候选数，`candidates` 仍按用户 `top_k` 截断；`evidence_row_count` 保留重复来源行计数，`matched_count` 仍按基因去重。用于反向查询、共享索引和正式导入的显式采集接口仍保留通用 `associations.csv` 合同。
- 产物下载 `GET /artifacts/{run_id}/{path}`：仅 manifest 登记且通过 public_artifact 白名单的文件；提示词、模型原始响应、私有日志不公开（Agent 会话仅 agent/execution.json 公开）。

### 正向 Agent 结果边界

2026-10-26采集校验修复：详情record_labels/note保留题录/全文替换/现行等已知控件标记，题名去除附标后仍需document.title精确对应。总数仅从可见countPageDiv读取，分页等待页码与整页书目稳定；result_total_changed与result_total_unreadable分别诊断，失败清单记录expected/observed总数、failed_page，未完成不发布。原扫描存于任务私有scan_history，状态runtime_version标记后端实际版本；原研究请求、范围、模型、排序和冻结快照合同不变。

2026-10-26自动文献证据：新查询未保存偏好时默认启用知网文献流程，显式保存`false`可关闭；首页正反向共用该新查询默认值，工作台正向一致。旧消息及旧工作台归档只有明确保存`enabled=true`才自动接续，缺少启用标记的历史结果不自动追采，仍可手动启用。靶点结果仍先返回，完成后展示知网原记录链接、标题/摘要引文和独立排序；原研究body、缓存键、归档及排名不变。

2026-10-25后台模式：正常采集自动使用无界面专用Edge，session.json新增mode（background/interactive）及可选私有manual_url，仅保存在项目本机资料区。POST /api/literature/browser/open仍只接受{}，只在明确请求时切换到可见窗口；活动采集返回409，续跑恢复后台。新增cnki_login_required诊断，隐藏登录模板不触发人工提示，真实可见登录且无可读结果时停止。文献数据及原研究请求、缓存键、排序合同不变。

2026-10-25：正常网页采集由Python Playwright顺序执行，模型仅按标题摘要抽取。collection_scan新增title_only状态：网站仅有节选时abstract=null、abstract_status=excerpt_only，节选保留abstract_excerpt、说明保留note，关系分析只用标题；no_abstract另表无文字摘要。所有位置均有可核对记录才可标全量完成，不意味着每条都有完整摘要。原句T/A编号只在模型输入使用，发布时复制同篇原句到原evidence字段，旧triples合同不变。新语料的is_modified同时核对标题/摘要文字，模糊加减信息记unknown。新增POST /api/literature/browser/open只接受{}、只连接项目专用本机会话，沿用本机及同源限制。原研究请求、ID、缓存、归档及排序公式不变。

2026-10-24网页接入：文献开关默认关闭，首页正反向和工作台正向在靶点结果返回后调用独立flow接口；已有快照优先恢复，不自动重采。只有首次not_started才resolve，后续轮询/刷新只读状态；失败需显式resume，状态连接故障不取消任务。工作台浏览器元数据新增最近完成结果编号及原文献开关/快照，刷新只GET原归档，不新研究POST。文献药材/方剂及全部证据不截断，原数据库数组/排序/请求body和缓存键不变。

2026-10-24后续：新增`POST /api/agent/{direction}/{run_id}/literature/flow/resolve`及对应GET状态、POST cancel/resume。本地文献优先；缺关系文件仅做local_analysis；无文献才后台采集，完成后生成独立快照。重复请求/活动关键词复用，其他关键词排队，失败不自动重试；原靶点结果、请求body、缓存键及归档不改。反向`candidates`为药材原名、`formula_candidates`为方剂原名，两组完整返回，未自动映射BATMAN实体。前端尚未接入，暂不真实采集“四方汤”。详见[LITERATURE_FLOW.md](LITERATURE_FLOW.md)。

2026-10-24：独立采集CLI及标题摘要抽取默认模型为Codex `gpt-5.6-luna`。新任务`limit=null`表示关键词全部结果，正整数表示显式限量新增；任务保存模型和范围，旧限量任务续跑不改范围。`papers.json`新增collector/collection来源记录，全量成功须有可核对的collection_scan翻页清单。成功只表示采集时该关键词结果页已遍历，不表示知网全部相关论文已穷尽；失败或不完整任务不发布。只分析标题摘要，全部文献疾病关联均返回/展示，原篇数排序及数据库Top-K合同不变。未进行真实模型/知网联网验证。

2026-10-23后续：文献运行时改读本项目`data/cnki`（可用`PHARM_CNKI_DATA_DIR`配置），不再自动访问旧UI-Agent。数据包manifest登记相对路径、文件SHA及篇数，导入/打包只包含分析所需JSON。内置采集执行器和提示模板由CLI显式启动，网页不自动采集。原文献排序字段和旧快照不改，仅换直观文案及来源读取路径。交付步骤见[CNKI_DELIVERY.md](CNKI_DELIVERY.md)。

2026-10-23：新增可选本地文献接入验证，原forward请求体、缓存和研究数组不改。文献只通过独立`GET /api/agent/forward/{run_id}/literature`读取已有UI-Agent题录；成功快照独立保存于`local/agent/literature/<run_id>/`，`snapshot`参数恢复旧分析，包含原研究及语料实际哈希、映射与支撑句。浏览器只保存启用状态/快照ID，原研究JSON不回写。排序是可解释的标题摘要关联频次，不是疗效；未映射/歧义保留，不替换父病。本轮无在线采集或模型调用，详见[CNKI接入](CNKI_INTEGRATION.md)。

2026-10-22：首页和工作台的正向证据增加药材→成分→靶点→疾病交互图，仅连接当前归档的`herb_relations`与被选候选的`evidence`，不增加研究API或改写数据。精确基因符号连接，成分CID决定身份，缺ID不按名称合并；重复原始行在详情中保留。按单靶点、每组最多8条路径显示，并标总数/范围，支持来源筛选及当前图SVG导出。缺失证据保留缺口，known/predicted按归档原值区分。原JSON导出、疾病排序和科学状态不变，详见[证据可视化](EVIDENCE_VISUALIZATION.md)。

合并远端后保留Open Targets基础/严格分层计数及layered_candidates完整参考（>0.2/≥0.6），仅OT分值参与此参考排序；默认仍遵循用户最新确认的support，背景仍为可选对照。分层参考不替换两种主视图。来源改为流式Parquet，旧CSV归档保留，新查询不生成GB级CSV。

正向新结果将大类单独返回 `broad_candidates`，默认 `candidates` 为按原支持度排序的非大类Top-K；`background_candidates` 独立保存完整候选集合的背景校正Top-K及逐条证据，页面切换不计算。`ranking.default_order=support`，`background_algorithm` 记录独立公式，科学验证状态仍待完成。原 `heuristic_v2` 分数不改写，反向不变；导出 `export_view` 只标记显示选择，不改写研究数组。字段、公式与初步文献对照见 [正向疾病排序](FORWARD_RANKING.md)。

药材映射的 `display_name` / `display_note` 仅用于中文显示及名称说明，不替换 `canonical` / `candidates` 指向的原始药材条目。原药材表中文名缺失但已有登记别名时补中文显示，仍用原记录检索成分；没有可靠标签的精确命中条目标“中文名缺失”，未命中标“待匹配”，两者区别展示。剂量/说明混入拼音字段时不自动清洗成确定药材。

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
- 候选分层：完整层（全部候选与全部原始 direct association）→ 基础层（associationScore > 0.2）→ 严格层（>= 0.6）→ 展示层（entity_type == disease 后排序取 Top-K）；排序字段依次为 strict_supported、strict_support_target_count、support_target_count、max_association_score、score_sum、disease_id；Top-K 在 disease_id 聚合与实体过滤之后生成。K 默认从 `configs/runtime.json` 的 `candidate_output_top_k` 读取（当前 50），可由任务同名字段或环境变量 `PHARM_CANDIDATE_OUTPUT_TOP_K` 覆盖（1—500），只影响持久化、报告和 UI，不截断完整计算。
- `configs/runtime.json` 默认 `output_retention=compact`：完整层、基础层、严格层参与计算和计数，但只持久化 `summary.json` 中的计数、Top-K 候选、Top-K 逐条证据及 Top-K 层级；不生成 GB 级 `result_full.json`、全量 `evidence.csv` 或完整分层 CSV。显式设置任务 `output_retention=audit` 或 `PHARM_OUTPUT_RETENTION=audit` 才保留这些全量产物。
- `top_k_per_target` / `top_k_per_disease` / `min_score` 只用于候选展示与排序，不删除完整层原始关联；启用这些过滤的批次 provenance 必须标记 `complete=false` 且 `raw_rows_complete=false`，不得称为完整层。
- `associationScore` 是 Open Targets 关联强度分数，不是疗效概率，也不是临床适应症证据。

旧数据库采集与 `/api/assist/*`、`/ws/assist` 已移除。旧任务启用不支持来源时明确 blocked；历史任务与运行不回写。

### Monarch Initiative（OMIM 功能替代来源，2026-10-12 接入）

本节“默认关闭/Top-K补充/不参与排序”描述旧discovery工作流及OT分层参考。正向会话沿用原支持度时，Monarch匹配基因可参与多来源匹配数与并列来源数；其原始分值不与OT分数相加，也不参与背景校正或OT分层排序。

- 定位：Monarch KG 是本项目 OMIM 所需功能（基因—疾病、HPO 表型、遗传方式、PMID/ECO 证据）的替代来源；Open Targets 仍是主疾病关联来源，associationScore 与 Top-K 排序不受 Monarch 影响。
- 默认关闭：`PHARM_MONARCH_ENABLED=true` 或任务 `monarch_enabled=true` 才启用；数据目录解析顺序为任务 `monarch_data_dir` > `PHARM_MONARCH_DATA_DIR` > `configs/monarch_data.local.json` > `data/monarch/<最新版本目录>`。无在线回退，缺文件明确报错。
- 匹配规则：基因为 gene_id 命名空间 > HGNC symbol > UniProt > 记录 unresolved；疾病为 candidate.mondo_id > Monarch 明确 MONDO ID > 其他明确本体 ID > 记录 unresolved。禁止按英文名称模糊合并；MONDO 是跨库映射字段，不是所有疾病的强制主键；无 MONDO 映射的记录保留。
- 当前只对 Top-K 候选做证据补充；产物独立保存：`monarch_gene_disease.csv`、`monarch_hpo.csv`、`monarch_inheritance.csv`、`monarch_publications.csv`、`monarch_unresolved.csv`、`monarch_provenance.json`（每条 HPO/遗传方式/PMID 单独一行），登记进运行 manifest 与下载白名单。
- Monarch 证据不参与 Open Targets 分数计算与候选排序，不能解释为临床疗效或诊断结论。

`heuristic_v2` 沿用权重，evidence_quality 仅取 BATMAN known 占比；原始关联分值不参与评分，缺失组件剔除。历史 v1 报告保留原版本。

## 中文疾病名称展示

反向解析保留远端新增的最长中文名称/显式input_aliases问句提取；冠心病精确映射MONDO_0005010，不以中文名替换数据库身份。

首页与工作台通过 `server/static/disease-labels.js` 读取 `disease-labels.json`，按精确 `disease_id`、`mondo_id` 或 `disease` 中的 ID 匹配，统一 MONDO/EFO/HP/OBA/DOID/Orphanet/OTAR 的下划线及冒号格式；最后可回退项目词表内的精确英文名。不做模糊翻译、不按中文名称合并实体，`breast cancer` 与 `breast carcinoma` 仍是两个原 ID。

当前词表包含 2,645 个疾病/性状 ID、94 个常用英文名/别名。广泛词条来自 Wikidata P5270→MONDO 与中文标签（CC0，元数据保留查询、生成时间及 Wikidata 实体 ID），优先 `zh-hans`、其次 `zh-cn`/`zh`；没有简体标签的词条保留来源中文。不同 Wikidata 实体映射同一 MONDO 或同优先级标签冲突时不收录。常用译名由 `pharm/diseases/chinese_labels_overrides.json` 明确标为 `project_glossary`，不冒充 Open Targets/Monarch 官方中文名称。

明确区分 `obesity disorder`（肥胖症，MONDO:0011122）、`binge eating disorder`（暴食障碍，MONDO:0005582）及 `bulimia nervosa`（神经性贪食症，MONDO:0005452）。补齐胎儿水肿、抑郁障碍、MC4R受体缺乏所致肥胖症等本次结果中漏译的明确实体；显示翻译不替换ID或科学结果。

人工维护常用词条后，显式运行 `python scripts/update_disease_labels.py --proxy http://127.0.0.1:7890` 更新提交的本地词表；也可用 `--input <保存的 SPARQL JSON>` 离线生成。用户查询时不会联网翻译。未知词条显示“中文名未收录”，加载失败显示“中文名暂不可用”；名称本已中文时不重复追加。

中文名称仅是 UI 补充：不修改候选、排序、分值、逐条证据或历史文件，历史结果可直接补充展示；JSON/CSV 导出保持原始研究字段。

## 正向 HTTP 查询的停止与重试

`POST /api/agent/forward` 另接受可选的 `request_id`（1—64 位字母/数字/下划线/连字符）、`supersedes` 与 `owner_id`（同一页签编号）。旧客户端可省略；首页/工作台为每次查询生成新编号，重试带上一条编号。`POST /api/agent/forward/cancel` 只接受 `{"request_id":"..."}`，作为显式取消入口；保留本机访问与 Origin 检查。

用户最后指定“关闭页面才停止”，覆盖本轮先前的离开页面即取消方案。`server/static/query-presence.js` 用 sessionStorage 保存页签编号，通过 `/api/agent/forward/presence/<owner_id>` 的 SSE 持续连接保持任务。项目内导航/刷新重连同一编号，后台给 10 秒接续缓冲；切换标签不关闭连接，不受后台 JS 定时器节流影响。没有同页签连接超过 10 秒时终止并等待该查询进程退出；关闭页签会断开持续连接，长期连接丢失也会触发该规则。应用外页面无法维持本项目连接，不承诺导航到其他网站后仍保持任务。

查询 POST 连接丢失或 `fetch` 报错本身不停止带 owner_id 的查询，前端可“恢复原查询”。`GET /api/agent/forward/request/<request_id>` 读取任务状态，首页/工作台返回后仅读取状态和结果，不新建计算。元数据记录 service_instance_id；原服务已重启的运行中状态返回 interrupted，避免无限等待。主动重试先停止 `supersedes` 指定的旧任务，停止失败不启动新的；显式取消/服务处理协程取消也终止对应子进程。未传 owner_id 的旧 API 客户端仍在自身 HTTP 连接断开时停止。纯 ASGI 中间件保留断开信号。归档 GET 失败只重读，不启动或取消研究计算。

任务状态写入 `local/agent/jobs/<request_id>.json`，结束后在本次 `local/agent/<direction>/<run_id>/job.json` 留存，包含工作进程 PID、状态、取消原因与时间。未完成记录标为 cancelled/failed；中间文件保留，不改成成功、不删除历史完整结果。重试不复用半成品、不提供断点续算；相同输入与数据/代码版本的已成功计算可按完整结果缓存规则复用。停止仅针对本服务创建的查询子进程，服务与其他查询继续运行。

## 归档布局

live 运行逐阶段归档到 `data/pharm/<归档名>/`（方名或 `custom_<task_id>`，analysis 继承来源任务）：

```text
01_preflight/  02_herb/  03_reverse/  04_review/
05_analysis/shared/  05_analysis/network/  05_analysis/enrich/  05_analysis/review/
  run_<run_id>/attempt_<nn>/
    _archive.json    批次哈希、任务参数、阶段状态、计数、来源访问日期记录
```

每批不可变：相同哈希复用，不同哈希拒绝覆盖；`_meta.md` 追加流水账；归档时间不可冒充数据库访问时间；partial/blocked 尝试同样归档；fixture 禁止进入；归档≠验收。
