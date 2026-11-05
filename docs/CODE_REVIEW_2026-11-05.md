# 代码审查报告：2026-11-05

审查版本：`30d0e2b9873e217270fb99d3e2d5168bca1c4874`。使用 code-review 技能的独立 Standards / Spec 双视角，并由主审复核代码位置、接口及前端路径。用户要求全项目审查，故以当前版本为范围，未仅比较最近一次提交。

结论：确认 7 项问题，Standards 3 项（2 P1、1 P2），Spec 4 项（4 P2）。P1 表示优先修复的数据身份或来源完整性问题；P2 表示明确的功能/契约缺陷。两个视角分开列出，不用一个视角的通过掩盖另一视角的问题。

## 后续修复记录（2026-11-05）

用户授权“能修的修，能测的测”后，7 项均已完成代码修复及隔离回归。下文缺陷描述与行号保留为审查时快照，不代表仍未修复。

| 编号 | 修复 | 验证 |
|---|---|---|
| S1 | 创建和续跑拒绝 fixture→live；归档核对来源模式和合成证据标记 | 非法转换、旧任务续跑、伪装 live 的合成产物拒绝；合法 live 工程桩归档通过 |
| S2 | 保留原始哈希，迁移索引须同哈希，缺少校验依据时拒绝继续 | 同版本迁移通过，不同内容拒绝 |
| S3 | 先收集全部命中，再按同 ID 冲突组决定映射 | 三别名全排列及加入 preferred name 的全排列 |
| R1 | 队列接收时在内存中捕获配置，状态只保存无密钥身份 | start/resume 入队后切 A→B，实际模拟抽取仍用 A |
| R2 | 全量请求拒绝快速/部分包短路，传播范围并显示警示 | 已抽取、未抽取快速包均进入补采；关闭联网不启动；快照及模型汇总保留范围 |
| R3 | 发布保留原始总数和实际检索位置数，页面区别去重论文数 | 6 条站点结果、采集 3 条的发布及界面检查 |
| R4 | 移除本地 24,000 字符截断，保留现有响应大小门禁和服务端截断状态 | Chat Completions / Responses / Anthropic 的 25,003 字符响应完整保留 |

新增 15 个回归测试；首次针对性检查为 12 failed / 2 passed，修复及扩展后全量 **644 passed / 3 skipped**，全部 Node **38 passed**。页面检查使用工程模拟资料。原两项 live 流水线测试已改用独立的 live 分支工程桩，避免依赖禁止的 fixture 升级行为。未进行真实采集、模型服务或科学有效性验收；用户已授权本次提交推送。

## 审查与验证边界

- 全仓文件清单与主要入口扫描；重点阅读核心归档、任务/流水线、正反查询、BATMAN/疾病索引、网络富集、统一模型、研究对话、CNKI 采集/队列/发布/证据、HTTP 接口和前端状态流。
- 需求来源为当前 README、ASSISTANT_CHAT、DATA_CONTRACT、CNKI_DELIVERY、QUERY_EXECUTION、PROJECT_STATUS 和本次会话中的用户要求。AGENTS.md 的数据纪律仍适用，过时架构信息不作为当前实现要求。
- 全量 Python：`629 passed, 3 skipped`；全部 `tests/*.test.cjs`：`38 passed`。
- 缺陷验证采用临时目录、工程样本和模拟模型/网络结果；未启动真实采集、模型请求、STRING/DAVID 提交或生产任务，也未修改业务源码。
- 这是全项目范围的源码扫描与关键路径审查，不等于每个函数的穷尽验证。真实知网 DOM、真实模型协议、真实大规模数据、科研有效性和长时间并发仍未验收。既有测试全绿不能排除下面这些边界缺陷。

## Standards：代码与数据纪律

### S1 · P1 · 测试来源可以转为 live 分析并进入正式归档

位置：`pharm/pipeline/engine.py:1241-1249`，归档门禁 `pharm/core/archive.py:60-63`。

当调用引擎 `start(pipeline="analysis", mode="live", ...)`，来源为 fixture discovery 时，任务创建只检查 mode 是否属于合法枚举，不检查与来源的兼容性。共同靶点仍来自合成输入，后续网络/富集却按 live 执行；归档只检查当前 manifest.mode，因此接受这条分析。默认网页入口继承来源模式，不会自动触发此转换；缺陷在显式指定模式的引擎调用路径。

证据：临时来源 manifest 的 `_analysis_task` 探针返回 `mode=live, agents=True`。现有 `tests/test_analysis.py::test_analysis_archive_layout` 使用 fixture 来源显式启动 live 分析，在外部调用被模拟的条件下通过，并断言四个正式归档阶段都已生成。这同时说明既有测试把不应允许的转换当成了成功条件。

违反：fixture 数据必须保持 synthetic_engineering 身份、不得进入正式研究归档。

建议：在创建分析任务时拒绝 fixture→live；归档再次验证来源和证据类型。测试改为验证拒绝转换，并用明确的独立 live 工程桩验证归档结构。

### S2 · P1 · 来源索引丢失时静默换用当前数据库

位置：`pharm/pipeline/engine.py:733-739`。

