# ACP Story Simulator V0.2 — Phase D 完成报告（实验与分析）

日期：2026-11-11。Phase D 是 V0.2 的最后一个阶段：在 Phase B 的事件驱动
调度 + Broker 审计、Phase C 的预测/信念/计划/记忆四张审计表之上，补齐
**可复现的实验仪器**——种子组运行器、多样性/一致性指标、只差一条投递的
反事实运行器，以及统一的实验 CLI。全部实验只用 rule/mock 确定性轨迹，
不调用任何真实 LLM：反事实对照要求两组输入除目标投递外严格一致，
只有确定性 provider 能保证这一点。

先行说明（影响下文的场景口径）：设计文档的验收例子是商人说出
"镇长付款拖了两个月"（`mayor_late_payment`）。核查 seed 42 规范轨迹
（`runs_v02/rule_fixed_seed42/world_log.json`）后确认：这条字面台词在
rule 轨迹中**从不出现**；实际承担同一语义功能的发言是商人回答士兵盘问
时的 `answer_mayor`（"镇长的钱袋子紧不紧……只能说，不太好看。"），
在认知层与 `mayor_late_payment` 走完全相同的解释分支（士兵听后形成
`mayor_embezzled`/heard 并推断 `investigation_widening`）。因此抑制规则
按**命题类**定义：`recipient=soldier, actor=merchant,
claim ∈ {mayor_late_payment, answer_mayor}`——规则同时覆盖设计文档的
字面标签与轨迹中实际出现的等价发言，报告对此保持透明。

## 一、实现

| 文件 | 内容 |
|---|---|
| `v02/broker.py`（改） | Broker 新增 `suppression_rules`：`match_suppression(rule, agent, event)` 按 recipient（必填）+ actor/claim/topic/type（claim/topic 可为列表=命题类）匹配；`publish()` 在去重**之前**对每个 Agent 的感知副本做抑制，被抑制的投递记入 `counterfactual_suppressions` 并由调度器落库。抑制不修改事件对象本身、不标记已投递、与其他接收者无关 |
| `v02/persistence.py`（改） | 新增 `counterfactual_suppressions` 表（run/tick/agent/event/kind/rule JSON），与 `dedup_drops` 分开审计；`Store.record_counterfactual_suppressions()`；`counts()` 含新表 |
| `v02/scheduler.py`（改） | 每轮 publish 后刷新抑制审计（与 dedup 同模式）；新增 `on_tick_end` 实验钩子：每次世界提交后（及最终感知轮后）产出全员主观状态快照（位置/信念含 kind/信任/情绪/计划） |
| `v02/runner.py`（改） | `run_simulation(..., suppression_rules=None, trace_beliefs=False)`：透传规则给 Broker；`trace_beliefs=True` 时落盘 `beliefs_trace.json`；`summary.json` 记录 `suppression_rules`。默认参数下行为与 Phase C 完全一致 |
| `v02/experiments.py`（新） | 指标函数（签名集合/Jaccard/一致性）、`collect_run_metrics()`（从 summary + SQLite 汇总单 run 指标：证伪数按 `error_json.mismatches` 非空计、晋升数按 consolidation 记录长度计）、`run_seed_group()`、`run_counterfactual()`（含验证逻辑与 comparison.json/.md 生成） |
| `experiments.py`（新，项目根） | CLI：`seed-group` 与 `counterfactual` 两个子命令，参数含种子组、tick、模式、抑制规则（recipient/actor/claim/topic/type）与输出目录 |
| `tests_v02/test_phase_d.py`（新） | 9 个测试（见第四节） |

既有目录零改动：V0.1 的 `src/ tests/ runs/ characters/` 未动；既有
`runs_v02/*` 未删未改；实验产物全部落在新目录
`runs_v02/exp_seedgroup_rule/` 与 `runs_v02/exp_counterfactual/`。

## 二、指标定义（先定义，后数字）

- **轨迹签名**：一个 run 的 world_log 全部事件构成的集合
  `{(tick, 事件类型, 主体 actor)}`。
- **轨迹多样性**：种子组内所有 run 两两之间的 **Jaccard 距离**
  `1 − |A∩B| / |A∪B|` 的均值（同时报告每一对的距离）。0 = 各轨迹作为
  事件集合完全相同；1 = 完全不相交。该指标对"同一 tick 同一主体做了
  同类事"敏感，对事件的具体文本不敏感。
- **关键结果一致性**：预注册的涌现链关键节点在种子组中成立的 run
  占比。本报告的主节点 = "士兵终局持有 `anonymous_letter_exists`
  且 kind=experienced 且置信度 ≥ 1.0"（即亲自读到信）。
- **信念终值分布**：逐种子列出原值（缺失记为"无"），不做跨种子平均
  ——平均会掩盖"某种子里该信念根本没形成"这类结构性差异。
