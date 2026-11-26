# Phase 2 Step 1 审计：模块映射、Phase 1 遗留验收清单与整合方案

日期：2026-11-26。方法：以当前代码与当日可复现运行结果为准，
不以历史报告为准。测试现状（当日实跑）：全量 350 passed，
1 failed = tests_v02/test_phase_d.py::
test_counterfactual_pair_seed42（P-C4-1，断言 9 == 8），
无 skip。该失败自 V0.2-C 起 parked，属等待 Hu 裁定的
pin 争议（见 §4），不是 Phase 1/2 引入的回归。

## 1. 当前数据流（叙事链）

world_log.json → observer → observations.json →
models/build.py → NarrativeEvent[] → semantic / causal_graph /
character_arc / story_views / setup_payoff → planner
（story_views + character_arcs + setup_payoff 三个输入）→
plan.json → writer2（material 适配 + already_revealed）→
小说 + trace → quality（12 维）/ metrics / scene_records /
fbudget（全部事后分析）。

涌现侧：v02 agent + Goal Engine（goal_engine.py，目标变化
写入 acp.sqlite3）→ World Engine 提交事件；v03
decision_quality Judge 在 tick 边界外只读诊断；
intervention 经 live.py 的 extra_hook 注入（默认关闭）。

## 2. 模块映射（任务书 §3.2）

| 能力 | 当前已有模块 | 当前职责 | 缺失能力 | 本阶段处理方式 |
|---|---|---|---|---|
| 情绪推断 | narrative/psychology.py | 逐场规则推断，证据+置信度，unknown 诚实 | 无轨迹；产物无人消费 | 增强：增量更新记录；接入状态视图 |
| 动机推断 | narrative/psychology.py（MOTIVATED_BY） | 假设级推断，物理隔离于事实层 | 全部停在 HYPOTHESIS；无人消费 | 增强：证据累积与状态视图接入 |
| 人物目标 | v02/goal_engine.py + v03 threads | 涌现侧目标引擎真实运行；叙事侧只有 thread 视图 | 叙事链完全不读人物目标 | 复用：状态视图只读聚合（acp goal_changes + 配置目标） |
| 人物知识 | character_arc 学习记录 + initial_knowledge 种子知识 | 学习事件可追溯；连续性校验 0 错误 | Writer 只收到 already_revealed 全局清单，无逐人物知道/不知道约束 | 增强：状态视图逐人物知识；Writer 注入约束；场景后验证 |
| 人物弧线 | narrative/character_arc.py | 信念学习、转折点门、flat_arc 诚实判定 | 只喂故事大纲，不喂场景 | 复用：状态视图聚合弧线阶段/约束 |
| 场景规划 | narrative/planner/ | 三级大纲，输入 = views+arcs+setup | 场景不读人物状态：无逐人物目标/冲突/信息差/预期状态变化 | 增强：scene_planner 接入状态视图 |
| Writer 输入 | writer2.py + material.py | 素材过滤、逐字引用纪律、already_revealed、F 软提示（规则 7） | 无人物心理/目标/知识约束；心理靠模型自由发挥 | 增强：assemble_context 注入人物状态与场景预期；show-don't-tell 规则 |
| 状态验证 | quality.py（12 维）+ planner 连续性校验 | 全书事后评分；知识一致性 = 大纲校验 + F1 顺序代理指标 | 无逐场景「计划 vs 实际」验证；无心理说明书式旁白检测 | 新增 scene_verification（见 §5 理由） |

## 3. 核心缺口（审计实证，非推测）

1. **psychology 产物无人消费**：全仓搜索显示
   psychology.json / inference_edges.json 的读取者只有
   metrics.py（算覆盖率统计）和 psychology 自己的测试。
   Planner 与 Writer 从不读取——心理推断与生成完全脱节。
2. **F 预算不在生成链上**：writer2.py 与 quality.py 均未
   调用 fbudget；analyze() 只被报告脚本事后调用。生成期
   唯一影响是 SYSTEM 提示规则 7（软约束）。Phase 1 收尾
   实测：F3 0.40 → 0.29，仍超 0.20 上限。
3. **场景规划不读人物状态**：build_plan 的输入只有
   story_views / character_arcs / setup_payoff；场景大纲
   没有逐人物目标、信息差、预期状态变化字段。
