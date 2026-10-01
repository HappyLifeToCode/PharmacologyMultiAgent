# 环境准备与验收

更新：2026-10-01（移除旧数据库采集与协助浏览器）。软件安装、数据可用与研究分析完成分别判断。完整缺口见 [项目进度](PROJECT_STATUS.md)。

## 必需软件

| 软件 | 用途 | 准备 |
|---|---|---|
| Python 3.9+（建议 3.11） | 全部运行时代码 | 官方安装包或既有环境 |
| Git | 版本管理 | 官方安装包 |

Python 依赖：`pip install -r requirements.txt`。纯程序运行（agents=false）、fixture 与自动测试不需要 Node.js、浏览器、Cytoscape、Java 或模型服务。

首次验证（不需要任何研究数据或外网）：

```powershell
.\.venv\Scripts\python.exe scripts/doctor.py
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

## 可选软件

- **Playwright Chromium**：仅供 Agent 核验运行时的浏览器 MCP；`agents=true` 的环境准备按 [Codex 执行配置](CODEX_RUNTIME.md) 操作。Open Targets 查询与工作台不使用浏览器。

- **Codex CLI（icrc profile）**：**live 多 Agent 模式必需**（任务 `agents=true`，live 默认）——六个核验会话由此启动；`agents=false` 的纯程序 live 与全部 fixture 不需要。配置方式见 [Codex 执行配置](CODEX_RUNTIME.md)。
- **Cytoscape 3.10.0 + CytoNCA 2.1.6 + JDK**：机制分析 network 阶段的可选依赖——仅当任务显式配置 `network_topology` 且要求 CytoNCA 度值时需要（否则度值用 NetworkX 并如实标注）。桥接插件用 `scripts/build_cytonca_bridge.py` 针对本机安装构建（源码在 `integrations/cytonca_bridge/`）。首次启动遇旧 JAR 兼容问题（`Invalid CEN header`）时，可在该软件自身 `Cytoscape.vmoptions` 加 `-Djdk.util.zip.disableZip64ExtraFieldValidation=true`（仅影响该应用，历史核验记录见 Git 历史文档）。
- **STRING v12.0 本地数据**：机制分析 network 阶段的可选数据来源（`configs/string_data.local.json`，模板 `configs/string_data.example.json` 指向 `data/string/v12.0/`，含 `9606.protein.info/aliases/links.detailed` 三件套，`.txt.gz` 或 `.txt`）。未配置时回退 STRING 公开 API（**需要外网**；任务可用 `string_source` 显式指定其一）。
- **Open Targets 26.09 本地快照**：支持疾病→基因查询，以及 BATMAN 靶点→候选疾病。目录 `data/open_targets/26.09/` 应含 `target/`、`disease/`、`association_overall_direct/` 和 `manifest.json`；设置 `PHARM_OPEN_TARGETS_MODE=local` 后，数据目录按 `PHARM_OPEN_TARGETS_DATA_DIR` > `configs/open_targets_data.local.json` > `data/open_targets/26.09/` 解析，采集不访问 GraphQL。可复制 `configs/open_targets_data.example.json` 为 Git 忽略的本地配置。数据文件不提交 Git，快照来源与哈希由 `manifest.json` 记录。

## BATMAN-TCM v2.0 本地全量数据（live 必需）

药材侧正式来源为 BATMAN-TCM v2.0 官网下载页的全量数据文件。本地目录必须包含：

```text
herb_browse.txt                            药材→成分（PubChem CID）
known_browse_by_ingredients.txt.gz         成分→已知靶点（文献验证，无分数）
known_browse_by_targets.txt.gz             靶点→成分（Entrez→symbol 映射依据）
predicted_browse_by_ingredients.txt.gz     成分→预测靶点（0~1 概率，可选）
predicted__browse_by_targets.txt.gz        预测靶点→成分（可选，部分镜像为单下划线文件名）
```

每位成员复制 `configs/batman_data.example.json` 为 Git 忽略的 `configs/batman_data.local.json`，将 `data_dir` 改为本机目录（绝对路径或相对项目根目录）。也可设环境变量 `PHARM_BATMAN_DATA_DIR`（优先于配置文件）；任务可用 `batman_local_dir` 显式指定。原始数据文件不提交 Git。

herb_targets 阶段的查询纪律：known 行 score 留空且不过滤；predicted 行仅在本地 predicted 文件存在时纳入，保留 score 严格大于任务阈值（默认 0.84）者；阈值依据（0.84）沿用此前医院方确认（juglone 抗膀胱癌研究），仍建议在导入时用真实导出核对分值尺度并留记录。provenance 记录各文件 SHA-256；`accessed_at` 取任务提供的真实下载日期，未知留 null，不用归档时间冒充。没有本地文件时阶段明确 blocked，无在线回退。

## 本地疾病索引或 Open Targets 查询

用合格批次建库（通用批次格式与校验纪律见 [导入说明](IMPORTS.md)）：

```powershell
.\.venv\Scripts\python.exe -m pharm.discovery.query --db local/discovery/disease_index.sqlite prepare --batch <批次目录>
```

- 默认索引 `local/discovery/disease_index.sqlite`（Git 忽略）；`configs/discovery_data.local.json`（模板 `configs/discovery_data.example.json`）可指向其他版本文件。批次与索引文件均不提交 Git。
- 索引未准备且未启用 Open Targets 查询时 disease_reverse 明确 blocked；preflight 的 availability.json 会记录缺失与指引。

## 已移除的数据源

GeneCards、OMIM 网页采集、导出转换和人机协助接口已移除，不再需要为其准备账号、浏览器或 websockets。历史私有数据与认证目录保留在本机，不上传 Git。

## 数据沉淀

`data/pharm/<归档名>/` 下固定四步目录（01_preflight/02_herb/03_reverse/04_review），live 每步结束即归档，哈希与受限状态保留，fixture 不进入。详见 [导入说明](IMPORTS.md) 与 [数据契约](DATA_CONTRACT.md)。
