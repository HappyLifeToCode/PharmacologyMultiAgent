# 项目交接说明

## 2026-10-24 网页连接本地优先文献流程

用户授权“推送，并将前后端连接”。首页正反向及工作台正向已接入：开关默认关闭；启用后靶点先显示，本地优先，无文献自动采集标题摘要，完成后独立补排序。可停止/续跑；刷新、会话/排序切换恢复，不重复起任务，工作台完成靶点查询后也可刷新恢复。旧冻结快照及原研究请求body保留，反向37药材/方剂线索分列、未自动匹配BATMAN实体。页面新增literature-flow.js静态路由，用户需原终端重启8766后刷新，本轮不操作生产服务。

已通过1440/390隔离HTTP浏览器联调：真实本地温经汤与明确工程采集样例，验证服务器禁止启动模型子进程；本轮不真实采集“四方汤”、不访问知网。原五组100篇、ZIP及冻结研究哈希不变。六项Node流程测试与五项既有证据图测试通过；浏览器材料local/checks/literature-ui-20261024/。当前篇数规则保持，远端独立文献证据分级工具另保留，不自动作为网页排序。

## 2026-10-24 本地优先文献后端（前端暂缓）

按用户明确的流程，靶点结果先返回，本地有文献则直接排序，无文献则后台采集标题摘要，完成后自动生成独立快照；只有题录则仅分析，不重采。新增正反向flow接口、关键词共享及串行队列、停止/续跑；失败不自动反复调用模型，原研究body/缓存/归档保留。反向药材与方剂原名分列，不自动对齐BATMAN，正向原篇数排序保留。

用户随后要求暂不采集“四方汤”、先完成后端。本轮未改前端、未启动真实模型或知网采集，生产8766及旧UI-Agent未动；原五组100篇及ZIP保留。全量396 passed、3 skipped（2个既有弃用警告，87.43秒），证据local/checks/literature-backend-20261024/。下一步才是把网页文献开关接入flow并验证自动补充排序，不将本轮后端完成宣称网页已自动采集。见[LITERATURE_FLOW.md](LITERATURE_FLOW.md)。

## 2026-10-24 Codex默认模型与关键词全量采集

按用户要求，论文采集及标题摘要抽取改用Codex CLI，默认`gpt-5.6-luna`。新任务默认遍历给定方剂/单味药材关键词全部结果页，不再默认新增20篇或限制100篇；显式`--limit`仍可限量试采。全量发布须核对保存的逐页/逐位置清单、总数、题录、成功状态及末页，验证/漏页/超时保留任务供显式续跑，不冒称全量完成。CLI通过本次配置参数传入项目Playwright MCP，不写全局配置，模型不可用时不自动换模型。旧限量任务续跑保留原范围。

用户再次明确核心仅标题和摘要：不下载或阅读全文，作者/日期/来源/链接只用于去重和追溯，不额外采期刊影响因子。核对文献后端和前端已完整遍历全部关联和题录，没有Top-N截断，数据库Top-K不限制文献列表；三项篇数排序及既有数据库排序不改。网页仍只读本地文献，不自动联网采集。

本轮是离线准备、模拟Codex进程及全量发布验证；真实模型账户/知网采集未验证。最终全量378 passed、3 skipped（2个既有弃用警告，75.80秒），Python编译及diff空白检查通过。37病名工程样例在1440/390宽度完整显示及导出；原五组100篇/98摘要和交付ZIP哈希不变。材料local/checks/cnki-codex-20261024/，未操作生产8766。命令和边界见[CNKI_DELIVERY.md](CNKI_DELIVERY.md)。

## 2026-10-23 清理无关文献

按用户要求，从本项目data/cnki删除“药物相互作用”主题20篇，更新manifest.json及local/deliverables/cnki-literature-data.zip。当前保留温经汤、半夏白术天麻汤、济川煎、桃核承气汤、芍药甘草汤五组，各20篇，共100篇题录、98篇摘要；十个保留数据文件哈希不变。旧UI-Agent工程和历史分析快照保留。交付说明的一次性导入命令明确指定这五个方剂，避免重新混入无关主题。下方120篇是首次迁移时的历史数量。

