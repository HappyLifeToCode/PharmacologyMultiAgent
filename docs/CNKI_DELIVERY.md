# 文献模块独立交付

本项目已拥有文献分析、数据校验/导入/打包、采集执行器、标题摘要关系抽取和提示模板。日常运行只读本项目`data/cnki`或显式配置的`PHARM_CNKI_DATA_DIR`，不再寻找同级UI-Agent，也不再使用`PHARM_CNKI_PROJECT_DIR`。

更新代码、导入数据并重启服务加载新版后，旧UI-Agent文件夹可以移走或自行删除，本项目的本地文献功能不受影响。本轮没有删除或修改旧工程。Python、Kimi、Node/浏览器等运行环境是独立的软件依赖，不属于旧工程源码。

## 文献数据随项目交付

当前已导入6组、120篇题录：温经汤、半夏白术天麻汤、济川煎、桃核承气汤、芍药甘草汤，以及“药物相互作用”主题词。最后一组只有旧题录，没有疾病关系抽取文件；它不是方剂验证样本。四个内置方剂仍是80篇题录、79篇摘要，关联结果与迁移前一致。

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
.\.venv\Scripts\python.exe scripts/import_cnki_data.py --source-project D:\UI-Agent
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

`pharm/literature/collector.py`及`prompts/cnki_collection.md`、`prompts/cnki_extract.md`移植了旧项目的逐篇保存、增量标题清单、断点续跑、关系抽取与合并流程。入口为`scripts/collect_cnki.py`，不是运行旧工程脚本。

默认只准备任务，不调用模型或浏览器：

```powershell
.\.venv\Scripts\python.exe scripts/collect_cnki.py --keyword 温经汤 --limit 5
.\.venv\Scripts\python.exe scripts/collect_cnki.py --check-runtime
.\.venv\Scripts\python.exe scripts/collect_cnki.py --print-mcp-config
```

第三条命令输出路径已指向本项目的MCP配置样例，浏览器配置是`configs/cnki_browser.json`。按照所安装Kimi版本的配置方式，将此样例用于Kimi运行时；不要沿用引用旧UI-Agent目录的MCP路径。项目不复制或写入用户的模型密钥和全局配置。`--check-runtime`只检查CLI位置和资源，不代表模型凭据、浏览器或知网已连通。

以后明确要在线采集时，才执行：

```powershell
.\.venv\Scripts\python.exe scripts/collect_cnki.py --keyword 温经汤 --limit 5 --execute --extract
```

`--limit`是本次新增篇数，`--extract`会额外调用模型分析新论文的标题摘要。任务保存在`local/agent/cnki-collection/`，有独立原子锁；新增不足、旧论文被改动、引用无法核对或目标版本变化时保留任务，不发布。成功后更新本项目数据及清单；旧分析快照不回写。`--resume <任务编号> --execute --extract`可续跑，任务编号由准备命令输出。

本轮只验证了准备任务、模拟采集/抽取、发布、失败不覆盖和超时进程清理，没有实际联网知网，没有调用真实模型。网页仍是默认关闭的本地论文读取入口，尚未连接在线采集队列；不把模拟验收称为实网成功。

## 来源记录

移植来源为用户提供的UI-Agent项目：`scripts/run_cnki_tasks.py`、`scripts/task_prompt_cnki.md`、`scripts/extract_triples.py`。原文件SHA-256分别为：

- `b7684dc90f382f5f7625d4b5491663873fab844a4ce29c8fa9a98e2259ab61c5`
- `641bf40c9b3f996e11fa5a852b5abe3c92f8c1269db52bbeb47c8b450e40248f`
- `f5c4c4ebeea11ba8485d5657c72b129666c19f99e81e2363c57f383371291082`

已改为本项目路径、受控发布和明确执行入口。没有复制旧训练数据管线、另一套Web服务、登录数据或反检测脚本。真实联网能力及摘要结论分类仍需后续单独验证。

工程验收为360项Python通过、3项跳过及5项既有Node图连接测试通过；含旧项目不可用、ZIP还原、只准备不调用模型、模拟采集/抽取发布、失败不覆盖和超时清理。真实6组数据包还原后，在禁止旧工程访问、网络和模型的条件下复核四方结果。桌面/手机页面亦通过；这些检查不替代真实知网网站和模型运行时验证。
