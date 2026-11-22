# 小说改编能力升级 — 总报告（Novel Adaptation Upgrade）

日期：2026-11-22。任务书：《小说自动生成能力升级任务书》（Hu 于本聊天交付）。
目标管线：World Simulation → Narrative Intelligence → Story
Architecture → Novel Writer → Quality Audit。
验收场景：酒馆 `rule_v02c4_seed42`、灯塔 `c5_lighthouse_seed7`、
诊所 `clinic_v02cfix_seed42`。

## 结论先行

升级前的叙事层是一个「忠实转录机」：产物与事件流一一对应、引文可
溯源，但同一句台词在一篇里重复 14 次（酒馆旧稿句子重复率 0.348），
因为它没有语义层——不知道哪些事件是同一件事的转述、谁的命运在哪个
事件上转了向、哪条伏笔收了哪条没有。

升级后，管线在转录之上补齐了五层：语义事件层（NarrativeEvent）、
因果层（统一因果图）、人物与结构层（弧线/线索/冲突双口径视图、
伏笔三级证据梯）、规划层（三级大纲 + 连续性校验）、以及带 F1/F2/F3
分级的新 Writer 与 12 维质量评价。三场新小说的句子重复率降至
0–0.013；事实与知情两项确定性指标在酒馆、灯塔满分，诊所事实一致性
2.0 不及格（原因已定位，见 Phase 6 报告）。全升级新增/改动代码全部
位于新模块，既有 `narrative/` 与 `v03/` 代码零修改；全量回归
327 passed + 1 parked red（P-C4-1，与升级无关，待 Hu 裁决）。

## 红线执行情况

- 叙事层只读下游：全部新模块只读 world_log / observations / 既有
  产物，不写世界状态、不干预 agent 决策。
- 事实与解释物理分离：NarrativeEvent 的 evidence_level 区分
  objective / subjective / interpretation / creative；Writer 输出按
  段标注 F1/F2/F3，F3 全量登记。
- 每条事实陈述可溯源到事件 id：F1 段落强制 event_ids，确定性审计
  逐字核对引文。
- 未决线索永不自动收束：ThreadView 双口径（v03 口径与叙事口径）
  并排保存、从不平均、从不代决；大纲的 ending_state 由线索状态
  原样组装，open 保持 open。
- 诚实零值：MOTIVATED_BY 边为零（无确定性动机证据）、
  FORESHADOWS/PAYOFF_OF 边为零（两处 payoff 均无独立的 setup 事件）
  ——都如实记录为零并注明原因，没有为了好看而造边。

## 各阶段交付

- **Phase 0 审计**（`docs/NOVEL_ADAPTATION_AUDIT.md`）：判定旧叙事层
  为转录机而非故事理解系统，给出硬证据（重复率、输入面缺失清单）；
  架构裁决（Hu 按推荐批准）：新模块、零改旧代码、v03 缺失时优雅降级。
- **Phase 1 语义模型**（`docs/NARRATIVE_UPGRADE_DESIGN.md` 全六期设计；
  `docs/NOVEL_PHASE1_REPORT.md`）：`narrative/models/` 全类型 schema
  （NarrativeEvent 含 16 类戏剧功能多标签、统一 11 关系词表、
  CausalEdge、CharacterArc、ConflictView 的 severity 与 tension 分列、
  ThreadView、SetupPayoffRecord 五态、Story/Chapter/Scene 大纲、
  带强制 evaluator 字段的 QualityReport）；确定性投影
  observations → NarrativeEvent 1:1（多源合并是抗重复的结构前提）。
  三场投影：73 / 104 / 107 个 NE。
- **Phase 2 语义+因果**（`docs/NOVEL_PHASE2_REPORT.md`）：v03 回填
  （为灯塔/诊所新著场景配置 `stories/lighthouse_watch.json`、
  `stories/clinic_fever.json` 并 replay 出 v03 产物）；escalation
  误标修复（仅限 contest 语境，酒馆 18→13、灯塔/诊所→0）；
  `narrative/semantic.py` 合并组量化重复（酒馆 39 个发言事件只有
  15 个唯一内容，62% 是转述）；`narrative/causal_graph.py` 统一因果图
  （抽取器 E 边 + v03 关系映射，独立验证器校验端点/证据/时序，
  CAUSED_BY 必须有交付或发现证据、纯命题拒绝）——三场 31/32/13 条
  边全部验证通过，`trace_causes` 可用（信件发现 E0008 是士兵断言
  E0022 的因果祖先得到验证）。诚实差异制度化：v03 线索进度对灯塔/
  诊所读 0.0（其答题规则只认信息型证据，而灯塔的收束是动作型），
  与叙事口径并排保存、不互相覆盖。