清单/ZIP成员和内容哈希验证通过；四个内置方剂重新分析与清理前完整报告相比仅生成时间变化。全量360 passed、3 skipped（2个既有弃用警告，70.98秒），diff空白检查通过。验证材料local/checks/cnki-cleanup-20261023/，仅本地维护。

清单/ZIP成员和内容哈希验证通过；四个内置方剂重新分析与清理前完整报告相比仅生成时间变化。全量360 passed、3 skipped（2个既有弃用警告，70.98秒），diff空白检查通过。验证材料local/checks/cnki-cleanup-20261023/，仅本地维护。

## 2026-10-23 文献文案直观化与摆脱旧工程依赖

用户要求提示更直观，随后授权迁移必要代码和数据，使旧UI-Agent可以独立移走。本项目新增数据清单/导入/打包和自有采集执行器、提示模板、标题摘要抽取及CLI；默认文献源改为data/cnki，PHARM_CNKI_PROJECT_DIR不再参与运行时。已只读迁移6组120篇，独立ZIP在local/deliverables/cnki-literature-data.zip，不进Git。分析读自有数据，旧快照仍可恢复，不再访问旧工程。四方关联篇数、顺序和疾病对齐状态与上版一致。

采集默认prepare-only；--execute才启动Kimi，--extract额外分析新标题摘要。已做模拟发布/失败不发布/独立锁/超时清理验收，不冒称知网实网已验证。部署机器仍需安装配置Python、Kimi、Node/Playwright通用运行时；MCP样例路径指向本项目，不复制旧登录状态/模型密钥/训练数据。网页当前仍只读已有论文。重启新版后旧工程可自行删除，本轮没有删除或改写旧工程。用法见[CNKI_DELIVERY.md](CNKI_DELIVERY.md)。

## 2026-10-23 UI-Agent本地文献接入验证

用户授权开始验证接入，并明确本轮先验证已有文献，覆盖此前整项暂缓的工作范围。新增`pharm/literature/`独立适配器及快照服务，读取UI-Agent的papers.json/triples.jsonl，只核对标题和摘要，不启动采集或模型。首页设置与工作台默认关闭，可在旧结果按需读取；原查询与排名不变。原文出处、方剂身份、去重篇数、疾病映射和研究类型独立展示、导出，旧结果以独立快照恢复。原项目文件只读，无生产服务启停。

80篇题录（79摘要）可用，65组文献疾病关联中21精确/3歧义/41未映射，不能以原前10名或父病代替完整目录核对。13条旧疾病关系来源未纳入，不等于13篇文献无效；医学语义尚待人工抽检。规则、配置及在线采集待验证事项见[CNKI_INTEGRATION.md](CNKI_INTEGRATION.md)。

## 2026-10-22 正向候选疾病证据可视化

用户授权实现并提交证据可视化，论文标题/摘要检索和文献排序暂缓。新增共用evidence-graph.js/CSS，在首页“查看证据”、工作台顶部候选详情及大类/OT参考证据中连接真实归档的药材、成分、精确基因符号和疾病记录。支持靶点搜索、疾病来源筛选、8路径分组、点击记录追溯、当前分组SVG导出；节点共享按原身份，重复记录保留，旧归档缺口不补猜。原证据表、排名、任务接续和研究归档不改。详情见[EVIDENCE_VISUALIZATION.md](EVIDENCE_VISUALIZATION.md)。

新增两个静态资源路由，生产8766仍由用户终端管理；加载新资源需在原终端重启后刷新。本机真实温经汤归档回放及边界验证位于local/checks/evidence-graph-20261022/，不把界面回放当成新研究或科学验收。最终测试与提交状态见本地AGENTS。

## 2026-10-19 远端合并与当前known-only对照

合并远端2434ec4及其流式读取、中文自然问句疾病解析、OT证据分层实现。默认仍按用户最新选择保留原支持度，背景为可选主视图；layered_candidates及candidate_layers只提供独立OT参考和完整证据，不作为第三种主排序。旧分层归档保留原生顺序，资源版本已更新。

用当前known-only重新查询后，温经汤-PCOS完整非大类名次104→27，半夏白术天麻汤-原发性高血压330→501；两例均未进Top-20，不能判定普遍优劣。旧温经汤归档含预测关系，旧103→38/316→458及CSV性能对照仅保留历史意义。新真实响应、完整名次及验证见local/checks/merge-ranking-20261019/和RANKING_COMPARISON.md；不回写旧归档。生产8766继续用户终端管理。

