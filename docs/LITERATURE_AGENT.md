# 文献检索 Agent 适配

本文件描述快照、概念对齐、证据分级及排序预览工具，原有独立命令和数据合同保留。2026-10-25合并远端更新后，`pipeline.py`也用于新生成的正向文献快照，并在网页另列证据分级候选；原篇数列表、数据库顺序和旧冻结快照保留，分级及篇数候选均完整展示，不设页面Top-N。默认采集仍为关键词全部结果，不采用远端新增的20篇默认值。网页使用自有后台采集及Codex标题摘要分析，不依赖旧UI-Agent或Kimi。下文外部Kimi工程说明仅为最初离线导入的来源。

下文最初离线导入阶段接收外部 CNKI Agent 已生成的 `papers.json`，规范化为项目自己的 `literature-search-v1` 合同。当时外部项目负责浏览器、Kimi、Playwright、验证码和断点 fragment；这不是当前网页的运行依赖。

## 离线导入

```powershell
C:\Users\Ye\anaconda3\python.exe scripts\import_cnki_snapshot.py `
  "D:\UI Agent项目\data\cnki_温经汤\papers.json" `
  --formula 温经汤 --limit 20
```

默认输出到 `local/literature/cnki/<方剂>/<采集日期>_<源文件哈希>/`：

- `raw_papers.json`：外部原始快照的逐字节副本；
- `request.json`：统一请求；
- `normalized_papers.json`：版本化统一结果；
- `manifest.json`：状态、数量、来源路径、哈希和人工审核状态。

CNKI URL 可能含时效 token，因此统一 `record_id` 使用来源、标准化标题、作者和发表日期计算，不依赖 URL。旧版记录缺少 URL、`western_diseases` 或更新时间时仍可导入，并在 warnings 中如实记录。

## 当前边界

- 只实现离线快照适配，尚未调用外部 HTTP API；
- PubMed、Europe PMC、PMID 和 CNKI DOI 尚未接入；
- 外部 `western_diseases` 缺失时可由本项目自动从关键词、题名和摘要补充候选概念；
- 自动抽取区分研究疾病、症状/结局和治疗场景，后续自动证据阶段继续判断研究设计、疗效方向和方剂贡献；
- 所有导入快照标记为 `collected_unreviewed`，不参与候选疾病排序；
- 原支持度、背景校正、Open Targets 分层和 UI 均不受影响。

当前已完成自动研究设计、方剂贡献和结论方向判断。排序仍是独立预览，不改写 Open Targets 原候选。

## 原方匹配初筛

规范化后可运行独立筛选。明确的加味、加减、改良方以及名称不同的衍生方会保留在审计表中，但从原方证据集排除；其他方剂与目标方剂合用也单独排除。原方联合针灸、西药或器械的文献暂时保留为 `original_with_cointervention`，不得冒充原方单独治疗证据。只在关键词或摘要提到目标方剂、题名未确认的记录暂不纳入。

```powershell
C:\Users\Ye\anaconda3\python.exe scripts\screen_literature_snapshot.py `
  "local\literature\cnki\温经汤\2026-09-12_e7de0a28b93d\normalized_papers.json" `
  --alias 金匮温经汤 `
  --exclude-formula 温经逐瘀汤 `
  --exclude-formula 大温经汤
```

输出包括完整审计表、原方保留表、排除表、JSON 结果和哈希 manifest。该步骤仍不判断疗效方向或证据等级，也不改变候选疾病排序。

## 自动疾病、症状与治疗场景抽取

`literature-concept-extraction-v1` 在原方筛选后运行，不要求人工逐条确认。它优先读取外部 `western_diseases`，缺失时从关键词和题名关系中提取，并把摘要或题名中的精确原文作为证据。术语分为 `study_disease`、`symptom_or_outcome` 和 `treatment_context`；炎症因子、评分、指标、网络药理学等方法或结局指标不会作为疾病输出。

```powershell
C:\Users\Ye\anaconda3\python.exe scripts\extract_literature_concepts.py `
  "local\literature\cnki\芍药甘草汤\2026-08-30_aa11693c0b28\normalized_papers.json" `
  "local\literature\shaoyaogancao_validation_20261023\screening\literature_screening.json" `
  --output-dir "local\literature\concept_extraction_shaoyaogancao_20261023"
```

输出 `literature_concept_extraction.json`、逐条 mention CSV、哈希 manifest 和可直接供对齐阶段读取的 `literature_screening_extracted.json`。原筛选决策不变，未映射术语也不会被删除。

## 中文文献术语与疾病本体

中文论文中的“疾病/症状”不能只翻译成英文后按名称合并。统一合同需要同时保存中文原词、原文上下文、标准实体 ID、实体类型和映射关系；映射关系至少区分 `exact`、`broader`、`narrower`、`phenotype` 和 `unresolved`。

温经汤离线验证显示：痛经在 Open Targets 26.09 中是 HPO 表型，而 Monarch 2026-10-07 已有独立 MONDO 疾病；月经不调同时存在疾病与表型实体；功能性便秘只能安全关联到更宽的 constipation disorder；子宫内膜增厚不能自动等同于 endometrial hyperplasia。文献只支持表型或上位概念时，应在文献证据区展示，不强行写入疾病 Top-K。

文献证据后续可以参与融合展示排序，但必须保留 Open Targets 原始分数与原名次，单独记录文献证据等级及映射质量。名称相似、中文常用范围接近或方剂临床背景均不能替代标准实体核验。

人工核对完成后，可用 `literature-concept-map-v1` 映射目录生成统一对齐产物：

```powershell
C:\Users\Ye\anaconda3\python.exe scripts\align_literature_concepts.py `
  "local\literature\cnki\温经汤\2026-09-12_e7de0a28b93d\literature_screening.json" `
  "local\literature\concept_maps\温经汤.json"
