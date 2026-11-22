# NOVEL_PHASE4_REPORT — 伏笔与回收管理

> 2026-11-22。产物：三场 upgrade/setup_payoff.json +
> upgrade/foreshadow_edges.json。回归 321 过 + 挂起一红。

## 核心纪律：三级证据阶梯（发现 ≠ 证实 ≠ 解决）

每条记录在 diagnostics 里分开保存 discovery（搜得/亲历
学到）、corroboration（第二个独立来源确认）、resolution
（问题被直接证据回答）三组事件。status 升格规则：
有 resolution 才 payoff；有 corroboration 或反复提及
（≥3 次）为 reinforcement；bluff 类天然为 misdirection；
其余为 unresolved。**发现物件只是第一级，永不直接等于
回收**——测试专门断言了这一点（见下）。

## 三场实测

**酒馆（7 条）**：信件存在问题 → payoff（E0008 搜得即
直接证据，正确）；**侵吞问题 → reinforcement 不回收**
（信被发现、被读、被转述，但侵吞从未被直接证据回答——
三级阶梯在这里拦住了最容易犯的错误：把「发现信」记成
「悬念解决」）；写信人身份、镇长是否知情 → unresolved；
商业交易 → reinforcement；信件物件 → payoff（其自身
事实就是「存在」，搜得即解决，与侵吞问题严格分开）；
bluff_hint → misdirection。

**灯塔（6 条）**：船期、雾号 → reinforcement；灯、皮带
→ unresolved；两个物件（信号灯笼、备用皮带）→ unresolved。
口径说明：灯塔的任务是动作型闭环，v03 问题未被「信息型
直接证据」回答，故本登记按信息口径给出 unresolved——
与 Phase 2/3 的双口径分歧同源，line_states 已随产物
保存（灯塔主线在 narrative 口径为 closed），Phase 5
规划时两口径并用，不在此处强行统一。

**诊所（6 条）**：病人将至、补药问题 → reinforcement；
药量问题 → unresolved；两个物件 → unresolved。断裂的
主线没有任何一条被冒记为回收，与 N2 的断裂判定一致。

## FORESHADOWS / PAYOFF_OF 边：本阶段产出为 0 条（如实）

规则：仅 payoff 记录且 setup 与 payoff 为不同事件时才发
边。酒馆两个 payoff 都不满足——信件物件的 setup 与 payoff
同为 E0008（自环边无意义，已抑制并在 diagnostics 注明）；
信件存在问题在事件流中**从未被任何人开口问过**（它只
存在于目标与配置里），没有可作 setup 的事件。结论：这批
run 的伏笔结构是「物件型」的，不是「提问-回答型」的，
边为零是数据事实，不是实现缺失；边的生成与自环抑制均有
测试覆盖（合成断言在 test_setup_payoff 中以 tavern 实测
形态验证）。

## 实现过程中的两处修正（已入测试）

1. 初版把信件物件的 fact 误读为侵吞事实——实际 fact_belief
   是「信件存在」，物件 payoff 因此合法；真正的防线在
   侵吞问题记录上（discovery 非空、resolution 为空）。
2. setup 事件回退链：问题事实从未被断言时，回退到首个
   携带该事实的 claim 话语；再无则留空，不硬凑。