## 2026-10-19 默认支持度、双视图对照与中文补充

后续用户确认默认回到分出大类后的原支持度，背景校正仅为辅助/可选排序。`candidates` 为原支持度非大类Top-K，`background_candidates` 独立从完整集合选择背景Top-K；两者及大类的完整证据共用两次来源扫描，前端切换不发新查询。首页追问保存排序快照，切换原消息不改变旧追问的对应疾病；导出含双列表和export_view。旧单列表归档保持原生顺序且禁用切换。

新增两条有可核查文献的暂定检索参考，以及完整排名/Top-10/20/50和性能对照记录；不当作临床真值或独立科学验收，默认策略保持保守。全量计算审计和测量证据在local/checks/ranking-comparison-20261019/，一轮A/B仅用于估计额外证据成本，不能代表整次查询固定耗时。详细公式和字段见FORWARD_RANKING.md及RANKING_COMPARISON.md。

补齐近期结果漏译并明确肥胖症/暴食障碍/神经性贪食症区别，支持MONDO/EFO/Orphanet等ID格式；静态词表缓存版本更新，刷新后可显示，无需重算旧研究结果。全部词表不等于所有实体都有中文：本轮核对44种返回实体，罕见未知仍如实提示。血清脂多糖活性是指标，已标注显示，EFO父类分类规则未在本轮修改。最新全量、浏览器验证、提交及代理状态见本地AGENTS。

## 2026-10-19 正向具体疾病候选与背景校正

用户确认调整正向大类占据Top-K的问题。新增 `pharm/agent/disease_background.py`：从完整OT direct association按当前实体规则、唯一批准符号建立疾病背景和IDF权重；不限制BATMAN known基因，不用输入交集冒充完整分母。首次构建额外耗时，之后按实际源哈希/代码指纹和内容校验复用本机派生JSON；临时SQLite只用于去重。校正值独立于保留不变的heuristic_v2，背景整体不可用显式回退，回退成功结果不缓存。

正向 `candidates` 为非登记大类Top-K，`broad_candidates` 为独立大类Top-K，新增两组完整数量、原排序前K摘要、原名次及排名来源。前端首页/工作台显示校正值、大类证据、原排序与旧版提示，导出保留新字段；反向及旧discovery流水线不变。分类不是所有本体非叶节点过滤，不合并父子疾病，不宣称关联排序等于临床适应症。公式、范围与字段见 [FORWARD_RANKING.md](FORWARD_RANKING.md)。

本机验证 `local/checks/disease-ranking-20261019/`：最终温经汤、半夏白术天麻汤冻结真实来源重算，原得分/候选总数/原Top-K顺序及来源SHA-256不变；新返回疾病和大类各10条，独立全量Parquet核对这20项完整背景分母。全量330 passed、3 skipped，2个既有弃用警告。本次包含后端改动，加载需要用户自己的终端重启；未操作生产8766。浏览器与最终Git状态见本地AGENTS，科学验收未完成。

## 2026-10-15 查询加速与进度

新增 `pharm/agent/progress.py`、`query_cache.py` 与前端 `query-progress.js`。实际阶段/计数通过原 request 状态接口读取，首次 POST 等待期间及刷新恢复均显示；进度文件独立于归档，不因轮询失败取消任务。Open Targets 采集先用 Arrow 过滤 ID，保留原完整证据和计算语义。缓存严格核对输入、源文件真实哈希、代码、配置及原归档，成功才保存；命中仍创建新运行，明确引用原计算，不复用半成品。旧来源 CSV 留在原运行；新正向流式来源改为Parquet版本/哈希及返回证据，缓存清理不删研究归档。完整行为与关闭方式见 [查询执行说明](QUERY_EXECUTION.md)。

全量 **322 passed, 3 skipped**；JavaScript 语法与 diff 空白检查通过，真实 14 分片筛选结果哈希一致。工程验证不构成科学验收。未操作生产 8766，加载新版后端需用户在自己的终端重启；本机验证及最终 Git 状态见 AGENTS.md。

## 2026-10-14 疾病到药材会话入口

