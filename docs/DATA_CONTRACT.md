# 数据交接约定（初版，待真实导出核验）

## 交接产物

| 环节             | 必需内容                                                       |
|----------------|------------------------------------------------------------|
| BATMAN         | 原始导出；herb_compound_target 关系；原始标识、成分标识、分数和阈值来源；去重靶点名单      |
| GeneCards      | 完整查询结果与 relevance score；完整性说明；中位数及严格大于中位数的保留名单             |
| OMIM           | 原始检索结果；疾病条目与关联基因的依据；可映射基因名单                                |
| 疾病合并           | 两库来源标记；合并去重名单；重复项和无法映射项记录                                  |
| 标准化与交集         | 原始 ID、HGNC symbol、映射来源/版本、匹配状态；两侧集合、Venny 原始交集/图/访问记录与独立核对 |
| STRING/CytoNCA | 输入名单与映射报告；网络边表、节点表、孤立/缺失节点；分析参数、度值及网络图；STRING 来源（在线 API 或本地 v12.0 文件）、歧义与未映射记录 |
| DAVID          | 输入名单、识别/未识别名单、背景集、注释类别、完整统计表及 FDR 筛选表                      |

关系表不能由一列去重 gene symbol 替代。所有中间产物都引用对应原始文件和批次。各库具体列名在真实导出检查后确定。

上表是研究交付要求，不表示每项已实现。当前导入器检查已提供的 gene_symbol 与映射依据，尚无完整 HGNC 权威映射自动化；NetworkX
和本地合成统计产物分别标注实际方法，不视为 CytoNCA/DAVID 交付。实现状态见 [项目进度](PROJECT_STATUS.md)。

## 任务输入与执行设置

研究任务保存在 `tasks/tasks.local.jsonl`，每行一条，以 task_id
标识；执行工具和模型来自 [configs/runtime.json](../configs/runtime.json)。字段说明见 [任务清单说明](../tasks/README.md)
。研究任务与运行状态分开保存。

task_id 标识研究任务，agent_role 标识执行阶段，attempt 标识重试。当前双库通过 genecards_targets、omim_targets 两个阶段键区分，不额外生成
subtask_id；将来拆分逐疾病子任务时再定义相应编号契约。disease_targets 在新版中是程序合并阶段，在旧版中是联合疾病会话，应结合
workflow_version 判断。

## 运行状态

每次运行使用独立 run_id。状态：pending、running、succeeded、partial、blocked、failed、skipped。只有产物存在且通过验证才标记
succeeded；零结果需注明完整查询或分析的依据。

当前交接信息分布在以下文件中，不能假定所有字段都在 handoff.json：

| 文件                   | 当前保存内容                                                                          |
|----------------------|---------------------------------------------------------------------------------|
| manifest.json        | 任务和执行设置快照、workflow_version（新版为 2）、各阶段状态、attempt、时间、输入签名、会话编号、产物索引与哈希            |
| handoff.json         | run_id、task_id、agent_role、recorded_at、status、summary、blockers、可选 findings、产物及哈希 |
| execution.json       | 独立模型会话的执行元数据；程序合并和交集没有该模型执行记录                                                   |
| venny_execution.json | Venny 浏览器工具的访问、操作、输入/产物哈希与核对状态，不是模型会话记录                                         |
| targets.json 等阶段产物   | 靶点、筛选计数、来源、方法和限制；具体字段随角色而定                                                      |
| input_snapshot/      | 真实导入及其来源、映射依据的冻结副本                                                              |

runs/<run_id>/events.jsonl 按时间追加 timestamp、role、type、message；任务和产物引用通过同目录 manifest
与阶段交接关联。不得记录密码或访问令牌。重试保留旧 attempt 文件；执行器核验输入签名、参数与产物哈希，匹配时才能复用，变更时重新处理依赖任务。

最小运行器已实现任务交接和事件记录。每个角色按 attempt 分目录保存；真实受限运行不生成替代分析数据。输入文件、参数及产物哈希用于恢复校验。

交集按 [Venny 接入契约](VENNY.md) 实际执行：`venny.png` 是官方页面原图；`venny_results.txt`
是三个集合区域原文；`venny_execution.json` 保存版本、实际访问日期、操作和哈希。`intersection.json` 的 `method` 标注 Venny
2.1.0 与 Python 独立核对，只有核对通过才发布 `genes.txt`。失败不发布下游名单，原始页面和 trace 留存但不通过 Web
公开。旧版 `venn.png` 属于本地绘图，不能引用为 Venny 产物。

## 分步归档约定

真实执行按 `data/pharm/<方名>/01_batman/`、`02_disease/`、`03_intersect/`、`04_ppi/`、`05_enrich/`
沉淀，每层为 `run_<run_id>/attempt_<nn>/`。新版疾病来源分别使用 `02_disease/genecards/` 与 `02_disease/omim/` 下的
run/attempt，程序合并沿用 `02_disease/` 根下的 run/attempt。每步结束即保存，partial/blocked
也保留访问证据；状态必须明确，目录存在不代表分析通过。fixture 禁止进入。

每批 `_archive.json` 保存哈希、来源访问日期、参数、状态、计数和原始 run 路径；`_meta.md` 追加流水账。归档时间不可冒充数据库访问时间。原始导出由本次运行的
input_snapshot 冻结后归档，禁止用未来变动的输入回填旧批次。相同哈希可复用，不同哈希拒绝覆盖。断电产生的 .pending
批次保留现场，不当作完成。

批次导入路径与选择方式见 [IMPORTS.md](IMPORTS.md)。

## 工作台任务提交

`POST /api/tasks` 接收 `formula`、`herbs`（数组）、`diseases`（数组）、可选 `research_notes` 与 `import_batch`
。服务端校验并追加到 `tasks/tasks.local.jsonl`，返回 `task` 和 `created`；重复的相同内容复用同一 ID。模型、凭据、任意输出路径不能经此接口设置。

`POST /api/run` 继续接收已保存的 `task_id` 与 `mode`，返回 `run_id`。任务保存成功而启动失败时不撤回任务；用户可稍后重试。新建/修改后的任务用于新的运行，已有
manifest 中的任务快照不变。

新版流程图使用 9 个节点、11 条依赖边：规划到药材、GeneCards、OMIM；两库到疾病合并；药材与疾病合并到交集；交集到网络与富集；两路分析到协调验收。旧版仍显示
7 节点、8 条边，不将新增节点虚构为过去已经执行。节点位置改变时连线重新计算。

左侧输入/管理任务，中间选择流程节点，右侧展示当前查看运行的阶段详情。产物和 report.md 的链接点击直接下载，保留文件名；原始提示词、模型日志、输入快照和浏览器
traces 仍不通过公开产物接口提供。

## 疾病来源并行交接（workflow_version=2）

`genecards_targets` 与 `omim_targets` 为独立 Agent 阶段，输出各自 targets.json。两者 succeeded
且产物校验通过后，`disease_targets` 程序合并 genes 和 gene_sources，再供 intersection
使用。source_counts、genecards_filter、policy 保留筛选流水账。疾病合并没有 agent_session_id。任一路受限则合并
blocked，已完成来源产物保留。

新任务默认 per_disease_median/confirmed：先对单个疾病完整记录分别计算中位数，严格大于再筛选；跨疾病同一基因保留各自分数，筛选后去重。该口径 2026-09-17 由医院方确认；旧任务保持冻结的 pooled_query_rows/provisional 口径。多疾病记录保留
disease 字段，详见 IMPORTS.md。
