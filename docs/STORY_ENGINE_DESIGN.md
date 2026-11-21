# STORY_ENGINE_DESIGN — Story Engine 设计（V0.3）

实现：`v03/story/`。设计原则：制造压力与机会，从不替人物
做决定（任务书 §2.2）。

## 组件

**Story Architect**（architect.py）：把故事配置（主题、核心
问题、初始线索/冲突、目标元数据、初始知识）实例化为
StoryState；人物目标视图从各人 goals.yaml 派生（权重→优先
级、条件树→成功/失败信念集），不另设一套目标。

**Thread Manager**（thread_manager.py）：线索状态机
active/dormant/escalating/blocked/resolved/abandoned。核心
诚实规则：问题被「回答」只认直接证据（亲历断言、搜得物品
的 fact_belief）；转述只记 addressed。停滞以 tick 计数器
驱动 dormant；依赖线索在被依赖者收束而问题未答时 blocked。

**Conflict Engine**（conflict_engine.py）：确定性检测四类
冲突——目标（两人目标的成功/失败信念集对同一命题反向）、
资源（配置声明的共享资源）、信息（敏感事实知情不对称）、
声明（配置 initial_conflicts）。severity 由对峙与敏感断言
事件累积、静默衰减；关联线索收束时冲突解决。

**Event Generator**（eventgen.py）：三条确定性模板规则——
G-A 压力（escalating 线索 + 敏感事实 ≥2 人断言 → 配置声明
的 pressure_event 入场）、G-B 机会（dormant 线索 + 已有知情
者 → opportunity_event）、G-C 后果（物品被搜走而原主未获
投递 → 原地痕迹事件）。候选只提议环境事实，永不提议
「某人知道了什么/将做什么」。

**双验证**：CausalValidator（causal_basis 必须全部存在于
已提交历史、触发条件对当前状态复核）；WorldRuleValidator
（地点/人物存在、指纹去重——同一候选永不重复提议）。

**Dramatic Evaluator**：七维分开报告（causality /
thread_progress / conflict_impact / goal_impact /
information_change / novelty / coherence），加权 rank 只排序，
不作闸门、不删史、不改果。

**节制**：冷却期（默认 5 tick 至多一批候选）；无触发不提议
（全静默合成世界 20 tick 提议数为 0，已测）。

## 权限模型

三级挂钩：observe（默认，只记账）→ propose（产候选，世界
验证后提交）→ never（对人物状态/历史无任何写路径）。
实时注入经 `WorldEngine.queue_external_event`（Phase 3b
批准补丁），世界以自己的 event_seq 盖号提交，broker 按既有
感知规则投递；管线延迟 2 tick（投递记录的产生时序决定）。

## 持久化

story.sqlite3（独立于冻结的 acp.sqlite3）：stories /
story_threads / thread_dependencies / character_conflicts /
event_candidates 四表 + schema_version。故事库是派生索引，
可由 world_log + 配置完全重建。