- **认知层活动量**（Phase C 口径）：预测证伪数 = predictions 表中
  error 的 mismatches 非空的行数；信念变化数 = belief_changes 行数；
  长期记忆晋升数 = memory_consolidations 各行 consolidated 列表长度之和。

## 三、实验一：种子组（rule，20 tick，种子 42/7/101/2024/555）

命令：`python experiments.py seed-group --seeds 42,7,101,2024,555
--ticks 20 --mode rule --out runs_v02/exp_seedgroup_rule`
（产物：5 个 run 子目录 + `seed_group_summary.json` /
`seed_group_table.csv` / `seed_group_table.md`）

| 种子 | 事件 | 士兵读信 | 商人听闻信 | 老板知信外泄 | 士兵信镇长贪腐 | 预测证伪/60 | 信念变化 | 长期记忆晋升 | 士兵终局位置 |
|---|---|---|---|---|---|---|---|---|---|
| 42 | 85 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 13 | 8 | 11 | bar |
| 7 | 87 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 23 | 8 | 10 | bar |
| 101 | 83 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 12 | 5 | 3 | tables |
| 2024 | 80 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 14 | 2 | 0 | bar |
| 555 | 81 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 23 | 5 | 6 | bar |

- **关键结果一致性 = 5/5（1.0）**：五个种子全部走完涌现链——士兵潜入
  后屋读信、商人听闻、老板意识到信已外泄；四个关键信念终值在五组中
  **逐项相同**（1.00 / 0.70 / 0.90 / 0.60）。宏观结局对种子扰动稳健。
- **轨迹多样性 = 0.7391**（两两距离 0.5652–0.8085，10 对全表在
  `seed_group_summary.json`）：微观轨迹差异很大——同一结局由不同的
  移动/寒暄/盘问时序实现。总事件数 80–87（均值 83.2）。
- **认知层活动量随种子显著变化**：证伪 12–23、信念变化 2–8、晋升
  0–11。种子 2024 只有 2 次信念变化、0 次晋升——结局相同不等于认知
  过程相同；Phase C 的审计表使这种差异可量化，正是本实验想暴露的。

## 四、实验二：反事实——士兵是否听到商人的镇长付款发言（seed 42）

命令：`python experiments.py counterfactual --seed 42 --ticks 20
--recipient soldier --actor merchant
--claim mayor_late_payment,answer_mayor
--out runs_v02/exp_counterfactual`
（产物：`control/`、`treatment/` 两个 run + `comparison.json` /
`comparison.md`）

### 4.1 两组只差一条投递——验证证据

| 验证项 | 结果 |
|---|---|
| 目标发言在对照组出现 | E0019（tick 5）、E0055（tick 14），claim 均为 `answer_mayor` |
| 对照组投递 | 两次均为 owner/merchant/soldier 三人 full |
| 实验组中该发言 | 仍然客观发生：E0019（tick 5，同事件）与 E0070（tick 17，下游轨迹变化后的第二次出现） |
| 实验组投递 | 两次均只有 owner/merchant full，**士兵不在投递名单**；老板两次都照常完整收到 |
| 抑制审计 | 实验组 `counterfactual_suppressions` 恰 2 条（tick 5/tick 17，均 soldier/full），对照组 0 条；与 dedup_drops 分开记录 |
| 客观事件前缀 | 两组逐 tick 的客观事件签名（类型/主体/地点/claim/topic）**一致到 tick 8**，首个分歧在 tick 9——位于被抑制投递（tick 5 感知）的下游，符合"差异只能由该投递的下游效应产生" |

### 4.2 士兵信念轨迹（每 tick 提交后快照摘录）

`mayor_embezzled`（镇长贪腐）：

| tick | 6 | 10 | 11 | 13 | 14 | 20（终局） |
|---|---|---|---|---|---|---|
| 对照组 | 0.50/听闻 | 0.50 | **0.60** | 0.60 | 0.60 | 0.60/听闻 |
| 实验组 | 无 | 无 | 无 | 无 | **0.60** | 0.60/听闻 |

对照组士兵在 tick 6（听到发言后的第一个快照）即形成该信念；tick 11
读信后升到 0.60。实验组直到 tick 14 才形成——来源只剩信本身（搜后屋
在 tick 13 找到信，下一个快照生效）。**终值相同（0.60）纯属信件内容
兜底，形成时间晚了 8 个 tick，且来源结构完全不同。**

`investigation_widening`（"调查范围在扩大"，仅由听到该发言触发的推断）：

| | tick 6 起 | 终局 |
|---|---|---|
| 对照组 | 0.60/推断，全程保持 | **0.60/推断** |
| 实验组 | 从未形成 | **无** |

