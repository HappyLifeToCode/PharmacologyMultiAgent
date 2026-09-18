# 环境准备与验收

更新：2026-09-13，依据当日已留存的软件与网站核验记录。本次文档整理没有重新访问五站；软件安装、网站可达与研究分析完成分别判断。完整缺口见 [项目进度](PROJECT_STATUS.md)。

## Playwright MCP

项目固定 `@playwright/mcp@0.0.64`，支持 `--save-trace`。

启动等待上限为 `startup_timeout_sec=120`，等价于 Kimi 的 `startupTimeoutMs: 120000`。浏览器使用 headless + isolated 和当前
Python 环境安装的 Chromium。

Codex 参数由 `pharm_demo/codex_runtime.py` 传入。它是允许 MCP 冷启动的等待上限，不是每一步固定睡眠 120 秒；普通工具调用超时另设为
90 秒。项目继续使用 icrc / gpt-5.6-luna / medium。

## Cytoscape 与 CytoNCA

- Cytoscape **3.10.0**：[官方发布](https://github.com/cytoscape/cytoscape/releases/tag/3.10.0)。成员自行选择安装目录，Windows
  安装后打开该目录的 `Cytoscape.exe`；个人路径不作为仓库启动要求。
- CytoNCA **2.1.6**：[官方 App Store](https://apps.cytoscape.org/apps/cytonca)。本机通过 Cytoscape 自身 App 安装接口安装官方
  JAR；桌面 App Store 列表及 Apps → CytoNCA → Open 已核验。
- 本机 Java 为 17.0.15。3.10.0 首次启动出现 `Invalid CEN header`；已核验安装包与官方 SHA-256
  一致，在该软件的 `Cytoscape.vmoptions` 增加 `-Djdk.util.zip.disableZip64ExtraFieldValidation=true`
  后启动正常。此为旧包兼容设置，其他机器仅在复现同类问题时评估，不照搬至全局 Java。
- 使用显式合成链 `TEST_A—TEST_B—TEST_C`，在 CytoNCA 勾选 Degree / without weight 并执行 Analyze，实际得到 **1、2、1**
  。已保存插件节点表和 `.cys` 会话，证明安装和该项计算可用。

CytoNCA 安装测试不代表已完成真实 STRING 网络分析。2026-09-14 起网络阶段经桥接插件自动调用已装 CytoNCA
计算非加权 Degree（NetworkX 仅作独立核对），详见 [CytoNCA 接入](CYTOSCAPE_INTEGRATION.md)；真实研究网络的正式运行与指标口径核验待真实上游数据到位后完成。

## 五站访问

以下来自 2026-09-13 开发机网络的已留存观察，不能替代当前连通性或学校网络实测。

| 来源         | 2026-09-13 实测             | 待办                      |
|------------|---------------------------|-------------------------|
| BATMAN-TCM | 跳转至端口 100 后返回 502         | 2026-09-17 新入口 `…/batman-tcm/#/home` 实测 200 可达；阈值口径待确认 |
| GeneCards  | Datacenter 网络验证页          | 用学校邮箱注册/登录，确认学术访问       |
| OMIM       | 403 / Cloudflare 安全验证     | 本人通过验证后再确认注册、下载及 API 权限 |
| STRING     | 首页可达，页面显示 12.0，并宣传另有 12.5 | 研究维持方案指定 12.0 稳定入口；已备 v12.0 人类本地数据作离线来源 |
| DAVID      | 首页可达，明确所有用户可免登录           | 真实导出已接入；正式背景集及统计口径仍需确认 |

普通网站账号、API 申请和批量下载授权分别核验；不能将注册成功视为全部访问权限已获得。密码和验证码由账号持有人保管，登录态仅存本机忽略目录。

STRING 离线来源：`data/string/v12.0/`（不入库）存放官方 v12.0 人类数据三件套（`9606.protein.info/aliases/links.detailed`，`.txt.gz` 或解压后的 `.txt` 均可）。网络阶段默认优先使用已配置的本地文件，未配置时调用在线 API；任务可用 `string_source`（`local_files` / `api`）显式指定其一，实际来源写入 `string_provenance.json`。映射为 preferred_name/aliases 精确匹配（歧义显式记录，不静默选择）；2026-09-14 用 TP53/MDM2/EGFR/AKT1/ZZZPHARMSMOKETEST 样本与在线 API 对拍，映射、边集与 Degree 完全一致。目录配置方式见下文“STRING 12.0 本地数据”。

OMIM 的普通复选框点击曾实际执行，随后仍出现新挑战，未进入主页。这只说明当次环境未通过，不能概括为“Playwright
永远不能通过”。注册与登录解决的是部分访问条件，尚需取得完整原始导出并接续数据导入。

重新采集带日期的首页证据：

```powershell
.\.venv\Scripts\python.exe scripts/check_sites.py
```

结果写入新的 `runs/diagnostics/sites_<日期时间>/`，包含 HTTP 原始响应、浏览器页面、截图和访问时间，不覆盖历史结果。首页核验没有正式查询或导出。

## BATMAN-TCM v2.0 本地全量数据

药味侧正式来源为 BATMAN-TCM v2.0 官网下载页的全量数据文件（条目数已与 2.0 官方说明核对：8,404 草药、2,339,061 预测 TTI）。`pharm_demo/batman_local.py` 按任务药材清单从本地文件生成契约导入包（`herb_targets.csv` + `provenance.json`），产物可直接通过 `imports.load_herb` 校验。本地目录必须包含：

```text
herb_browse.txt                            药材→成分（PubChem CID）
known_browse_by_ingredients.txt.gz         成分→已知靶点（文献验证，无分数）
known_browse_by_targets.txt.gz             靶点→成分（Entrez→symbol 映射依据）
predicted_browse_by_ingredients.txt.gz     成分→预测靶点（0~1 概率，可选）
predicted__browse_by_targets.txt.gz        预测靶点→成分（可选，部分镜像为单下划线文件名）
```

每位成员复制 `configs/batman_data.example.json` 为 Git 忽略的 `configs/batman_data.local.json`，再将 `data_dir` 改为本机目录。路径可使用绝对路径，也可使用相对于项目根目录的路径。也可设置 `PHARM_BATMAN_DATA_DIR`，环境变量优先于本机配置文件；任务可用 `batman_local_dir` 显式指定目录。共享代码、任务清单和文档不写死个人盘符；原始数据文件不提交 Git。

known TTI 为文献验证的二值证据，生成的行 `evidence=known` 且 score 留空、不参与阈值过滤；predicted 行仅在任务 `batman_include_predicted=true` 时生成，score 为 v2.0 概率值，由 `load_herb` 按任务阈值（默认 0.84，医院方已确认）过滤。任务必须提供 `batman_accessed_at`（实际下载日期 YYYY-MM-DD），归档时间不可冒充数据库访问时间。运行时核验各文件表头、分隔符与文件 SHA-256（写入 `raw/batman_full_files.manifest.json`）。BATMAN 无在线回退：未配置本地文件时生成分明确失败，见 [分来源数据能力](#分来源数据能力含回退语义)。

## STRING 12.0 本地数据

正式网络阶段优先读取已配置的 STRING 物种数据，未配置时才调用公开 API；任务可用 `string_source`（`local_files` / `api`）显式指定其一，实际来源写入 `string_provenance.json`。本地目录必须同时包含与任务 `taxon_id`、`string_version` 一致的三个文件（官方 `.txt.gz` 压缩包直接读取，已解压的 `.txt` 也可接受，同名时优先 `.txt.gz`）：

```text
9606.protein.aliases.v12.0.txt.gz
9606.protein.info.v12.0.txt.gz
9606.protein.links.detailed.v12.0.txt.gz
```

每位成员复制 `configs/string_data.example.json` 为 Git 忽略的 `configs/string_data.local.json`，再将 `data_dir` 改为本机目录。路径可使用绝对路径，也可使用相对于项目根目录的路径；模板采用 Git 已忽略的 `data/string/v12.0`。也可设置 `PHARM_STRING_DATA_DIR`，环境变量优先于本机配置文件。共享代码、任务清单和文档不写死个人盘符；原始 STRING 文件不放入 `docs/`，也不提交 Git。

运行时核验 gzip 表头、物种前缀、评分范围和文件 SHA-256。`combined_score` 从 0–1000 转为项目使用的 0–1，按任务阈值筛选并将双向重复记录合并为一条无向边。本地模式目前要求 `string_additional_nodes=0`。

## 分来源数据能力（含回退语义）

各数据库的接入方式与"没有本地数据时怎么办"并不相同，2026-09-17 团队确认按此表执行，不做跨来源的类推：

| 来源 | 接入方式 | 没有本地数据时 | 说明 |
|---|---|---|---|
| BATMAN-TCM | 本地全量数据 或 人工导出导入批次 | **明确 blocked，无在线回退** | 网页查询是人工表单，不是等价的程序化接口；自动回退网页等于爬虫，属于另行建设的采集自动化，不作默认路径 |
| STRING | 本地 v12.0 文件优先 | 回退公开 API | 团队显式决定的特例：API 是与本地文件等价的程序化接口；任务可用 `string_source` 强制某一侧 |
| GeneCards | 仅人工导出导入 | 明确 blocked | 需学术访问；无程序化替代 |
| OMIM | 仅人工导出导入 | 明确 blocked | 人机验证需本人完成；无程序化替代 |
| DAVID | 仅在线服务（新版工作台 JSON 接口） | 不可用即 blocked | 无本地等价物；本地合成统计只作工程验证并显式标注 NOT DAVID |

统一原则：任何来源缺失时都是 blocked/partial 加证据保留，不用其他来源或本地绘图顶替；回退只存在于团队逐项显式确认过的地方（目前仅 STRING）。

## Venny 与测试依赖
Venny 2.1.0 由 Python Playwright 直接操作官方页面，不通过上述 MCP。新 live/fixture 运行都依赖 Chromium、模型服务和 Venny
访问；Venny 不读取其他 Agent 的登录态或兼容脚本配置。2026-09-13
已验证有交集3及无交集0，真实药理输入仍待上游完整数据。产物与失败规则见 [Venny 接入](VENNY.md)。

项目单元测试会替换远端调用；本地43项自动测试通过不代表所有网站当下可达。已有 HTML 报告可离线展示，新执行不能当作纯离线测试。

## 数据沉淀

`data/pharm/<方名>/` 下固定五步目录；每个 live 步骤结束即归档，原始快照及哈希保留，受限状态明确记录。重复运行新建
attempt，合成数据不进入此处。详见 [导入与归档说明](IMPORTS.md)。