用户现已明确要求并授权实现反向“疾病 → 靶点 → 成分 → 候选药材”，第一版采用 known-only，覆盖此前“反向暂缓”记录。本次基于 main/fef7227 完成独立 BATMAN 全药材反向索引、完整疾病靶点读取、Monarch 按疾病补充、基因别名归一及原始符号追溯、药材聚合、API、首页双向入口与疾病歧义选择。旧 discovery 中的“反查”仍是靶点到候选疾病，与此新入口区分。

模块见 `pharm/batman/reverse_index.py`、`pharm/agent/disease_input.py`、`pharm/agent/reverse.py` 与 `server/static/reverse-chat.js`，使用与契约见 [疾病到药材](DISEASE_TO_HERB.md)。正反向共用 jobs.py 的进程、页签连接、取消/重试和状态记录；反向归档在 local/agent/reverse，浏览器按查询方向恢复和读取。方剂组成选择、旧工作台和 favicon 保留。顺带修复 EFO 疾病分类读取嵌套 Parquet parents 时误用 schema.names，统一使用顶层 schema_arrow.names；分类规则本身未改。

真实 BATMAN 索引：8,404 条药材原始记录、39,169 个成分、15,322 条 known 成分—靶点关系、3,116 个有效符号，4,417 条药材记录具备 known 靶点。源指纹及拒绝记录保留，重复中文名不合并；数据与派生索引不上传 Git。known_by_targets 的全部符号数不等于药材成分实际可达符号数，不能混用覆盖率分母。

真实肺腺癌验证运行 `reverse_a8ed1f82a1ea421d9a530f3f2665bd64`：Open Targets/Monarch 工程状态 succeeded，10,694 个唯一已解析疾病靶点，2,460 个可追溯 BATMAN 药材关系，4,389 条候选药材记录，展示 5 条；HTTP 实测约 24 秒，仅代表该次任务。独立读取原始 Parquet 核对完整 Open Targets 靶点集合，独立核对五条展示药材的原始成分/known 路径、拒绝符号与覆盖率。

验证目录 `local/checks/reverse-20261014/`：桌面/移动端使用上述真实归档回放测试列表、证据、指标、JSON 导出及刷新只 GET；疾病歧义先选择/取消、未知疾病明确拒绝、原正向组成选择通过，无 JS 错误或页面横向溢出。另真实启动反向查询验证刷新同 PID/同 request_id/单 POST，关闭承载页签后 cancelled/tab_closed，服务仍响应。临时 8773 已由验证脚本关闭，未启停生产 8766，仍由用户终端启动管理。工程验证不构成疗效或科学验收，scientific_complete=false。

最新全量回归 **303 passed, 3 skipped**；修改的 JS 语法及差异空白检查通过。推送与最终工作树状态见本地 AGENTS.md。

2026-10-14 中文显示补充：用户截图中的拼音分别来自未匹配拼写、含糊名称、原药材表缺中文名。修复 `BAI SHAO YAO` 有登记别名但精确命中 NA 中文条目后漏显示中文的问题，新增仅展示字段，不换原检索条目（真实 BAI SHAO YAO 为 30 个成分，BAI SHAO 为 301 个，不能为显示中文而静默替换）。未命中/中文名缺失分开标记；SHAO YAO 及剂量说明混入项仍不猜测。修复自身回归 284 passed / 3 skipped；合并团队 `0d92152` 的旧 discovery 流程 Top-K 产物优化后，最终全量 **287 passed / 3 skipped**。显示字段不改变组成 ID 或数据指纹，已保存默认仍有效；此次合并不是新增疾病→药材对话功能。

## 2026-10-14 方剂组成表接入

最终全量回归 **282 passed, 3 skipped**，四份 JS 语法检查通过；浏览器验证说明见下文。

基于 main/`0d48f54`，接入用户补充的 `data/batman/v2.0/formula_browse.txt`：54,832 行，字段为方剂拼音名、中文名、药材拼音组成，没有出处/剂量。新 `pharm/batman/formula_catalog.py` 按明确名称及组成合并重复记录，保留不同组成和原表行号，不取第一条为标准方；真实四物汤 19 行合并为 14 个组成候选。原四方及旧任务面板候选规则保留。首页和工作台顶部共用组成选择器，支持编辑、恢复原候选、保存默认、取消不启动计算及数据变更后重新选择。

