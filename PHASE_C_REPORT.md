# ACP Story Simulator V0.2 — Phase C 报告（认知与记忆层）

日期：2026-11-10　环境：rule / mock 模式（按约束未调用任何真实 LLM/Gemini）

一句话结论：设计 §4.3 的认知循环已落成四个独立可测组件（PredictionEngine /
BeliefManager / PlanManager / MemoryManager），决策全链可从 DB 单独还原；
**tests_v02 31/31 + V0.1 10/10 = 41/41 全过**；rule seed 42 在 3 个
PYTHONHASHSEED 下 world_log 逐字一致，且**与规范轨迹 rule_fixed_seed42（85 事件）
逐字相同**——预测误差在本轨迹中真实发生了 13 次并按规则改变了信念/信任，
但被改变的量都不构成本轨迹中 rule provider 的门控条件（逐项分析见 §5）。

---

## 1. 组件设计（新增文件 + 协议扩展）

### 1.1 `v02/prediction.py` — PredictionEngine（纯确定性结构化计算）

- `predict(agent, context, decision)`：决策生成后、执行前调用。产出
  `Prediction`（protocol 新增模型）：prediction_id（`pred_<角色>_<tick>`）、
  动作类型/目标 + 五个**可核对**字段：
  - `target_present`：目标是否在本 tick 感知中同处一地；
  - `action_success`：move 看目标地是否相邻；言语/展示看目标在场；
  - `refusal_expected`：仅 ask/confront——对目标信任 < **0.25** 时为真；
  - `new_information_expected`：observe/listen/search/发问类、带目标的 speak；
  - `found_expected`：仅 search——地点为 back_room 且尚无"亲历信件"信念。
  - `basis`：预测依据快照（地点、同处者、对目标的信任、已感知事件 id），
    保证每条预测可事后审计"当时凭什么这样预测"。
- `compare(prediction, action_result, next_perception)`：结果回来后逐字段
  核对，产出 `PredictionError`（mismatches 列表 + 加权 surprise：
  target_present 0.4 / action_success 0.3 / refusal 0.2 / new_information
  0.1 / found 0.4；surprise = 落空权重 / 已核对权重）。compare 本身**不改
  任何状态**。严守任务书约束 12：全程无 LLM 自由文本，rule/mock 下同输入
  必同输出（有确定性单测）。

### 1.2 `v02/belief_manager.py` — BeliefManager.apply_prediction_error

明确规则表（方向与幅度可断言，均经 AgentMemory 的钳制/合并规则落账，
三类记忆分账不变；信念写入沿用既有合并规则）：

| 落空情形 | 效果 |
|---|---|
| target_present 落空（目标离场） | 对该目标信任 **−0.05**；suspicion **+0.05** |
| action_success 因其他原因落空 | 有目标：信任 **−0.03**；无目标：suspicion **+0.03**（与上一行同根时不重复计） |
| 未料到被拒（预测不拒、实际被拒） | 信任 **−0.08**；新建/更新信念 `<目标>_cooperative`=0.25（inferred） |
| 料到被拒却被接纳 | 信任 **+0.04**；`<目标>_cooperative` 向 0.65 更新 |
| new_information / found 落空 | 不改信念（只计入 surprise 与巩固重要性） |
| 预测命中 | **零调整** |

每次调整都带 before/after 记入 PredictionError 的 belief_effects /
trust_effects，并独立落 belief_changes 表。

### 1.3 `v02/plan_manager.py` — PlanManager（设计 §4.4 完整化）

- plan 结构沿用 PlanState（plan_id/goal/steps/current_step/commitment/
  status/interruption_conditions/interrupt_log），Phase C 扩展三个字段：
  `step_history`（每步推进记录，标注 counted 与否）、`last_progress_tick`、
  `commitment_updated_tick`。
- **同动作不重复计步（治 V0.1 oscillation）**：advance 时若动作签名与上一次
  *被计数* 的步相同 → 记 `advance_dedup` 迁移，不推进、不算进展（不阻止衰减）。
- **承诺度衰减可量化**：每个无进展 tick −0.02（下限 0.1），有进展 +0.05
  （上限 0.95）；衰减按自上次进展/更新以来的**经过 tick 数**计，decide 前
  由 `reconcile()` 执行，正好等于 0.8−5×0.02=0.70 这类断言可精确成立。
- Phase A 的无原因 abandon/replace 拒绝语义原样保留并扩展到 replace，
  拒绝写 interrupt_log 且有独立迁移记录。**每一次**计划状态变化（含
  set/advance/dedup/decay/complete/rejected）都产生一条带 before/after
  快照的 plan_transitions 记录。
- 决策上下文新增 `plan_step` extra：{plan_id, goal, step_index, step,
  steps_total, commitment, status}（设计 §4.4"注入当前步"）；另注入
  `last_prediction_error`（mismatches+surprise），使认知循环闭环进下一次
  决策的上下文。

