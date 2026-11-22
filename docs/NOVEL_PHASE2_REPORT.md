# NOVEL_PHASE2_REPORT — 因果图增强 + 语义解释（规则版）

> 2026-11-22。范围：统一因果词表图 + 独立验证器 + 链追溯 +
> 重复语义结构 + escalation 修正 + 灯塔/诊所 v03 补跑。
> LLM 语义标注未启用（Hu 选规则版），本报告不含 LLM 产物。

## 前置：v03 补跑（口径统一）

为灯塔、诊所各写了一份故事配置（stories/lighthouse_watch.json、
stories/clinic_fever.json，线程由各自场景的命题/物件结构导出），
scripts/backfill_v03.py 跑通回放链，v03_runs/ 下两场补齐
story_state / scenes / event_graph / scene_packages。

**如实记录的口径分歧**：两场 v03 线索 progress 均为 0.0——
v03 的问题回答只认「信息型直接证据」（搜得/亲历断言），而
灯塔的任务闭环是**动作型**完成（修灯、展示），不产生命题
断言；narrative Extractor 侧灯塔主线是 closed（E4 链完整）。
结论：v03 线索进度对动作型场景系统性低估，Phase 3 的
ThreadView 必须合并两个来源并标注口径，不许单取一侧。

## 交付

1. **escalation 修正**（build.py）：升级标签只在对峙语境
   （confront，或命题已被否认/对峙过）成立；单纯转述不再
   标升级。酒馆 18 → 13；灯塔、诊所归零（其重复断言全是
   转述，与数据相符）。
2. **narrative/semantic.py**：重复结构显式化——verbatim_repeat
   （同人同词）与 fact_relay（同命题同立场）两类合并组，
   每组指定 primary（首次出现），其余为复述。产物
   upgrade/semantic.json。
3. **narrative/causal_graph.py**：统一词表边构建（Extractor
   E2→CAUSED_BY、E3→RESPONDED_TO、E4→ENABLED_BY、E5→
   CONTRADICTS、E6→ENABLED_BY 弱；v03 reveals/enables/
   escalates/causes/depends_on/contradicts/prevents 映射）、
   **独立验证器**（端点/证据存在性、时间序、CONTRADICTS
   须否认-断言同命题、CAUSED_BY 必须有投递或搜得证据——
   仅同命题拒绝立边）、**因果链追溯** trace_causes。
   E1/E7 按设计不进因果图（组织关系≠因果）。

## 实测（三场）

| run | 统一边 | 验证通过 | 关系分布要点 | 合并组 | 台词去重 |
|---|---|---|---|---|---|
| 酒馆 | 31 | 31/31 | CAUSED_BY 19、ESCALATED_BY 5、RESPONDED_TO 4、REVEALED_BY 2、ENABLED_BY 1 | 12 | 39 句 → 15 唯一 |
| 灯塔 | 32 | 32/32 | CAUSED_BY 23、RESPONDED_TO 6 | 13 | 34 句 → 16 唯一 |
| 诊所 | 13 | 13/13 | ENABLED_BY 2、REVEALED_BY 4、CAUSED_BY 5 | 7 | 16 句 → 9 唯一 |

- 链追溯验证：酒馆信件搜得 E0008 是士兵后续断言 E0022 的
  因果祖先（经 E4 使能 + 投递链），与 N2 金标准一致。
- 验证器负面测试：同命题无投递证据的 CAUSED_BY、时间倒置
  的边均被 rejected（tests_narrative/test_causal.py 5 个
  测试全过）。
- 重复量化：酒馆 39 句台词中 62% 是复述——Phase 0 报告的
  「14 次同句」在结构层坐实，Writer 阶段（Phase 6）按
  primary 叙述一次即可治本。
- causal_links 已回填进三场 narrative_events.json（仅
  verified 边）。全量回归 312 过 + P-C4-1 挂起一红。

## 未实现（如实）

- **MOTIVATED_BY 零产出**：叙事层输入里没有确定性的动机
  证据（目标条件在涌现层 profile/plan 里，叙事产物未携带），
  按任务书「动机必须有证据或标记推断」的要求，本阶段宁缺
  勿造；后续如需，可做一个读 v02 plan 记录的适配器（推断
  级、confidence ≤0.5），需 Hu 点头才做。
- FORESHADOWS / PAYOFF_OF：按设计由 Phase 4 伏笔登记反哺。
- v03 resolves 边未映射：其方向语义在本阶段未核实，计入
  unmapped 不猜。
- LLM 语义标注（摘要改写、功能复核）：未启用，预算另议。
