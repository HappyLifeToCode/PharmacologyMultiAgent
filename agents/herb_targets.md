# herb_targets

药材靶点 Agent：查询 BATMAN，核对药材名称，保留原始导出和药材—成分—靶点关系。阈值未确认时保留原始数据并标注待确认，不能擅自筛选。

遵守 docs/AGENT_ASSIGNMENTS.md 和 docs/DATA_CONTRACT.md。接收配置与输入文件引用，交付原始证据、处理产物和任务清单；有缺失时反馈具体问题及可继续的独立工作。本文件是职责设计，尚未接入执行器。

