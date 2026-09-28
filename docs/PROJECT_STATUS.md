# 项目进度与问题清单

更新：2026-09-28（同步同门提交 `e87b51d`，保留本地在线采集改动；OMIM 协助画布重复点击修复及完成状态门禁，真实站点仍待复测）。旧方向（药物×疾病交集）的进度记录已随重构废弃，见 Git 历史（commit `6779567` 之前）。

## 当前结论

"方剂→BATMAN 本地靶点→本地疾病索引反查"主链路与"候选疾病→共同靶点→网络→富集"机制分析链路均已实现并通过端到端工程验证（fixture 全链、mock Agent 会话、合成数据 live 路径）。**真实验证缺口如实保留**：2026-09-22 完成单次真实 Codex 会话冒烟（gpt-5.6-luna，结构化交接返回正常，证据 runs/diagnostics/model_smoke_20260922.json）；济川煎真实运行发现并修正 Agent 将唯一匹配基因数与原始证据行数混淆的问题；六会话完整 live 运行仍未做。STRING/CytoNCA/DAVID 仅完成可达性核验（2026-09-22：API/首页 200、软件在位），正式桥接计算与真实提交仍待做，不等于正式科研验收；BATMAN 获取日期记录为 2026-09-18。2026-09-24 协助画布 WS 404 卡点已修复（根因：websockets 包未写入 requirements.txt；真实协议栈实测通过）。

## 已实现及尚缺内容

