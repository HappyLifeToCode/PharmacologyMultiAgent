# 项目进度与问题清单

更新：2026-09-22（阶段G，与重构后代码对齐）。旧方向（药物×疾病交集）的进度记录已随重构废弃，见 Git 历史（commit `6779567` 之前）。

## 当前结论

新方向"方剂→BATMAN 本地靶点→本地疾病索引反查"的**本地链路已实现并通过端到端工程验证**：四阶段纯程序 pipeline、可插拔疾病索引、人机协助浏览器桥、单页工作台，自动测试 119 项通过。真实数据验证未做——本机没有研究数据（BATMAN 全量文件与疾病索引批次在团队机器上），且在线采集流程尚未实现。

## 已实现及尚缺内容

| 模块 | 已实现 / 已验证 | 尚未完成 |
|---|---|---|
| 四阶段 pipeline | preflight→herb_targets→disease_reverse→review 固定 DAG；零模型会话；manifest 单一状态源；attempt 不可变；resume 复用（输入/代码/数据/产物哈希全一致）；live 逐阶段不可变归档；fixture 全链端到端通过（不访问外网） | 真实 BATMAN 数据 + 真实索引的完整 live 验收（待数据同步） |
| 任务模型与四方剂 | 任务字段 formula/herbs/research_notes/batman_threshold/mode；task_id 内容哈希；内置四方组成表 + BATMAN 候选名解析；动物/矿物药未命中如实记 unmatched | 组成与候选名映射出处待用户确认（source=standard_reference_pending_user_confirmation） |
| BATMAN 本地查询 | v2.0 全量文件核验（表头/哈希/预测文件双名兼容）；known 保留不过滤、predicted 严格大于阈值；provenance 含文件 SHA-256，accessed_at 未知留 null | 本机无数据；药材炮制/名称与 BATMAN 目录的对照待真实目录核验 |
| 疾病索引 | SQLite 只读可插拔；疾病集合来自批次实际值（首次出现序，上限 500）；三源批次（GeneCards+OMIM）与通用 associations.csv 批次，prepare 自动识别、共存报错；旧索引兼容（无疾病清单回退 DISTINCT）；expand-batman 全药材扩展（schema_version=2）保留 | 宽覆盖疾病-基因关联来源批次；索引内容的来源验收 |
| 反向查询 | 逐疾病 matched/coverage/逐条证据；固定顺序无疗效排名、零匹配如实显示；genes 超 3000 分块查询合并并记录；不命中靶点/药材如实列出 | 关联≠疗效之外的证据分层/评分方法（研究问题，未排期） |
| 人机协助 | /api/assist/start·stop·status + WS /ws/assist；headed Chromium + CDP screencast 推流（ack 保活、最新帧覆盖、慢客户端丢帧）；鼠标/键盘回传（相对坐标换算）；guidance 历史+增量；单会话管理；headed 失败显式 AssistUnavailable 不降级 | Agent 在线采集编排（人在环等待/继续）——预留 agents/runtime.py 与 blocked 阶段 assist 标记 |
| Web 工作台 | 单页三栏：选方/编辑组成→运行与四阶段状态图→阶段详情/候选疾病表/逐条证据/协助面板；/api/formulas；5 秒轮询；真实浏览器验证（含 WS 帧像素级闭环） | 全新开发机完整安装验收 |
| 测试与工程 | 119 passed / 1 skipped；fixture 合成索引与靶点全程标注 synthetic_engineering 且不进归档 | 无 |

## 尚未解决的问题

| 问题 | 现状 | 接下来需要什么 |
|---|---|---|
| 在线采集未实现 | blocked 阶段带 assist 升级标记与 guidance；工作台可手动启动协助会话（画面/输入已通），但 engine 不会等待或驱动采集 | 设计人在环编排：Agent 采集（agents/runtime.py 预留）遇验证→assist 会话→用户接管→产物导入→resume |
| 四方组成出处 | 标准教材通用口径，标注 pending_user_confirmation | 用户确认组成与 canonical→BATMAN 候选名映射（如 芍药→白芍、橘红→橘红/陈皮） |
| 疾病覆盖范围 | 索引疾病集合完全取决于导入批次 | 合规宽覆盖批次来源（GeneCards 导出/OMIM Gene Map/其他关联表均可经两类通道入库） |
| 本机无研究数据 | data/batman、索引批次均在团队机器 | 数据同步后跑真实 live 并人工抽检 |
| 动物/矿物药覆盖 | 阿胶、芒硝等 BATMAN 可能无成分记录 | 真实目录核验；未命中已在 unmatched 如实呈现，不视为错误 |

## 工程纪律（验收口径）

- 不编造数据、不用合成顶替、空结果如实、scientific_complete 恒 false。
- 参数即身份（task_id 哈希）、代码即签名（改代码后 resume 全量重跑为预期行为）。
- 归档不可变（01_preflight/02_herb/03_reverse/04_review），归档≠验收，fixture 禁止入归档。
- 完成声明分级：代码存在 / 独立测试通过 / 端到端工程通过 / 真实数据验收通过——当前所有模块最高到第三级。
