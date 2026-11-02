# 统一模型 API 执行配置

更新：2026-11-02。本文件沿用原文件名以保留文档链接；原 Codex CLI 执行方案已退出生产调用，历史记录仍可从 Git 查看。

## 配置一次，所有模型功能共用

在首页“模型与 API”中保存并选用配置。当前支持 Chat Completions、Responses（含 Codex 兼容中转）和 Anthropic Messages。Codex 配置仅可作为用户显式导入 API 的来源，运行任务不会再读取 Codex 全局默认配置、启动 CLI 或使用固定模型。

| 功能 | 统一调用方式 |
| --- | --- |
| 研究问答、联网资料分析 | 问答开始时绑定选中的配置，模型继续调用既有受限工具 |
| CNKI 文献抽取 | 每次执行/续跑绑定配置，标题摘要按最多4篇一批调用，仍核对同篇原句 |
| 疾病名称翻译 | 绑定配置，单次限60秒，仍只输出候选英文同义名并精确回查目录 |
| 六角色核验 | 一次流水线内各角色使用同一配置，独立提示词和 JSON 校验；不修改程序产物 |
| 模型连通诊断 | 调用同一个选中 API，不启动 Codex 进程 |

共同入口 `pharm/llm.py` 负责选取配置、调用、超时/取消、文本结果和安全元数据。实际协议由 `pharm/chat/provider.py` 处理。缺 API、凭据不可用或模型失败时明确报错；没有 CLI、固定模型或另一服务的兜底。`configs/runtime.json` 只声明 configured-api 策略和程序配置，不保存具体模型、地址或凭据。

## 切换、续跑与溯源

- 开始执行时在内存中锁定配置。页面切换后，已运行任务继续使用原选择；新任务或新的 resume 采用当前选择。
- 凭据只留在内存或本机加密文件。执行记录只保存 runtime/profile_id/model/api_style/config_fingerprint、输入输出摘要哈希与状态，不保存原始地址或密钥。
- 翻译成功缓存以模型配置指纹和查询区分。换模型后不会冒用旧模型的缓存署名。
- Agent 输入签名加入配置指纹；换模型后对应已核验阶段会按现有规则生成新 attempt，不覆盖原 attempt。
- 文献抽取片段记录 llm，发布数据的 extraction_models 按论文序号保存实际来源。续跑可以复用已完成片段，不能把复用片段标为新模型重新计算；旧片段无记录时标 legacy_unknown。extraction_llm 只表示当前尝试，范围由 extraction_model_scope 明示。
- 页面选“仅本次服务”时密钥只在该服务进程有效。独立 CLI 或重启后的服务应使用加密保存配置，否则明确提示未就绪。

## 保留的边界

fixture 不调用任何模型；live + agents=false 不调用核验模型。核验模型只接收程序提交的有界证据摘要，没有文件系统或浏览器工具，不得声称自行打开原文件或访问网站；摘要不足时返回 partial。核验失败仍保留程序产物并按原规则降级。

知网页面由 Playwright 程序采集；STRING/CytoNCA/DAVID 由既有程序执行。统一模型 API 不等于已经把这些任务全部接入对话 Agent 的自动调度。

## 诊断

`python scripts/doctor.py` 和 `python scripts/collect_cnki.py --check-runtime` 只检查配置及依赖，不发送模型请求。`python scripts/check_runtime.py` 会真实调用当前模型进行短结构化检查。带 --browser 时由程序打开指定公开站点、保存页面截图，再将实际页面摘要交给同一 API 核验；不交给模型自行操作网页。

`collect_cnki.py --model` 已停用，应在页面选择；默认仍只准备任务，明确 --execute 才运行采集，--extract 才执行文献抽取。本次新增/更新用例在 tests/test_unified_llm.py 等文件；只做了隔离工程检查，完整真实数据与回归验收交由对接方。
