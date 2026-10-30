# CNKI 来源排序与文献溯源图

更新：2026-10-29。实现交付，运行验收由对接方负责。本轮不采集真实知网、不调用模型、不运行完整测试、不启停服务；新增用例尚未执行，不声明运行或科学验收通过。

## 页面行为

首页和工作台共用文献组件。新生成的文献快照首先显示“文献来源优先排序”，优先顺序固定为 **机构 → 第一作者 → 通讯作者 → 影响因子 → 期刊**。每项从高到低比较，只有前项相同才比较后项。原临床证据 P1/P2/P3 通道与篇数参考仍保留，数据库靶点排序不变。反向查询的药材、方剂仍分别列出。

每个疾病以其有原句出处的最佳关联论文作为排序依据：先计算每篇论文的五项值，再选择完整向量最优的一篇。不能用甲论文的机构加上乙论文的影响因子组成虚构来源，也不按篇数累加或打破并列。多机构或共同第一作者在同一篇论文内取已匹配规则的最高优先值。页面可查看各项值、缺失原因、对应论文、规则及出处。

缺失/未匹配用 null 表示，排在该维度有依据的数值之后；不是零分，也不能解释为来源差。同向量并列（如 1、1、3），名称只用于稳定显示。没有可用规则和可比影响因子时全部并列，明确提示尚待配置。**项目不内置机构或作者的权威榜单**。

第一作者可来自明确字段，或可靠分隔的作者列表首位，此时标记“共同第一作者未核实”。不从模糊拼接的作者字符串拆名字；通讯作者必须明确记录，不把末位作者视为通讯作者。作者优先规则必须同时匹配姓名与其明确所属机构，不能用论文所有单位推定某位作者的所属单位。

## 配置优先规则

复制 `configs/literature_ranking.example.json` 为 `configs/literature_ranking.local.json`，再填入研究团队确认的名单。所有 score 都是本项目的优先值，不是机构、作者或期刊的客观质量分；越大越优先。每条需要 source 说明依据，精确匹配 name，不做模糊别名或声望推断。下面仅为格式示例，不是真实榜单，不要直接用于研究：

```json
{
  "schema_version": "cnki_source_priority_v1",
  "institutions": [
    {"name": "示例机构甲", "score": 2, "source": "研究团队确认的优先名单及日期"}
  ],
  "first_authors": [
    {"name": "示例作者甲", "affiliation": "示例机构甲", "score": 2, "source": "团队确认记录"}
  ],
  "corresponding_authors": [],
  "impact_factor": {"kind": "CNKI_CIF", "year": 2025},
  "journal": [
    {"name": "示例期刊甲", "score": 1, "source": "团队确认的期刊优先名单"}
  ]
}
```

影响因子支持 `CNKI_CIF`（知网复合）、`CNKI_AIF`（知网综合）、`JCR_JIF` 三种口径，一次只比较一种。必须有有限非负数值、指标年份和出处。year 为 null 时从本批**确有候选关联**的论文中选择该口径最新已记录年份；其他年份只展示，不比较。论文发表年不能替代指标年份。旧 cif 数值若没有年份/出处，仅保留展示，不参与排名。

## 补充元数据

正常详情页采集会读取可见的机构、作者角色、期刊及带年份的影响因子字段，保存到论文 `bibliography`；不额外访问期刊主页或获取全文。读取不到即缺失。新增 DOM 选择器尚待实际知网页面核验，不能把读取代码存在当成真实字段已采到。

历史论文无需改写 `papers.json` 或重新采集。复制 `configs/literature_metadata.example.json` 为 `configs/literature_metadata.local.json`，从文献分析 JSON 或图中复制 **paper_id（64 位哈希）**，按篇补充有出处的信息：

```json
{
  "schema_version": "cnki_bibliography_v1",
  "papers": {
    "此处替换为实际64位paper_id": {
      "institutions": [{"name": "示例机构甲", "source": "可核验的论文或机构记录URL"}],
      "first_authors": [{"name": "示例作者甲", "affiliations": ["示例机构甲"], "source": "明确记载作者单位的记录URL"}],
      "corresponding_authors": [{"name": "示例作者乙", "affiliations": ["示例机构乙"], "source": "明确通讯作者角色的记录URL"}],
      "journal": {"name": "示例期刊甲", "source": "期刊题录URL"},
      "impact_factor": {"value": 2.5, "kind": "CNKI_CIF", "year": 2025, "source": "该年度指标的出处URL"}
    }
  }
}
```

