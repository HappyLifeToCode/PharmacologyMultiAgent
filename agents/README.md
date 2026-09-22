# agents/ 说明

当前四阶段 pipeline 是纯程序，**不读取任何角色提示词**（engine 无模型会话，input_signature 也不含本目录内容）。

多 Agent 在线采集是预留方向：实现该阶段时将在此定义角色提示词（采集、核验等），由 `pharm/agents/runtime.py` 启动模型会话，并经人机协助桥（`pharm/assist/bridge.py`）处理人机验证。

旧方向的运行时角色提示词（coordinator、herb_targets、genecards_targets、omim_targets、disease_targets、network_analysis、enrichment_analysis 共七个文件）已随重构删除，内容见 Git 历史（commit `6779567` 之前）。
