# 多 Agent 对照实验框架

比较同一任务在两种 Agent 会话策略下的**工程行为**：`independent`（每阶段独立 Codex 会话，现状默认）与 `shared`（单会话顺序续接，`codex exec resume`）。比较指标为会话数、耗时、工具调用、阶段状态与尝试次数；**不构成药理分析质量结论**。

## 策略语义

- `independent`：六个角色七次独立会话，DAG 并行（默认 max_workers=2）。
- `shared`：首个阶段启动会话，后续阶段全部 `resume` 同一线程；共享会话不能并发续接，因此该策略自动降为顺序执行（max_workers=1）。这是对照的一部分：多 Agent 并行 vs 单 Agent 串行。

策略记录在 manifest 的 `agent_strategy` 与报告"Agent 会话策略"行；断点续跑沿用运行创建时的策略。

## 运行

```powershell
# 同一任务跑两种策略并生成对比报告（默认 fixture 合成模式）
python -m pharm_demo.benchmark --task <task_id> --output local/checks/benchmark-01

# 仅对比两个已有运行
python -m pharm_demo.benchmark --run-a <run_id_1> --run-b <run_id_2> --output local/checks/benchmark-02
```

产物：`benchmark.json`（两侧会话数、墙钟/Agent 累计耗时、工具调用、阶段状态与尝试、返工轮数）、`benchmark_report.md`（对比表）、`benchmark_artifacts.json`（哈希）。

## 口径与边界

- 对比的是工程指标（成本、耗时、步骤正确性）；分析质量对比需要真实数据到位后另行设计。
- `--ephemeral` 与 resume 的组合以真实运行验证为准；共享会话失败时阶段按 failed 处理并可自动重试（重试仍是同一会话续接）。
- 对照实验不进入 `data/pharm/` 研究归档；fixture 结果只证明调度与计账正确。