```

只有 `review_status=reviewed`、`relation=exact`、`entity_type=disease` 且显式 `promote=true` 的映射可以与 Open Targets 疾病候选合并。映射资格不再决定文献优先级：有明确人体临床证据的疾病或症状即使尚未映射，也可以进入独立文献优先区，但必须标记 `ontology_merge_eligible=false`，不得伪造 disease_id 或改写 Open Targets 名次。

温经汤真实映射产物位于 `local/literature/cnki/温经汤/2026-09-12_e7de0a28b93d/`：排除摘要中明确的加减方后，11 个文献词术中 5 个具备未来提升的实体映射资格，2 个只能映射到上位概念，3 个保持未解析。这个数量不是疗效证据评分，也不代表 5 个最终适应症。

## 摘要证据审计

术语抽取后运行 `literature-evidence-extraction-v1`，自动区分人体对照研究、联合干预、综述、动物实验、计算机制、验案和历史背景，并将存档摘要原文写入 claim：

```powershell
C:\Users\Ye\anaconda3\python.exe scripts\extract_literature_evidence.py `
  "local\literature\cnki\芍药甘草汤\2026-08-30_aa11693c0b28\normalized_papers.json" `
  "local\literature\concept_extraction_shaoyaogancao_20261023\literature_screening_extracted.json" `
  --output-dir "local\literature\evidence_extraction_shaoyaogancao_20261023"
```

自动目录使用 `review_status=auto_verified`，与人工目录的 `reviewed` 明确区分。无法支持的设计、疗效方向或方剂独立贡献不会获得临床优先级；动物和计算研究分别保持前临床或机制证据。自动目录再经过统一证据审计：

```powershell
C:\Users\Ye\anaconda3\python.exe scripts\review_literature_evidence.py `
  "local\literature\cnki\芍药甘草汤\2026-08-30_aa11693c0b28\normalized_papers.json" `
  "local\literature\concept_extraction_shaoyaogancao_20261023\literature_screening_extracted.json" `
  "local\literature\evidence_extraction_shaoyaogancao_20261023\literature_evidence_catalog_auto.json" `
  --alignment "local\literature\concept_extraction_shaoyaogancao_20261023\alignment\literature_concept_alignment.json" `
  --output-dir "local\literature\evidence_review_auto_shaoyaogancao_20261023"
```

每个 claim 的 `excerpt` 必须是已存档摘要中的原文子串，否则程序拒绝生成产物。`reviewed` 或 `auto_verified` 的阳性人体主要研究，且方剂作用可区分时，才形成文献候选提示信号；精确疾病映射只控制能否与 Open Targets 合并。联合干预始终单列，不冒充原方单用证据。当前产物不修改 Open Targets 排序。

## 排序预览

`literature-ranking-preview-v1` 用于验证文献分层展示，不改写 Open Targets 候选、分数或 Top-50。其中 P1 为原方直接人体证据，P2 为可区分的联合/加用证据，P3 为综述、贡献不可完全区分或较低级人体支持；单篇验案/经验进入 C 层，动物与计算机制进入 M 层。`concept_role` 区分研究疾病、症状/结局和治疗场景，治疗场景进入 CTX 层，不作为独立适应症。`ontology_merge_eligible` 单独表示是否能与 Open Targets 精确合并。

```powershell
C:\Users\Ye\anaconda3\python.exe scripts\preview_literature_ranking.py `
  --forward-result "local\agent\forward\forward_d7941c55067c4f67903dc292a85c539a\result.json" `
  --db "local\literature\ot_index_entity_fix_validation_20261022\ot_known_index.sqlite" `
  --alignment "local\literature\cnki\温经汤\2026-09-12_e7de0a28b93d\literature_concept_alignment.json" `
  --evidence "local\literature\cnki\温经汤\2026-09-12_e7de0a28b93d\literature_evidence_review.json" `
  --output "local\literature\ranking_preview_wenjingtang_20261022_v2"
```

2026-10-24 自动证据验证后，温经汤产生月经不调 P2、更年期综合征 P2、卵巢功能减退 P3、痛经 P3；题名中的“肾虚型更年期综合征”作为未映射的限定术语同时保留，不按名称擅自与上位名称合并。芍药甘草汤产生视疲劳 P1、膝骨关节炎 P2、精神分裂症 P2、臀中肌综合征 P2；其中视疲劳和臀中肌综合征没有精确 OT 映射，仍可作为独立文献概念展示。术后镇痛被识别为 CTX 使用场景；类风湿关节炎和三叉神经痛只有动物或计算证据，保持 M。两个案例的原 Open Targets Top-50 顺序和分数均保持。
