# NARRATIVE_EXPORT_GUIDE — 小说素材导出指南（V0.3 Phase 5）

每场运行可导出一份素材包（`v03/planning/exporter.py` 的
`export_run`），写到该场目录下：

- `novel_export.json`：机器可读全量包；
- `novel_export.md`：人类可读渲染（弧线、章节、场景要点、
  伏笔回收表、连续性结论），事件文本逐字引用原文。

## 包内容

| 部分 | 内容 | 来源 |
|---|---|---|
| story | 标题、主题、核心问题 | Story Architect 配置 |
| plan.arc | 阶段转换表（每条带触发器与证据）、每 tick 阶段、未抵达阶段 | Arc Manager（事件结构触发，非 tick 计数） |
| plan.chapters | 按弧线阶段切分的章节，每章含场景切片、关键事件（图谱边端点）、关联线索 | Narrative Planner |
| plan.setups | 伏笔登记：问题型（线索问题，planted/payoff tick）与物件型（发现→披露）；未回收者 status=open，如实列出 | Setup/Payoff 登记 |
| scene_packages | Phase 4 场景包（参与者、知识起止、传播进出、戏剧功能） | v03/graph |
| event_graph | Phase 4 事件图谱全量（节点认识论分类 + 带证据的边） | v03/graph |
| knowledge_timelines | 每人每事实的学到时刻与来源事件/方式 | 图谱推导的副产品 |
| continuity | 连续性校验结论（错误/警告逐条） | Continuity Validator |

## 使用纪律（给小说创作阶段）

1. 素材包只含已提交事实与可追溯推导；写作时新增的任何
   情节都属于创作加工，不得回填为「模拟事实」。
2. `epistemic` 标为 rumored / unverified 的内容是人物层面的
   传闻与无据之言，写作时应保持其不确定性，不得升格为
   客观真相。
3. status=open 的伏笔是模拟未走到的地方：可以留白、可以
   在创作层收束，但要意识到那是创作决定，不是模拟结论。
4. continuity 有 error 的运行不应进入写作阶段，先回查
   世界/推导问题。
