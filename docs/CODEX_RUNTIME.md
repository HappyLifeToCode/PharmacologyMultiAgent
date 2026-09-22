# Codex CLI 执行配置

> **现状（2026-09-22）**：多 Agent 核验层**已启用**——live + `agents=true`（live 默认）时，引擎在程序计算完成后启动六个独立 Codex 会话（协调/药材靶点/疾病发现/网络/富集/验收，提示词在 [agents/](../agents/README.md)）。因此 **live 多 Agent 模式必需可用的 Codex CLI 与 profile**；`agents=false` 的纯程序 live 与全部 fixture 运行不需要模型服务。仍属预留的是**在线采集编排**（Agent 驱动站点采集）。

## 配置

[runtime.json](../configs/runtime.json) 当前内容（可共享，不含凭据）：

| 项目 | 值 |
|---|---|
| agent_runtime | codex-cli |
| codex_profile | icrc |
| model | gpt-5.6-luna |
| model_reasoning_effort | medium |

该文件被 engine 的 input_signature 读取（执行环境快照的一部分）；修改它会改变后续 resume 的签名，属预期行为。

每个开发者自行准备本机 `<profile>.config.toml` 与认证信息（默认读取 `<用户主目录>/.codex/`，可用 `PHARM_CODEX_SOURCE_HOME` 指向其他目录），仓库不分发凭据；普通 Codex 登录不保证与该适配器兼容，接入前需核验 CLI 版本与配置布局。模型不可用时报告错误，不自动切换模型，也不修改用户全局默认配置。

## 适配器与核验层行为

`pharm/agents/runtime.py` 提供：`execute`（启动独立 `codex exec` 会话、收集 JSON 事件与结构化交接，RESULT_SCHEMA 含可选 confidence 自评字段）、`prepare_home`（在 Git 忽略的 `local/codex-home/` 下建立精简执行环境）。

核验层行为（engine 实现）：

- 运行开工前检查 CLI 与 profile 可用性；不可用 → 运行 failed，提示核验环境或将任务 `agents` 设为 false。
- 程序计算成功后启动对应角色会话；证据按计数+样例裁剪；Agent 报 failed 只把阶段降为 partial（程序产物保留）；会话错误记录 `agent_review.error`，阶段状态由程序结果决定。
- `scripts/check_runtime.py` 可做模型/浏览器连通性握手（只读核验，不产生研究数据）。

在线采集阶段的设计意图（预留）：Agent 经该适配器执行站点采集，遇人机验证时经人机协助桥（/api/assist/* + WS /ws/assist）指引用户在内嵌浏览器中接管，完成后继续。该编排尚未实现。

## 接入要求（实现时遵守）

1. 任务参数、输入哈希及实际使用的模型配置写入运行记录。
2. 命令成功退出不等于分析完成，必须校验交接状态、输出文件和来源记录。
3. 登录、权限、模型不支持和数据缺失等错误明确记录；不得用推测数据替代实际查询结果。
4. 不绕过站点人机验证；账号与验证码留待人工。
