# 真实数据导入与人工接续

网络或账号受限时，Agent 保留页面证据和缺口；不生成替代靶点。可以在授权访问后获取原始导出，按以下格式整理后恢复运行。人工导入是可选接续方式，报告中明确标记，不冒充 Agent 自动采集。

## 目录

在 `data/imports/<task_id>/` 下保存：

```text
herb_targets.csv     herb,compound_id,gene_symbol,score
genecards.csv       gene_symbol,relevance_score
omim.csv            gene_symbol
provenance.json      来源、完整性、查询范围、阈值与映射记录
raw/                原始导出与基因映射依据
```

UTF-8 CSV 保留原始分数，不能只导入已筛选的 GeneCards 记录。BATMAN 阈值依据平台说明确认，不预设分数尺度。表中药材名称必须对应任务中的药材；炮制形式匹配问题先复核。所有原始文件保留不覆盖。

## 来源台账模板

下面是待填写模板，不是已完成的来源记录。`complete` 与 `confirmed` 只有核验后才能改为 `true`，URL、日期和版本必须填写实际值。`raw_files` 为相对当前导入目录的真实文件路径。

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

运行器检查文件、查询范围、原始路径和确认状态，但不能仅凭台账证明每条数据真实性；实际原文抽检仍需保留。基因拼写校验不等于 HGNC 官方映射。

## 补充后运行

在页面选择此前运行并点击“断点续跑”，或在项目目录使用：

```powershell
& D:\Anaconda\envs\prim\python.exe scripts/run_tasks.py --resume 实际运行编号
```

输入或参数变化会触发对应步骤重新执行，新产物保存到新的 attempt 目录；旧文件保留。输入与产物哈希一致且验收成功的步骤才复用。

当前真实网络分支可调用 STRING 公开 API；NetworkX 度值会明确标注方法，不能冒充 CytoNCA。DAVID 正式导出、背景集确认及 CytoNCA 接入仍是待完成项，已有界面和角色不代表这些分析已完成。

## 分步归档与批次导入

每个 live 步骤结束即归档到 `data/pharm/<方名>/01_batman/`、`02_disease/`、`03_intersect/`、`04_ppi/`、`05_enrich/`；各阶段内使用 `run_<run_id>/attempt_<nn>/`。访问受限和部分完成也保存证据并保留真实状态；**归档不等于研究完成**。fixture 禁止进入该目录。

原始导入取运行时的 `input_snapshot/`，不从之后已变化的导入目录回填。保留完整文件相对路径，避免重名文件相互覆盖。`_archive.json` 记录每个原始文件/产物的 SHA-256、来源访问日期、任务参数、状态及运行时计数；方名目录 `_meta.md` 追加流水账。重复归档只复用哈希一致的批次，变动时拒绝覆盖。

旧的 `data/imports/<task_id>/` 仍可使用。推荐新批次放在：

```text
data/pharm/<方名>/imports/<task_id>/<批次名>/
  herb_targets.csv
  genecards.csv
  omim.csv
  provenance.json
  raw/
```

在 `tasks/tasks.jsonl` 该任务增加 `"import_batch":"20260913_01"` 即明确选用该批次；不猜测“最新文件夹”。不配置时使用旧入口。每次补充/修订新建批次，`raw/` 原始导出保持不变。阶段归档为不可变历史，不能直接把历史归档当作可编辑导入区。
