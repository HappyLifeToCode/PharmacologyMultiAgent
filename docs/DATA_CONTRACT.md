# 数据交接约定（初版，待真实导出核验）

## 交接产物

| 环节 | 必需内容 |
|---|---|
| BATMAN | 原始导出；herb_compound_target 关系；原始标识、成分标识、分数和阈值来源；去重靶点名单 |
| GeneCards | 完整查询结果与 relevance score；完整性说明；中位数及严格大于中位数的保留名单 |
| OMIM | 原始检索结果；疾病条目与关联基因的依据；可映射基因名单 |
| 疾病合并 | 两库来源标记；合并去重名单；重复项和无法映射项记录 |
| 标准化与交集 | 原始 ID、HGNC symbol、映射来源/版本、匹配状态；两侧集合、交集、计数及韦恩图 |
| STRING/CytoNCA | 输入名单与映射报告；网络边表、节点表、孤立/缺失节点；分析参数、度值及网络图 |
| DAVID | 输入名单、识别/未识别名单、背景集、注释类别、完整统计表及 FDR 筛选表 |

关系表不能由一列去重 gene symbol 替代。所有中间产物都引用对应原始文件和批次。各库具体列名在真实导出检查后确定。

## 任务输入与执行设置

研究任务保存在 [tasks/tasks.jsonl](../tasks/tasks.jsonl)，每行一条，以 task_id 标识；执行工具和模型来自 [configs/runtime.json](../configs/runtime.json)。字段说明见 [任务清单说明](../tasks/README.md)。研究任务与运行状态分开保存。

交接清单中的 task_id 标识研究任务，agent_role 标识角色，attempt 标识重试；若同一角色进一步拆分多个子任务，调度器另设 subtask_id，避免混用研究任务编号。

## 运行状态

每次运行使用独立 run_id。状态：pending、running、succeeded、partial、blocked、failed、skipped。只有产物存在且通过验证才标记 succeeded；零结果需注明完整查询或分析的依据。

每个任务交接清单至少包含：run_id、task_id、agent_role、attempt、status、输入文件及哈希、参数、输出文件及哈希、原始来源、开始/结束时间、输入输出计数、问题列表、下一步建议。

runs/<run_id>/events.jsonl 追加记录事件：时间、任务、角色、事件类型、输入/输出引用、消息。不得记录密码或访问令牌。重试保留旧 attempt 文件；脚本和协调 Agent 验证输入哈希与参数，匹配时才能复用，变更时重新处理依赖任务。

这些是拟实现接口，当前没有运行记录或真实分析产物。