新增 `/api/formulas/resolve`、`/api/formulas/preference`；正向接口可携带组成 ID、编辑清单及数据指纹，歧义在启动子进程前返回 409。归档 `input.formula_resolution` 保存原始组成、映射/未匹配、编辑类型、行号和文件哈希；`reference:null` 如实表示无出处。默认选择只写本机偏好，不是科学复核。明确列出的项目别名处理熟地/生地/白芍药/麦门冬及对应拼音，不自动修正 `CHUAN QIONG` 等拼写、不判定不明确的芍药、不替代炮制形式。缺失匹配允许编辑或明确按匹配部分继续；至少一味明确匹配后才进入新方剂查询。

验证目录 `local/checks/formula-catalog-20261014/`：真实全表解析及哈希核对；选定的四味编辑清单在真实 BATMAN 命中 973 个 known 靶点、2,158 条关系，无未命中。浏览器桌面/移动/工作台通过选择、编辑、保存复用、刷新、取消及部分匹配确认；偏好使用真实 API 但写隔离测试目录，查询结果是明确标注的界面合成响应，不写研究归档、不代表新方剂疾病查询完成。最终全量回归和当前服务 PID 见 AGENTS.md；不修改数据源、评分或科学验收状态。

## 2026-10-14 最新接续

最新起点 main/`be2409c`，默认首页是会话，研究工作台在 `/workbench`；自然问句方剂识别与 BATMAN 本地优先回退已经合入，不能把下方 10-12 页面状态当最新。10-14 新增疾病中文名称展示与正向请求取消，不改变研究数据和排序。

首页/工作台共用 `server/static/disease-labels.js` 与提交的本地 `disease-labels.json`：2,621 个 MONDO ID、68 个精确英文名/别名，来源 Wikidata CC0 标签与 `pharm/diseases/chinese_labels_overrides.json` 的项目译名。反向查询可使用词表中文名和明确维护的 `input_aliases` 从完整问句提取疾病实体，仍以原始 ID 查询；旧归档读取后直接显示中文，无需重算、不回写旧文件。未知译名明确标注，维护与来源见 DATA_CONTRACT，更新脚本不在用户查询时运行。

用户最后指令为“关闭页签才自动停止”，覆盖先前的页面离开/连接报错即取消策略。`pharm/agent/jobs.py` 管理独立子进程，`query-presence.js` 用 sessionStorage 页签编号 + SSE 持续连接跟踪 owner_id；项目内导航/刷新重连同页签、切换标签继续，断开超过 10 秒才停止该页签的查询。POST 断开/前端报错可恢复原查询，状态接口 `/api/agent/forward/request/<request_id>` 支持接续；服务实例变更的运行记录返回 interrupted。主动重试 supersedes 先等待旧任务退出，显式 cancel 入口仍保留；无 owner_id 的旧 API 仍在 HTTP 断开时停止。任务记录见 local/agent/jobs/ 与查询目录 job.json，重试从头运行、不自动续算。纯 ASGI 保留访问校验/断开信号；工作台新标签页打开，归档 GET 可只重读。

验证使用自行启动的 8771 服务和真实既有温经汤归档，保存于 `local/checks/chinese-disease-20261014/`；自动测试包含独立进程被终止、先停旧任务再重试、请求编号重复与提前取消。更新后仍用原命令启动服务，实时端口/PID看本地 AGENTS.md。三个此前重叠的真实查询均已保存 succeeded 结果（第二条 12:08，第一/第三 12:09），并非进程自动停止；新策略只适用于更新后的服务。

最新浏览器验证通过：真实白芍查询进入工作台超过缓冲期、返回/刷新保持同一 PID，仅一个 POST；关闭查询页签后才 cancelled/tab_closed，另一工作台页签继续可用。桌面/移动端真实温经汤归档双语候选/证据/评分、GET 故障重读通过，原 43MB result.json SHA-256 完全不变。网络故障/合成错误响应验证保留原请求编号和显式重试 supersedes，不把错误响应当研究结果；`scientific_complete=false`。最终全量结果记录在 AGENTS.md/PROJECT_STATUS。

最终全量 **269 passed, 3 skipped**；四份 JS 语法检查通过，本已中文的名称不重复追加、未收录/异常键不误匹配的浏览器核对通过。

## 2026-10-12 最新接续

