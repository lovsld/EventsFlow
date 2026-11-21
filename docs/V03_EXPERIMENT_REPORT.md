# V0.3 Phase 6 实验报告（对照实验）

> 任务书 §19 Phase 6。矩阵经 Hu 2026-11-21 批准：酒馆场景、
> 20 tick、12 场。产物：`v03_runs/phase6/`（12 场完整 run +
> STORY_METRICS.json + 旗舰三件套 NARRATIVE_EVENT_GRAPH /
> SCENE_PACKAGES / NARRATIVE_PLAN，旗舰取 rule_on_seed42——
> 唯一故事机制全程咬合的组别，理由见 §3）。

## 1. 矩阵与指标总表

| 场次 | 事件 | 主线进度 | 传播 | 图谱边 | 弧线终态 | 注入/被感知 | 连续性错误 |
|---|---|---|---|---|---|---|---|
| rule_off_seed42 | 73 | 0.5 | 10 | rev2/ena1/esc5 | climax | – | 0 |
| rule_on_seed42 | 76 | 0.5 | 10 | +dep5 | climax | 3/2 | 0 |
| rule_off_seed7 | 73 | 0.5 | 3 | rev2/ena1/esc1 | escalation | – | 0 |
| rule_on_seed7 | 75 | 0.5 | 3 | +dep2 | escalation | 2/1 | 0 |
| rule_off_seed123 | 70 | 0.5 | 10 | rev2/ena1/esc4 | climax | – | 0 |
| rule_on_seed123 | 73 | 0.5 | 10 | +dep5 | climax | 3/2 | 0 |
| rule_off_goalvar | 77 | 0.5 | 0 | cau1/rev1/esc2/**res1** | escalation | – | 0 |
| rule_on_goalvar | 79 | 0.5 | 0 | +dep5 | escalation | 2/1 | 0 |
| llm_off_seed42 | 73 | 0.0 | 0 | 无 | setup | – | 0 |
| llm_on_seed42 | 73 | 0.0 | 0 | 无 | setup | 0/0 | 0 |
| llm_off_seed7 | 73 | 0.0 | 0 | prev1 | setup | – | 0 |
| llm_on_seed7 | 73 | 0.0 | 0 | 无 | setup | 0/0 | 0 |

红线指标「Story Engine 直接改变人物决定次数」：**12 场全 0**。
DeepSeek 用量：合计 863,169 in / 43,216 out（硬顶 120 万 /
15 万，未触顶；单场 19–24 万 in，与预估量级一致）。

## 2. rule 组结论

- **主线进度对 seed 与目标变体都稳健**：8 场全 0.5（信件问题
  被直接证据回答、贪腐问题只被 addressed）——这不是调参巧合，
  是 Phase 1 诚实口径在多 seed 下的复现。
- **开组每场注入 2–3 条、被感知 1–2 条**：未被感知的恒为
  back_room 痕迹事件（提交次 tick 无人在场，V0.2 感知语义），
  bar 内的抵达/场合事件全部被在场者感知。注入不改变 rule
  人物的行为（3b 已证），图谱以 depends_on 如实记下因果。
- **目标变体产生了质不同的图谱**：荣誉优先的士兵不再跨场景
  携带事实（传播 0），却走出了全矩阵唯一的 resolves 边与
  causes 边，弧线停在升级段——目标组合确实改变故事形状，
  指标管线能把这种差异测出来。

## 3. LLM 组结论（如实，且重要）

DeepSeek 决策下，4 场里**故事机制全程没有咬合**：无人搜
后屋、无人使用场景 claim、信件从未被发现，主线进度 0.0，
弧线停在 setup，候选为 0，开/关无差异。人物在吧台社交
（speak/ask/wait 为主），调查线没有启动。这不是 Story
Engine 的失败——它的触发器挂在 claim 断言与搜查事件上，
而本轮 LLM 决策层没有产生这些事件；同批还观测到每场
60 次决策中 12–15 次（20–25%）输出解析失败降级为安全
动作，进一步压低了目标导向行为的比例。

含义：V0.3 的故事动力层在 rule 动力学上完整成立；在 LLM
动力学上，瓶颈在涌现层的决策质量（计划层推进与 claim
使用），不在故事层。若后续要测「LLM 人物回应注入压力」，
前置条件是先让 LLM 人物能稳定走完「搜查→发现→断言」的
行动链——那是涌现层（主聊天）的课题，本报告只登记现象
与数据，不在此越界修。

## 4. 测试与回归

Phase 6 验收测试 5 项（矩阵完整、红线全 0、rule 开组注入
达标、预算未超顶、旗舰三件套存在）全过；v03 合计 36/36；
全量回归见随附数据（仅停放红 P-C4-1 一项，冻结既有）。

## 5. V0.3 收官清单

任务书交付物：V03_MIGRATION_PLAN ✓、ARCHITECTURE_V03 ✓、
STORY_ENGINE_DESIGN ✓、EVENT_GRAPH_SCHEMA ✓、
SCENE_SYSTEM_DESIGN ✓、NARRATIVE_EXPORT_GUIDE ✓、
V03_EXPERIMENT_REPORT（本文件）✓、STORY_METRICS.json ✓、
NARRATIVE_EVENT_GRAPH.json ✓、SCENE_PACKAGES.json ✓、
NARRATIVE_PLAN.json ✓。Phase 0–6 全部完成。
