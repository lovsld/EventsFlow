# Phase B 完成报告（2026-11-10）

范围：任务书 Phase B（事件与交互层）。结论先行：**Phase B 全部交付并通过
验收；新栈在相同条件下与 Phase A 输出逐字节一致。**唯一需要如实说明的
一点：官方回归运行 `rule_phaseb_seed42` 与 Phase A 基线的差异（85 vs 91
事件）源于一个 **Phase A 就存在、与本次改动无关**的 provider 层非确定性，
已用隔离实验定位并证明（见第 4 节）。

## 1. 架构变化

Phase A 的 `v02/world.py` 单文件（Engine/Resolver/Perception/Broker/
Interaction 五合一）和 runner 里的顺序 tick 循环，拆成边界清晰的组件：

| 模块 | 职责 | 与 Phase A 的关系 |
|---|---|---|
| `v02/interaction.py`（新） | **Interaction Manager**：会话生命周期一等建模——started/joined/left/listen/interrupt/ended 全部显式记录在 session 的 lifecycle 日志里，关闭的会话进 `history` 可审计；发言"地板"（floor）由它管理 | 从 world.py 拆出；Resolver 改为通过 `take_floor()` 向它询问打断判定、通过 `register_listener()` 登记 listen 姿态 |
| `v02/broker.py`（新） | **Event Broker**：唯一的"已提交事件 → Agent"通道。`publish()` 按房间/muffled 规则算感知并做**去重**；`dispatch()` 把感知信封放上每 Agent 的 asyncio 队列 | 感知规则逐字沿用 Phase A；`deliver()` 保留为兼容入口 |
| `v02/scheduler.py`（新） | **事件驱动调度 + 统一提交**：每 Agent 一个 asyncio 任务，阻塞在自己的队列上，**被投递信封唤醒**才解释/决策；意图只能经 `SubmissionGate.submit()` 进入，每 tick 一个提交窗口、全员到齐后调度器做全 tick **唯一一次** `world.step()` 提交 | tick 只保留为实验控制外壳（设计文档 4.6 的取舍）；Agent 不持有 world 引用，无旁路 |
| `v02/world.py`（改） | 只剩 WorldEngine + Action Resolver；Resolver 咨询 Interaction Manager 判定交互类行动（打断、listen 登记） | 验证规则、initiative、speak_failed、search/show 规则未动 |
| `v02/persistence.py`（改） | 新增 `dedup_drops` 表：每次被 Broker 抑制的重复投递落一行（run/tick/agent/event/kind/reason） | 其余三表不变 |
| `v02/runner.py`（改） | 组装 World/Broker/Scheduler/Agents/Store 并驱动；summary/narrative/world_log 产物格式不变 | `preflight`、叙事渲染未动 |

未动：`agent.py`、`memory.py`、`protocol.py`、`context_builder.py`、
`config.py`、全部 providers（含凭据/密钥池配置，未读未改）、`main.py`、
以及 V0.1 全部代码与数据（实测：`src/ tests/ characters/` 下 .py/.json/.md
源文件今日修改数 = 0；`runs/run_seed42、run_seed7` 完好）。

## 2. 验收逐项实测

| 验收项 | 验证方式 | 结果 |
|---|---|---|
| tests_v02 全过 | `.venv/bin/python -m pytest tests_v02/` | **21/21**（Phase A 15 + Phase B 新增 6） |
| tests/（V0.1）全过 | 同上 | **10/10**；整套 31 个测试连续跑 3 遍均 31/31 |
| 重复投递去重 | `test_broker_dedup_delivers_once_and_audits_drops`：同一事件批次 publish 两次 | 第二次 publish 对每个 Agent 的 delivered/muffled 均为空；3 条抑制记录（owner full / merchant full / soldier muffled）落入 `dedup_drops` 表可查；DB 中该事件对每 Agent 只有一条投递 |
| 打断 interrupt | `test_interrupt_second_speaker_in_session_is_flagged`：同 tick 同房间两人发言 | 恰好一人 `interrupted=False`、一人 `True`（initiative 规则未变）；会话 transcript 两条、一真一假；lifecycle 有 interrupt 记录 |
| 监听 listen | `test_listen_registers_listener_and_receives_utterance` | soldier 执行 listen 后出现在会话 `listeners` 名单且 lifecycle 有 listen 记录；同 tick 老板的会话发言以**全文**投递给 soldier |
| 离场 leave | `test_leave_updates_membership_and_stops_full_delivery` | soldier 移出后吧台会话成员变为 [merchant, owner]、新会话 id；关闭会话 lifecycle 有 `left(soldier)` + `ended`；此后吧台发言只以 muffled 形式到达 soldier，全文不再投递 |
| 统一提交 | `test_submission_gate_contract` + 代码结构 | 窗口外提交/未知 Agent/同 tick 重复提交全部抛错；全员提交后才解锁一次 commit；Agent 任务只持有 gate 与自己的队列 |
| CLI 兼容 | `main.py --mode rule/mock --ticks 20 --seed 42` | 两模式均完整跑满 20 tick 并正常落盘（见第 3 节） |

## 3. rule 回归对照（seed 42，20 tick）

**mock 对照**：`mock_phaseb_seed42` 与 Phase A 的 `mock_seed42` —— 70 事件，
`world_log.json` **完全一致**。