4. **知识约束是全局的**：already_revealed 是「全场已揭示
   事实」清单，不是逐人物的知道/不知道/误信；quality 的
   knowledge_consistency 是代理指标，不是「人物在文本中
   用了未掌握信息」的直接检查。
5. **实时干预未验证**：Judge 与 intervention 代码在位、
   离线回放通过；实时 A/B 于 2026-11-24 中断后经 Hu 决定
   停止，未完成。本任务书 §2.3 要求完整重跑一组对照。

## 4. Phase 1 遗留验收清单（初步判定，Step 2 定版）

| 项目 | 判定 | 依据 |
|---|---|---|
| 素材适配 / 日志隔离 | 通过 | 真实生成验证：诊所事实一致性 2.0 → 5.0，泄漏 2 → 0 |
| 决策进度 Judge | 通过 | Phase 6 四场回溯验证，诊断与实际一致 |
| 实时干预 A/B | **未通过** | 从未完整运行；Step 2b 完整重跑一组 |
| F 预算执行 | 部分通过 | 统计/诊断/软提示在位；无生成期闭环；F3 仍超标。Step 2a 做 ON/OFF 对照并定版 |
| 场景重复检测 | 部分通过 | 确定性层 1–2 通过（三场 0.0）；第 3 层人工判定未做 |
| 情绪/动机数据 | 通过（数据层） | 三场均有产物、证据纪律合格；未接入生成（Phase 2 核心） |
| 人工抽样 | **未完成** | human_sampling 字段至今为 null；Step 2c 建抽样包，评分由 Hu 本人给出 |
| 章节标题 | 通过 | 2026-11-26 收尾：13 章 literary_title 已生成并生效 |
| P-C4-1 parked 测试 | 已知债务 | pin 值 8 vs 实际 9 的裁定在 Hu 处（已在 Reminders）；不影响叙事链与 Phase 2，Step 2d 报告中定版说明 |
| 报告与代码一致性 | 已核 | Phase 1 曾有一处 erratum（narrative_events 的 causal_links 被重建清空），已从基线修复并复验值级一致；本次审计复查三场 narrative_events 与基线仅 summary_source 差异 |

## 5. Phase 2 计划改动（最小必要，禁止平行系统）

修改现有模块：
- narrative/psychology.py：新增增量状态更新记录（事件 →
  状态增量：前后值、原因、证据、置信度、替代解释），
  仍只写推断层产物，绝不写事实层。
- narrative/planner/scene_planner.py：场景大纲增加人物
  状态派生字段（逐人物场景目标、目标冲突、信息差、
  预期状态变化、必须保持不变项、无变化场景标记）。
- narrative/writer2.py：assemble_context 注入人物状态
  视图与场景预期；提示词新增 show-don't-tell 与逐人物
  知识约束（知道/不得使用/只能猜测）；F 预算闭环
  （生成后测量 → 超标场景定向改写一轮 → 复测 → 仍超标
  则显式诊断写入 trace，绝不静默）；预算开关供 A/B。
- narrative/quality.py / metrics.py：接入场景验证结果；
  human_sampling 支持真实评分写入。

新增模块（各附必要性）：
- narrative/character_state.py：统一状态「读取视图」。
  必要性：目标/知识/弧线/心理分属四个模块与 acp 存储，
  无任何现有模块承担聚合职责；本模块只是适配层，
  不新增存储、不产生新事实（符合任务书 §4.1）。
- narrative/scene_verification.py：逐场景计划-实际验证。
  必要性：quality.py 是全书事后评分，粒度与时机都不同；
  §4.7 要求场景级差异记录与重写判定，现有模块无此职责。

不新增：Character State 数据库、第二套 Psychology /
Goal / Knowledge 系统。一切状态仍由现有模块产生，
character_state 只做聚合读取。

## 6. Step 2 实验预算（先行报备）

- F 预算 ON/OFF 对照（Gemini，酒馆同流两场生成+评审）：
  约 28k 入 / 15k 出。
- 实时干预 A/B（DeepSeek，OFF+ON 各 20 tick 完整运行，
  中断按任务书要求完整重跑）：约 430k 入 / 22k 出
  （按 Phase 6 实测单场约 215k 入推算）。
- 人工抽样包：纯脚本抽取，无模型费用；评分需 Hu 本人
  约 15 段文本的时间。
- Step 3/4 的生成与反事实测试预算在 Step 3 方案定版时
  另行报备。
