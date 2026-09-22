# agents/ 说明

本目录是**多 Agent 核验层**的角色提示词，live + `agents=true`（live 默认）时由引擎实际使用：

| 文件 | 角色 | 触发时机 |
|---|---|---|
| coordinator.md | 协调 · 开场核对 | preflight 之后、herb_targets 之前 |
| batman_targets.md | 药材靶点核验 | herb_targets 程序产物完成后 |
| disease_discovery.md | 疾病发现核验与解释 | disease_reverse 程序产物完成后 |
| network_analysis.md | 网络分析核验 | analysis 流水线 network 阶段后 |
| enrichment_analysis.md | 富集分析核验 | analysis 流水线 enrichment 阶段后 |
| review.md | 验收 | review / analysis_review 程序核验通过后 |

纪律（每个提示词均写明）：计算由确定性程序完成，Agent 只做核验与解释，不修改程序产物数字、不编造数据；返回结构化结论（status/summary/findings + confidence 自评）。Agent 报 failed 只把阶段降为 partial（程序产物保留）；会话错误记录 agent_review.error，阶段状态由程序结果决定。提示词内容哈希参与阶段 input_signature——修改提示词后 resume 会使对应阶段重跑（预期行为）。

fixture 运行强制不调模型；live + `agents=false` 为纯程序调试开关；Codex 环境不可用时 live 多 Agent 运行直接 failed 并提示该开关。

仍属预留的是**在线采集编排**（Agent 驱动站点采集、经人机协助桥接管人机验证）——提示词核验层已启用，采集编排未实现。旧方向（九阶段多 Agent 主流程）的角色提示词见 Git 历史（commit `6779567` 之前）。
