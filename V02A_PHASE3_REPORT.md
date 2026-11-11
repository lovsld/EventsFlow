# V0.2-A Phase 3 报告：状态系统核心（信念证据化 / 目标生命周期 / 计划补强）

日期：2026-11-11。范围：任务书「V0.2-A 通用 Agent 决策系统」Phase 2（协议与校验
加固）与 Phase 3（状态系统核心）的实施记录。Phase 1 审查见
`docs/V0.2-A_PHASE1_REVIEW.md`；实验与通用性结论见
`docs/V0.2-A_EXPERIMENT_REPORT.md`；结构说明见 `docs/V0.2-A_ARCHITECTURE.md`。

## 1. Phase 2：协议与校验加固

- `AgentDecision` / `ActionSpec` / `PlanUpdate` 全部 `extra="forbid"`：
  未知字段在协议层即被拒绝（多后端 `_sanitize` 不剥字段，靠严格校验 +
  修复重试收敛）。
- 新增 `selected_goal_id`：provider 在决策时声明本轮服务的目标；运行时在
  `CharacterAgent.decide()` 校验其必须属于该角色当前目标集，非法 → 决策
  判无效、错误落入该决策的审计记录、调度层降级为 wait，绝不静默放行。
  rule provider 按「当前计划目标（若是目标 key）→ 最高优先级 active 目标」
  确定性回填；两个 LLM provider 的 JSON 契约文本同步声明该字段。
- LLM 行动在提交 Resolver 前做 `available_actions` 成员校验（审计 M4c 的
  事后检查前移为运行时校验，匹配语义与审计一致）；仅对真实 LLM provider
  生效（类属性 `validates_membership`），rule/mock 路径行为不变。
- LLM 超时环境变量可配：`ACP_<BACKEND>_TIMEOUT` > `ACP_LLM_TIMEOUT` >
  内置默认 60s（与现状一致）。
- 新增测试 `tests_v02/test_v02a_phase2.py`（12 个）：未知字段拒绝（顶层 /
  action / plan_update 三处）、伪造 goal id 拒绝且落盘、合法 goal id 放行、
  越界发言运行时拦截与在集内放行、超时优先级。

## 2. 信念证据化（Phase 3-1）

`Belief` 增加 `evidence` 列表，每条含 `source_event_id`、`source_type`
（direct_observation / direct_interaction / testimony / inference /
assumption）、`reliability`、`observed_at`、`source`。与既有 kind 的映射：
direct_* → experienced、testimony → heard、inference/assumption →
inferred（等级 4/3/2/1/0）。

- kind 由证据派生：取证据中最高等级者的映射。
- 置信度由证据重算：按「独立通道」分组（传闻按说话人分组——同一人重复
  转述只算一条；亲历类按来源类型/对象分组），组内取最大 reliability，
  组间 noisy-OR 合并 `1 − Π(1 − r)`，钳制 (0,1] 并四舍五入到 3 位。
  同一 `source_event_id` 对同一信念只入账一次（去重）。
- 旧 state.json 兼容：无证据的旧信念在载入时迁移为一条等价 evidence
  （`seed:<key>`，reliability = 原置信度）；检查点/续跑测试保持全绿。
- 任务书 §十三 C 组语义由 `tests_v02/test_v02a_phase3.py` 覆盖：传闻 0.7 →
  亲历 1.0 后等级升级为 experienced 且两条证据并存；亲历后传闻不再拖低；
  同事件不重复累积；同源传闻不叠加、独立来源 corroborate（0.5+0.5→0.75）；
  多源传闻置信度 < 1。

### 新规范轨迹（如实记录，未为凑旧值调参）

新规范轨迹：`runs_v02/rule_v02a_seed42`（85 事件，world_log md5
`035483fae5…`）。旧规范 `rule_planlayer_seed42`（87 事件）原样保留为
V0.2-A 前基线。

种子组（42/7/101/2024/555，`runs_v02/exp_seedgroup_v02a`）：

| 种子 | 事件 | 士兵信 | 行商信 | 老板知信外泄 | 士兵信镇长贪腐 |
|---|---|---|---|---|---|
| 42 | 85 | 1.00/experienced | 0.70/heard | 0.90/inferred | **0.80/heard** |
| 7 | 91 | 1.00/experienced | — | — | 0.60/heard |
| 101 | 83 | 1.00/experienced | 0.70/heard | 0.90/inferred | **0.80/heard** |
| 2024 | 80 | 1.00/experienced | 0.70/heard | 0.90/inferred | **0.80/heard** |
| 555 | 85 | 1.00/experienced | 0.70/heard | 0.90/inferred | **0.80/heard** |

