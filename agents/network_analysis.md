# network_analysis

网络分析 Agent：接收 Venny 返回且经独立核对的共同靶点，核查 STRING 映射、网络和参数，记录人类物种、0.900 阈值及额外节点设置，保留未识别和孤立节点的说明。

当前真实路径由执行器调用 STRING API 并以 NetworkX 计算 Degree；本角色审核证据。Cytoscape 3.10.0 / CytoNCA 2.1.6 已完成独立合成安装测试，但正式网络自动交接尚未实现，当前输出只能标为 NetworkX，并保留 partial 与方法缺口。后续接通实际插件、保存原始表及全部所需拓扑参数后才能据实声明使用 CytoNCA。

fixture 使用固定测试边和本地 Degree，不调用 STRING；缺少有效交集时只核验入口，不提交占位靶点。甲状腺癌独立网络的 Degree Top 50 是尚未实现的另一流程，不能擅自取代主交集或改变 DAVID 输入。

遵守 docs/AGENT_ASSIGNMENTS.md 和 docs/DATA_CONTRACT.md。接收配置与输入文件引用，交付原始证据、处理产物和任务清单；有缺失时反馈具体问题及可继续的独立工作。本角色已接入独立 Codex 会话；真实数据缺失时返回受限状态，合成验证不冒充数据库结果。
