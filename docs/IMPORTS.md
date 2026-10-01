# 真实数据导入与批次格式

更新：2026-10-01。GeneCards/OMIM 专用采集与导入已删除，新批次统一使用 `associations.csv`。历史 SQLite 索引仍可查询；原始导出与历史报告不删除。

## 通用疾病关联批次

```text
associations.csv    必需 disease,gene_symbol；可选 score,source,extra
provenance.json     sources.associations + mapping
raw/               原始数据、查询响应或快照清单
```

可选同时携带 BATMAN：

```text
herb_targets.csv    herb,compound_id,gene_symbol,score,evidence
provenance.json     另含 sources.batman（herbs、threshold、threshold_confirmed）
```

- `complete=true`、`mapping.confirmed=true` 仅在对应来源与映射已核验时填写。
- `source_url` 为 http(s) URL；`accessed_at` 为实际访问 ISO 日期；`raw_files` 必须存在且不得越出批次目录。
- 原始文件哈希记录于索引，输出库不覆盖旧版本。
- 关联 `score` 可空，非空须为非负有限数；不同来源的分值不混成统一疗效分数。
- 同疾病/基因/来源重复行拒绝；非法基因符号剔除并留档，不补造别名。
- 疾病顺序按 CSV 首次出现；零有效关联的疾病不进索引；通用建库最多 500 种疾病。
- BATMAN known 是文献验证的二值证据，score 留空；predicted 必须有分值，仅保留严格大于声明阈值者。

```powershell
.\.venv\Scripts\python.exe -m pharm.discovery.query `
  --db local/discovery/disease_index.sqlite prepare --batch <批次目录>
```

## Open Targets 查询

工作台“启用 Open Targets 数据查询”对应任务 `online_collect=true`，`online_sources` 仅支持 `["open_targets"]`。留空疾病范围时使用 BATMAN 靶点发现候选疾病；填写范围时执行疾病→靶点查询。结果写入本次运行专用 SQLite 文件，不覆盖全局索引。

`PHARM_OPEN_TARGETS_MODE=online`（默认）调用 GraphQL API；`local` 读取本地 Parquet 快照。两者均不使用网页登录、验证码或协助画布。

独立查询与建库：

```powershell
.\.venv\Scripts\python.exe -m pharm.diseases.open_targets collect `
  --genes EGFR TP53 --output local/open_targets_target_pilot

.\.venv\Scripts\python.exe -m pharm.diseases.open_targets collect `
  --diseases "Hyperthyroidism" --output local/open_targets_disease_pilot

.\.venv\Scripts\python.exe -m pharm.discovery.query `
  --db local/open_targets_target_pilot/disease_index.sqlite `
  prepare --batch local/open_targets_target_pilot
```

Open Targets 以疾病 ID 与标签组成 disease 键，Ensembl ID、疾病 ID 和可获得的数据源分数保存在 `extra`；API 原始完整分页保存于 `raw/open_targets_responses.jsonl`。

第三阶段现存试验默认为每疾病或每靶点 Top-100、association score≥0.2，未改为正式研究规则。调整入口：`PHARM_OPEN_TARGETS_TOP_K`、`PHARM_OPEN_TARGETS_MIN_SCORE`、`PHARM_OPEN_TARGETS_PAGE_SIZE`、`PHARM_OPEN_TARGETS_REQUEST_DELAY`、`PHARM_OPEN_TARGETS_TIMEOUT`。直接 CLI 查询可显式指定 `--top-k-per-disease` 或 `--top-k-per-target`、`--min-score`；不传时不自动沿用主流程试验选择。实际执行规则始终记录于产物。

## Open Targets 本地快照

```text
data/open_targets/26.09/
  manifest.json
  target/*.parquet
  disease/*.parquet
  association_overall_direct/*.parquet
```

`manifest.json` 记录 release 与 datasets 文件信息。需要 `pyarrow`（已列入 requirements）。

```powershell
$env:PHARM_OPEN_TARGETS_MODE = "local"
$env:PHARM_OPEN_TARGETS_DATA_DIR = "D:\PharmacologyMultiAgent\data\open_targets\26.09"
.\.venv\Scripts\python.exe -m pharm.diseases.open_targets collect `
  --mode local --data-dir $env:PHARM_OPEN_TARGETS_DATA_DIR `
  --genes EGFR TP53 --output local/open_targets_local_pilot
```

可用 `configs/open_targets_data.local.json` 指定 data_dir，模板在同目录；环境变量优先。`PHARM_OPEN_TARGETS_RELEASE` 可固定期望版本。

本地模式读取 `association_overall_direct`，不混入 indirect 关联。基础三类数据集没有 `datasourceScores`，因此不伪造；需要分源分数时另行准备相应官方数据集。

## 兼容与归档

- 旧任务若指定已移除的在线来源，应保存为新任务；不回写旧任务或旧运行快照。
- 旧 SQLite 的来源标签、原始分值仍可查询，旧库不会被改名为 Open Targets。
- `relevance_score` 是现有证据输出字段名，为历史接口兼容保留；对 Open Targets 存放其原始 association score，不能解释为治疗概率。
- `heuristic_v2` 去除旧库专用归一分值，沿用原权重；BATMAN known 占比是 evidence_quality 唯一来源，缺失时 null。原始关联分值仅展示。
- live 逐阶段归档至 `data/pharm/<方名或 custom_task_id>/`；blocked/partial 也保留；fixture 不进入科研归档。
