# 环境准备与验收

2026-09-13 核验。本页区分软件安装、网站可达与研究分析完成。

## Playwright MCP

项目固定 `@playwright/mcp@0.0.64`，支持 `--save-trace`。

Codex 参数由 `pharm_demo/codex_runtime.py` 传入。它是允许 MCP 冷启动的等待上限，不是每一步固定睡眠 120 秒；普通工具调用超时另设为 90 秒。项目继续使用 icrc / gpt-5.6-luna / medium。

## Cytoscape 与 CytoNCA

- Cytoscape **3.10.0**：[官方发布](https://github.com/cytoscape/cytoscape/releases/tag/3.10.0)。本机安装于 `D:\Tools\Cytoscape_v3.10.0`，打开 `Cytoscape.exe`。
- CytoNCA **2.1.6**：[官方 App Store](https://apps.cytoscape.org/apps/cytonca)。本机通过 Cytoscape 自身 App 安装接口安装官方 JAR；桌面 App Store 列表及 Apps → CytoNCA → Open 已核验。
- 本机 Java 为 17.0.15。3.10.0 首次启动出现 `Invalid CEN header`；已核验安装包与官方 SHA-256 一致，在该软件的 `Cytoscape.vmoptions` 增加 `-Djdk.util.zip.disableZip64ExtraFieldValidation=true` 后启动正常。此为旧包兼容设置，其他机器仅在复现同类问题时评估，不照搬至全局 Java。
- 使用显式合成链 `TEST_A—TEST_B—TEST_C`，在 CytoNCA 勾选 Degree / without weight 并执行 Analyze，实际得到 **1、2、1**。已保存插件节点表和 `.cys` 会话，证明安装和该项计算可用。

CytoNCA 安装测试不代表已完成真实 STRING 网络分析。现有网络 Agent 的自动计算仍注明 NetworkX；正式 CytoNCA 交接与研究参数核验待真实上游数据到位后完成。

## 五站访问

以下来自本机当前网络，不能替代学校网络实测。

| 来源 | 2026-09-13 实测 | 待办 |
|---|---|---|
| BATMAN-TCM | 跳转至端口 100 后返回 502 | 站点恢复或学校网络复核 |
| GeneCards | Datacenter 网络验证页 | 用学校邮箱注册/登录，确认学术访问 |
| OMIM | 403 / Cloudflare 安全验证 | 本人通过验证后再确认注册、下载及 API 权限 |
| STRING | 首页可达，页面显示 12.0，并宣传另有 12.5 | 研究维持方案指定 12.0 稳定入口 |
| DAVID | 首页可达，明确所有用户可免登录 | 工作区、正式导出、背景集及统计口径仍需验收 |

普通网站账号、API 申请和批量下载授权分别核验；不能将注册成功视为全部访问权限已获得。密码和验证码由账号持有人保管，登录态仅存本机忽略目录。

重新采集带日期的首页证据：

```powershell
& D:\Anaconda\envs\prim\python.exe scripts/check_sites.py
```

结果写入新的 `runs/diagnostics/sites_<日期时间>/`，包含 HTTP 原始响应、浏览器页面、截图和访问时间，不覆盖历史结果。首页核验没有正式查询或导出。

## 数据沉淀

`data/pharm/<方名>/` 下固定五步目录；每个 live 步骤结束即归档，原始快照及哈希保留，受限状态明确记录。重复运行新建 attempt，合成数据不进入此处。详见 [导入与归档说明](IMPORTS.md)。
