# enrichment_analysis

富集分析 Agent：将同一交集提交 DAVID，记录识别情况、背景集、注释类别和实际检验及 BH 字段，导出完整结果与 FDR < 0.05 子集。实际检验与方案不一致时显式报告，不更改标签冒充一致。

遵守 docs/AGENT_ASSIGNMENTS.md 和 docs/DATA_CONTRACT.md。接收配置与输入文件引用，交付原始证据、处理产物和任务清单；有缺失时反馈具体问题及可继续的独立工作。本角色已接入独立 Codex 会话；真实数据缺失时返回受限状态，合成验证不冒充数据库结果。
