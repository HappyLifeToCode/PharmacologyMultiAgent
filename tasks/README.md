# 任务清单

[tasks.jsonl](tasks.jsonl) 保存要执行的研究任务，每行一个完整 JSON 对象。首条任务为芍药甘草汤 × Hyperthyroidism。新增任务时追加一行，并使用不重复的 task_id。

## 文件分工

| 文件或目录 | 内容 |
|---|---|
| tasks/tasks.jsonl | 做什么：方剂、药材、疾病、筛选和分析参数 |
| configs/runtime.json | 用什么执行：Codex CLI、profile 名称、模型和思考强度；可共享，不含凭据 |
| 各成员本机的 icrc profile | 服务连接和本地认证配置；不随仓库分发 |
| runs/<run_id>/ | 实际执行状态、使用参数、统计方法、产物和事件记录 |

任务定义不保存账号、密钥、运行状态或分析结果。运行器尚未实现；编辑任务清单不会自动开始执行。

## 当前任务字段

| 字段 | 说明 |
|---|---|
| task_id | 研究任务的唯一标识；同一任务可以对应多次 run_id |
| formula / herbs | 方剂名称、药材名单 |
| diseases | 疾病检索关键词列表；当前仅一个甲亢关键词 |
| organism / taxon_id | 物种及分类编号 |
| batman_threshold / batman_threshold_confirmed | BATMAN 阈值及确认状态；未知为 null，不自行猜测 |
| genecards_filter | 完整查询结果中 relevance score 严格大于中位数 |
| string_confidence / string_additional_nodes | 网络置信度及额外节点数量 |
| enrichment_input / enrichment_background | 富集输入及背景集；未确定背景集保留 null |
| enrichment_test_required | 方案要求的统计检验；实际使用的方法写入运行记录 |
| multiple_testing / fdr_lt | 多重检验校正方法及显著性阈值 |

使用 UTF-8，每个任务占一行，不添加注释或尾逗号。保留数值、布尔值、数组和 null 的 JSON 类型。首轮之外的多疾病合并口径须先明确，再添加相应批量任务。
