# SCENE_SYSTEM_DESIGN — 多场景系统设计（V0.3）

实现：`v03/scenes/`。场景是对同一个客观世界的登记与索引，
不拥有独立状态（任务书 §10）。

## 场景登记（SceneManager）

依据已提交 tick 的 positions（唯一权威）：某地点有人在场
且有事件发生 → 场景开启；地点清空 → 关闭（end = 最后有人
的 tick）；会话未正常结束而人散尽 → interrupted。参与者
的加入/离开逐 tick 记录。同 tick 多地点有场景即多场景
并行（酒馆规范轨迹 t1–3 bar 与 back_room 并行，实测）。

场景是长驻的地点登记，不是戏剧单元：bar 场景横贯全场
20 tick。戏剧切分由 Phase 5 的弧线段落对场景做 tick 切片
完成，场景包本身保持登记口径。

## 跨场景传播（PropagationTracker）

事实跨场景只有一条合法路径：某人在场景 A 经投递/搜得/
初始知识学到事实，随后移动，在场景 B 亲口说出。账本记录
携带者、来源/目标场景、学习与说出时刻、旅行 tick 数
（≥1 是时空下限）。无来源断言单独标记（unexplained）——
但场景定义中 basis_required=false 的 claim（提议、唬人）
属起源行为，不报警：它们不是知识主张。

## 一致性校验（ConsistencyValidator）

独立复核事件流：人物行为地点与其世界位置不符（分身）、
同一事件重复提交，逐条标记。规范轨迹全程 0 问题。

## 运行时（SceneRuntime）

逐 tick 串联：Story Engine 记账 → 场景登记 → 传播追踪 →
一致性校验 → 提议管线，产出 pending world events（注入
契约）。同一套处理既用于回放（replay_runtime），也用于
实时驱动（v03/live.py 的链式钩子）——两条路径同口径，
回放结论可迁移到实时。

## 场景包

见 EVENT_GRAPH_SCHEMA.md：场景包在图谱层组装（知识起止
快照来自图谱推导的学习时刻表），任一事件至多属于一个
场景包。
