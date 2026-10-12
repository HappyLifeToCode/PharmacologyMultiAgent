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

`PHARM_OPEN_TARGETS_MODE=local`（默认）读取本地 Parquet 快照；只有显式指定 `online` 才调用 GraphQL API。两者均不使用网页登录、验证码或协助画布。

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

Open Targets 以疾病 ID 与标签组成 disease 键，Ensembl ID、疾病 ID 和可获得的数据源分数保存在 `extra`；API 原始完整分页保存于 `raw/open_targets_responses.jsonl`。每条关联的 `extra` 同时携带疾病实体元数据：`disease_id`（主键）、`canonical_disease_id`、`canonical_name`、`aliases`、`mondo_id`、`mondo_mapping`、`ontology`、`parents`、`ancestors`、`entity_type`。disease_id 为 Open Targets 原始 ID；MONDO 仅为可选映射；exactSynonyms 只作别名展示；父子疾病只标记关系不合并。批次产物按候选分层保存：`candidates_complete.csv`（完整层，含全部原始 direct association）、`candidates_base.csv`（>0.2）、`candidates_strict.csv`（>=0.6）、`candidates_top50.csv`（disease 实体排序 Top-50）、`candidate_hierarchy.json`（候选间父子关系及来源）。

第三阶段采集默认不做 top-k/min-score 过滤（完整层必须保留全量 direct association），筛选只发生在候选分层。试验性过滤为显式启用：`PHARM_OPEN_TARGETS_TOP_K`、`PHARM_OPEN_TARGETS_MIN_SCORE` 设置后才生效（其余入口 `PHARM_OPEN_TARGETS_PAGE_SIZE`、`PHARM_OPEN_TARGETS_REQUEST_DELAY`、`PHARM_OPEN_TARGETS_TIMEOUT`）。直接 CLI 查询可显式指定 `--top-k-per-disease` 或 `--top-k-per-target`、`--min-score`；不传时不自动沿用主流程试验选择。实际执行规则始终记录于产物。启用 top-k/min-score 的批次未保留全量 direct association，provenance 标记 `complete=false` / `raw_rows_complete=false`，严格导入校验将拒绝该批次；完整层必须使用未过滤的采集。

## Open Targets 本地快照

```text
data/open_targets/26.09/
  manifest.json
  target/*.parquet
  disease/*.parquet
  association_overall_direct/*.parquet
```

`manifest.json` 记录 release 与 datasets 文件信息。需要 `pyarrow`（已列入 requirements）。

## Monarch 本地全量数据（OMIM 功能替代，默认关闭）

```text
data/monarch/2026-10-07/
  metadata.yaml
  omim_gene_to_disease_edges.tsv        基因—疾病（OMIM morbidmap 摄入）
  hpoa_gene_to_disease_edges.tsv        基因—疾病（HPOA/Orphanet 等）
  clingen_gene_disease_edges.tsv        基因—疾病（ClinGen）
  hpoa_disease_to_phenotype_edges.tsv   疾病—HPO 表型（频率/negated/onset/PMID）
  hpoa_disease_mode_of_inheritance_edges.tsv  遗传方式
  hgnc_gene_nodes.tsv                   symbol ↔ HGNC/OMIM 基因映射
  phenio_nodes.tsv                      实体名称（NCBIGene/MONDO/HP…）
  mondo.sssom.tsv                       疾病本体 → MONDO（subject=MONDO，取 skos:exactMatch）
```

配置：复制 `configs/monarch_data.example.json` 为 Git 忽略的 `configs/monarch_data.local.json` 填本机 `data_dir`，或设 `PHARM_MONARCH_DATA_DIR`（优先）。启用：`PHARM_MONARCH_ENABLED=true` 或任务 `monarch_enabled=true`；默认关闭时 Open Targets 流程行为完全不变。原始文件不提交 Git；下载后经 metadata.yaml 官方 SHA-256 校验方可使用（校验记录见 `local/monarch_validation_20261010/`）。仅对 Top-50 候选补充证据；结果不能解释为临床疗效或诊断结论。

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
