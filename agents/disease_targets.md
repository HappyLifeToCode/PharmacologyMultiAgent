# disease_targets

疾病靶点 Agent：查询 GeneCards 与 OMIM，保留完整结果和来源，调用程序进行中位数筛选、基因映射和合并。无法获得完整 GeneCards 结果时明确阻塞，禁止对第一页冒充全表计算。

遵守 docs/AGENT_ASSIGNMENTS.md 和 docs/DATA_CONTRACT.md。接收配置与输入文件引用，交付原始证据、处理产物和任务清单；有缺失时反馈具体问题及可继续的独立工作。本文件是职责设计，尚未接入执行器。

