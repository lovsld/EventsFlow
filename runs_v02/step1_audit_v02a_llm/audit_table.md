# 第一步收官审计汇总（工具生成）

指标定义见 `v02/audit.py` 模块文档与 STEP1_AUDIT_REPORT.md。

| run | 种子 | 决策 | 事件 | M1a 当前步 | M1b 任一步 | M2 知识支撑 | M3 被拒案例/重复 | M4 拒单率 | M4 已提交违规 | M6 依据真实 |
|---|---|---|---|---|---|---|---|---|---|---|
| llm_full20_planlayer_seed42 | 42 | 60 | 81 | None（0/0） | None（0/0） | None（0/0） | 0/0 | 0.0167（1/60） | 0 | 1.0 |

聚合（1 个 run，60 次决策）：M1a None（0/0）；M1b None；M2 None（违例 0，反向违例 0）；M3 案例 0、重复 0、重复再被拒 0；M4 拒单 0.0167、已提交违规 0；M5 abandon/replace 行 0、context 中计划 2 个 vs 表中 set/replace 行 2；M6 1.0。
