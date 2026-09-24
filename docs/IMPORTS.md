# 真实数据导入与批次格式

疾病索引与 pipeline 只消费**本地批次**：在授权访问后获取原始导出，按以下两类格式之一整理。不生成替代数据；不完整、未确认的来源一律被拒绝而不是放行。

两类批次共用一套 provenance 校验纪律（`pharm/core/imports.py`）：

- `complete: true`、`mapping.confirmed: true` 只有核验后才能填写；
- `source_url` 必须是 http(s) URL；`accessed_at` 必须是 ISO 日期（YYYY-MM-DD），不能用"待填写"或归档时间冒充；
- `raw_files` 为相对批次目录的真实文件路径，必须存在且不得越出批次目录；
- 建库时对批次文件做 SHA-256 快照，建库期间文件变动即失败；库 SHA-256 写入查询结果。
- 基因符号只做格式校验（不等于 HGNC 权威映射）；非法符号按批次类型的规则处理并留档。

## 批次类型一：GeneCards+OMIM 三源批次

用于同时携带 BATMAN 药材关系与两库疾病关联的完整批次（可按药材反查）：

```text
herb_targets.csv    herb,compound_id,gene_symbol,score,evidence
genecards.csv       disease,gene_symbol,relevance_score
omim.csv            disease,gene_symbol
provenance.json     sources.batman / sources.genecards / sources.omim + mapping
raw/                原始导出与基因映射依据
```

- `herb_targets.csv` 的 `evidence` 列取 `known` 或 `predicted`：known 为文献验证的二值证据，score 列必须留空、不参与阈值过滤；predicted 行 score 必填且按阈值过滤（严格大于）。行级校验拒绝非法行；药材名必须对应 provenance 声明的 herbs。
- provenance 每个来源声明 `diseases`（查询范围）：CSV 行不得超出声明；声明了但零关联的疾病不进索引（如实）。
- 多疾病 CSV 必须含 disease 列；单疾病旧文件可省略，程序按唯一声明补齐。GeneCards 同疾病/基因重复行拒绝（防重复分页）。
- BATMAN 侧可用 `pharm/batman/local.py` 的 `generate_import` 从 v2.0 全量文件直接生成该批次（含 per-file SHA-256 清单），需要任务提供真实下载日期 `batman_accessed_at`。

## 批次类型二：通用 associations.csv 批次

用于接入任意本地合规疾病-基因关联表（只可建疾病索引，不含药材关系）：

```text
associations.csv    必需列 disease,gene_symbol；可选列 score,source,extra
provenance.json     sources.associations + mapping
```

- `source` 缺省填 `associations`；`score` 必须是非负有限数值（可空）；`extra` 等其余列原样进记录。
- 同疾病/基因/来源重复行拒绝（防重复导出页）；非法符号剔除并留档 `<索引名>.rejected_symbols.csv`，不补造；全批次零有效行拒绝建库；疾病数量上限 500。

## 自动识别与建库

```powershell
.\.venv\Scripts\python.exe -m pharm.discovery.query --db local/discovery/disease_index.sqlite prepare --batch <批次目录>
```

批次目录有 `associations.csv` 走通用通道；有 `genecards.csv`/`omim.csv` 走三源通道；**两类文件共存时报错而不是猜测**。输出索引必须不存在（不覆盖）；来源修订建新版文件。

## 导出辅助工具

以下工具把人工取得的导出转换为批次所需 CSV，均核对完整性、不一致即报错（保留证据），不允许第一页冒充全表：

```powershell
# GeneCards：人工保存完整结果页 HTML（每页一个文件）后解析合并
.\.venv\Scripts\python.exe -m pharm.diseases.genecards_export collect --disease "Rheumatoid arthritis" --pages page1.html page2.html --output local/gc-collect
.\.venv\Scripts\python.exe -m pharm.diseases.genecards_export combine --inputs local/gc-collect/genecards_*.csv --output genecards.csv
# GeneCards：转换登录后官方导出 CSV
.\.venv\Scripts\python.exe -m pharm.diseases.genecards_export convert --files export1.csv --output genecards.csv
# OMIM：转换 Gene Map 导出（xlsx/zip）
.\.venv\Scripts\python.exe -m pharm.diseases.omim_export convert --files genemap2.xlsx --output omim.csv
```

OMIM 在线采集（只使用本人有权访问的页面；遇到登录/验证码会转入工作台协助）可调用：

```powershell
.\.venv\Scripts\python.exe -c "from pharm.diseases.omim_online import collect_online; collect_online(['Hyperthyroidism'], 'local/omim-online')"
```

采集器只保存授权页面提供的 Gene Map 导出文件和原始 HTML，不把搜索摘要当作 OMIM 基因关联；下载文件仍需用上面的 `omim_export convert` 转换并经过批次校验。

`pharm/diseases/genecards_online.py` 是在线检索采集模块（headed Chromium 逐页读取并核对声明总数；Cloudflare 拦 headless）。遇到人机验证时，它会通过 `/api/assist/request` 把当前页面交给工作台，等待用户完成验证并点击“验证完成，继续采集”，再回到原疾病关键词继续采集。当前 discovery pipeline 尚未自动调用该在线采集器，接入入口仍需由后续在线采集编排触发。

## 导入后运行

- 疾病索引更新后恢复运行：`scripts/run_tasks.py --resume <run_id>` 或工作台"恢复运行"。数据文件哈希是 input_signature 的一部分，索引/BATMAN 文件出现后签名变化，受阻阶段会自动重跑；成功阶段只在签名与产物哈希全一致时复用。
- 归档：live 运行逐阶段归档到 `data/pharm/<方名或 custom_task_id>/01_preflight/`、`02_herb/`、`03_reverse/`、`04_review/`；partial/blocked 证据同样归档，归档不等于研究完成；fixture 禁止进入。重复归档只复用哈希一致的批次，变动拒绝覆盖。
