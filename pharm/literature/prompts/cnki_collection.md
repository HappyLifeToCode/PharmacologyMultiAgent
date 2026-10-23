<!-- Adapted from UI-Agent/scripts/task_prompt_cnki.md; paths owned by this project. -->
你是一个网页数据采集 Agent。使用 playwright MCP 浏览器工具（mcp__playwright__browser_*）完成下面的任务。

【工具纪律】
- 只允许用三个浏览器工具：browser_run_code（所有跳转、输入、点击、提取）、
  browser_take_screenshot（截图）、browser_wait_for（等待）。其他 browser_* 工具一律不用。
- 本任务的检索与采集【必须由你自己用上述浏览器工具逐篇执行】，不要派子代理。
- browser_run_code 里是 Node.js 环境：document/window 只能在 page.evaluate
  内部使用，外面直接写会报 ReferenceError。
- browser_run_code 里只允许 page.goto 跳转和 page.evaluate 读页面，
  禁止 fetch / XMLHttpRequest 等网络请求；禁止用 Bash / curl 请求网页。
- 每次 run_code 的 return 只返回需要的字段，控制在 2000 字符以内
  （摘要全文超长时可分篇返回，但单篇摘要一般 500~800 字）。

# 任务：中国知网（CNKI）关键词文献采集（关键词：{{KEYWORD}}，前 {{LIMIT}} 篇）

按知网默认相关度排序，采集前 {{LIMIT}} 篇文献的题录信息 + 完整摘要，
【每篇采完立即写 fragment，再采下一篇】，防止会话中断导致整批丢失。

- task_id：{{TASK_ID}}
- 关键词：{{KEYWORD}}
- 数量：前 {{LIMIT}} 篇（列表不足 LIMIT 篇时全取）
- 已有标题数：{{RANK_OFFSET}}（0 = 全新任务；> 0 = 增量更新模式）

## 增量更新模式（已有标题数 > 0 时执行）

该关键词此前已采集过。本次为【追加更新】：

- 已有标题清单在文末「已有标题清单」一节。搜索列表展开后，逐条比对
  （忽略大小写、全半角、标点差异，标题核心一致即视为同一篇）；
- 已在清单中的篇目【跳过不采】，只采集清单外的新篇目；
- 本次最多新增 {{LIMIT}} 篇（没有新篇就一篇不采，直接写 meta 后结束）;
- fragment 编号从已有篇数顺延：第一篇新文献写 paper_{{RANK_OFFSET_PADDED}}.json，
  其后连续编号（不要和已有 fragment 重号）;
- 其余字段规则与全新模式完全相同。

# 执行步骤

1. 【搜索——必须走首页，不要直接访问结果页 URL】
   直接访问 kns8s 结果页 URL 不渲染结果表格、且极易触发滑块验证；
   必须从首页真实键入搜索词：

```js
async (page) => {
  await page.goto('https://www.cnki.net/');
  await page.waitForTimeout(2500);
  const input = await page.$('#txt_sug');
  await input.focus();
  await page.keyboard.type({{KEYWORD_JSON}}, { delay: 60 });   // 必须真实键入
  await page.evaluate(() => document.querySelector('.search-btn').click());
  await page.waitForSelector('table.result-table-list tr .name', { timeout: 20000 });
  await page.waitForTimeout(1500);
  return await page.evaluate(() => {
    const rows = [...document.querySelectorAll('table.result-table-list tr')]
      .filter(tr => tr.querySelector('.name'));
    return {
      total: (document.body.innerText.match(/共找到[：:]?\s*([\d,]+)\s*条/) || [])[1] || null,
      list: rows.map((tr, i) => ({
        rank: i + 1,
        title: tr.querySelector('.name a')?.innerText?.trim(),
        href: tr.querySelector('.name a')?.href,
        author: tr.querySelector('.author')?.innerText?.trim(),
        source: tr.querySelector('.source')?.innerText?.trim(),
        date: tr.querySelector('.date')?.innerText?.trim()
      }))
    };
  });
}
```

   注意：列表默认每页 20 条；{{LIMIT}} ≤ 20 时取本页前 {{LIMIT}} 条，
   > 20 时需要翻页（点下一页按钮后再用同样选择器提取，rank 连续编号）。

2. 写 meta：用 Write 工具写 {{OUTPUT_DIR}}/meta.json：

```json
{ "keyword": {{KEYWORD_JSON}}, "total_results": 1951, "collected": "前 N 篇",
  "sort": "中国知网默认相关度排序", "collected_at": "<今天日期>" }
```

   total_results 用第 1 步返回的 total（去掉逗号转整数）。

