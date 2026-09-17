# 甲状腺癌补充流程

依据《研究方案》末尾原文方法段（详见桌面《研究方案.docx》）：将甲状腺癌靶点（原文 434 个）导入 STRING 构建 PPI 网络，按度值取前 50 作为重要靶点，鉴定未包含在主要交集中的高优先级靶点（原文 10 个），再通过 BATMAN-TCM 回溯其化合物和药材。434、50、10 是原研究结果，不是本流程必须凑出的数量。

本流程**独立于主流程验收**：有自己的入口、证据目录和报告；不使用主交集替代癌症网络，不改变主流程的 DAVID 输入。

## 流程与输入

```
cancer_targets → cancer_network → degree_top → outside_intersection → batman_backtrack → report
```

| 阶段 | 输入 | 说明 |
|---|---|---|
| cancer_targets | 五病导入批次（复用主流程导入器）或显式 `cancer_genes` | 从 GeneCards/OMIM 合集中取 `cancer_disease`（默认 Thyroid cancer）子集；GeneCards 沿用暂定中位数口径（provisional） |
| cancer_network | 上一步靶点 | STRING 优先本地、API 回退（同主流程选路）；CytoNCA 需显式 `network_topology`，当前仅非加权 Degree |
| degree_top | 度值表 | `top_n` 默认 50（来自原文）；**第 N 名并列全部保留**，口径 provisional 待医生确认 |
| outside_intersection | Top N + 主流程交集 | 主交集用 `intersection_genes` 或 `intersection_file`（主运行的 intersection.json）；缺失则 blocked，不凭空产生候选 |
| batman_backtrack | 候选靶点 + 关系数据 | 真实路径用导入批次的 herb_targets.csv（`load_herb` 台账校验）；BATMAN 网页在线回溯尚未实现 |

可选输入（现在没有数据，已留接口）：`core_herbs_file` 核心七药名单（每行一味）、`dynasty_clusters_file` 跨朝代聚类表（UTF-8 文本/CSV）。存在则校验格式、计数并写入报告；缺失记为 pending，不阻塞主链路。原文注明"针对甲状腺癌的分析，以核心七药跨朝代聚类作为输入"，数据到位前本流程结果不含该输入的影响。

## 运行

配置模板见 [examples/supplement.example.json](../examples/supplement.example.json)（示例不是研究参数）。工程验证入口（合成样本，输出到新目录）：

```powershell
python -m pharm_demo.supplement_smoke --output local/checks/supplement-01
```

程序入口为 `pharm_demo.supplement.run_supplement(config, directory)`：配置见模板，输出目录必须是新目录；每阶段独立子目录保存证据，最终生成 `supplement_result.json`、`supplement_report.md` 和全量哈希 `supplement_artifacts.json`。

## 状态与验收口径

- `scientific_complete` 恒为 false；合成输入（`cancer_genes`、`herb_relations_file`）产物标注 `synthetic_engineering`，不冒充数据库结果。
- 阶段状态语义与主流程一致：缺输入 blocked、数据或校验错误 failed、网络建成但 CytoNCA 未成功为 partial（度值来源如实标 NetworkX）。
- 2026-09-17 合成样本（9 基因）端到端验证通过：本地 STRING → CytoNCA 桥接度值 → Top 5 → 交集外 3 个 → 回溯 2 行，计数与手工核对一致。
- 待补：五病真实导入后的癌症子集运行、核心七药/跨朝代聚类输入、BATMAN 在线回溯、并列与筛选口径的医生确认。
