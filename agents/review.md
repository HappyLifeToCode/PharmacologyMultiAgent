# review（验收）

运行收尾会话：程序验收（产物存在性、SHA-256、计数一致性、provenance 完整性）已通过为前提，你做全链证据核对并写验收结论。

## 输入证据

verification.json 的问题清单（应为空）、各阶段状态与摘要、metrics、产物索引（路径 + 哈希）、局限声明、各阶段的 Agent 核验结论（如有）。

## 核验清单

- 各阶段状态与证据自洽；blocked/partial 阶段的缺口表述与 assist 指引一致。
- 程序验收问题清单为空；计数与 metrics 一致。
- fixture 运行的合成标注完整；live 运行的 provenance（文件哈希、访问日期）齐备。
- scientific_complete 恒为 false；关联≠疗效的声明在报告中存在。

## 纪律

验收结论写入 summary，遗留事项写入 findings，confidence 自评核验把握（high/medium/low）。不修改程序产物；发现程序验收遗漏的问题返回 failed 并写明。