机制分析发现原始反查记录的数据库路径不存在时，改用当前配置解析出的索引，并将 `recorded_hash` 清空。新索引即便与原研究版本不同也不会触发哈希不一致，后续共同靶点可以正常生成。

证据：临时来源记录保存一个不存在的旧索引路径及原始哈希，当前配置指向不同关联内容的有效 SQLite。执行 `shared_targets_stage` 返回 succeeded，并采用替代索引中的基因。

违反：缺数据应明确受限，不顶替来源；机制分析应继承原研究的来源版本与可追溯身份（DATA_CONTRACT 的参数/代码/数据签名约定）。

建议：路径搬迁时允许重新定位，但必须验证原始哈希；找不到同一版本时 blocked。若用户明确希望使用新版索引，应创建有明确新来源身份的研究。

### S3 · P2 · 三个 STRING 别名冲突时错误接受第三个

位置：`pharm/network/string_local.py:163-175`。

两个别名命中同一 STRING ID 时，程序删除该映射并标两者歧义，却没有保存“这个 ID 已发生冲突”的状态。第三个别名随后会作为新映射接受，结果依赖输入顺序，并可能把边连接到错误的基因。

证据：临时 STRING info 的 preferred name 为 NATIVE，A/B/C 三个 alias 均指向 `9606.ENSP1`。`_map_symbols(['A','B','C'])` 返回该 ID 对应 C，只将 A/B 标为歧义。

违反：当前 mapper 自身约定的“多个别名冲突不静默选择”及不猜测基因身份的纪律。

建议：先按 STRING ID 收集完整命中组，再统一处理 preferred-name 优先与别名歧义；测试三重冲突和不同输入顺序。

## Spec：需求与实际行为

### R1 · P2 · 排队文献任务会随配置切换改用另一个模型

位置：`pharm/literature/jobs.py:193-196,203-206`；实际捕获配置在 `pharm/literature/collector.py:451`。

独立网页文献入口在 start/resume 中默认传递 `model_selection=None`，等排队结束进入执行才捕获当前配置。A 配置下接受的任务，可以因随后切换 B 而向 B 发送文献。对话 CNKI 桥显式传入 selection，不受同一缺陷影响。

证据：模拟前序采集占用队列，第二任务入队状态显示 chosen-A；切换当前配置为 chosen-B，再释放前序任务，实际抽取收到 chosen-B。

违反：`docs/ASSISTANT_CHAT.md:37` “用户切换后只影响新任务和新的续跑”。

建议：接受 start/resume 时固定配置，并将该快照传给工作线程；配置不可用时明确提示，不能在等待后悄悄换用别处。

### R2 · P2 · 对话复用快速采集包而不展示截取范围

位置：`pharm/chat/cnki_bridge.py:58-59,97-104`。

桥接仅判断本地结果 status=ready，就直接复用，没有像数据库文献 flow 一样检查 quick/all。即使用户要求全量，已有快速包也不会触发补采；发送给模型的汇总和页面 cnki_result 又没有 collection/selection 信息，导致用户和模型难以知道所用资料是截取样本。

证据：将工程语料标为 `collection.mode=quick, complete=false`，桥接 start 返回 `cnki_ready, local_reused=True`；模型 summary 没有 collection 字段。

违反：`docs/CNKI_DELIVERY.md:141` 快速结果必须标非全量，不能与默认全量任务混用。

建议：把请求范围与实际语料范围一起判断；全量请求不能用 quick 就绪短路，有限资料回答必须把范围、排序和数量传入模型并显示给用户。

### R3 · P2 · 快速采集发布包丢失站点原始总数

位置：`pharm/literature/collector.py:546-549`。

冻结清单中保存的 `source_total_results` 没有写入发布的 papers/collection。下游只有本次截取位置数，无法直接还原本次采集覆盖了站点结果的多少。

证据：现有 Site 工程样本共有 6 条，quick limit=3；冻结清单为 `source_total_results=6`，经实际 inventory.collect 与 collector.execute 发布后仅有 `total_results=3`，顶层和 collection 均无原始总数。

违反：`docs/DATA_CONTRACT.md:216` “source_total_results 保留站点总数”。

建议：随发布包、source 快照、对话汇总保留站点总位置数、实际采集位置数及去重论文数，三个计数分开表达。

### R4 · P2 · 长模型回答被静默截断后仍显示完成

位置：`pharm/chat/provider.py:137,145,151-153`。

三个协议均对提取后的正文执行 `[:24000]`，但 finish_reason 继续沿用服务端的正常完成状态。尾部结论、局限或引用可能丢失，问答仍记为成功，也不会触发截断提示。

证据：MockTransport 返回 25,004 字符的正常完成响应，适配器只返回 24,000 字符，finish_reason 仍为 stop。

违反：`docs/ASSISTANT_CHAT.md:31` “截断……仍保留警示”。

建议：在已有响应大小门禁内保留完整正文；如必须限制长度，返回明确的本地截断标记，让问答展示不完整状态，并验证三种协议。

## 当前结果

Standards：3 项，最严重的是合成数据身份和历史来源完整性；Spec：4 项，主要影响配置锁定、采集范围表达与回答完整性。本报告不把测试通过或引用存在等同于科研验收。建议先消除各轴中已经复现的结果身份/来源问题，再补充对应回归检查。
