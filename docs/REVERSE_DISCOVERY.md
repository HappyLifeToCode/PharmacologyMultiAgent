# 五病范围本地反向查询

本功能开发在 `codex/reverse-disease-discovery`，`main` 保留研究方案原流程。新分支首页改为反向查询，原工作台仍可通过 `/legacy` 访问，历史任务和结果不迁移、不改写。

## BATMAN 全药材扩展（2026-09-21 最新）

独立工作目录为 `D:\PharmDiscovery`，当前演示地址 `http://127.0.0.1:8771/`。本地 BATMAN 数据只读复用原目录的下载文件；索引、查询产物及配置全部写在新工作目录。

- 目录 8,404 条，其中 5,652 条在当前口径下有可用靶点，2,752 条无可用靶点，页面保留并禁用选择，不将“无可用靶点”解释为药材没有作用。
- 39,169 个成分标识，114,215 条去重的成分—靶点证据记录；472 条成分标识／靶点符号或映射问题留存在 SQLite 的 `batman_rejected` 表中。
- 支持中文、拼音、英文和拉丁名检索、分页、多选（最多 30 条），过滤和翻页不会丢失已选项。没有中文名时保留原拼音；同名条目使用拼音和原文件行号区分，避免擅自合并不同条目或炮制形式。
- 全量扩展复用当前批次 `known` 保留、预测 `score > 0.84` 的演示口径，明确标为 `inherited_demo_not_new_research_confirmation`；不表示所有新增药材已取得正式研究参数确认。符号格式检查也不能单独证明物种正确。
- 疾病侧仍为原五病 GeneCards／OMIM 全部合格导出；不增加疾病范围或疗效排名。

完整下载文件的哈希必须与原批次登记清单一致，否则拒绝扩展，不能冒用旧下载日期。成分—靶点表与药材—成分表分开存储，避免把共有成分的全部靶点为每个药材重复复制。原始两药 v1 索引保持不变，新版索引为 schema_version=2。

### 重要结果修正

全量检查发现旧预测解析器会在带空格的 IUPAC 名称中匹配数字括号。例：白芍成分 CID `5316526` 的名称中 `2(7)` 被误读为 Entrez 2 / score 7.0，产生 A2M 假关联。新版只解析行尾完整靶点字段并验证分值范围，已增加回归测试。

因此当前白芍由 **688 → 687** 个靶点；双药由 **793 → 792**，至少匹配一病的双药靶点由 **742 → 741**。本节以下的初版计数保留作历史，不能混用于新版。差异保存在 `dataset.batman_expansion.baseline_comparison` 中，旧运行和 main 未回写；旧流程若继续研究，应另行复核其来源解析。

### 当前本机启动

新工作树未单独安装虚拟环境，当前使用原目录的 Python 解释器和依赖；导入的项目代码来自新工作树。PyCharm 可使用同一解释器，工作目录设为 `D:\PharmDiscovery`。

```powershell
Set-Location D:\PharmDiscovery
& 'D:/PharmacologyMultiAgent/.venv/Scripts/python.exe' -X utf8 server/app.py --port 8771
```

当前 `configs/discovery_data.local.json` 指向 `local/discovery/five_diseases_all_batman_v2.sqlite`。配置不提交 Git；可参考 `configs/discovery_data.example.json`。网页与未指定 `--db` 的 CLI 都读取此配置，没有配置时回退到原默认索引。

从已准备的原始五病索引扩展（输出路径必须不存在）：

```powershell
python -m pharm_demo.discovery --db local/discovery/five_diseases.sqlite expand-batman --data-dir "D:/PharmacologyMultiAgent/data/batman/v2.0" --manifest "D:/PharmacologyMultiAgent/data/pharm/芍药甘草汤/imports/web_ddc19c70894f4140/20260918_01/raw/batman_full_files.manifest.json" --output local/discovery/new_batman_version.sqlite
```

扩展后的查询 API 不变，例：`{"herbs":["黄芪"]}`。示例实测靶点数／至少命中一病：黄芪 1196/1116、丹参 1055/973、人参 1365/1282、当归 1303/1229、白芍 687/641、炙甘草 214/206。六味药已通过独立读取原始文件的集合核对。

最新回归：203 passed、4 skipped、两个既有依赖警告；浏览器验证覆盖中文／拼音搜索、选择保留、同名展示、空搜索、查询、含扩展来源的 JSON 下载和手机宽度。演示产物及截图在 `local/discovery/batman-demo-20260921/`。

## 能力与范围

流程：选择已收录药材或输入基因符号 → 本地读取成分靶点 → SQLite 反查 → 逐疾病整理关联证据 → 保存结果与文件哈希。

无需输入疾病，不调用旧流程的疾病靶点合并、Venny、STRING、CytoNCA 或 DAVID。当前是确定性本地查询工具和三阶段执行记录，不创建新的模型会话；模型可通过 CLI 或本地 API 调用，不能把程序查询描述为多 Agent 自主研究已验证。

检索范围固定为五个已有导出关键词：Hyperthyroidism、Hypothyroidism、Thyroid cancer、Thyroid nodules、Thyroiditis。关键词不是统一的疾病本体标识，检索命中也不等同于疾病因果证据。输出称为候选疾病关联，不宣称治疗效果。

## 数据口径