### 1.4 `v02/memory_manager.py` — MemoryManager.consolidate

- 每次预测评估后巩固一次：对评估窗口内新增的短期记忆条目逐条评分
  importance = 类型基分（experienced 0.5 / heard 0.35 / inferred 0.25）
  **+ 0.5 × 本次 surprise**（评分与预测误差显式关联），≥ **0.65** 晋升到
  `CharacterState.long_term_memory`（protocol 新增字段，随 state.json 落盘）；
  评分与结果全量写入 memory_consolidations 表（entries_json 含每条的
  importance 与 consolidated 标记）。

### 1.5 决策全链落库（`v02/persistence.py` 扩展）

- 链的连接键为客户端生成的 `decision_key = "<角色>:<tick>"`（预测在执行前
  就要落库，不能等自增 id）。`agent_decisions` 新增 `decision_key` 与
  `context_json`（DecisionContext 全量快照）两列。
- 新增四表：`predictions`（预测 JSON + 误差 JSON + surprise + 评估 tick）、
  `belief_changes`（含信任变化，kind=trust、key=`trust:<对方>`）、
  `plan_transitions`、`memory_consolidations`。
- `Store.chain_for(decision_id)`：只读 DB 即可重建
  context → prediction → decision → execution_result → prediction_error →
  belief_changes → plan_transitions → consolidation 完整链。

### 1.6 接线（agent.py / scheduler.py）

- `CharacterAgent.receive()` 开头先评估上一条决策的待决预测（compare →
  BeliefManager → consolidate），再走原有感知/解读路径，语义顺序与 Phase A/B
  不冲突；评估产物经"chain updates"缓冲由 Scheduler 落库，agent 不碰 DB。
- `decide()`：reconcile 衰减 → 建 context（含 plan_step）→ provider →
  PlanManager.apply_update → 生成本条决策的 Prediction 挂起待评估。
  provider 失败时不生成预测（诚实失败语义不变）。
- Scheduler 在每次 receive 后（含终局投递）排空 chain updates 落库；
  决策落库时同键写入 prediction 与本次 plan transitions。

## 2. 验收逐项实测

### 2.1 测试总数

| 套件 | 结果 |
|---|---|
| tests_v02（21 旧 + 10 新） | **31/31 通过** |
| tests（V0.1 基线，未动） | **10/10 通过** |
| 合计 | **41 passed（pytest 实测 5.64s）** |

新增 `tests_v02/test_phase_c.py`（10 个测试）：

- (a) 计划持续性（设计 12.5 口径）：四步计划跨 tick 推进、同动作重复
  advance 被 dedup（步数不变且留 `advance_dedup` 记录）、承诺度 5 tick 无
  进展恰衰减 0.80→0.70、dedup 不算进展（衰减照常从上次真进展计）、无原因
  abandon/replace 被拒并写 interrupt_log 与迁移记录、计划跨真实 decide()
  存续且 context 的 plan_step 与当前步一致。
- (b) 预测误差更新：构造 target_left 证伪（信任 0.30→0.25、suspicion
  +0.05、surprise=1.0、三类 chain 记录齐备）；未料被拒证伪（链上信任
  0.30→0.22、新信念 owner_cooperative=0.25/inferred）；预测命中时
  mismatches 为空、surprise=0、零 belief_change、信任不变、无虚假信念。
- 巩固评分：surprise=1.0 时 experienced 条目重要性 1.0、inferred 0.75 均
  晋升；surprise=0 时 0.5 不晋升——评分与误差的关联可量化断言。
- (c) DB 全链还原：真实 rule 运行 12 tick（36 决策），断言
  decisions=predictions=consolidations=36，任取士兵一条决策用
  `chain_for` 重建并断言八个环节齐备（含 context 快照、basis 快照、
  execution_result 的 ok 字段、误差的 decision_tick 对齐、consolidation
  的 surprise 与误差一致）。

### 2.2 rule 模式 seed 42 回归（runs_v02/rule_phasec_seed42）

| 检查 | 结果 |
|---|---|
| PYTHONHASHSEED ∈ {0, 1, 42} 三跑 world_log | **逐字一致**（相互之间） |
| 与规范轨迹 rule_fixed_seed42（85 事件） | **逐字相同**，事件总数 85 |
| 士兵 anonymous_letter_exists | **1.0 / experienced** ✓ |
| 商人 anonymous_letter_exists | **0.7 / heard** ✓ |
| 老板 letter_known_by_others | 0.9（与规范一致） |
| DB 计数 | decisions 60 / events 85 / deliveries 60 / dedup_drops 0 / **predictions 60 / belief_changes 8 / plan_transitions 84 / consolidations 60** |

## 3. 轨迹差异分析：为什么误差真实发生、轨迹却不变

