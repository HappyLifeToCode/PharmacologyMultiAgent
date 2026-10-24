# 本地优先文献流程后端

2026-10-24：按用户明确的流程实现后端。用户随后要求暂不采集“四方汤”、先完成后端，因此本轮不修改前端，也不启动真实模型或知网采集。

## 调用顺序

1. 原正向/反向查询照常返回靶点排序及研究编号，不等待文献，不更改原查询body、缓存键或研究归档。
2. 文献辅助启用时，调用`POST /api/agent/{direction}/{run_id}/literature/flow/resolve`，body为`{"enabled":true}`或`{}`；`direction`为`forward`或`reverse`。传`{"enabled":false}`不会启动文献工作。
3. 本地已有分析结果时立即返回`state=ready`和`literature`独立快照。已有标题摘要但缺关系文件时仅安排本地分析；没有文献才返回`state=collecting`并排队采集。
4. 后台完成后自动生成排序及快照。调用`GET /api/agent/{direction}/{run_id}/literature/flow`读取状态和文献结果，将其补充到原靶点排序旁。状态读取不启动新模型；当前服务中的观察线程也会在没有客户端轮询时生成完成快照。

现有网页尚未调用新接口。本轮后端完成不代表网页已经自动采集。

## 状态与恢复

- 流程状态：`not_started/disabled/collecting/ready/failed/cancelled/unsupported_input`。
- `collection`包含任务编号、排队/运行/停止/成功/失败/中断状态、实际题录数、摘要数、页数、抽取数和时间。不提供假百分比或ETA。
- `POST .../literature/flow/cancel`及`POST .../literature/flow/resume`的body必须为`{}`。失败不自动反复调用模型，需显式续跑，中间文件保留。
- 同一研究重复resolve复用流程；相同关键词在执行/排队期间被其他研究请求时共享任务，不同关键词串行排队。停止共享任务会影响所有等待该任务的文献流程，原靶点结果保留。
- 刷新和重发不重复启动任务。服务重启后不能确认归属的旧任务标为中断，不认领旧PID或擅删锁，核对后再显式续跑。正常关闭服务会停止其文献工作及本任务拥有的Codex子进程树。

流程记录在`local/agent/literature-flows/<run_id>.json`；采集任务和快照分别在`local/agent/cnki-collection/`及`local/agent/literature/<run_id>/`。快照核对原研究SHA-256，原归档变化后拒绝沿用。数据、日志、快照和验证材料不上传Git；API不返回私有模型日志或凭据。

`GET /api/literature/corpora`返回本地关键词及篇数；`GET /api/literature/collections`及`GET /api/literature/collections/{id}`只查看任务。原正向literature GET继续不启动模型，另支持对应的reverse读取接口。

## 关键词和排序

默认模型仍为`gpt-5.6-luna`，采集当时该关键词可访问的全部检索页，仅取标题和摘要，不获取正文；通过已有完整性检查才发布。损坏的本地文件提示失败，不通过重新采集静默覆盖。

正向按归档的方剂名或单味药材名查询；多味自由组合不拆开混采。保持“名称一致且未标加减/联合篇数→全部可追溯篇数→原病名”排序，不改变数据库支持度/背景排序，不设文献Top-N。

反向以已解析疾病的中文名（缺失时用标准名）为关键词。关系必须指向相同病名或唯一精确映射的本次疾病ID，不以父病替换亚型、不采用歧义映射。`candidates`为药材原名，`formula_candidates`为方剂原名，两组均完整返回，分别按未标加减/联合篇数、全部关联篇数和原名排序。`mapping.status=literature_name_only`表示尚未自动对齐BATMAN实体；方剂不算成单味药材。

排序描述当前语料的研究关联，不代表疗效；疾病映射、抽取关系及排序科学有效性仍待评价，`scientific_complete=false`。

## 验证

最终全量396 passed、3 skipped（2个既有弃用警告，87.43秒）。新增18项覆盖本地命中、仅摘要分析、缺失后台采集、无人轮询生成快照、幂等/共享/排队、失败/停止/续跑、原归档保护、反向精确/歧义边界、37药材不截断、外部锁及跨站/参数保护。使用隔离目录和模拟采集/抽取；取消测试仅运行本地Python子进程，没有启动真实Codex或浏览器。原五组100篇文献、交付ZIP及生产8766不动。
