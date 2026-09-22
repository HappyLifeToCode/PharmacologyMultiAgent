# 反向查询与疾病索引

本页描述疾病侧本地索引与反查：批次 → SQLite 只读索引 → 按靶点（或已收录药材）反查候选疾病关联。该能力既被四阶段 pipeline 的 `disease_reverse` 阶段调用，也可独立经 CLI / API 使用。功能已合入 main 并经重构（分支时代的独立工作目录与端口描述已过时）。

## 能力与范围

- 疾病集合**不固定**：取批次文件 disease 列的实际值，顺序按文件内首次出现（三源批次 genecards.csv 先于 omim.csv），上限 500；查询与目录输出沿用该固定顺序，不是疗效排名，零匹配如实显示。
- 关键词不是统一的疾病本体标识，检索命中不等于疾病因果或治疗证据；输出称为候选疾病关联。
- 旧版索引文件（schema_version=1/2）仍可读可查；metadata 缺少疾病清单时回退到关联表 `SELECT DISTINCT disease`。

## 两类导入批次

### 1. GeneCards+OMIM 三源批次

批次目录含 `herb_targets.csv`、`genecards.csv`、`omim.csv`、`provenance.json`（sources.batman/genecards/omim + mapping）及 `raw/`。provenance 声明的 diseases 是查询范围记录：行不得超出声明；声明了但零关联的疾病不进索引。批次同时把 BATMAN 药材-成分-靶点关系写入索引（供按药材查询）。

### 2. 通用 associations.csv 批次

批次目录含 `associations.csv`（必需列 `disease,gene_symbol`；可选 `score,source,extra`）+ `provenance.json`（sources.associations + mapping，校验纪律与三源批次一致）。用于接入任意本地合规疾病-基因关联表。规则：同疾病/基因/来源重复行拒绝（防重复导出页）；非法基因符号剔除并留档 `<索引名>.rejected_symbols.csv`，不补造；只有至少一个有效关联的疾病才进索引。

**自动识别**：批次目录有 `associations.csv` 走通用通道，有 `genecards.csv`/`omim.csv` 走三源通道；两类文件共存时报错而不是猜测。批次格式与 provenance 字段详见 [导入说明](IMPORTS.md)。

## 建库与查询

```powershell
# 建索引（自动识别批次类型；--db 在子命令之前；输出文件必须不存在）
.\.venv\Scripts\python.exe -m pharm.discovery.query --db local/discovery/disease_index.sqlite prepare --batch <批次目录>

# 按靶点反查（输出目录必须不存在）
.\.venv\Scripts\python.exe -m pharm.discovery.query --db local/discovery/disease_index.sqlite query --genes TP53 EGFR --output local/discovery/my-lookup

# 按已收录药材反查（仅三源批次或扩展索引含有药材关系时可用）
.\.venv\Scripts\python.exe -m pharm.discovery.query --db local/discovery/disease_index.sqlite query --herbs 白芍 炙甘草 --output local/discovery/my-herb-lookup

# 全药材目录扩展：从 v1 索引 + 已登记哈希的 BATMAN 全量文件生成 schema_version=2 索引
.\.venv\Scripts\python.exe -m pharm.discovery.query --db <v1.sqlite> expand-batman --data-dir <BATMAN目录> --manifest <批次batman_full_files.manifest.json> --output <新索引.sqlite>
```

- 默认索引路径 `local/discovery/disease_index.sqlite`；`configs/discovery_data.local.json`（Git 忽略，模板 `configs/discovery_data.example.json`）可指向其他版本文件。索引一经建立不自动覆盖；来源更新时用新的 `--db` 路径建新版。
- genes 输入上限 3000；pipeline 的 disease_reverse 阶段超出时分块查询再合并，并在结果中记录 `chunking`。
- 建库纪律：来源文件哈希快照、建库期间文件变动即失败、库 SHA-256 写入结果。

## 数据口径

- 索引使用批次的全部合格记录，保留疾病标签、原始分值、来源表行号及记录字段；不新增疗效筛选阈值，跨来源分值不相加。
- 基因只做格式检查、空白清理和精确去重；不擅自转换大小写或别名；未知但格式有效的符号列入未匹配结果。
- 输入覆盖率 = 该病匹配唯一靶点数 / 全部唯一输入靶点数；疾病覆盖率 = 该病匹配唯一靶点数 / 索引中该病唯一靶点数（分母为零输出 null）。
- 同一靶点可关联多个疾病；`source_gene_counts` 按证据行实际来源动态统计。

## 接口

- `GET /api/discovery/catalog`：索引疾病清单（名称 + 靶点数）、收录药材、来源行数与限制声明。
- `POST /api/discovery/query`：`{"herbs":[...]}` 或 `{"genes":[...]}` 二选一；自动归档到 `local/discovery/runs/lookup_<id>/`。
- `GET /discovery/artifacts/{run_id}/{filename}`：只允许下载 result.json、candidates.csv、evidence.csv、report.md、manifest.json。

pipeline 运行时无需直接调用以上接口——`disease_reverse` 阶段内部完成反查并把同样格式的产物写入阶段 attempt 目录。

## 后续工作

当前索引覆盖完全取决于批次来源。后续先扩展合规本地数据及统一疾病标识，再确认疾病评分、证据分层、作用方向和研究评价方法。