当前优先链路是“方剂/药材 → BATMAN 靶点 → 候选疾病”。顶部对话框支持直接输入“温经汤”等内置方剂名，后端展开多味药材后查询本地 BATMAN、Open Targets 26.09 和 Monarch 2026-10-07，展示药材组成与 BATMAN 实际命中、候选疾病和逐条来源证据。

`POST /api/agent/forward` 接受 `text`、`formula` 或 `herbs`（只能选择一种），前端直接发送 `text`，不依赖下方任务面板的方剂目录。当前已收录温经汤、半夏白术天麻汤、济川煎、桃核承气汤；未收录方剂或加减方需手工提供组成。内置组成与 BATMAN 名称候选沿用 `pharm/batman/formulas.py`，来源尚待研究者确认，不由程序确认正式研究口径。下方旧任务面板本轮仍保留；仅对话框页面的初始化不再访问旧面板或任务 API。

启动命令仍为：

```powershell
.\.venv\Scripts\python.exe server/app.py --port 8766
```

本次相关文件为 `pharm/agent/forward.py`、`server/app.py`、`server/static/`、`tests/test_forward_agent.py` 与 `tests/test_server.py`。新增方剂展开、甘草候选回退、未命中与混合输入校验；全量回归 **222 passed, 3 skipped**。温经汤真实查询暴露原聚合将约 200 万条记录整体驻留内存的问题，已改为两次流式读取：第一次汇总所有候选并按原规则排序，第二次仅加载 `top_k` 候选完整证据；完整采集 CSV 保留，评分与来源规则未变。

真实验证保存于 `local/agent/formula-validation-20261012/`：温经汤 12/12 味 BATMAN 命中，2670 个唯一靶点、34571 条成分—靶点关系。首次查询完成 Open Targets 采集后因内存压力主动停止；修复后的验证复用该真实采集批次，核对输入靶点、选择参数与 CSV SHA-256，继续执行真实 Monarch 与 API 聚合，不伪造疾病证据；原未完成目录保留。成功结果为 `local/agent/forward/forward_9ed3b61f9e3b4f34acf40d9ffd7808d1/`：Open Targets 1996649 条采集关联，Monarch 95 条 ClinGen / 2113 条 OMIM 匹配，过滤后 13177 个候选，按用户设置返回 10 个，两个来源工程状态均 succeeded，`scientific_complete=false`。

浏览器检查通过：现有工作台和移除下方面板的仅对话框页面，桌面与移动端均可提交方剂、显示 12 味组成、查看 10 个候选与逐条证据，无 JS 错误或移动端页面溢出；仅对话框模式未请求旧任务 API。成功响应使用上述真实 API 归档回放，混合输入与未知显式方剂通过真实 API 校验并返回 400。截图及 `browser_summary.json` 保存于验证目录；临时服务 8771 验证后关闭，原 8766 服务未改动，用户需按原命令重启后刷新。当前结果是候选关联，不提供治疗有效率或临床疗效证据。

本轮开始时 `main` 与 `origin/main` 同步到 `f8a713a`。接续前先检查 `git status`，保留用户未提交修改；GitHub 推送需显式使用本机代理 `127.0.0.1:7890`，除非用户另行配置 Git 代理。

更新：2026-10-02。阅读顺序：本文件 → [项目进度](PROJECT_STATUS.md) → [数据契约](DATA_CONTRACT.md)。

## 最新决定与修改

用户暂停新数据库选型，要求删除不可用的 GeneCards、OMIM 及其在线采集代码。基于同门提交 `0e4abd0`，已移除两库采集器、HTML/官方导出转换、专用导入与中位数过滤、协助浏览器桥、验证码画布及 `/api/assist/*`、`/ws/assist` 接口。前端与新任务仅支持 Open Targets 数据查询。

保留 BATMAN 本地查询、Open Targets GraphQL API 与 Parquet 快照、通用 `associations.csv` 导入、双流水线、网络与富集功能。没有增加新数据库，也没有下载研究数据或改动正式研究阈值。

Open Targets 本地结果会记录疾病/表型实体类型；工作台候选疾病列表使用独立可展开、可滚动区域，仅展示疾病实体。EFO/OBA 等表型或测量性状仍保留在运行证据中，不冒充疾病显示。

