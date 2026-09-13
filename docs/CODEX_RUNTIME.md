# Codex CLI 执行配置

## 项目默认值

| 项目 | 值 |
|---|---|
| 运行工具 | Codex CLI |
| 本地 profile | icrc |
| 模型 | gpt-5.6-luna |
| 思考强度 | medium |
| 生效范围 | 本项目五个角色的任务会话 |

这些可共享的执行设置已写入 [runtime.json](../configs/runtime.json)，不含密钥或服务地址。研究内容保存在 [任务清单](../tasks/tasks.jsonl)，填写方法见 [任务说明](../tasks/README.md)。运行器分别读取两份文件，通过 scripts/run_tasks.py 或工作台启动角色；配置文件本身不会自动开始任务。

每个开发者自行准备可用的本地 icrc profile 和认证信息，仓库不包含组内服务凭据。模型是否可用由组内服务决定；不支持时应报告错误，不自动切换模型。

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

浏览器任务每个会话独立使用 Playwright MCP 0.0.64、headless、isolated 和 120 秒启动超时，使用 prim 环境已有 Chromium。执行权限使用 workspace-write 与自动审批审查，不启用绕过审批的命令参数。被拦截的操作记录原因；账号和验证码留待人工。
