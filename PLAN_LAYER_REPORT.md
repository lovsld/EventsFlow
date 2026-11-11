# ACP Story Simulator — 计划层升级报告

日期：2026-11-11。第一步收官审计（STEP1_AUDIT_REPORT.md）发现 2 的量化版：
rule 模式下计划步从不推进——300/300 个 plan_update 为 continue、step_index
176/176 停在 0，计划靠解释整段替换，实际是"意图标签"而非"执行结构"；
且老板的 find_letter_source 意图没有任何行为能执行（M1a 0.240）。
本次升级按 Hu 批准的设计把计划变成执行结构，全程不调用任何真实
LLM/Gemini。

## 一、设计与实现

核心原则：**一步是否完成，是关于世界的事实（兑现该步的行动已经执行
并拿到结果），不是 provider 的意见。** 判定链：

```
decide() 时：暂存 _pending_step_check（计划 id / 步序号 / 步名 /
            决策所见步视图 / 行动 / 双方位置）
   ↓ 下一 tick 的结果到达（receive → _evaluate_pending）
指针守卫：仍是同一计划、仍 active、指针仍停在暂存步
   ↓ （provider 已显式 advance 或解释已换计划 → 本结果无可推进）
step_fulfilled(step, action, result.ok, ...) —— plan_steps.py 谓词表
   ↓ 兑现
PlanManager.advance_from_result() —— 走既有 _advance 的签名去重 /
承诺加分 / 完成语义，reason 记 result_driven:<step>，
迁移以被评估决策的 decision_key 落 plan_transitions 审计行
```

| 文件 | 内容 |
|---|---|
| `v02/plan_steps.py`（新） | 审计的计划步→行动谓词表原样搬入运行时（`step_consistent`、`_toward`、FRONT、PLACEHOLDER_STEPS 语义未动），另加 `action_signature()`、`step_fulfilled()`（执行口径：结果须 ok；return_to_front 在原地视为已满足）、`owner_location_from_perception()`。审计与运行时从此共用同一定义 |
| `v02/audit.py`（改） | 改从 plan_steps 导入谓词。已验证：对旧种子组重跑审计的输出与已发布的 `runs_v02/step1_audit/` 逐字相同——只搬家不改义 |
| `v02/plan_manager.py`（改） | `_advance` 新增可选 reason；新增 `advance_from_result()` 走既有去重/完成语义 |
| `v02/agent.py`（改） | decide() 暂存步检查；`_evaluate_pending` 末尾 `_advance_step_from_result()` 做守卫 + 判定 + 推进 + 落审计；ProviderError 路径清除暂存 |
| `v02/providers/rule_provider.py`（改） | 两处：(1) 老板查来源候选块——当前步为 find_letter_source/find_who_talked 时，对同场者生成 ask（后者另加 confront），claim=letter_question、台词 TOPIC_LINES["probe_source"]，全部既有动词，未新增世界动词；(2) **当前步对齐加分**——最终 sort 之前，对被共享谓词判为与当前步一致的候选加固定 `STEP_ALIGNMENT_BONUS = 0.35`（纯函数、无集合枚举，确定性不受影响；`step_consistent`/`owner_location_from_perception` 两个导入至此都有使用方） |

provider 的 plan_update 语义保留：abandon/replace 的 reason 门控不变，
显式 advance 仍有效。第 234 行恒 continue 按设计未动——步进不再依赖
provider 开口，rule 路径的 advance 全部来自结果驱动（见第四节统计）。
LLM 路径未改接口：结果驱动步进对所有 provider 生效，显式 advance 与
之由指针守卫 + 签名去重防双计（有专门测试）。

## 二、交接项 1 的定位结论：反事实测试失败

`test_counterfactual_pair_seed42` 在交接时失败于断言
`verification["control_targets_all_heard_by_recipient"] is False`。
定位分两个阶段，结论不同，必须分开说：