- **Phase 3 人物弧线与故事视图**（`docs/NOVEL_PHASE3_REPORT.md`）：
  `narrative/character_arc.py` 的转折点设门槛——学到线索核心事实
  只有在有下游因果使用或本人转述时才算转折，未达标的进诊断
  （impact_without_followthrough）不删除不拔高。结果与涌现事实
  吻合：士兵在两封信事实上都有转折（贪腐事实 10 次下游使用），
  商人 flat_arc，诊所林护士 flat_arc（学到 2 个事实、零下游使用——
  正对应她断裂的补药线）。弧线价性类型（悲剧/成长等）规则层从不
  自动指派。`narrative/story_views.py`：ThreadView 双口径合并；
  ConflictView 的 tension 独立计算（0.5·升级 + 0.5·对峙密度）——
  酒馆 C003 证明必要性：severity 0.4 而 tension 0.0，是一个从未
  真正爆发的声明冲突。情感/价值变化诚实为空（涌现数据无情感轨迹）。
- **Phase 4 伏笔管理**（`docs/NOVEL_PHASE4_REPORT.md`）：
  `narrative/setup_payoff.py` 三级证据梯（发现/佐证/解决）逐条显式
  保存，payoff 只认解决级。关键案例：酒馆信件存在性问题 payoff
  （发现即直接证据）；贪腐问题只到 reinforcement 不许算 payoff
  （信被找到、读到、转述，但贪腐本身从未被直接证据回答）——证据梯
  拦住了一次最容易发生的误判。诊所 6 条记录无一被假收束，断线保持
  断线。
- **Phase 5 三级规划**（`docs/NOVEL_PHASE5_REPORT.md`）：
  `narrative/planner/` 按地点/阵容/静默间隔 + 转折点切分场景
  （酒馆酒吧长段从 1 个 38 事件块切成 5 场），章节在高潮/线索切换/
  4 场处收章，故事大纲的标题/主题/核心问题取自场景配置、从不杜撰。
  连续性校验 4 项确定性检查。本期关键发现：叙事层此前从未消费
  **种子信念**（角色 state.json 中 evidence 为 `seed:<fact>` 的开局
  知识），校验器因此误报灯塔守塔人的船讯断言；新增
  `narrative/initial_knowledge.py` 统一推导初始知识后，三场计划
  全部 0 错误通过。
- **Phase 6 Writer 与评价**（`docs/NOVEL_PHASE6_REPORT.md`）：
  见该报告。要点：九项输入装配 + 合并组 repeat 标注让重复在结构上
  消失（重复率 0.348/0.222 → 0.013/0）；F1/F2/F3 分级与登记落地；
  12 维评价（6 确定性 + 6 独立评审）三场均值 3.95 / 3.70 / 3.56；
  总用量 37k 入 / 22k 出（批准硬顶 200k/60k）。引擎为 Gemini
  gemini-2.5-flash（三场同引擎，Hu 决定等配额恢复而非换引擎）。

## 仍然存在的边界（如实）

1. 诊所事实一致性 2.0：动作事件的系统日志串（`search @ oncall` 等）
   被 Writer 当引文写入正文。输入侧标注「日志非台词」是明确的修复
   方向，未在本期实施。
2. 对话自然度 1.5–2.8：跨场景的同句复述来自涌现层规则模式的台词
   池，叙事层合并组只能压同场重复；根治在涌现层（主聊天的线）。
3. F3 创造性段落约占 40%：已全量登记可审计，但比例偏高，后续可
   在 prompt 设上限。
4. MOTIVATED_BY 与 FORESHADOWS/PAYOFF_OF 边为零：前者缺确定性
   动机证据（v02 plan 适配器可做到推断级，需另行批准）；后者是
   这批数据的真实形态（payoff 与 setup 同事件）。
5. 情感维度只有结构代理：涌现数据没有情感轨迹，弧线的
   emotional_changes 诚实为空，评审的情感变化分是文学判断而非
   数据测量。

## 产物索引

- 代码：`narrative/models/`、`narrative/semantic.py`、
  `narrative/causal_graph.py`、`narrative/character_arc.py`、
  `narrative/story_views.py`、`narrative/setup_payoff.py`、
  `narrative/initial_knowledge.py`、`narrative/planner/`、
  `narrative/gemini_client.py`、`narrative/writer2.py`、
  `narrative/quality.py`；脚本 `scripts/backfill_v03.py`、
  `scripts/run_phase6_remaining.py`。
- 测试：`tests_narrative/test_models.py`、`test_causal.py`、
  `test_arcs_views.py`、`test_setup_payoff.py`、`test_planner.py`、
  `test_writer2_quality.py`（升级新增 24 项）。
- 数据：每场 `narrative_runs/<run>/upgrade/`（narrative_events、
  semantic、causal_graph、character_arcs、story_views、
  setup_payoff、foreshadow_edges、plan、novel_v2、trace_v2、
  narrative_quality_report、comparison）；灯塔/诊所的 v03 回填产物
  在 `v03_runs/<run>/`。
- 文档：`docs/NOVEL_ADAPTATION_AUDIT.md`、
  `docs/NARRATIVE_UPGRADE_DESIGN.md`、
  `docs/NOVEL_PHASE{1..6}_REPORT.md`、本报告。