只提供需要替换的字段，未提供的沿用题录。每条补充记录必须有 source，空列表可明确清除不可靠的作者/机构。论文编号采用项目既有 bibliographic identity；更换网页临时 token 或题录序号不会改变它。同一身份多条题录在某字段上冲突时，该字段不排序，并记录 `bibliography_conflicts`，不挑有利值。

修改规则或补充文件后，在结果里点“重新读取本地文献”，生成新快照；不会重新计算靶点，不改历史文献快照。已有 flow/会话继续恢复旧快照，不自动套用新规则。配置、补充信息及实现代码纳入排序指纹；`.local.json` 被 Git 忽略，需由双方另行对接。

## 文献图

展开候选的“查看文献证据溯源图”，查看 **方剂/药材 → 论文 → 标题/摘要原句 → 疾病**。论文下方连接作者角色、论文所列机构和期刊；点击节点可看完整原句、元数据、影响因子、原始关系、论文编号、三元组/来源下标与源文件哈希，并打开知网原记录。

每组 3 条原句路径，可前后翻页；长标题完整保留在节点提示和详情内。相同论文的不同原句分别保留，完全相同路径去重，未提供必要字段的记录明确计数、不补画。作者身份按论文区分，不因同名建立跨论文身份合并；机构边只代表论文列出的机构，不推断作者任职。无靶点证据时不添加基因或机制边。旧快照仍可展示已有原句路径，元数据缺失明示。

可导出当前组 SVG、全部路径 JSON；文献分析 JSON 同时包含五项来源列表、原篇数列表、规则快照及出处。图在打开时才构建，节点支持键盘 Enter/空格操作，窄屏可横向滚动。

## 接口与代码位置

- `pharm/literature/source_ranking.py`：规则、元数据规范化、来源排序及指纹。
- `pharm/literature/service.py`：新快照增加 `source_candidates`、`source_formula_candidates`、`source_ranking`、`bibliographies`、`bibliography_conflicts`；原 `candidates` 等字段含义不变。
- `pharm/literature/browser_bibliography.js` / `browser_collector.py`：正常详情页读取及保存。
- `server/static/cnki-graph.js` / `cnki-literature.js`：来源列表、可追溯图；新静态路由 `/assets/cnki-graph.js`。
- 不新增研究查询 API；两方向沿用原文献 GET/flow，更新后需加载新版后端与静态资源。

## 对接方运行验收清单

本轮提供以下自动用例，**未执行**：

已执行的检查仅为 Python AST、JavaScript `--check`、配置 JSON 解析及差异空白检查；不包含导入运行、页面渲染、真实采集或测试用例执行。

```powershell
python -m pytest tests/test_literature_source_ranking.py -q
node --test tests/cnki_graph.test.cjs
python -m pytest tests/ -q
node --test tests/*.test.cjs
```

重点人工检查：

1. 空规则与元数据缺失显示并列；按上述格式加入确认规则后顺序符合五级比较，原篇数列表、靶点排序和归档哈希不变。
2. 同名异机构作者不误命中；未知通讯作者不取末位；影响因子不同年度/口径不混排；多篇论文的优点不拼接。
3. 正向疾病与反向药材/方剂页面均可打开图，原句与论文一致；翻页、键盘、原记录链接和 SVG/JSON 导出可用。
4. 旧快照仍按旧数据恢复，新元数据和规则需重新读取才生效；错误配置明确报错，不悄悄降级为有效排序。
5. 用正常知网详情页面验证机构、作者与期刊 DOM；没读到的字段保持缺失，不修改验证码/访问限制逻辑，不下载正文。
6. 首页、工作台、移动视口及大量文献渲染；停止/续跑、原文献分级、刷新恢复等既有流程回归。

来源优先级、研究设计分级、文献关联与临床有效性是不同概念。`scientific_complete=false`，不以这些排序结果证明治疗有效。