- 涌现链完整：士兵亲读信一致性 **5/5**（全部种子 1.0/experienced）；
  轨迹多样性 Jaccard 均值 0.7232（旧 0.739）。
- 关键变化：士兵 mayor_embezzled 在 4/5 种子由 0.6 → **0.8**。根因是新
  语义本身：士兵在这些轨迹里先后从**两个独立说话人**处听到镇长贪腐的
  说法，独立证言 corroborate（noisy-OR）合理地高于单源 0.6；只有单源的
  种子 7 保持 0.6。这是设计目标（多源证据应比单源更可信），不是回归。
- 种子 7 中信件消息未传到行商/老板（两人终局无信件信念）：该种子轨迹里
  士兵读信后未发生有效的信件讨论投递——属轨迹方差，旧种子组亦有过
  类似缺口（如旧组行商 mayor 相关信念的种子间差异）。

## 3. 信念变化历史补全（Phase 3-2）

解释路径（`_interpret` / 感知引发的 `update_belief` 与信任调整）现在同样
产生 `belief_changes` 审计行（cause="interpretation"，带 before/after 与
证据事件 id）。新规范轨迹中 belief_changes 共 44 行，其中 **42 行来自
解释路径**——这些变化在 V0.2-A 之前完全不入表（旧 LLM 轨迹全程只有 1 行）。
`chain_for()` 的因果链因此对解释路径同样可复原。

## 4. Goal 结构化（Phase 3-3）

`Goal` 扩展为运行时对象：goal_id(=key)/description/priority(=weight)/
status(active|paused|completed|abandoned)/progress/commitment（缺省 =
weight)/created_at/deadline。生命周期由**确定性规则**驱动，只由「计划
目标标签与 goal key 对齐」的计划事件触发：设计划 → active（paused 的
重新激活）；计数步进 → progress = 已完成步数/总步数；计划完成 →
completed/1.0；provider 经既有 plan_update 通道 abandon → abandoned。
全部变更落 `goal_changes` 表（含 before/after 快照）。**未引入 LLM 自由
增删目标**：`selected_goal_id` 只许指向既有目标（Phase 2 已校验）；
plan_update 的 new_goal 沿用既有的计划标签自由命名空间语义（计划标签
≠ 目标 key 时不触碰目标生命周期）——此点与任务书字面（§八.5 要求
new_goal 归属校验）的偏离及理由记于本报告 §7。

上下文中目标按优先级（weight 降序）呈现；多目标并存、暂停/恢复、
完成、放弃、优先级呈现均有测试（test_v02a_phase3.py D 组）。

**轨迹中的实际啮合情况（如实）**：酒馆三人中只有老板的计划标签与目标
key 对齐（find_letter_source 等），士兵/行商的计划标签
（press_for_evidence 等）不在其目标 key 集合内，因此目标生命周期在
rule 轨迹中主要在老板身上运转（新规范轨迹 goal_changes 2 行）；实验 1
三条 run 的目标完成率均为 0.0——生命周期机制本身经单元测试证明，但
「计划标签 ↔ 目标 key」的对齐在酒馆剧本里偏薄，这是剧本/候选层的事，
列入 V0.2-B 建议（让 rule 候选的计划标签与目标 key 对齐）。

## 5. Plan 补强（Phase 3-4）

- blocked 状态：当前步骤被执行且与步骤一致、但未达成（被拒/未命中）
  累计 2 次 → 计划 blocked 并记录原因（plan_transitions 中
  step_failure/blocked 行）；provider 显式 advance 可解阻并继续步进；
  replace/abandon 对 blocked 计划同样有效；blocked 期间承诺不衰减、
  结果驱动步进不触发。
- 高承诺替换门控：commitment ≥ 0.7 的计划不被普通信息事件替换
  （尝试被记录为 replace_blocked_by_commitment 并记 interrupt_log）；
  只有关键新信息（亲历发现信件、对话者直接揭示与本人相关的事）可
  替换。新规范轨迹中门控实际触发 **5 次**（种子组共 16 次）——它是
  本次轨迹变化（87 → 85 事件）的来源之一。