本地原始数据、登录资料、SQLite 索引与历史运行保持原样。旧 schema 1/2 索引仍可读，来源字段不会被改成 Open Targets；新建索引只走通用批次。旧任务若启用已删除的在线来源，会明确 blocked 并提示保存新任务，不静默切换数据来源。查看历史结果不需要恢复旧采集器。

清理移除了评分中的 GeneCards 专用归一分值组件；新计算标为 `heuristic_v2`，沿用原权重，`evidence_quality` 仅使用 BATMAN known 占比，无证据则 null 并剔除。Open Targets 原始分数保留展示，不自动变成疗效概率。历史 `heuristic_v1` 报告不重写。该启发式仍需研究评审，不是老师要求的临床治疗置信度。

## 当前流程

`discovery`：数据预检 → BATMAN 药材成分靶点解析 → 疾病查询 → 程序验收与报告。

- 未启用数据查询时读取已配置的本地 SQLite 疾病索引；缺索引明确 blocked。
- `online_collect=true` 启用 Open Targets 查询。疾病范围为空时输入 BATMAN 唯一靶点；填写范围时按疾病查询靶点。
- `PHARM_OPEN_TARGETS_MODE=local` 读取本地快照；当前默认也是 `local`，只有显式指定 `online` 才使用 GraphQL。字段名称 `online_collect` 为现有任务协议保留，本地模式不联网。
- 查询结果生成本次运行专用索引；存在基础库时复制扩展，不覆盖基础库。查询失败保留中间产物，提示检查连接或快照配置后恢复。
- 现有试验选择规则为 Top-100、score≥0.2；这是同门试验默认，不能宣称已确认的正式研究口径。

`analysis`：选择候选疾病 → 共同靶点 → STRING/CytoNCA 网络与 DAVID 富集 → 验收。数据库关联、网络指标、富集与启发式分数均不等于临床疗效，`scientific_complete` 恒 false。

## 关键位置

| 能力 | 入口 |
|---|---|
| 任务与双流水线 | `pharm/pipeline/tasks.py`、`scheduler.py`、`engine.py` |
| Open Targets API | `pharm/diseases/open_targets.py` |
| Open Targets 本地快照 | `pharm/diseases/open_targets_local.py` |
| 第三阶段数据查询编排 | `pharm/diseases/online_pipeline.py`（仅 Open Targets） |
| 通用批次导入 | `pharm/diseases/associations.py`、`pharm/core/imports.py` |
| SQLite 查询与证据 | `pharm/discovery/query.py` |
| Agent 核验 | `pharm/agents/runtime.py`、`agents/*.md` |
| 网络与富集 | `pharm/network/`、`pharm/enrich/`、`integrations/cytonca_bridge/` |
| 工作台 | `server/app.py`、`server/static/` |

Python `playwright` 仍供独立 Agent 核验运行时配置浏览器 MCP；疾病数据查询与工作台不再启动浏览器。`websockets` 已从工作台必需依赖中删除。

本次全量回归为 `197 passed, 3 skipped`，Open Targets 本地 Parquet 测试已实际运行；API 和模型采用测试替身，未执行真实科研分析。JavaScript 语法检查通过。浏览器工具的本机页面访问权限检查不可用，视觉实测未完成。

## 启动与配置

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server/app.py --port 8766
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe scripts/run_tasks.py --task <task_id>
.\.venv\Scripts\python.exe scripts/run_tasks.py --resume <run_id>
```

BATMAN：`configs/batman_data.local.json`；已建疾病索引：`configs/discovery_data.local.json`；Open Targets：`configs/open_targets_data.local.json`。模板均在 `configs/`。数据、个人任务、运行与认证目录不上传 Git。

## 接续事项

1. 数据库选型目前暂停；待用户决定后再接入治疗证据源，不继续 GeneCards/OMIM 登录与验证码排查。
2. 对 Open Targets 做真实快照或 API 的小样本抽检，记录 ID 映射覆盖、direct/indirect 范围与试验选择规则。工程测试不能代替科学验收。
3. 多 Agent 完整真实运行、STRING/CytoNCA/DAVID 本机正式验证与四方组成来源确认仍需继续，不能因本次清理而宣布完成。
4. 旧阶段记录可从 Git 历史与本地运行查阅，保留原始日期、计数和 partial/blocked 状态。

遵循用户后续指令在 main 工作，中文提交；推送前全量 pytest 通过。当前阶段不自行确定正式研究参数或签署人工复核。