**rule 对照（锁定哈希种子时）**：在 `PYTHONHASHSEED ∈ {1, 3, 4}` 三个进程中
跑新栈全程，`world_log.json` 与 Phase A 基线 `rule_seed42` **逐项完全一致**
（91 事件、每 tick 的事件/投递/位置全等），summary 的 total_events、
final_positions、letter_seen_by_objective、final_beliefs、memory_entries、
db_counts（60/91/60）全部相等。回归测试
`test_scheduler_rule_run_matches_phase_a_baseline` 把这一条做成了套内断言
（子进程锁定 `PYTHONHASHSEED=1`）。

**rule 对照（官方运行，常态哈希种子）**：`runs_v02/rule_phaseb_seed42` ——
85 事件（基线 91），decisions 60、deliveries 60、dedup_drops 0。差异见第 4 节：
分歧点只有一个（tick 1 士兵的开局动作），且分歧后的涌现链关键节点全部
仍然成立——士兵最终亲读信（anonymous_letter_exists 1.0/experienced）、
商人传闻知情（0.7/heard）、老板推断信已外泄（letter_known_by_others 0.9）、
士兵认定老板藏事（owner_hiding_something 0.65），与基线定性一致；定量差异：
士兵终局位置 bar（基线 tables）、士兵多一条次要信念 speaker_knows_secret
0.51、记忆条数 29/24/20（基线 28/21/15）。

## 4. 差异根源分析（重要，已实证）

`rule_phaseb_seed42` 在 tick 1 与基线分道：基线士兵 door→tables，本次士兵
原地 observe。此后连锁反应造成 85 vs 91。定位过程与证据：

1. 两边 DB 中 tick 0 三名 Agent 的 `context_summary` **逐字相同**——感知与
   上下文没有差异，分歧发生在 provider 内部的候选打分。
2. Rule Provider（Phase A 移植代码，本次未动）第 103 行用
   `for dest in ADJACENCY.get(loc, set())` 迭代**字符串集合**枚举移动候选，
   每个候选的效用抖动 `rng.random() * 0.18` 按迭代顺序抽签分配。集合迭代
   顺序由进程级 `PYTHONHASHSEED` 决定 → 同一种子、同一上下文，抖动落到
   哪个目的地随进程而变，近分候选（如士兵开局 move 0.50 vs observe 约
   0.39+抖动）偶尔被翻转。
3. 隔离实验（无调度器、无 world，只构造士兵 tick-0 上下文直接调
   provider）：10 个哈希种子中，`{0,2,5,6,8} → observe`、`{1,3,4,7,9} →
   move tables`，五五开——非确定性完全位于 provider 内部，与 Phase B
   的调度/Broker/Interaction 改动无关，Phase A 的顺序循环跑同一 provider
   同样受它支配（基线本身只是该分布的一次抽样）。
4. 反证一致性：`PYTHONHASHSEED=0` 的整场运行与 `rule_phaseb_seed42` 的
   world_log **完全相等**——新栈是 (seed, 哈希种子) 的确定性函数，调度
   本身不引入任何额外随机性。

处理决定：**本次不改 provider**。理由：rule provider 是实验对照组，改其
候选枚举等于改变对照分布，应作为独立决定另行批准；且设计约束要求
Phase B 不改变行为语义。回归测试因此锁定哈希种子做精确对照，常态运行的
分布内波动在报告中如实记录（如上）。修复建议见第 5 节。

## 5. 遗留问题与建议

1. **Rule Provider 的哈希序非确定性（Phase A 遗留）**：建议在 Phase C 或
   独立小改中把 provider 的目的地枚举改为 `sorted(...)`（context_builder
   里已经是排序的），使 rule/mock 运行与进程哈希种子无关、严格可复现；
   改后需重跑基线对照并更新基线说明，因为历史基线是在未排序分布下抽样
   得到的。
2. 会话 lifecycle 日志（started/joined/left/listen/interrupt/ended）目前只
   存在于 Interaction Manager 的内存与测试断言中，未落库；若 Phase D 的
   实验分析需要按会话检索，建议届时新增 `interaction_sessions` 表
   （Phase C 的表清单里本就规划了它）。
3. `summary.json` 的 `db_counts` 新增 `dedup_drops` 键（干净运行恒为 0）；
   投递记录改为在 publish（消费）时产生，tick 与事件 id 内容与 Phase A
   一致（已由 world_log 全等验证），仅产生时机不同，属内部实现细节。
4. Scheduler 对非 ProviderError 的 Agent 异常做了加固：该 Agent 本 tick
   降级为 wait、异常在屏障后重新抛出使运行失败可见；Phase A 的顺序循环
   遇同类异常会直接中断。ProviderError 路径行为与 Phase A 一致。这是刻意
   的健壮性差异，记录在此。
5. listen 的建模口径：listener 是"持有监听姿态的会话参与者"，投递仍走
   房间规则（同房间全文/相邻 muffled）——这与设计文档 4.6 的第三方监听
   描述一致；"相邻房间以 listen 获得全文"之类的扩展语义本次未引入。
6. Phase C 范围（Prediction Engine / Prediction Error / Plan Manager 完整
   版 / 其余 DB 表）未在本阶段触碰，按原计划推进。