**阶段一（加对齐加分之前，纯属验证口径问题）。** 失败与反事实机制
无关：新老板 probe 行为使目标命题类 `answer_mayor` 在对照组中多出现
了 2 次——都是商人回答**老板**盘问的谈话（tick 16/18），士兵不在场，
只收到 muffled 偷听（无内容）。旧验证定义要求"该命题类的**每次**
出现士兵都 full 听到"，那是旧轨迹的偶然性质（该命题类当时只在与
士兵的谈话中出现）。同一代码下新旧对照对的**全部实质数字逐项
相同**：事件 85/83、mayor_embezzled 形成 tick 6/14 且终值 0.6/0.6、
investigation_widening 与 owner_hiding_something 实验组缺失、
怀疑峰值 1.00/0.80、搜后屋与指控时点 +3 tick、分歧 tick 9、
实验组全部抑制。是验证定义编码了旧轨迹，不是机制坏了。

**阶段二（加对齐加分之后，轨迹实质变化、定性结论保持）。** 加分使
士兵的计划执行明显变快，对照对的量化面貌随之改变（见第五节）：
分歧 tick 9→6、行动时序差 +3→+7 tick、怀疑差 0.20→0.15、事件差
−2→−6。但定性结论逐条保持：实验组仍永久缺失两个推断信念、
mayor_embezzled 终值仍靠信件兜底回到 0.6 但形成晚得多（tick 16 vs
6）、怀疑仍达不到 1.00、抑制仍恰好只差那一类投递（审计 3 条、
对照组 0 条）。时序效应变强有可解释的机制：听到发言→信念形成→
计划步进与对齐加分共同加速调查，这正是计划层的中介作用。

据此修正验证定义（`v02/experiments.py`）：断言改为**首次出现**
（分歧前同一事件）士兵必须 full 听到——这是"听到 vs 没听到"对照
成立的真正前提；其余出现的投递改为描述性分布
`control_target_deliveries_to_recipient`（本次 {full: 2, muffled: 0,
absent: 1}），字段相应改名为 `control_first_target_heard_by_recipient`。
旧产物目录 `runs_v02/exp_counterfactual/` 只读保留旧字段与旧数字。

## 三、新规范轨迹

- 旧规范轨迹 `runs_v02/rule_fixed_seed42`（85 事件）转为**升级前
  基线**，只读保留。
- 新规范轨迹 **`runs_v02/rule_planlayer_seed42`**：87 事件，
  world_log md5 `645c155e434207689d419cd69b278255`，在
  PYTHONHASHSEED 0/1/42 下逐字一致（实测三份 md5 相同）；种子组内
  的 seed 42 run 与之逐字相同。
- 行为变化来源：结果驱动步进改变了承诺与步指针 → 决策上下文
  改变 → 行动选择改变；对齐加分再直接倾斜选择。轨迹变了，宏观
  结局没变（下节）。

## 四、种子组复跑（`runs_v02/exp_seedgroup_planlayer/`）

命令：`python experiments.py seed-group --seeds 42,7,101,2024,555
--ticks 20 --mode rule --out runs_v02/exp_seedgroup_planlayer`

| 种子 | 事件 | 士兵读信 | 商人听闻信 | 老板知信外泄 | 士兵信镇长贪腐 | 证伪 | 信念变化 | 晋升 |
|---|---|---|---|---|---|---|---|---|
| 42 | 87 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 22 | 7 | 10 |
| 7 | 87 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 22 | 5 | 5 |
| 101 | 85 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 19 | 4 | 6 |
| 2024 | 84 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 11 | 3 | 5 |
| 555 | 85 | 1.00/亲历 | 0.70/听闻 | 0.90/推断 | 0.60/听闻 | 22 | 6 | 6 |

- **关键结果一致性 5/5，四个关键信念终值逐项与升级前相同**
  （1.00 / 0.70 / 0.90 / 0.60）。宏观不变量守住。
