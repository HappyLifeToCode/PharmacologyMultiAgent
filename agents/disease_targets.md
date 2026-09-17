# disease_targets（旧版联合会话）

疾病靶点 Agent：查询 GeneCards 与 OMIM，保留完整结果和来源，调用程序进行中位数筛选、基因映射和合并。无法获得完整 GeneCards 结果时明确阻塞，禁止对第一页冒充全表计算。

遵守 docs/AGENT_ASSIGNMENTS.md 和 docs/DATA_CONTRACT.md。接收配置与输入文件引用，交付原始证据、处理产物和任务清单；有缺失时反馈具体问题及可继续的独立工作。本角色已接入独立 Codex 会话；真实数据缺失时返回受限状态，合成验证不冒充数据库结果。

新版 workflow_version=2 的 disease_targets 是确定性合并节点，不加载本文件启动模型；两库分别由 genecards_targets.md 和 omim_targets.md 定义的独立会话处理。本文件保留供旧版历史运行恢复使用。

恢复旧版时同样区分合格导入与访问核验；不声称已完成全量网页导出。新版默认口径为按单个疾病分别计算中位数、严格大于筛选（2026-09-17 医院方确认），OMIM 不套用该分数；旧任务保持冻结的 pooled_query_rows 口径。历史产物不回写；重试按当前执行器产生新 attempt，若重新执行交集则使用当前 Venny 工具，不能据此追认旧 attempt 已使用 Venny。
