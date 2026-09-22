# 数据交接约定

四阶段 pipeline（workflow_version=3）的产物、状态语义与归档布局。旧九阶段契约已随重构废弃（Git 历史可查）。

## 阶段产物

| 阶段 | 必需产物 | 内容 |
|---|---|---|
| preflight | availability.json | 每项本地数据 available=true/false、检查细节、缺失时的 guidance 文本；任一不可用不在此失败 |
| herb_targets | targets.json | herbs、threshold、relations（药材→成分→靶点，含 known/predicted、score）、genes（唯一靶点集）、unmatched_herbs、rejected_symbols、per_herb、source_counts、provenance（BATMAN 文件清单及 SHA-256、accessed_at 可空） |
| disease_reverse | reverse/result.json、candidates.csv、evidence.csv、report.md、manifest.json | 逐疾病 matched/coverage/逐条证据、未命中靶点、索引 metadata、数据库 SHA-256；超 3000 靶点分块时记录 chunking |
| review | verification.json + 运行根 report.md | 程序核对结果（产物存在性/SHA-256/计数一致性/provenance 完整性）；report.md 含输入、阶段概览、候选疾病表、局限声明 |

阶段 blocked 时不写上述数据产物（不伪造），只保留 handoff 与证据。fixture 模式的同类产物全部标注 `evidence_type="synthetic_engineering"`。

## 状态语义

- 阶段状态：pending / running / succeeded / blocked / failed。blocked 与 failed 也是终态：调度器在依赖到达终态后放行下游，是否继续由阶段自行判断（如 disease_reverse 在 herb_targets 未成功时 blocked）。
- 运行状态：running / succeeded / partial（存在 blocked）/ failed（存在 failed 或异常）。
- `scientific_complete` 恒为 false：关键词关联≠疗效；零匹配、空靶点集、未命中药材如实保留，不调参数凑数。

## 交接与运行记录

| 文件 | 内容 |
|---|---|
| manifest.json | 任务快照、mode、workflow_version=3、各阶段状态/attempt/input_signature/产物索引与 SHA-256、verified_targets、metrics（unique_targets、matched_targets、candidate_diseases、evidence_rows 等）、scientific_complete、report 路径 |
| handoff.json（每 attempt） | run_id、task_id、agent_role、recorded_at、status、summary、blockers、findings、artifacts 及哈希；blocked 且可协助时含 `assist: {"available": true, "guidance": "..."}` |
| events.jsonl | timestamp、role、type、message；事件类型含 stage.started/completed/reused、tool.succeeded、assist_requested、run.completed |

- **参数即身份**：task_id = 任务内容哈希；**代码即签名**：input_signature 含任务、`configs/runtime.json`、`pharm/**/*.py` 哈希与相关数据文件哈希。resume 只复用签名与产物哈希全一致的成功阶段；review 每次重新核对。
- 同一时刻一个完整运行（`runs/.runner.lock`）；异常留下的锁先核验进程再处理。

## 任务输入与 API

- `POST /api/tasks`：白名单字段 `formula`（内置四方之一，可选）、`herbs`（1–30 项，与 formula 二选一或覆盖其组成）、`research_notes`、`batman_threshold`（默认 0.84）、`mode`（live/fixture）。任务体另存 `composition`（formula/herbs_override/custom_herbs）。
- `POST /api/run`：task_id + 可选 mode（缺省用任务 mode）。
- `GET /api/formulas`：内置方剂组成与 BATMAN 候选名。
- 产物下载 `GET /artifacts/{run_id}/{path}`：仅 manifest 登记且通过 public_artifact 白名单（.png/.json/.csv/.tsv/.txt/.md）的文件；提示词、私有日志不公开。

## 人机协助桥（WS /ws/assist）

协议全文见 `pharm/assist/bridge.py` 模块文档字符串。要点：

- 下行：`{"type":"frame","width","height","ts"}` 文本帧后紧跟二进制 JPEG 帧；`guidance`（历史快照+增量）；`state`；`error`。
- 上行：`click/mousedown/mouseup/mousemove`（x、y 为 0–1 相对坐标，按最新帧原始尺寸换算）、`wheel`、`key`、`text`。
- 状态机 running→waiting_human→done→closed；同一时刻一个会话（重复启动 409）；生产路径 headed，启动失败抛 AssistUnavailable 不降级 headless。

## 归档布局

live 运行逐阶段归档到 `data/pharm/<归档名>/`，归档名 = 方名或 `custom_<task_id>`（自由药材组合）：

```text
01_preflight/  02_herb/  03_reverse/  04_review/
  run_<run_id>/attempt_<nn>/
    _archive.json    批次哈希、任务参数、阶段状态、计数、来源访问日期记录
```

- 每批不可变：相同哈希复用，不同哈希拒绝覆盖；`_meta.md` 追加流水账；归档时间不可冒充数据库访问时间。
- partial/blocked 的尝试同样归档（保留现场）；fixture 禁止进入归档。
- 疾病索引与 BATMAN 原始文件不入库（本机配置指向，见 IMPORTS.md 与 ENVIRONMENT_SETUP.md）。