- 事件数 84–87（均值 85.6；升级前 80–87，均值 83.2）；轨迹多样性
  0.7357（升级前 0.7391，基本不变）；证伪 11–22（前 12–23）、
  信念变化 3–7（前 2–8）、晋升 5–10（前 0–11）。

## 五、反事实复跑（`runs_v02/exp_counterfactual_planlayer/`）

| 指标 | 升级前（已发布） | 升级后 |
|---|---|---|
| 对照/实验事件数 | 85 / 83（−2） | 87 / 81（−6） |
| 客观前缀一致到 tick | 8（分歧 9） | 5（分歧 6） |
| mayor_embezzled 形成 | 对照 6 / 实验 14 | 对照 6 / 实验 16 |
| mayor_embezzled 终值 | 0.6 / 0.6 | 0.6 / 0.6 |
| investigation_widening 终值 | 0.6 / 无 | 0.6 / 无 |
| owner_hiding_something 终值 | 0.65 / 无 | 0.65 / 无 |
| 怀疑峰值 | 1.00 / 0.80 | 1.00 / 0.85 |
| 首次搜后屋/找到信 | 10 / 13 | 8 / 15 |
| 首次指控 | 13 / 16 | 11 / 18 |
| 抑制审计 | 实验 2 条 / 对照 0 | 实验 3 条 / 对照 0 |

定性结论保持：一条投递之差的效应仍在时序与推断层而非最终命题
真值；量化上时序差由 +3 扩到 +7 tick（机制解释见第二节阶段二），
怀疑差由 0.20 收窄到 0.15（实验组士兵从其他感知源获得的怀疑略
多）。这些变化如实记录，不调参回凑旧数字。

## 六、审计前后对比（`runs_v02/step1_audit_planlayer/`）

同一把尺子（audit.py，谓词同源）对新种子组重算：

| 指标 | 升级前 | 升级后 | 说明 |
|---|---|---|---|
| M1a 当前步对齐 | 0.600 (96/160) | **0.754 (95/126)** | 分母变小：计划会走完并清除，无计划期决策不计入 |
| M1b 任一步对齐 | 0.7375 | **0.8175** | M1a 与 M1b 差距由 0.138 收窄到 0.064——指针滞后基本消除 |
| M2 知识来源合法 | 1.0 (24/24) | 1.0 (38/38) | 违例与反向违例仍为 0 |
| M3 被拒后适应 | 5 案例 / 3 重复 | **0 案例** | 新轨迹中没有出现"未预料拒绝"样本，本轮无证据（既非改善也非恶化） |
| M4 拒单率 | 4.0% (12/300) | 6.33% (19/300) | 上升：对齐后的定向发言更多，目标已离开的时序拒单随之变多；**已提交违规仍为 0/428 事件** |
| M5 变更有据率 | 不可测（语料 0 行） | **1.0**（abandon/replace 44 行全带 reason） | 含链修复与本次落审计的共同结果 |
| M6 预测依据真实 | 1.0 (300/300) | 1.0 (300/300) | 不变 |

**步进统计（新种子组 5 个 run 的 plan_transitions 实查）**：
advance 共 **83 行，全部为 result_driven**（rule provider 恒
continue，显式 advance 为 0——步进来源构成与设计一致）；
advance_dedup 3 行；**计划完成 32 次**（advance 后 status=
completed）。step_index 分布：升级前 176/176 停在 0；升级后
可判定决策中 index 0/1/2 = 78/35/13——指针真实移动。

**逐步 M1a（亮点与残留）**：

| 步 | 升级前 | 升级后 |
|---|---|---|
| find_letter_source | 0.240 (6/25) | **0.958 (23/24)** |
| assess_damage | 0.294 (5/17) | **1.000 (6/6)** |
| press_for_evidence | 0.925 | 1.000 (10/10) |
| deny_and_observe | 0.683 | 1.000 (12/12) |
| probe_more_mayor_news | 0.500 | 1.000 (9/9) |
| assess_letter_rumor_value | 0.450 | 0.750 (9/12) |
| confront_owner | —（无样本） | 0.500 (5/10) |
| **observe_response** | —（无样本） | **0.000 (0/13)** |
| **protect_wealth** | —（无样本） | **0.000 (0/9)** |

