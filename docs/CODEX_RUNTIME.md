# Codex CLI 执行配置（预留给在线采集阶段）

> **现状（2026-09-22）**：当前四阶段 pipeline 是纯程序，**没有任何模型会话**。`pharm/agents/runtime.py`（Codex CLI 适配器）与 `configs/runtime.json` 是为未来在线采集阶段预留的代码与配置，**当前未被调用**，不是运行主流程或测试的依赖。本文保留配置说明供实现该阶段时使用；旧九阶段多角色的会话编排已随重构删除（Git 历史可查）。

## 预留配置

[runtime.json](../configs/runtime.json) 当前内容（可共享，不含凭据）：

| 项目 | 值 |
|---|---|
| agent_runtime | codex-cli |
| codex_profile | icrc |
| model | gpt-5.6-luna |
| model_reasoning_effort | medium |

该文件同时被 engine 的 input_signature 读取（作为执行环境快照的一部分）；修改它会改变后续 resume 的签名，属预期行为。

每个开发者自行准备本机 `<profile>.config.toml` 与认证信息（默认读取 `<用户主目录>/.codex/`，可用 `PHARM_CODEX_SOURCE_HOME` 指向其他目录），仓库不分发凭据；普通 Codex 登录不保证与该适配器兼容，接入前需核验 CLI 版本与配置布局。模型不可用时报告错误，不自动切换模型，也不修改用户全局默认配置。

## 适配器现状

`pharm/agents/runtime.py` 提供：`execute`（启动独立 `codex exec` 会话、收集 JSON 事件与结构化交接）、`prepare_home`（在 Git 忽略的 `local/codex-home/` 下建立精简执行环境）、TOML 配置读取。`scripts/check_runtime.py` 可做模型/浏览器连通性握手（只读核验，不产生研究数据）。

在线采集阶段的设计意图：Agent 经该适配器执行站点采集，遇人机验证时经 [人机协助桥](../README.md)（/api/assist/* + WS /ws/assist）指引用户在内嵌浏览器中接管，完成后继续。该编排尚未实现——当前 blocked 阶段只带 assist 升级标记，不会启动 Agent 或等待人工。

## 接入要求（实现时遵守）

1. 任务参数、输入哈希及实际使用的模型配置写入运行记录。
2. 命令成功退出不等于分析完成，必须校验交接状态、输出文件和来源记录。
3. 登录、权限、模型不支持和数据缺失等错误明确记录；不得用推测数据替代实际查询结果。
4. 不绕过站点人机验证；账号与验证码留待人工。
