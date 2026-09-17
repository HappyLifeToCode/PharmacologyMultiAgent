# STRING → Cytoscape / CytoNCA 接入

2026-09-14：已在本机 Cytoscape 3.10.0 / CytoNCA 2.1.6 验证网络导入与非加权 Degree。此处是工程接入记录，研究所需指标及加权口径尚未确认，不代表正式研究完成。

## 实际能力与实现方式

CytoNCA 2.1.6 的运行时 `/v1/commands` 无 CytoNCA 命名空间；已安装 JAR 的 CyActivator 只注册菜单任务及分析动作，没有注册命令。插件包含 DC、BC、CC、EC、LAC、NC、SC、IC 算法及加权分支，但本轮只验证非加权 DC。

项目新增独立 OSGi 应用 **Pharmacology CytoNCA Bridge 0.1.0**，提供 `pharmCytoNCA degree` 命令。它通过版本限定的内部接口取得已安装插件的 ProteinUtil，调用 `org.cytoscape.CytoNCA.internal.algorithm.DC.run(..., false)`，把原始 getter 结果写入 `CytoNCA_DC` 节点列。不重写算法，不分发或修改 CytoNCA JAR，也不宣称这是 CytoNCA 官方 API 或菜单导出流程。插件升级后需重新核实内部接口。

导入使用独立的新网络及确切 SUID，不依赖当前选中网络。CyREST 的 JSON 网络导入会把边建为有向边，因此适配器先创建所有节点，再调用明确支持 `directed=false` 的 edges API，并写入 STRING score。桥接端再次拒绝有向边、自环、重复边，以及不带项目输入标记的网络。保留所有映射节点，未映射基因留在 STRING provenance，不能当作孤立节点。

## 构建与安装

需已安装 Cytoscape 3.10.0、CytoNCA 2.1.6 和完整 JDK。下面路径均为示例占位符，替换为本机路径；构建物放在被忽略的 local 下。

```powershell
python scripts/build_cytonca_bridge.py --cytoscape-home <Cytoscape目录> --jdk-home <JDK目录> --output local/build/PharmacologyCytoNCABridge-0.1.0.jar
```

通过 Cytoscape 的 Apps 管理器从文件安装该 JAR，或向 `/v1/commands/apps/install` POST `{"file":"JAR绝对路径"}`。不要把路径放入 `app` 参数。安装是显式部署步骤，Runner 不自动安装软件。卸载只需移除这个桥接应用，不影响 CytoNCA。

CyREST 默认 `http://127.0.0.1:1234/v1`，可通过 `PHARM_CYREST_URL` 覆盖；本地请求不走环境代理。每次执行保存版本、命令清单和插件状态。能力探测：

```powershell
python -m pharm_demo.network_smoke --source probe --output local/checks/cyto-probe-01
python -m pharm_demo.network_smoke --source synthetic --output local/checks/cyto-synthetic-01
```

输出目录必须是新目录，重试另建目录保留旧证据。合成输入是三节点链和一个孤立节点；期望 Degree 为 1、2、1、0。

真实 API 技术样本示例（以下参数只用于该样本，不写入个人研究任务）：

```powershell
python -m pharm_demo.network_smoke --source string --output local/checks/string-cyto-01 --genes TP53 MDM2 EGFR AKT1 ZZZPHARMSMOKETEST --species 9606 --confidence 0.9 --string-version 12.0
```

`ZZZPHARMSMOKETEST` 是刻意设置的未识别输入。本机验证 5 个输入映射 4 个节点、4 条边；Degree 为 AKT1=2、EGFR=1、MDM2=2、TP53=3。STRING 版本变化时会拒绝自动降级；这些数量是带日期的观察，不是未来查询的硬编码目标。

同一组基因也可用本地 v12.0 文件离线执行（2026-09-14 与上述 API 结果对拍一致：映射 4/5、边 4 条、Degree 相同）：

```powershell
python -m pharm_demo.network_smoke --source string-local --output local/checks/string-local-01 --genes TP53 MDM2 EGFR AKT1 ZZZPHARMSMOKETEST --species 9606 --confidence 0.9 --string-version 12.0 --data-dir data/string/v12.0
```

本地来源在配置数据目录后默认优先使用（任务可用 `string_source` 显式指定其一），映射为 preferred_name/aliases 精确匹配，与 API 解析口径不同，歧义与未映射分别记录；`data/string/` 不入库。

## 调度配置、产物和状态

已有 Runner.analysis_stage 在 live 网络阶段自动导入 STRING 网络。任务可添加 `network_topology` 对象，目前通过本地任务 JSON 配置；网页输入表单尚未提供此字段。技术验证配置如下：

```json
{"network_topology":{"metrics":["Degree"],"weighted":false,"purpose":"engineering_smoke"}}
```

研究计算必须使用 `purpose="research"` 和显式 `confirmed=true`；这应反映真实的方法确认，不可为了通过程序填入。无配置、未确认、指标不支持或桥接缺失时仅导入网络，插件不计算。即使 Degree 调用成功，当前阶段仍保留研究方法完整性限制和 `partial`，全流程 `scientific_complete=false`。Cytoscape 离线时保留可交接输入和 `blocked` 子状态，已有 STRING 产物使阶段为 `partial`。数据丢失、命令异常或数值不一致则阶段为 `failed`。不会把模型审核中的失败提升为成功。

产物包括 STRING 输入/版本/映射/原始边/来源，节点与边 CSV（含孤立标记），Cytoscape 导入请求/响应及网络快照，能力清单、CytoNCA 原始命令响应/节点表/拓扑 CSV、独立 Degree 核对和终态执行记录。命令返回值和原始表是插件执行证据；NetworkX 仅用于独立核对，降级输出明确标为 NetworkX。产物自动进入现有阶段详情、哈希交接和逐次归档；不新增验收平台。网络 Degree 排序不改变 DAVID 的共同靶点输入。

## 验证范围

已验证真实安装插件计算、孤立节点、未映射输入、STRING 小样本，以及离线/缺桥接/未确认/不支持指标/表格损坏的自动回归。调度集成通过隔离模型和外部服务的回归验证；未运行正式上游完整案例或新增完整模型会话。DAVID 正式提交和富集统计导出仍是下一项工作。
