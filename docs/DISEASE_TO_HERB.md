# 疾病 → 靶点 → 成分 → 候选药材

首页支持疾病到药材的反向查询，与既有方剂/药材到疾病的正向入口并存。第一版为单疾病、BATMAN known-only、本地 Open Targets 主关联和 Monarch ClinGen/OMIM 补充。结果是候选关联药材，不是治疗推荐；不自动执行网络、富集或通用模型聊天。

## 使用

在会话首页输入“查询肺腺癌关联哪些药材”，也可输入“冠心病可能被什么药物缓解或者治疗呢？”等包含已收录中文疾病名称的完整问句，或直接输入英文名及 MONDO/EFO ID。解析器先做精确名称/ID 匹配，再在完整问句中按最长中文疾病名称或别名匹配，命中后仍以本地数据库的原始 disease_id 查询，不用中文名称合并实体。输入框上方可选择自动识别、正向或反向；自动模式依据本地疾病目录的明确匹配，无法确定时可手动选择方向。同名、别名冲突或部分名称匹配时先选择具体疾病，未选择不启动计算。中文词表只覆盖已收录名称，未匹配时可使用英文名或 ID；父病和亚型不自动合并。

返回候选药材、唯一匹配靶点数、匹配成分数及来源。可追问“展开第一个药材的证据”“查看疾病靶点”“查看匹配指标”，也可导出完整 JSON。页面逐条证据最多展示 100 行；返回的候选药材保留完整路径，疾病侧保留完整已解析靶点及来源证据。

查询设置 top_k 默认 10、范围 1—100，只控制候选药材展示数量，不裁剪疾病靶点。工程展示顺序为匹配靶点数降序、药材 ID 升序；不沿用正向 heuristic_v2，不生成综合药效分数。BATMAN 阈值不会开启 predicted，反向接口明确拒绝预测选项。

导航、刷新、切换对话或后台标签继续同一查询；关闭承载页签或持续失联超过 10 秒才停止对应进程。恢复原查询只读取状态和归档；主动重试先停止旧请求再从头启动。生产服务由用户终端管理，更新后在原终端 Ctrl+C，再运行 `python server/app.py --port 8766`，不要重复启动后台服务。

## 数据与索引

BATMAN 原文件沿用 `PHARM_BATMAN_DATA_DIR` / `configs/batman_data.local.json` 配置，需要 herb_browse.txt、known_browse_by_ingredients.txt.gz、known_browse_by_targets.txt.gz。原文件及旧索引不修改。首次有效查询自动构建独立索引，之后按源文件指纹复用，也可预先执行：

```powershell
.\.venv\Scripts\python.exe -m pharm.batman.reverse_index
```

默认派生索引保存至 `data/batman/index/v2.0/known_<fingerprint>.sqlite`，不提交 Git。构建临时文件后原子发布，数据变化生成新版本；显式指定旧输出路径遇到源指纹变化则拒绝复用。元数据记录三个源文件 SHA-256、schema_version、known-only、构建时间、获取时间（如已有）、目录及关系计数和拒绝记录。药材身份按原表行号保存，不按中文同名合并；已登记中文别名只改变展示，保留原始成分记录。

关系表为 herbs、herb_compounds、compound_targets，分别有药材主键、成分到药材、基因到成分的查询索引。只加载展示 Top-K 的完整成分路径，其他药材仍参与候选总数与匹配范围计算。

疾病侧复用完整 Open Targets `association_overall_direct` 查询，不使用只覆盖 BATMAN known 靶点的精简共享索引冒充完整疾病靶点。保留 Open Targets 原 ID 与唯一 MONDO 对应；无唯一对应不并入 Monarch。Monarch 只接受选定疾病的 ClinGen/OMIM 边，HPOA 缺少可靠基因映射的记录继续跳过并计数，不把表型当疾病。

BATMAN 原符号经人类 Open Targets target 的 approved symbol/唯一别名映射关联；歧义映射单独记录。Open Targets 映射不可用时，只允许与 Monarch HGNC 符号精确匹配，不猜测别名。已通过格式校验不等于已证明物种，具体映射和未接受符号随结果保存。

## 计数及证据

- `disease_target_count`：本次可用来源的全部唯一已解析疾病靶点，Top-K 不影响它。
- `matched_input_count`：疾病靶点中能关联至少一个原始药材记录的数量，与展示 Top-K 无关。
- `matched_count`：一个药材的唯一疾病侧靶点数；同一靶点经过多个成分只计一次。
- `disease_coverage`：matched_count / disease_target_count。
- `herb_known_target_count`：该原始药材通过项目符号校验的全部 known 符号数。
- `herb_coverage`：匹配的唯一 BATMAN 原符号数 / herb_known_target_count；原符号与批准符号可能多对一，两个计数分别保存。
- `excluded_known_symbols`：未通过项目既有符号格式校验的原始记录，未计入药材覆盖率。

`disease_evidence` 保存按疾病侧基因组织的 Open Targets/Monarch 原始证据；候选 `evidence` 保存 gene_symbol、batman_symbol、compound_id/name、known 标记与 source key，herb_record 保存原始药材名称、拼音、拉丁名和行号。两个来源的原分值不相加。来源不可用时状态 partial，`target_scope_complete=false`，覆盖率仅对应本次可用来源；两源均不可用则失败。零匹配明确返回空候选，不换用 predicted。

## HTTP 与归档

`POST /api/agent/reverse/resolve`：text 或 disease，另可带 disease_id 验证选择；仅解析名称，不构建索引、不启动计算。返回 ready / needs_selection / not_found。

`POST /api/agent/reverse`：text 或 disease（也可仅 disease_id）、top_k、request_id、owner_id、supersedes。疾病歧义返回结构化 409；无匹配或非法输入返回 400。成功返回 request_id、run_id、output_dir、result。

`GET /api/agent/reverse/<run_id>` 读取归档；`/request/<request_id>` 读取状态；`POST /api/agent/reverse/cancel` 接受 request_id。正反向共用进程管理器、页签连接与重试逻辑，旧正向路径保持兼容。

新归档位于 `local/agent/reverse/reverse_<id>/result.json`，workflow=reverse_disease_target_herb_v1、direction=reverse；任务记录仍在 local/agent/jobs 和运行目录 job.json。`scientific_complete=false`，不存在自动人工签字、断点续算或临床有效性确认。
