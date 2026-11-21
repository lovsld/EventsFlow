# V0.3 Phase 3 阶段报告（Multi-Scene Runtime）

> 任务书 §10/§19 Phase 3。新增 `v03/scenes/`（models / scene_manager /
> runtime）；V0.2 零改动，规范轨迹 md5 不变。实时注入的最后一环需要
> 一处 V0.2 世界层小补丁，本报告 §4 给出提案，**未擅自实施**。

## 1. 交付物

| 类别 | 内容 |
|---|---|
| 场景模型 | `Scene`（§10.1 字段：地点、起止 tick、参与者及加入/离开时刻、关联线索/冲突、事件队列、状态 active/closed/interrupted、结局摘要） |
| 场景管理器 | `SceneManager`：以已提交 tick 的 positions 为唯一依据登记场景——地点有人且有事件发生则开启；地点清空则关闭；会话未正常结束而人散尽记 interrupted；参与者的加入/离开逐 tick 记录 |
| 传播账本 | `PropagationTracker`：事实的跨场景移动只认一条合法路径——某人在场景 A 经投递/搜得/初始知识学到事实，随后移动，在场景 B 亲口说出；记录携带者、来源/目标场景、学习与说出时刻、旅行 tick 数 |
| 一致性校验 | `ConsistencyValidator`：独立复核事件流——人物行为地点与其世界位置不符（分身）、同一事件重复提交，逐条标记 |
| 运行时 | `SceneRuntime`：逐 tick 串起 Story Engine 记账 → 场景登记 → 传播追踪 → 一致性校验 → Phase 2 提议管线，产出 pending world events（注入契约，见 §4） |
| 产物 | `v03_runs/rule_v02c4_seed42/scenes.json`（场景 + 传播 + 待注入事件全记录） |
| 测试 | `tests_v03/test_phase3.py` 新增 6 项；v03 合计 **20/20**；全量回归 297 过 + 停放红 P-C4-1（冻结既有） |

## 2. 酒馆规范轨迹上的多场景实况

登记到 4 个场景：S01 bar（t1–20，62 个事件，三人全程）、
S02 back_room（t1–3，士兵搜信）、S03/S04 tables（t1–2、t4）。
t1–3 期间 bar 与 back_room **两个场景同 tick 并行**——多地点
并行不是合成演示，是规范轨迹本身的事实。一致性校验全程
**0 问题**：无人分身、无事件重复提交。

跨场景传播共 10 条，主线一条清清楚楚：士兵 t3 在 back_room
场景（S02）搜得匿名信、学到「镇长贪腐」，t6 起在 bar 场景
（S01）反复说出——携带者、旅行时长（3–10 tick）逐条在案。
这正是 §10.3 要求的「跨场景影响必须有因果传递路径」。

传播校验还抓出过两条「无来源断言」，复核后确认是校验口径
问题而非世界问题：商人的 business_opportunity 是他自创的
提议、speaker_knows_secret 是唬人话术——场景定义里这两类
claim 本就 `basis_required: false`（无需依据）。校验器已按
此区分：无依据断言只针对「需要依据的事实型命题」报警，
提议/唬人属起源行为不报警。修订后酒馆回放传播问题 **0 条**。

## 3. Phase 2 候选在本阶段的落点

`SceneRuntime` 每 tick 末运行提议管线，验证通过的候选以
`to_world_event()` 转成普通 ambient 事件 dict 列入 pending
（酒馆回放 3 条，与 Phase 2 影子结果一致）。它们目前只是
**待注入队列**：真正进入一场运行中的模拟，还差世界一侧的
接收口——即 §4 的补丁。

## 4. 待 Hu 批准：v02/world.py 注入补丁提案（未实施）

现状勘查结论：V0.2 的 ambient 事件在 `WorldEngine.step()`
内部由 rng 生成（35% 概率抽场景 ambient 表），外部没有
投递口；`Scheduler` 的 `on_tick_committed` 钩子在提交后才
触发，适合记账、不适合注入。因此实时注入的最小改动是：

1. `WorldEngine.__init__` 增加 `self._external_queue: list[dict] = []`
   与公开方法 `queue_external_event(event: dict)`；
2. `step()` 在 resolve 之后、返回之前：若队列非空，逐条按
   世界自己的 event_seq 编号盖上 event_id，作为普通事件
   追加进本 tick 的提交列表（并照常进入 deliveries 计算——
   ambient 的可见性规则现成）；
3. runner 侧由 v03 驱动器在 `on_tick_committed` 里把
   SceneRuntime 的 pending 事件塞进队列，下一 tick 生效。

**关闭态等价性**：队列为空时 step() 的代码路径与 rng 消耗
与现状逐字相同——Story Engine 关闭 ⇒ 规范轨迹 md5 必须仍为
`cbf84d26…`，补丁落地时以此为回归门。不动决策代码、不动
resolve 规则、不动 broker。

批准后我做「Phase 3b」：实施补丁 + 回归门验证 + 第一次真
注入 A/B（rule 模式，Story Engine 开/关各跑一场酒馆，对比
事件流与人物反应——人物接不接受代理人带来的压力，仍由
人物自己决定）。
