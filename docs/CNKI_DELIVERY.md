# 文献模块独立交付

本项目已拥有文献分析、数据校验/导入/打包、采集执行器、标题摘要关系抽取和提示模板。日常运行只读本项目`data/cnki`或显式配置的`PHARM_CNKI_DATA_DIR`，不再寻找同级UI-Agent，也不再使用`PHARM_CNKI_PROJECT_DIR`。

更新代码、导入数据并重启服务加载新版后，旧UI-Agent文件夹可以移走或自行删除，本项目的本地文献功能不受影响。Python、Codex CLI、Node/浏览器等运行环境是独立的软件依赖，不属于旧工程源码。

## 文献数据随项目交付

当前保留5组、100篇题录、98篇摘要：温经汤、半夏白术天麻汤、济川煎、桃核承气汤、芍药甘草汤，各20篇。2026-10-23按用户要求删除与当前方剂文献验证无关的“药物相互作用”主题20篇，并同步更新清单和交付ZIP。四个内置方剂仍是80篇题录、79篇摘要，关联结果与迁移前一致。旧UI-Agent原始数据及历史分析快照保留。

目录结构：

```text
data/cnki/
  manifest.json
  cnki_温经汤/
    papers.json
    triples.jsonl
  ...
```

`manifest.json`登记相对路径、篇数及文件实际SHA-256。读取时验证数据；导入默认拒绝覆盖不同版本，不拷贝旧项目代码库、日志、模型轨迹、浏览器登录状态或凭据。数据继续不进Git。

一次性迁移旧数据（迁移完成后不再需要来源目录）：

```powershell
.\.venv\Scripts\python.exe scripts/import_cnki_data.py --source-project D:\UI-Agent --keyword 温经汤 --keyword 半夏白术天麻汤 --keyword 济川煎 --keyword 桃核承气汤 --keyword 芍药甘草汤
```

生成独立数据包：

```powershell
.\.venv\Scripts\python.exe scripts/import_cnki_data.py --zip local/deliverables/cnki-literature-data.zip
```

在另一台机器上交付本项目代码及该ZIP，将ZIP解压到`data/cnki`，确保清单位于`data/cnki/manifest.json`，不要多套一层目录。可以直接拷贝已经包含此目录的完整项目文件夹。核对数据及四方分析：

```powershell
.\.venv\Scripts\python.exe scripts/import_cnki_data.py
.\.venv\Scripts\python.exe scripts/validate_cnki_bridge.py --output local/checks/cnki-delivery
```

旧文献快照已经包含当时题录、摘要和支撑句，恢复时不访问旧工程。原研究归档仍须保留且哈希一致。

## 本项目自带的采集代码

`pharm/literature/collector.py`及其`prompts/cnki_collection.md`、`prompts/cnki_extract.md`保留逐篇保存、增量题录、断点续跑、关系抽取与合并流程。入口为`scripts/collect_cnki.py`。2026-10-24起，采集及标题摘要抽取默认调用Codex CLI，固定模型`gpt-5.6-luna`，不再调用Kimi。可用`CODEX_BIN`指定Codex可执行文件，新任务可用`--model`显式覆盖模型；续跑沿用任务保存的模型。模型不可用时失败并保留任务，不自动换模型。

默认只准备任务，不调用模型或浏览器：

```powershell
.\.venv\Scripts\python.exe scripts/collect_cnki.py --keyword 温经汤
.\.venv\Scripts\python.exe scripts/collect_cnki.py --check-runtime
.\.venv\Scripts\python.exe scripts/collect_cnki.py --print-mcp-config
```