- 计划层指标复算（STEP1 审计口径，新种子组 300 决策）：**M1a 0.6932
  （旧 0.754）、M1b 0.7727（旧 0.8175）**；M2 1.0、M4 拒单率 0.0367、
  已提交违规 0、M6 1.0 不变。M1 回落的构成：门控让高承诺计划存活更久，
  角色出于其他缘由的行动不再靠「换计划」对齐口径，而是如实计为偏离
  当前步——即旧口径的一部分高分来自计划频繁改名追随行为，门控把这
  部分水分挤掉了；同时 M1 分母由 126 缩至 88（完成计划更快清理）。
  这是指标口径与新机制之间的预期张力，不是行为退化：硬性不变量
  （M2 知识支撑、M4 零违规、M6 依据真实）全部保持满分。

## 6. 情绪口径对齐（Phase 3-5）

按审查意见只做对齐与文档化，不扩建多维模型：运行时情绪状态就是
`{arousal, suspicion}` 两个标量加关系中的 per-agent trust；删除了从未
被写入的死字段 valence。人格五维中 **honesty 至今没有任何读取点**
（其余四维 caution/curiosity/impulsiveness/sociability 均有实际写点/
读点），在架构文档中如实标注为「未生效维度」。

## 7. 与任务书/审查的偏离（如实记录）

1. **plan_update.new_goal 的归属校验未按字面实施**。审查与代码事实：
   计划目标标签是与 Goal key 相互独立的既有自由命名空间（既有测试
   `test_phase_c.py` 即以计划标签 assess_damage 做 replace 并断言成功，
   它不是任何角色的 goal key）。若对 new_goal 施加 goal-key 归属校验，
   将推翻既有确立语义且需改动既有断言。故归属校验严格施加于
   `selected_goal_id`（任务书 §八.5 的本意——决策声明的目标必须真实），
   计划标签与 Goal 的联动只在两者相等时发生（审查 3-3 的对齐表述）。
2. **替换门控的 target 维度未单独实现**：计划对象没有「目标角色」
   字段，「target 离开」类中断没有可判定的数据基础；门控以
   critical/普通的信息类别实现，target 维度记为部分满足。
3. 情绪 valence 字段被删除（而非保留占位）：它只在 protocol 默认值
   里出现过一次，无任何读写点，删除不改变任何序列化结果之外的行为；
   旧 state.json 中即使带 valence 也被 pydantic 忽略（模型未设
   extra=forbid 于 CharacterState）。

## 8. 既有测试断言调整清单（逐条）

| 测试 | 原断言 | 新断言 | 理由 |
|---|---|---|---|
| test_plan_layer.py::test_unfulfilled_or_failed_result_advances_nothing | 步进失败后 plan 更新列表为空 | 指针仍不步进，且恰有一条 step_failure 记录 | Phase 3 的 blocked 记账要求失败留痕；原断言编码的是「失败不留计划层痕迹」的旧行为 |
| test_phase_c.py::test_prediction_error_unexpected_refusal_creates_belief | belief_changes 按 key 取唯一 trust:owner 行断言 0.3→0.22 | 改为取带 prediction_id 的预测错误行断言（数值不变），并新增断言解释路径的 trust 行（0.22→0.17）也被记录 | Phase 3-2 后同一场景合法地产生两行 trust 记录；原断言的字典折叠假设失效 |
| test_phase_b.py::test_scheduler_rule_run_deterministic_across_hash_seeds | total_events == 87 | == 85 | 新规范轨迹（本报告 §2）；跨 hash 种子确定性断言本身未动且仍成立 |
| test_phase_d.py::test_seed_group_complete_and_reproducible | group42 total_events == 87 | == 85 | 同上 |
| test_plan_chain_fix.py::test_trajectory_matches_current_canonical | world_log == rule_planlayer_seed42 | == rule_v02a_seed42（docstring 已注明历次重钉原因） | 规范轨迹按既定先例随有意行为变更重钉 |

除以上五处外，既有测试断言无任何改动；全套测试见最终汇总
（基线 97 → 现 134，新增 37 个 V0.2-A 测试）。

## 9. Phase 4 回归确认

Prompt 分段、JSON 协议、修复重试与回退均在位未重写；本轮补的是
审查指出的两件：(1) LLM 契约文本补上 relationships/trust 的主观性
说明与 selected_goal_id 约定（两后端文本一致）；(2) Prompt 级信息
隔离测试（test_v02a_phase4.py）——已知/未知/仅传闻三态下断言各自
prompt 文本与序列化上下文的含/不含，并与角色终局状态、证据条目、
听闻记录交叉验证（不只查文本）。