| 模块 | 已实现 / 已验证 | 尚未完成 |
|---|---|---|
| discovery 四阶段 pipeline | preflight→herb_targets→disease_reverse→review；零模型会话纯程序；manifest 单一状态源；attempt 不可变；resume 复用；live 不可变归档；fixture 端到端 | 真实 BATMAN 数据 + 真实索引的完整 live 验收（待数据同步） |
| 多 Agent 编排层 | 六角色 Codex 会话（协调/药材/疾病/网络/富集/验收，agents/*.md）；程序计算·Agent 核验、结论不改产物（failed→partial、错误记 agent_review.error）；任务 agents 字段（live 默认 true、fixture 强制 false）；环境不可用即 failed 并提示 agents=false；提示词哈希进签名；mock 会话测试通过 + **2026-09-22 单次真实会话冒烟通过** | 六会话完整 live 运行未做（缺研究数据）；在线采集编排（预留） |
| 置信度 heuristic_v1 | 候选疾病四维组件（match_score/log 归一、input_coverage、disease_coverage、evidence_quality=known 占比+genecards 归一分值；权重 0.3/0.3/0.2/0.2，null 剔除归一）；固定顺序非排名；手算对拍测试；前端列展示+组件展开；结果显式区分唯一证据基因数与原始证据行数 | 公式本身的研究评审（属研究问题） |
| 机制分析链路（analysis） | shared_targets→network→enrichment→analysis_review（workflow_version=4，v3 resume 拒绝）；POST /api/analysis（校验来源 run 与疾病）；交集空如实；缺省继承来源运行模式；fixture 端到端；归档 05_analysis/；Agent 序列 mock 验证 | STRING API 真实可达性、CytoNCA 真实桥接、DAVID 真实提交——均未在本轮验证（门禁保留：network_topology/david_enrichment 未确认即 blocked/partial） |
| STRING/CytoNCA/DAVID 模块 | 恢复至 pharm/network/（string_local 本地优先+API 回选路、cytoscape 桥、metrics 度值核对）、pharm/enrich/david；integrations/cytonca_bridge 原样恢复；历史测试原样通过（63 项） | 真实服务/软件冒烟；正式研究参数确认 |
| 人机协助 | API 请求/状态、后台 headed Chromium、截图兜底、cookies/storage state 回传均已实现并通过测试；**WS 404 已修复（2026-09-24）**；同门提交 `e87b51d` 修复画布一次点击重复派发和 OMIM 已知验证页误报完成 | OMIM/GeneCards 真实站点仍待复测，自动化浏览器可能仍被网站拒绝 |
| Web 工作台 | 三栏工作台；候选疾病置信度列+组件展开；"机制分析"按钮与 analysis 阶段图；Agent 核验展示；真实浏览器验证（含 WS 帧像素级、置信度与分析全链截图） | 全新开发机完整安装验收 |
| 测试与工程 | 209 passed / 4 skipped（2026-09-28 本机；环境敏感用例的跳过数可能不同）；fixture 产物标注 synthetic_engineering 且不进归档 | 真实模型/真实数据验收 |

## 2026-09-28 同步后的当前卡点

- 已从 `origin/main` 拉取同门提交 `e87b51d`，并与本地未提交修改合并；该提交主要修复协助画布重复点击和 OMIM 验证完成状态误判。
- BATMAN-TCM v2.0 在本地，可生成药材—成分—靶点；本机仍缺完整 GeneCards/OMIM 疾病索引。在线采集代码已进入第三阶段，但真实站点登录、反爬和权限仍未完成端到端验证。
- 当前在线采集器的实际输入仍是疾病关键词。正式目标不应要求研究者先知道疾病，下一步必须改为靶点驱动的 GeneCards/OMIM 查询或接入有许可的疾病目录/API；在此之前，空关键词不能被当成“全疾病反查”运行。
- 无本地疾病索引时的临时建库路径已经实现，但尚未用真实在线产物完成导入、哈希、重复行和恢复运行验收。
- 受 Windows 临时目录权限影响，全量 pytest 曾出现环境级 `PermissionError`；将 `TEMP/TMP` 指向仓库可写目录后，协助、任务、引擎相关测试为 **38 passed**。

## 尚未解决的问题

2026-09-25 本机反馈：协助画布已能显示 OMIM 页面，但存在闪烁。前端已在工作树中改为尺寸变化时才重设 canvas，并过滤过期解码帧；本次 Python 回归为 209 passed、3 skipped，尚未进行该前端修改的浏览器实测。用户另外观察到点击验证后加载并返回验证页，疑似 Cloudflare 验证循环，原因未确认。仍需排除当前前端同时发送 mousedown、mouseup、click 导致的重复点击；不能仅凭页面循环断定网站封禁，也不能保证同步 cookies 即可通过挑战。

| 问题 | 现状 | 接下来需要什么 |
|---|---|---|
| 多 Agent 核验未过完整真实运行 | 编排、提示词、降级与签名均有 mock 对拍；2026-09-22 单次真实会话冒烟通过（通道与 schema 验证），六会话完整 live 未跑 | 数据同步后一次完整真实 live 冒烟（agents=true），核对六个会话产物与降级行为 |
| STRING/CytoNCA/DAVID 正式验证 | 模块与历史测试恢复；2026-09-22 可达性实测（STRING API v12.0 200、DAVID 首页 200、Cytoscape 3.10.0 在 D:/Tools）；正式桥接/提交未做 | 冒烟：STRING API 小样本、CytoNCA 桥三节点、DAVID 小列表提交；结果如实记录 |
| 在线采集编排 | 已移入 discovery 第三阶段；在线采集失败会保留中间产物并将阶段 blocked；已有本地索引时可生成运行专用扩展索引，没有本地索引时可由 BATMAN 阶段产物创建运行专用初始索引 | 当前采集器仍按疾病关键词工作；“BATMAN 靶点→自动枚举 GeneCards/OMIM 疾病”的正式靶点驱动接口尚未完成，不能把手工疾病关键词当正式反查 |
| 四方组成出处 | 标准教材通用口径，标注 pending_user_confirmation | 用户确认组成与 canonical→BATMAN 候选名映射 |
| 疾病覆盖范围 | 索引疾病集合完全取决于导入批次 | 合规宽覆盖批次来源 |
| 本机无研究数据 | data/batman、索引批次均在团队机器 | 数据同步后跑真实 live 并人工抽检 |

## 工程纪律（验收口径）

- 不编造数据、不用合成顶替、空结果如实、scientific_complete 恒 false。
- 参数即身份（task_id 哈希）、代码即签名（改代码或改 agents/*.md 后 resume 重跑为预期）。
- 归档不可变（01_preflight..04_review + 05_analysis/*），归档≠验收，fixture 禁止入归档。
- 完成声明分级：代码存在 / 独立测试通过 / 端到端工程通过 / 真实数据验收通过——多 Agent 编排为"mock 测试通过 + 单次真实会话冒烟通过"，机制分析链路为"fixture 端到端通过"，均未达到真实验收。