3. 【逐篇采集——拿到列表后立即连续访问，不要中途离开再回来】
   详情页链接（kcms2/article/abstract?v=...）的 token 有时效，离开列表页后
   选择器取不到 href；必须按「第 1 步拿列表 → 立刻逐篇 goto」的顺序，
   每篇一篇做完再做下一篇。对列表中的每一篇：

   a. goto 该篇 href；等 2~3 秒。
   b. 【摘要必须点"更多"展开拿全文】详情页 #ChDivSummary 默认只显示
      约 500 字（结尾是 "..."），点"更多"后才显示完整摘要：

```js
async (page) => {
  await page.evaluate(() => {
    const more = [...document.querySelectorAll('a, span, div, p')]
      .find(e => (e.innerText || '').trim() === '更多' && e.children.length === 0);
    if (more) more.click();
  });
  await page.waitForTimeout(1200);
  return await page.evaluate(() => ({
    abstract: document.querySelector('#ChDivSummary')?.innerText?.trim()
      || document.querySelector('.abstract-text')?.innerText?.trim() || null,
    keywords: [...document.querySelectorAll('.keywords a')]
      .map(a => a.innerText.trim().replace(/;$/, '')).filter(Boolean)
  }));
}
```

   c. 立即用 Write 工具写 fragment {{OUTPUT_DIR}}/papers/paper_NN.json
      （NN = rank 两位补零，如 rank 3 → 03）：

```json
{
  "rank": 1,
  "title": "论文标题（与列表一致）",
  "url": "该篇详情页链接（列表里提取的 href，原样保存）",
  "authors": "作者1;作者2",
  "source": "期刊或来源名称",
  "date": "2026-08-18",
  "keywords": ["关键词1", "关键词2"],
  "abstract": "完整摘要全文（点更多展开后的）",
  "journal_level": "北大核心",
  "cif": 2.31,
  "is_modified": false,
  "western_diseases": ["类风湿关节炎"],
  "note": null
}
```

   字段规则：
   - url 原样保存列表里提取到的详情页 href（不去 token 参数），
     它是该篇文献的稳定指纹，供增量更新去重使用；
   - abstract 必须是点"更多"后的【全文】；该篇确实没有摘要（如新闻报道）
     填 null 并在 note 说明原因（如"新闻报道，知网仅提供节选"）。
   - keywords 去掉知网自带的分号后缀。
   - date 取列表里的发表时间，截断到日（"2026-08-18 15:04" → "2026-08-18"）。
   - journal_level（期刊等级）：详情页来源期刊名称附近若带收录标签
     （"北大核心"、"CSCD"、"科技核心/中国科技核心"、"JST" 等），
     按标签填；没有任何标签填 null。学位论文等非期刊文献填 null。
   - cif（影响因子）：点详情页的来源期刊名进期刊主页，读"复合影响因子"
     数值填之；拿不到填 null。【同一期刊只查一次】：会话内已查过的期刊
     直接复用数值，不要重复进期刊主页。
   - is_modified（加减方标记）：标题含"加味/加减/合××方/化裁/复方"等
     表明非原方的标记，或摘要明确说是原方的加减方时填 true，否则 false。
   - western_diseases：摘要中出现的西医病名（如"类风湿关节炎""高血压"），
     中文原样列出，没有则填 []。
   - 单篇出问题（页面打不开/token 过期）：回到首页重新搜索刷新列表，
     按相同 rank 重取一次；仍失败则该篇写 fragment：note 注明失败原因、
     abstract 填 null，【继续下一篇，不要中断整批】。

4. 全部完成后结束。不写 papers.json（合并由执行器完成）。

# 异常处理

## 人机验证（滑块 CAPTCHA）
出现"安全验证 / 请完成安全验证 / 向右滑动"页面：这是无法绕过的人工验证，
【立即停止，不写任何文件】——缺失的 fragment 由执行器识别并安排重试，
你写了反而会掩盖失败。截图留证（browser_take_screenshot）。

## 限流（页面能打开但连续报错/一直加载）
连续 2~3 篇详情页打不开且排除单篇问题后：已完成的 fragment 保持不动，
停止剩余篇目并结束（缺失由执行器重试），不要刷页面硬闯。

# 完成标准

- {{OUTPUT_DIR}}/meta.json 存在；
- 清单里每篇都有 {{OUTPUT_DIR}}/papers/paper_NN.json（触发人机验证中止的除外）；
- 每篇 fragment 必须带 url 字段（详情页链接，作为该篇的溯源凭证）；
- 不写 papers.json / summary / Word（合并导出由执行器完成）。

# 已有标题清单

{{EXISTING_TITLES}}
