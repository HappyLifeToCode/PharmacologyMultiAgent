# 环境准备与验收

更新：2026-09-22（阶段G，与重构后代码对齐）。软件安装、数据可用与研究分析完成分别判断。完整缺口见 [项目进度](PROJECT_STATUS.md)。

## 必需软件

| 软件 | 用途 | 准备 |
|---|---|---|
| Python 3.9+（建议 3.11） | 全部运行时代码 | 官方安装包或既有环境 |
| Playwright Chromium | fixture 之外全部浏览器能力（人机协助、GeneCards 辅助工具、测试） | `python -m playwright install chromium` |
| Git | 版本管理 | 官方安装包 |

Python 依赖：`pip install -r requirements.txt`。不需要 Node.js、Cytoscape、Java 或模型服务即可运行主流程与全部测试。

首次验证（不需要任何研究数据或外网）：

```powershell
.\.venv\Scripts\python.exe scripts/doctor.py
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

## 可选软件（预留给在线采集阶段）

- **Codex CLI（icrc profile）**：当前 pipeline 无模型会话，`pharm/agents/runtime.py` 与 `configs/runtime.json` 为在线采集阶段预留，现状不调用、非必需。配置方式见 [Codex 执行配置](CODEX_RUNTIME.md)。

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

## 本地疾病索引（live 必需）

用合格批次建库（两类批次格式与校验纪律见 [导入说明](IMPORTS.md)）：

```powershell
.\.venv\Scripts\python.exe -m pharm.discovery.query --db local/discovery/disease_index.sqlite prepare --batch <批次目录>
```

- 默认索引 `local/discovery/disease_index.sqlite`（Git 忽略）；`configs/discovery_data.local.json`（模板 `configs/discovery_data.example.json`）可指向其他版本文件。批次与索引文件均不提交 Git。
- 索引未准备时 disease_reverse 明确 blocked；preflight 的 availability.json 会记录缺失与指引。

## 人机协助会话的显示要求

协助会话启动的是 **headed Chromium**（甲方要求用户可见并接管页面），需要本机有显示环境（Windows 桌面）。无显示环境启动会显式报错 `AssistUnavailable`，不静默降级为 headless。自动化测试使用 headless 是代码中明示的测试例外。

## 数据来源与账号纪律

不自动注册账号，不绕过验证码或 Cloudflare 等人机验证；密码和验证码由账号持有人保管，登录态仅存本机 Git 忽略目录。普通账号、机构授权、API 与批量下载权限分别核验；注册成功不等于取得合格导出。历史站点观察（BATMAN 502、GeneCards 要求学校邮箱、OMIM Cloudflare 挑战等）见 Git 历史中的旧文档，不能当作当前连通性结论。

## 数据沉淀

`data/pharm/<归档名>/` 下固定四步目录（01_preflight/02_herb/03_reverse/04_review），live 每步结束即归档，哈希与受限状态保留，fixture 不进入。详见 [导入说明](IMPORTS.md) 与 [数据契约](DATA_CONTRACT.md)。
