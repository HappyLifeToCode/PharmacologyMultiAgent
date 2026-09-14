# 任务清单

`tasks.local.jsonl` 保存本机研究任务，每行一个完整 JSON 对象，由 Git 忽略。首次克隆没有个人任务，网页保存时自动创建此文件。新增任务使用不重复的 task_id。仓库仅提供 [任务模板](tasks.example.jsonl)，不会自动加载或执行。

## 文件分工

已有安装升级前，请把旧 `tasks/tasks.jsonl` 复制为 `tasks/tasks.local.jsonl` 后再更新代码；目标已存在时先备份两份清单，按 task_id 核对后手动合并，不覆盖个人任务。新版本不会自动读取旧文件，以免把历史仓库中的测试任务重新导入。

仅使用命令行时，可手动参照 `tasks.example.jsonl` 创建 `tasks.local.jsonl`，再执行任务。模板不含个人测试记录。`runs/` 中的历史快照独立保留，恢复旧运行不要求当前任务清单存在。

旧提交中已经上传的任务仍可从 Git 历史查看；本次移除和忽略只改变后续版本，不会删除历史提交。

| 文件或目录 | 内容 |
|---|---|
| tasks/tasks.local.jsonl | 做什么：方剂、药材、疾病、筛选和分析参数 |
| configs/runtime.json | 用什么执行：Codex CLI、profile 名称、模型和思考强度；可共享，不含凭据 |
| 各成员本机的 icrc profile | 服务连接和本地认证配置；不随仓库分发 |
| runs/<run_id>/ | 实际执行状态、使用参数、统计方法、产物和事件记录 |

任务定义不保存账号、密钥、运行状态或分析结果。运行器已实现；编辑任务清单后仍需通过 scripts/run_tasks.py 或工作台启动。

## 当前任务字段

| 字段 | 说明 |
|---|---|
| task_id | 研究任务的唯一标识；同一任务可以对应多次 run_id |
| formula / herbs | 方剂名称、药材名单 |
| diseases | 一个或多个疾病检索关键词；页面支持一键填入五类甲状腺疾病，已有单病种任务保持原范围 |
| organism / taxon_id | 物种及分类编号 |
| batman_threshold / batman_threshold_confirmed | BATMAN 阈值及确认状态；未知为 null，不自行猜测 |
| genecards_filter | 完整查询结果中 relevance score 严格大于中位数 |
| genecards_median_scope | 默认 pooled_query_rows：合并所选疾病全部查询记录，统一计算中位数 |
| genecards_median_status | 默认 provisional：待医生确认；确认后可配置 confirmed，保留确认依据 |
| string_confidence / string_additional_nodes | 网络置信度及额外节点数量 |
| string_version | STRING 要求版本；缺省按 12.0 检查，版本不一致时停止并请求复核 |
| enrichment_input / enrichment_background | 富集输入及背景集；未确定背景集保留 null |
| enrichment_test_required | 方案要求的统计检验；实际使用的方法写入运行记录 |
| multiple_testing / fdr_lt | 多重检验校正方法及显著性阈值 |

使用 UTF-8，每个任务占一行，不添加注释或尾逗号。保留数值、布尔值、数组和 null 的 JSON 类型。当前多疾病规则暂定为合并查询记录统一计算中位数，确认状态保持 provisional；医生确认或改变方法后，应另存任务版本并新建运行，不改写历史运行快照。


可选 `import_batch`：显式选择 `data/pharm/<方名>/imports/<task_id>/<import_batch>/` 下的导入批次；未设置继续使用 `data/imports/<task_id>/`。批次名不含路径分隔符。原始文件不覆盖，修订建立新批次。


工作台左侧“新建任务”可填写方剂、药材、疾病关键词和 `research_notes`（可选研究说明，最多 4000 字）。任务 ID 以 `web_` 开头，按内容生成，相同内容重复保存复用；研究说明会随 manifest 中的任务快照交给各 Agent。任务文件仅保存在本机，不随 Git 分发；仍不填账号、密码或验证码。

“研究任务”选择准备执行的任务；“查看运行”选择某次已执行记录，右侧阶段详情跟随该运行和中间节点。保存不等于启动；同一任务可以有多次 run_id。新版运行将疾病模块拆为 GeneCards、OMIM 两个独立来源阶段和一个程序合并阶段。

页面新任务默认写入 pooled_query_rows/provisional。任务文件可显式设置这两个字段，当前只实现 pooled_query_rows；不支持的中位数口径会拒绝处理，不能仅改字段值就认为新算法已经实现。单疾病旧任务没有这两个字段时，疾病处理默认按统一中位数、待确认规则记录。

## 任务、执行模式与实际能力

`live` / `fixture` 是启动时选择的运行模式，不是从方剂名称或研究说明猜测。fixture 始终使用固定合成集合，填写五类疾病不会产生五类疾病的真实采集结果；两种模式均调用 Agent，Venny 交集均实际访问官方网页。

Venny 当前固定使用 2.1.0，任务中没有选择“Python 替代 Venny”的开关。未知药理参数保持 null/provisional，不为跑通流程而假定已确认。完整原文药材名单需另行提供；甲状腺癌 Top 50/回溯尚未实现，不能仅在研究说明中写入就视为已启动第二流程。

修改任务字段（含 import_batch）后创建新运行；恢复使用已有运行冻结的任务内容。完整输入要求见 [导入说明](../docs/IMPORTS.md)，实现缺口见 [项目进度](../docs/PROJECT_STATUS.md)。