连带差异：`owner_hiding_something` 对照组终局 0.65/推断，实验组**无**
（未形成）。`anonymous_letter_exists` 两组均为 1.00/亲历（调查主线
未被切断）。怀疑度（suspicion）：对照组 tick 6 跳到 0.80、tick 7 达
1.00 并保持；实验组全程缓慢爬升（0.40→0.55），tick 14 读信后才到
0.80，**终局 0.80，从未达到 1.00**。

### 4.3 士兵行动对照

| 指标 | 对照组 | 实验组 |
|---|---|---|
| 盘问 ask / 指控 confront / 搜查 search | 3 / 3 / 1 | 3 / 3 / 1 |
| 其中以镇长贪腐指控老板的 confront | 2 | 2（由信件内容驱动） |
| 首次盘问 tick | 4 | 4 |
| **首次指控 tick** | **13** | **16** |
| **首次搜后屋 / 找到信 tick** | **10** | **13** |
| 全程事件总数 | 85 | 83（−2） |

### 4.4 回答设计文档之问

**"C 是否听到 B 的镇长付款发言"造成的差别是：时序与推断层，而非
最终命题真值。** 听到时（对照组）：士兵在 tick 6 就把"镇长贪腐"纳入
信念并推断"调查在扩大"，怀疑度两 tick 内冲到 1.00，tick 10 搜后屋、
tick 13 开始指控。没听到时（实验组）：他的调查仍然完成（信的线索
独立存在），但整体**晚 3 个 tick**（搜后屋/读信 13、首次指控 16），
怀疑度终局只到 0.80，且**永久缺失两个推断信念**
（`investigation_widening` 0.60、`owner_hiding_something` 0.65）——
`mayor_embezzled` 的终值靠信件兜底回到 0.60，但他对局面的"解释框架"
明显更薄。一条投递之差，可测量的下游效应是：关键行动时序 +3 tick、
两个推断信念有无、怀疑度上限 −0.20、全程事件 −2。

## 五、测试

`tests_v02/test_phase_d.py` 新增 9 个，全部通过：

- 指标小样本手工核对：Jaccard 已知值（2/3、0、1、空集）、两两均值
  （2.5/3)、一致性（3/4=0.75)、签名集合形状；
- Broker 抑制单元测试：规则匹配（含命题类与字面 `mayor_late_payment`
  标签）、full 形式只挡目标接收者（老板/商人照常收到、审计 1 条、
  与 dedup 区分、事件对象深拷贝不变）、muffled 形式（邻桌偷听也被
  抑制并审计）、无规则时行为不变；
- 种子组：种子 [42, 7] 产出完整汇总与三份表文件；seed 42 在组内
  run 与独立重跑的 world_log **逐字节相同**，且等于规范轨迹
  （85 事件、士兵读信成立）；
- 反事实整对（seed 42）：目标发言在对照组被士兵完整听到、在实验组
  每次出现都被抑制且审计完备、第一 occurrence 的老板投递两组一致、
  客观前缀一致到 ≥ 首个目标 tick、首个投递后快照对照组有
  `mayor_embezzled` ≥0.5 而实验组无、终局 `investigation_widening`
  仅对照组存在。

全套回归：**tests_v02 40/40（31 旧 + 9 新）、tests/（V0.1) 10/10，
合计 50/50**；另在 `PYTHONHASHSEED=7` 下 tests_v02 40/40（确定性
不回退）。

## 六、遗留问题与说明

1. **场景口径**：如报告开头所述，设计文档的字面台词
   `mayor_late_payment` 在 rule 轨迹中不出现，本实验以命题类规则
   覆盖其实际等价发言 `answer_mayor`（同一解释分支）。若未来希望
   字面台词出现，需要改 rule provider 的台词触发条件——那属于
   场景内容变更，不在 Phase D 范围。
2. **反事实的"第二次出现"**：实验组中该发言在 tick 17 再次出现
   （E0070）并同样被抑制——这是规则语义（一类投递）的自然结果，
   报告与 comparison 产物中均按"每次出现"口径如实记录；若要做
   "只抑制第一次"的变体，规则需扩展 occurrence 序号字段。
3. **指标粒度**：轨迹多样性基于 (tick，类型，主体） 集合，对文本
   与对象差异不敏感；如需更细的轨迹比较可引入编辑距离类指标，
   本阶段未做。
4. **llm 模式实验**：Phase D 的 seed-group/counterfactual 均支持
   `--mode` 参数结构，但 llm 轨迹非确定性，反事实"只差一条投递"
   的严格性不成立，故本阶段只验收 rule/mock；llm 实验若要做，
   应改用统计口径（多次重复 + 分布比较），留待后续。
5. 至此 V0.2 四个阶段（A 最小 LLM 实验、B 事件驱动与交互、
   C 认知与记忆、D 实验与分析）全部完成。
