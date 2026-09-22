# 任务清单

`tasks.local.jsonl` 保存本机研究任务，每行一个完整 JSON 对象，由 Git 忽略。首次克隆没有个人任务，网页保存时自动创建此文件。仓库仅提供 [任务模板](tasks.example.jsonl)，不会自动加载或执行。

## 文件分工

| 文件或目录 | 内容 |
|---|---|
| tasks/tasks.local.jsonl | 做什么：方剂/药材、阈值、运行方式（Git 忽略） |
| configs/runtime.json | 多 Agent 核验层的模型配置（live 默认启用）；可共享，不含凭据 |
| runs/<run_id>/ | 实际执行状态、使用参数、产物和事件记录 |

任务定义不保存账号、密钥、运行状态或分析结果。

## 当前任务字段

| 字段 | 说明 |
|---|---|
| task_id | 唯一标识，`web_` + 任务内容哈希前 16 位；**参数即身份**，相同内容复用同一 ID |
| formula | 内置方剂名称（温经汤/半夏白术天麻汤/济川煎/桃核承气汤），可选 |
| herbs | 药材清单（1–30 项）；与 formula 二选一，或覆盖 formula 组成 |
| research_notes | 可选研究说明（最多 4000 字） |
| batman_threshold | BATMAN predicted 行筛选阈值（严格大于），默认 0.84 |
| mode | live（本地数据反查）或 fixture（合成工程验证），默认 live |
| agents | 多 Agent 核验开关：live 默认 true（需 Codex 环境），fixture 强制 false；false 为纯程序调试开关 |
| composition | 组成来源记录：formula / herbs_override / custom_herbs（由程序写入） |

使用 UTF-8，每个任务占一行，不添加注释或尾逗号。保留数值、布尔值、数组和 null 的 JSON 类型。

任务模板（`tasks.example.jsonl`）每行一个示例，不会被自动加载；仅使用命令行时可参照它手动创建 `tasks.local.jsonl`。

## 任务、运行与恢复

- `live` / `fixture` 是任务的运行方式：fixture 始终使用固定合成集合并全程标注，不产生真实药理结论；live 需要本机 BATMAN 数据与疾病索引，缺失时阶段 blocked 不伪造。
- 修改任务内容（任何字段）等于创建新任务（新 task_id）；已有运行的 manifest 冻结当时的任务快照，不回写。
- 恢复运行使用冻结快照；恢复只复用输入、代码、数据与产物哈希全一致的成功阶段。补齐本地数据后恢复，受阻阶段会自动重跑。
- 完整输入要求见 [导入说明](../docs/IMPORTS.md)，实现缺口见 [项目进度](../docs/PROJECT_STATUS.md)。
