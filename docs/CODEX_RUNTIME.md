# Codex CLI 执行配置

## 项目默认值

| 项目 | 值 |
|---|---|
| 运行工具 | Codex CLI |
| 本地 profile | icrc |
| 模型 | gpt-5.6-luna |
| 思考强度 | medium |
| 生效范围 | 本项目六种角色的任务会话 |

这些可共享的执行设置已写入 [runtime.json](../configs/runtime.json)，不含密钥或服务地址。研究内容保存在 [本地任务说明](../tasks/README.md)，填写方法见 [任务说明](../tasks/README.md)。运行器分别读取两份文件，通过 scripts/run_tasks.py 或工作台启动角色；配置文件本身不会自动开始任务。

每个开发者自行准备可用的本地 icrc profile 和认证信息，仓库不包含组内服务凭据。模型是否可用由组内服务决定；不支持时应报告错误，不自动切换模型。

## 首次接入

当前运行适配器按组内 CLI 的独立 profile 文件布局实现，完整流程仅在 Windows 验证。请从所用模型服务的维护者处获取兼容 CLI 的安装方式与配置说明，安装后确保终端能执行 `codex --version`。公开仓库不附带组内 CLI 安装包或服务凭据；仅安装另一版本的 Codex CLI 并登录，并不保证能直接使用此适配器。

运行器默认从当前用户的 `.codex/` 目录读取：

```text
<用户主目录>/.codex/
  config.toml           公共服务设置（如需）
  icrc.config.toml      与 runtime.json 中 codex_profile 同名的独立 profile
  auth.json             所用认证方式需要时提供
```

`icrc` 是默认 profile 名称，并非通用账号。其他 profile 对应 `<profile>.config.toml`；服务连接和认证必须由成员在本机配置，不能提交仓库。当前代码不会从 `config.toml` 的 `[profiles.<名称>]` 区段自动读取 profile，因此使用这种布局的 CLI 需要另行适配并验证。

如需切换模型服务，编辑 `configs/runtime.json` 中的 `codex_profile`、`model` 和 `model_reasoning_effort`，并确认服务支持所选模型及思考强度。执行器还依赖 JSON 事件、结构化结果、MCP 配置和审批参数，兼容性以实际调用为准。项目不会修改用户全局默认模型。

若配置文件存放在其他目录，可通过环境变量 `PHARM_CODEX_SOURCE_HOME` 指向该目录。环境检查与执行器均使用该来源目录。

## 命令形式

交互式执行时，在项目目录使用：

```powershell
codex -p icrc -m gpt-5.6-luna -c 'model_reasoning_effort="medium"'
```

调度器启动独立任务会话的基本命令形式：

```powershell
codex exec -p icrc -m gpt-5.6-luna -c 'model_reasoning_effort="medium"' --json -
```

末尾的 `-` 表示从标准输入读取完整任务。调度器需要传入角色职责、具体输入、参数、输出位置和交接要求，收集 JSONL 事件、退出状态和真实产物，再决定下一步。该命令不会自动完成角色分配或完整 Demo。

若项目尚未初始化 Git，明确需要在非 Git 目录执行时可添加 `--skip-git-repo-check`。工作目录由启动进程指定为项目根目录；输出按 run_id 和 task_id 分目录保存。执行权限及浏览器工具另行配置，不以关闭安全机制作为默认接入方式。

模型和思考强度每次都显式传入，避免继承 profile 中不同的默认值。组内 profile 的服务连接与认证留在各成员本机。

## 接入要求

1. 各角色独立任务上下文，使用对应 agents/ 文件及团队共享的数据约定组装任务。
2. 任务参数、输入哈希及实际使用的模型配置写入运行记录；不同角色的工具和浏览器会话按资源限制隔离。
3. 命令成功退出不等于分析完成，必须校验交接状态、输出文件和来源记录。
4. 登录、权限、模型不支持和数据缺失等错误明确记录；不得用推测数据替代实际查询结果。

## 核验状态与参考

2026-09-12 已核对开发机 Codex CLI 0.153.2 支持 exec、-p、-m、-c、--json 及标准输入任务。已核验本地 icrc profile 存在；已实际调用组内服务验证指定模型，并运行独立角色的真实站点核验。

官方参数说明：[CLI Reference](https://developers.openai.com/codex/cli/reference/)、[Configuration Reference](https://developers.openai.com/codex/config-reference/)。具体参数以成员本机版本帮助为准。


## 本地隔离与 Playwright

运行器从本机配置中提取所用模型服务设置，在被忽略的 local/codex-home/ 下建立精简执行环境并保存必要认证副本；不复制其他桌面插件，也不修改全局配置。该目录含私人配置，不能分发。

浏览器任务每个会话独立使用 Playwright MCP 0.0.64、headless、isolated 和 120 秒启动超时，使用当前 Python 环境对应的 Playwright Chromium；首次安装命令见 [快速启动](../README.md#快速启动)。MCP 通过 npx 按锁定版本启动，无需复制其他项目的 playwright-mcp 文件夹，首次使用需要网络下载。执行权限使用 workspace-write 与自动审批审查，不启用绕过审批的命令参数。被拦截的操作记录原因；账号和验证码留待人工。

Venny 阶段使用 `pharm_demo/venny.py` 直接驱动 Python Playwright；它实际操作网页并保留 trace，不启动额外 Codex 会话，也不经过 MCP。疾病合并是另一个无模型工具步骤。因此当前完整流程是六种角色、七个模型会话、九个阶段。工具的启动超时分别生效，不能把 MCP 的 120 秒等同于所有浏览器操作超时。

2026-09-13 完整合成演练已验证七个不同会话均使用指定模型与思考强度，实际 Venny 操作通过；这不代表已自动导出五库真实数据。更新 `agents/*.md` 可能改变后续恢复的角色输入签名，历史运行不回写；状态与缺口见 [项目进度](PROJECT_STATUS.md)。