- 索引使用 `genecards.csv` 和 `omim.csv` 的全部合格记录，保留疾病标签、原始分值、来源表行号及记录字段。GeneCards 不新增中位数等筛选阈值；跨来源分值不相加。
- 输入药材的成分靶点复用已有批次已确认的 BATMAN 处理：known 保留，predicted 严格大于该批次声明的阈值，known/predicted 分开留存。该处理只代表已有输入数据口径。
- 基因只做格式检查、空白清理和精确去重；不擅自转换大小写或别名。未知但格式有效的符号列入未匹配结果。
- 同一靶点可关联多个疾病；按疾病分别计数，两库重复靶点不重复计数，原始关联行仍保留。
- 输入覆盖率 = 该病匹配唯一靶点数 / 全部唯一输入靶点数。
- 疾病覆盖率 = 该病匹配唯一靶点数 / 索引中该病的唯一靶点数。分母为零时输出 null，页面显示“—”。
- 五病采用固定顺序，无疗效排名、p 值或科学确认结论。零匹配如实显示，不解释为不存在生物学关联。
- 来源声明、原始文件存在性、符号和有限数值校验通过才建立索引。保留完整 provenance、来源文件 SHA-256、数据库 SHA-256、执行阶段和输出文件 SHA-256。转换前剔除的符号仍需通过原批次记录追溯，索引不补造被剔除的行。

## 准备与运行

使用项目虚拟环境，在仓库根目录执行。研究数据保存在被 Git 忽略的 `local/`、`data/` 中，克隆源码不会获得数据。

```powershell
# 一次性建立默认本地索引。需要已授权的五病导入批次及其原始文件。
.\.venv\Scripts\python.exe -X utf8 -m pharm_demo.discovery prepare --batch "data/pharm/芍药甘草汤/imports/web_ddc19c70894f4140/20260918_01"

# 启动新分支工作台；与另一个工作目录的旧版服务使用不同端口。
.\.venv\Scripts\python.exe -X utf8 server/app.py --port 8770

# 直接运行药材反查，输出目录必须不存在，防止覆盖历史结果。
.\.venv\Scripts\python.exe -X utf8 -m pharm_demo.discovery query --herbs 白芍 炙甘草 --output local/discovery/my-lookup

# 或直接输入靶点，无需疾病参数。
.\.venv\Scripts\python.exe -X utf8 -m pharm_demo.discovery query --genes TP53 EGFR AKT1 --output local/discovery/my-target-lookup
```

页面地址：`http://127.0.0.1:8770/`。已有服务不会自动加载 Python 改动，需在正确分支上停止并重新启动；不要在两个模型共用的目录里切换分支，同时开发应使用不同 worktree。

默认索引：`local/discovery/five_diseases.sqlite`。索引一经建立不会自动覆盖或刷新；来源更新时用 `--db 新文件路径`（放在 prepare/query 子命令之前）建立新版本。CLI 可读取指定版本；网页当前固定读取默认索引，替换前应停止服务并另存旧版。

接口：

- `GET /api/discovery/catalog`：收录药材、五病范围及数据状态。
- `POST /api/discovery/query`：`{"herbs":["白芍","炙甘草"]}` 或 `{"genes":["TP53","EGFR"]}`，二选一；自动归档到 `local/discovery/runs/lookup_<id>/`。
- `GET /discovery/artifacts/{run_id}/{filename}`：只允许下载 result.json、candidates.csv、evidence.csv、report.md、manifest.json。

完整 `result.json` 保留药材→成分→靶点关系与靶点→疾病关联，可通过 gene_symbol 连接。直接输入靶点时不推断其药材来源。页面可按疾病、基因及来源查看分页明细，展开成分证据，下载汇总与 Markdown 报告。缺索引、未知药材或非法输入会明确报错，不用合成数据顶替。

## 2026-09-21 真实数据验证

索引：GeneCards 59,902 行，OMIM 217 行。白芍＋炙甘草得到 793 个唯一输入靶点，其中 742 个至少命中一个疾病关键词，51 个未命中。

| 关键词 | 匹配唯一靶点 | 库内该病唯一靶点 |
| --- | ---: | ---: |
| Hyperthyroidism | 266 | 1,914 |
| Hypothyroidism | 471 | 8,946 |
| Thyroid cancer | 735 | 19,614 |
| Thyroid nodules | 453 | 4,975 |
| Thyroiditis | 734 | 24,479 |

与旧流程交集 659 不同是预期结果：旧流程对 GeneCards 进行单疾病中位数筛选，这里使用全部合格导出。不同疾病导出的规模差异很大，不能根据 735 与 734 等数量比较疗效。

验证证据保存在本机 `local/discovery/demo-20260921/`；逐疾病匹配集合和分母已由独立读取 CSV 的集合计算核对一致。浏览器验证覆盖药材查询、靶点无匹配、非法输入后清除旧结果、疾病切换、基因筛选、成分展开、下载、手机宽度与旧页面入口。截图为 desktop.png 和 mobile.png。

本次完整回归：200 passed、3 skipped、2 个既有依赖弃用警告。新增测试覆盖跨疾病关联、重复行去重、保留低分记录、零匹配、精确别名边界、缺失／不完整／错误范围来源、版本及文件哈希、禁止覆盖、API 输入与下载边界。

## 后续工作

当前五病范围适合验证查询与证据展示，不能检索范围外适应证。后续先扩展合规本地数据及统一疾病标识，再确认疾病评分、证据分层、作用方向和研究评价方法。网络与富集可作为候选疾病的后续解释工具，模型审核也应与确定性查询状态分开。
