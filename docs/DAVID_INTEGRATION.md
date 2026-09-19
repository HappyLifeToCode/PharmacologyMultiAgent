# DAVID 真实接入

2026-09-14 已在本机完成真实 DAVID 小样本提交、识别、物种背景/自定义背景选择、GO BP/CC/MF 与 KEGG 导出，并接入现有 Runner。正式研究背景与统计口径尚未确认，本次结果均为工程验证。

## 实际使用的服务

DAVID 的 SOAP Web Service 要求注册邮箱。本轮采用新版工作台自身使用的会话式 JSON 接口，不依赖 SOAP 注册，不使用本地富集算法替代官方统计：

| 操作 | 已观察并验证的路径 |
|---|---|
| 按物种转换标识 | `PUT /REST/convertToDAVIDListJSONLower` |
| 添加前景或背景列表 | `PUT /listManager?action=addList` |
| 回读前景与识别计数 | `getCurrentList`、`getCurrentListDIDCount` |
| 选择并回读背景 | `getAllPopulationNames`、`setCurrentPopulation`、`getCurrentPopulationName` |
| 注释类别清单 | `GET /getAnnotationSummary` |
| 富集原始统计 | `GET /getAnnotationChart?annot=数字类别ID&ease=1&count=1` |

请求格式依据官网 `sidebar.html`、`assets/scripts/listManager.js`、`summary_ws.html`、`chartReport.html` 和 `assets/scripts/workspace.js` 核实。数字类别 ID 从当次官方清单获取，不在代码中硬编码。网页内部接口并非有稳定版本保证的公共 REST API；结构变更时适配器失败并保留证据，不猜测字段。

每次运行创建全新会话，前景和背景处于同一会话中；不复用浏览器个人会话。进程内请求间隔至少 10 秒，提交不自动重试，访问限制不会绕过。初版适配器保守限制前景与自定义背景各不超过 400 个标识；这是当前工程支持范围，不是对新版服务官方上限的断言。物种背景由 DAVID 提供，不受这个本地上传数量限制。

## 方法与配置

DAVID 网页的 `P-Value` 对应 **EASE（修改版 Fisher exact test）**。`benjamini` 对应 BH 1995 线性 step-up 校正；另一个 `fdr` 是不同的自适应校正。`fisher` 也单独返回，不能把它的值与 EASE 对应的 BH 混用。原始字段全部保留，不重新生成或替换官方 p 值。

说明来源：[DAVID 检验与校正说明](https://davidbioinformatics.nih.gov/helps/functional_annotation.html#fisher)。本次官网公告最新知识库为 `v2026_1`；统计接口本身未返回构建版本，因此记录为“官网公告版本”，不声称已证明当前查询后端的精确构建号。

任务的 `david_enrichment` 为显式配置接口，目前通过本地任务 JSON 使用，网页表单尚无该配置编辑器。下面是**技术样本配置**，不得据此确定正式研究参数：

```json
{
  "taxon_id": 9606,
  "david_enrichment": {
    "purpose": "engineering_smoke",
    "confirmed": false,
    "test": "EASE",
    "correction": "Benjamini",
    "fdr_lt": 0.05,
    "categories": ["GOTERM_BP_DIRECT", "GOTERM_CC_DIRECT", "GOTERM_MF_DIRECT", "KEGG_PATHWAY"],
    "background": {
      "mode": "species",
      "name": "Homo sapiens",
      "rationale": "仅用于技术验证，不代表正式背景"
    }
  }
}
```

自定义背景改为 `{"mode":"custom","genes":["实际背景符号"],"rationale":"选择依据"}`。背景须包含所有前景输入；转换后还会核对已识别前景 DAVID ID 是否包含在已识别背景内，以及统计表的有效背景总数是否超出该背景。实际背景名须回读一致，选择背景后再次回读前景，防止前景发生变化。

正式运行需 `purpose=research` 和真实的方法确认 `confirmed=true`。缺少背景依据、类别或方法确认时，程序不提交。旧任务中的 `enrichment_test_required=hypergeometric` 与 EASE 不一致时也会阻塞正式提交；需在研究方确认实际方法后同步，不可只修改名称冒充一致。DIRECT、FAT、ALL 为不同 GO 类别，必须由任务显式选择。

## 小样本复现

```powershell
python -m pharm_demo.david_smoke --background species --output local/checks/david-species-01
python -m pharm_demo.david_smoke --background custom --output local/checks/david-custom-01
```

输出必须使用新目录，重试不会覆盖旧结果。样本为 TP53、MDM2、EGFR、AKT1、BRCA1、BRCA2、CDKN1A、BAX 和刻意设置的未识别标识 ZZZPHARMSMOKETEST；它们不代表用户方剂的靶点。自定义背景再加入 ALB、VEGFA、IL6、TNF、GAPDH、ACTB，同样只用于工程测试。

本机真实观察：

| 技术样本 | 输入识别 | 背景 | 返回条目 | Benjamini < 0.05 |
|---|---|---|---|---|
| species | 8/9；1 未识别 | DAVID Homo sapiens | 822 | 76 |
| custom | 8/9；1 未识别 | 上传 15，识别 14 | 822 | 0 |

两次全量表均为 BP 489、CC 94、MF 101、KEGG 138 条。本次包含 EASE=1 的记录，确认没有沿用网页默认 EASE=0.1 / Count=2 的预筛选。以上是带日期的实测结果，不是程序必须凑出的数量；零显著结果是有效结果。

## 产物、调度与边界

- `david_input.json`：完整共同靶点、物种与显式配置。
- `david_identification.json`、`david_mapping.csv`、`david_unmapped.csv`：官方识别与完整输入计数。
- `david_background.json`：背景依据与实际选择；自定义背景另存识别报告和映射 CSV。
- `david_categories.json`、`david_chart_raw.json`：当次类别 ID、完整官方统计响应。
- `david_all_terms.csv`、`david_significant_terms.csv`：全量返回表和 BH 筛选表；各 GO/KEGG 类别另存分表，即使无显著结果也保留表头。
- `enrichment_david.json`、`david_execution.json`：计数、方法、状态、限制及执行时间。
- `david_requests.json`、`david_artifacts.json`、`sources/`：请求记录、哈希和原始响应；不保存认证头或会话 cookie，原始 sources 不作为网页公开下载产物。

统计检查包括完整输入记账、物种、DAVID ID、类别范围、列联计数、有限概率范围，以及背景内包含前景。转换接口当前返回的列标题与数组位置存在不一致：代码显式区分转换记录与标准前景报告的结构，并交叉核对，避免照标题错读 ID。

Runner 使用与网络分支相同的完整共同靶点，接入现有阶段详情、哈希归档和恢复指标。Agent 审核执行证据，不再次重复提交。缺参数/无识别结果为 blocked，识别后服务失败为 partial，结构或数据不一致为 failed。技术样本在调度阶段保留 partial 和科学限制；独立小样本入口的 succeeded 仅表示工具链通过。全流程 scientific_complete 仍为 false。

已做真实工具小样本和隔离模型的调度回归；未执行用户正式上游数据或完整真实模型会话。未开发独立产物验收平台。

2026-09-17 医院方经同门确认：正式背景为 Homo sapiens 物种背景，GO 类别用 DIRECT（BP/CC/MF），KEGG 保留。2026-09-19 医院方进一步确认：使用 DAVID 原生导出结果（BH 校正用于筛选，绘图用原始 p 值）——即接受 DAVID 实际统计方法 EASE（修改版 Fisher exact test），任务 enrichment_test_required 已同步为 EASE，原始字段标签不变。