本 run 中 60 条预测全部被评估，**13 条被证伪**（surprise 0.1–1.0），其中
8 条产生了信念/信任调整，全部与规则表逐条对得上：

- tick 8（评估时点）：老板、商人对士兵的 speak 因士兵先行离场失败
  （target_left）→ 两人对士兵信任 0.24→0.19（target_present_miss −0.05）；
  同类事件在 tick 16、19 再次发生（商人对士兵信任 0.218→0.168→0.147）。
- tick 14：士兵 confront 老板未料被拒 → 信任 0.30→0.22、新信念
  owner_cooperative=0.25（unexpected_refusal）。
- tick 18：士兵此时对老板信任已降至 0.12 < 0.25，预测"会被拒"，老板却
  接话 → 信任 +0.04（0.12→0.16）、owner_cooperative 按既有合并规则更新至
  0.65（expected_refusal_but_engaged）——预测规则随信任变化而翻转、误差
  方向也随之翻转，闭环行为符合设计预期。
- 其余证伪为 new_information 类（预期有新信息而无），按规则表只计
  surprise 与巩固评分，不改信念。

轨迹不变的原因（逐项核对 rule provider 的判定条件）：
1. 被调低的"对士兵信任"属于老板/商人，而 rule provider 中以信任为门控的
   分支（商人 0.45 的 mayor_business 披露）只看**对当前对话者**的信任，
   本轨迹中该分支的触发判定不依赖对士兵的信任值；
2. 士兵对老板信任的变化（0.30→0.22→0.12→0.16）不进入士兵任何分支条件——
   士兵的言语/搜索决策由信念（信件/镇长）与 suspicion 门控，suspicion 在
   本轨迹中未被误差路径触碰（士兵从未遭遇 target_present 落空）；
3. owner_cooperative 是 Phase C 新增信念键，rule provider 不读它。
结论：**无差异是机制作用面与本轨迹判定面的真实交集结果，不是误差未生效**
——DB 中 8 条 belief_changes 与 13 条证伪记录即为生效证据；轨迹保持规范
形态反而使 Phase C 的回归结论最强（认知层全程运转且零回归）。

另：全 run 共 11 条短期记忆被巩固晋升为长期记忆（60 次巩固评估中 4 次有
晋升，均为 surprise=1.0 的 speak_failed 评估窗口内的亲历条目）。

## 4. 约束遵守

- V0.1（src/、tests/、runs/、characters/）零改动（文件时间戳仍为 11-03）；
  既有 runs_v02/ 目录未删未改，仅新增 rule_phasec_seed42。
- 未调用真实 LLM/Gemini；未触碰任何凭据/密钥池；rule provider 的 sorted()
  确定性修复未回退（三哈希种子复验通过即为证据）。
- 信息隔离、三类记忆分账、信念合并规则、Resolver 语义、Phase B 调度/
  去重/会话语义均未改动（21 个旧测试含 Phase B 六项定向测试全过）。

## 5. 改动文件清单

新增：`v02/prediction.py`、`v02/belief_manager.py`、`v02/plan_manager.py`、
`v02/memory_manager.py`、`tests_v02/test_phase_c.py`、本报告、
`runs_v02/rule_phasec_seed42/`（新 run，未覆盖任何既有 run）。

修改：`v02/protocol.py`（PlanState +3 字段、CharacterState +long_term_memory、
新增 Prediction/PredictionError 模型）、`v02/agent.py`（认知组件接线、
plan 委托 PlanManager、chain updates 缓冲）、`v02/context_builder.py`
（注入 plan_step 与 last_prediction_error）、`v02/scheduler.py`（chain
updates 落库、决策同键写 prediction/transitions）、`v02/persistence.py`
（agent_decisions +2 列、四张新表、chain_for 与新计数）。

## 6. 遗留问题（如实）

1. **llm 模式未在本期验证**：Prediction 的 refusal 阈值（0.25）与误差权重
   是针对 rule 轨迹校准的先验值；Gemini 决策的言语模式不同（全 20-tick
   llm 轨迹中无人进后室），误差分布会不同，权重/阈值可能需要按 llm 实测
   再校准——本期按约束未跑真实 LLM。
2. **计划"当前步"目前只进 context、不约束 rule provider**：rule provider
   仍按自身规则选动作，PlanManager 只做记账与门控；让计划真正引导动作
   选择属于行为层改造，不在 Phase C 范围。
3. **巩固窗口的近似**：consolidate 以"本次评估 tick 前后新增条目"为窗口，
   跨 tick 迟到的解读条目会被计入相邻决策的巩固批次；评分本身无误，但
   归属粒度是 tick 级而非事件级。
4. `basis.perceived_ids` 只存事件 id 快照，不存事件全文——全文在
   world_events 表中可按 id 找回，链还原时需一次 join 式查询（chain_for
   未代劳，属有意保持轻量）。