残留缺口如实说明：observe_response 要求被动观察（observe/listen），
但士兵在该步的实际行动被更强的 confront/ask 候选支配（摊牌候选
0.95 基分高于 observe+加分）；protect_wealth 要求 work，但商人在该
步仍优先做生意发言。两者现在都是**选择侧效用支配**问题——步进
结构已通，指针会真实停在这两步上等待，行动却不兑现，于是这两步
只能靠计划被整体替换才离开。这是下一步若要继续调的方向（步专属
候选或更高的步对齐权重），本次不顺手调参掩盖。

## 七、被更新的既有测试清单（逐处附理由注释）

| 测试 | 更新 | 理由 |
|---|---|---|
| `tests_v02/test_phase_b.py` 跨哈希确定性测试 | 事件数断言 85→87，注释补记计划层 | 轨迹被有意改变；确定性与涌现链断言（读信 1.0/听闻 0.7）不变且仍通过 |
| `tests_v02/test_phase_d.py` 种子组测试 | seed 42 事件数 85→87 + 注释 | 同上 |
| `tests_v02/test_phase_d.py` 反事实测试 | "每次出现都听到"断言改为首次出现断言 + 投递分布 full ≥ 1，注释写明口径修正始末 | 见第二节：旧断言编码了旧轨迹的偶然性质；实质断言（信念快照、investigation_widening 有无）保留不动 |
| `tests_v02/test_plan_chain_fix.py` 轨迹等值测试 | 改名 `test_trajectory_matches_current_canonical`，指向 rule_planlayer_seed42，docstring 说明改指原因 | 该测试原为证明链修复不改行为（当时已以 md5 验证完毕）；计划层有意改行为后，钉子改指新规范轨迹，守的是确定性 |
| `tests_v02/test_audit.py` | **未改动** | 其新鲜 run 不变量（M5 rate == 1.0 等，链修复时已更新）在新代码下仍成立 |

新增 `tests_v02/test_plan_layer.py` 8 个：兑现→advance 落库且指针
前进（reason/decision_key 逐项断言）、未兑现与失败结果不推进、
同签名不双计（advance_dedup）、末步兑现→计划 completed、
provider 显式 advance 与结果驱动不双计、老板 probe 行为的谓词级
命中、老板在 find_letter_source 步下经真实 decide() 的行动一致性、
真实 run 集成（result_driven advance > 0、完成 > 0、老板 probe
事件存在）。

**全套测试：tests_v02 55/55、tests/（V0.1）10/10，合计 65/65。**

## 八、遗留问题

1. observe_response / protect_wealth 两步被效用支配（见第六节），
   M1a 的剩余差距主要在此；属选择侧调参问题，留待 Hu 决定是否做。
2. M3（被拒后适应）本轮种子组无样本，无法复验；旧结论（预期翻转
   3/3）仍只由旧轨迹支持。
3. LLM 路径未实测：结果驱动步进对 LLM provider 同样生效且有单元
   级防双计测试，但真实 LLM 轨迹上的步进构成不在本次验收内
   （20 tick 补跑维持搁置，不耗 Key 的约束不变）。
4. 计划覆盖率约 0.42–0.53：计划完成/被替换后，Agent 有相当比例
   的 tick 无计划行动——这是"计划会走完"的直接结果，是否为期望
   行为留待第二步（改编层）视角再评。
5. 反事实验证字段已改名（`control_first_target_heard_by_recipient`
   + 投递分布），旧产物只读保留；后续若有工具消费 comparison.json
   需按新字段读。

产物索引：新规范轨迹 `runs_v02/rule_planlayer_seed42/`、种子组
`runs_v02/exp_seedgroup_planlayer/`、反事实
`runs_v02/exp_counterfactual_planlayer/`、审计
`runs_v02/step1_audit_planlayer/`。