第三条命令输出Codex格式的`mcp_servers`配置样例。实际采集会用本次CLI的`-c`参数传入本项目Playwright配置，浏览器设置来自`configs/cnki_browser.json`，轨迹保存在当前任务目录。项目不写用户全局配置或模型密钥。抽取只接收标题、摘要，不额外配置浏览器。`--check-runtime`只检查Codex/Node/npx位置和资源，不代表模型账户、凭据或知网已连通。参数及配置依据[OpenAI官方CLI说明](https://developers.openai.com/codex/cli/reference)、[模型说明](https://developers.openai.com/codex/models)及[MCP说明](https://developers.openai.com/codex/mcp)。

以后明确要在线采集时，才执行：

```powershell
.\.venv\Scripts\python.exe scripts/collect_cnki.py --keyword 温经汤 --all --execute --extract
```

不指定`--limit`时默认按关键词遍历全部结果页，`--all`只是显式写出同一模式。关键词可以是方剂名或单味药材名；不按候选疾病二次筛选、不把方剂拆开混采。采集和分析只关注标题、完整摘要，作者/日期/来源/链接用于去重与追溯，不下载或阅读全文，不额外查询期刊影响因子。

全量模式必须保存`collection_progress.json`：每页实际总数、连续页码、每个检索位置对应的题录编号/标题/知网链接、采集状态，以及是否还有下一页。执行器核对页码和位置连续、总数一致、题录可对应、末页无下一页且无失败记录后才发布。完整进度也随`papers.json`交付。范围仅指采集当时该关键词、当前检索入口可访问的结果，不能称为知网所有相关论文均已穷尽。

网站每页20条不是采集上限，也没有100篇程序上限。需限量试采时可显式传`--limit 5`，这只限制本次新增篇数；旧版准备的限量任务续跑仍沿用原限量。`--extract`额外调用同一模型分析新论文的标题摘要。文献疾病列表不设Top-N，后端返回及前端展示全部关联，数据库查询的Top-K不限制该列表；篇数排序规则不变。

任务保存在`local/agent/cnki-collection/`，有独立原子锁。验证/限流、漏页、采集中总数变化、单次模型进程超时（40分钟）、引用无法核对或目标版本变化时保留进度，不发布为全量完成；`--resume <任务编号> --execute --extract`可显式续跑。续跑不能同时重设关键词、范围或模型。成功后更新本项目数据及清单；旧论文和旧分析快照不回写。

采集器迁移时通过官方文档和本机CLI帮助核对调用参数，并验证离线准备、模拟Codex子进程/全量翻页发布/失败不覆盖及超时清理；没有调用真实模型或启动知网采集。现有五组100篇仍是历史样本，不会因新默认模式自动变成全量。后续已补齐并连接[本地优先文献流程](LITERATURE_FLOW.md)：网页启用文献辅助后，本地有文献直接排序，无文献才后台采集，完成后自动补充排序，可停止/续跑及刷新恢复。本轮仍不采集“四方汤”，真实模型及知网网站未实跑。

## 来源记录

移植来源为用户提供的UI-Agent项目：`scripts/run_cnki_tasks.py`、`scripts/task_prompt_cnki.md`、`scripts/extract_triples.py`。原文件SHA-256分别为：

- `b7684dc90f382f5f7625d4b5491663873fab844a4ce29c8fa9a98e2259ab61c5`
- `641bf40c9b3f996e11fa5a852b5abe3c92f8c1269db52bbeb47c8b450e40248f`
- `f5c4c4ebeea11ba8485d5657c72b129666c19f99e81e2363c57f383371291082`

已改为本项目路径、受控发布和明确执行入口。没有复制旧训练数据管线、另一套Web服务、登录数据或反检测脚本。真实联网能力及摘要结论分类仍需后续单独验证。

首次迁移工程验收为360项Python通过、3项跳过及5项既有Node图连接测试通过；含旧项目不可用、ZIP还原、只准备不调用模型、模拟采集/抽取发布、失败不覆盖和超时清理。当时的6组数据包还原后，在禁止旧工程访问、网络和模型的条件下复核四方结果。桌面/手机页面亦通过；这些检查不替代真实知网网站和模型运行时验证。

2026-10-23清理后核对五组清单、题录及摘要数量、保留文件哈希和ZIP成员及内容哈希，均通过；四个内置方剂的完整分析报告仅生成时间变化，关联与映射结果一致。当轮全量Python回归360 passed、3 skipped（2个既有弃用警告）。

2026-10-24另验证关键词全量模式的0/23/125篇跨页发布、漏页/失败/变更总数拒绝发布及续跑、缺摘要明确记录、Codex长提示词stdin传递/最终响应与日志分离，以及分析输入不包含正文。最终全量378 passed、3 skipped（2个既有弃用警告，75.80秒）。37病名工程样例在1440/390宽度验证完整展示和导出，无截断、JS错误或额外研究POST。原五组数据及交付ZIP哈希不变；这些是工程验证，真实Codex模型及知网网站仍未实跑。
