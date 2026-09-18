# 真实数据导入与人工接续

网络或账号受限时，Agent 保留页面证据和缺口；不生成替代靶点。可以在授权访问后获取原始导出，按以下格式整理后恢复运行。人工导入是可选接续方式，报告中明确标记，不冒充
Agent 自动采集。

## 目录

在 `data/imports/<task_id>/` 下保存：

```text
herb_targets.csv     herb,compound_id,gene_symbol,score,evidence
genecards.csv       disease,gene_symbol,relevance_score
omim.csv            disease,gene_symbol
provenance.json      来源、完整性、查询范围、阈值与映射记录
raw/                原始导出与基因映射依据
```

`herb_targets.csv` 的 `evidence` 列取 `known` 或 `predicted`：known TTI 为文献验证的二值证据，score 列必须留空、不参与阈值过滤；predicted 行 score 必填且按阈值过滤。

UTF-8 CSV 保留原始分数，不能只导入已筛选的 GeneCards 记录。BATMAN
阈值 score>0.84 已由医院方于 2026-09-17 确认（依据 juglone 抗膀胱癌研究），仅作用于 predicted 行；导入时仍须用 v2.0 真实导出核对分值尺度并保留记录。表中药材名称必须对应任务中的药材；炮制形式匹配问题先复核。所有原始文件保留不覆盖。

## 来源台账模板

下面是待填写模板，不是已完成的来源记录。`complete` 与 `confirmed` 只有核验后才能改为 `true`
，URL、日期和版本必须填写实际值。`raw_files` 为相对当前导入目录的真实文件路径。

```json
{
  "sources": {
    "batman": {
      "complete": false,
      "source_url": "http://bionet.ncpsb.org.cn/batman-tcm",
      "accessed_at": "待填写",
      "herbs": ["白芍", "炙甘草"],
      "raw_files": ["raw/batman_export.csv"],
      "threshold": null,
      "threshold_confirmed": false
    },
    "genecards": {
      "complete": false,
      "source_url": "https://www.genecards.org/",
      "accessed_at": "待填写",
      "diseases": ["Hyperthyroidism"],
      "raw_files": ["raw/genecards_export.csv"]
    },
    "omim": {
      "complete": false,
      "source_url": "https://omim.org/",
      "accessed_at": "待填写",
      "diseases": ["Hyperthyroidism"],
      "raw_files": ["raw/omim_export.csv"]
    }
  },
  "mapping": {
    "confirmed": false,
    "method": "待填写实际 HGNC 标识核验或映射方法",
    "version": "待填写使用的数据版本",
    "raw_files": ["raw/gene_mapping.csv"]
  }
}
```

运行器检查文件、查询范围、原始路径和确认状态，但不能仅凭台账证明每条数据真实性；实际原文抽检仍需保留。基因拼写校验不等于 HGNC
官方映射。

各来源补充实际数据库 `version` 及阈值依据；BATMAN 要核对 v2.0，GeneCards 原文 v5.26.0
不能直接当成本次访问版本。当前导入校验尚未逐库强制验证版本字段或网页版本，人工来源核验仍是验收要求。`accessed_at`
在导入台账中使用 `YYYY-MM-DD`，不能填“待填写”后宣称可运行。

## 补充后运行

在页面选择此前运行并点击“断点续跑”，或在项目目录使用：

```powershell
.\.venv\Scripts\python.exe scripts/run_tasks.py --resume 实际运行编号
```

输入或参数变化会触发对应步骤重新执行，新产物保存到新的 attempt 目录；旧文件保留。输入与产物哈希一致且验收成功的步骤才复用。

当前真实网络分支优先读取已配置的 STRING 12.0 本地物种文件，未配置时调用 STRING 公开 API；本地文件目录按 [环境准备](ENVIRONMENT_SETUP.md#string-120-本地数据) 设置，任务可用 `string_source` 显式指定来源。CytoNCA 桥接（非加权 Degree）与 DAVID 正式导出均已接入，NetworkX 与本地合成统计产物仍分别标注实际方法，不冒充 CytoNCA/DAVID。正式拓扑指标口径、DAVID 背景集与统计方法确认仍是待完成项，已有界面和角色不代表研究参数已确认。

两侧导入通过后实际执行 Venny 2.1.0，保存输入、原始结果和图，并独立核对后才发布下游名单。Venny
失败时保留证据，不用本地交集替换；两非空列表无重叠则如实记录零交集。完整导入也不自动代表科学验收完成，详见 [当前进度](PROJECT_STATUS.md)。

## 分步归档与批次导入

每个 live 步骤结束即归档到 `data/pharm/<方名>/01_batman/`、`02_disease/`、`03_intersect/`、`04_ppi/`、`05_enrich/`
；各阶段内使用 `run_<run_id>/attempt_<nn>/`。访问受限和部分完成也保存证据并保留真实状态；**归档不等于研究完成**。fixture
禁止进入该目录。

原始导入取运行时的 `input_snapshot/`
，不从之后已变化的导入目录回填。保留完整文件相对路径，避免重名文件相互覆盖。`_archive.json` 记录每个原始文件/产物的
SHA-256、来源访问日期、任务参数、状态及运行时计数；方名目录 `_meta.md` 追加流水账。重复归档只复用哈希一致的批次，变动时拒绝覆盖。

旧的 `data/imports/<task_id>/` 仍可使用。推荐新批次放在：

```text
data/pharm/<方名>/imports/<task_id>/<批次名>/
  herb_targets.csv
  genecards.csv
  omim.csv
  provenance.json
  raw/
```

在 `tasks/tasks.local.jsonl` 该任务增加 `"import_batch":"20260913_01"`
即明确选用该批次；不猜测“最新文件夹”。不配置时使用旧入口。每次补充/修订新建批次，`raw/`
原始导出保持不变。阶段归档为不可变历史，不能直接把历史归档当作可编辑导入区。

修改任务的 `import_batch` 后需要从任务创建新运行；`--resume` 使用旧 manifest
的冻结任务，不会自动读取任务清单中的新批次值。仅在同一冻结导入位置补齐缺失文件时可恢复原运行；已有原始文件仍不得覆盖。

## 双库独立导入与暂定中位数规则

GeneCards 与 OMIM 各自校验本库来源台账、原始文件及共同映射依据；缺少 OMIM 文件不会阻止 GeneCards
独立处理。每支仅快照本库所需文件和元数据，另一数据库的单独更新不会使已成功分支失效。共同映射或任务参数变化仍会重新核验。

多疾病 CSV 必须含 disease 列，与任务关键词完全对应；单疾病旧文件可省略，程序按唯一关键词补齐。GeneCards 先对单个疾病的查询行分别计算中位数，再严格保留
score > 该疾病中位数，最后按基因去重并与 OMIM
合并。跨疾病同一基因保留各自分数，不预先取最大值或平均值；同一疾病/基因重复行会拒绝导入，避免重复分页改变中位数。零结果查询也必须包含在完整性台账的疾病范围中；台账不能代替原始查询证据。

`genecards_median_scope=per_disease_median`，`genecards_median_status=confirmed`
是 2026-09-17 医院方确认后的新任务默认规则；旧任务保持冻结的 pooled_query_rows/provisional 口径，不能改写旧运行结果。分支产物保留中位数、输入/保留行数、每个查询的行数、保留记录及规则状态。

新版真实来源证据分别归档在 `02_disease/genecards/run_<id>/attempt_<nn>/` 和 `02_disease/omim/run_<id>/attempt_<nn>/`
；合并结果仍在 `02_disease/run_<id>/attempt_<nn>/`。两路原始文件不互相覆盖，旧版归档保持原样。
