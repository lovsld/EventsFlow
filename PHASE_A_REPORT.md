# Phase A 完成报告（2026-11-04）

范围：任务书 Phase A（最小 LLM 实验）。结论先行：**除"真实 LLM 调用"一项因无
API Key 按约定后补外，Phase A 全部完成并通过测试。**

## 交付内容

| 项 | 状态 |
|---|---|
| .venv + requirements.txt（pydantic 2.14.2 / pytest 9.2.0 / SQLAlchemy 2.1.7 / openai 3.44.0 / PyYAML） | ✅ |
| 人物 YAML 四件套 characters_v02/{owner,merchant,soldier}/（由 V0.1 profile 逐字迁移） | ✅ |
| v02/protocol.py：Event/Belief/PlanState/CharacterState/ActionSpec/AgentDecision/DecisionContext/ProviderResult（Pydantic v2，第 6 节协议逐字落地，action.type 枚举限定） | ✅ |
| v02/providers/：base（统一异步接口）/ rule（V0.1 规则移植）/ mock（确定性）/ openai（结构化输出+超时+重试+token 统计+校验修复重试） | ✅ |
| v02/agent.py CharacterAgent：独立 CharacterState、私有 AgentMemory、感知解释（V0.1 _interpret 移植）、计划更新的原因门禁（无理由 abandon/replace 被拒绝并记录） | ✅ |
| v02/context_builder.py：只用本人状态+投递感知构建 DecisionContext，available_actions 按位置/同处计算 | ✅ |
| v02/world.py：V0.1 世界层（Engine/Resolver/Perception/Broker/Interaction）向新 ID 的忠实移植，规则未改 | ✅ |
| v02/persistence.py：SQLite（agent_decisions / world_events / event_deliveries），DATABASE_URL 可切 Postgres | ✅ |
| main.py 三模式 CLI（--mode llm/rule/mock --ticks --seed） | ✅ |
| tests_v02/test_phase_a.py：pytest 10 个测试 | ✅ 10/10 通过 |
| V0.1 回归：原 tests/ 未动，复跑 | ✅ 10/10 通过 |

## 运行结果（实测）

- `main.py --mode rule --ticks 20 --seed 42` → runs_v02/rule_seed42：
  91 个客观事件；信的客观见证者只有 owner 与 soldier；最终主观世界与 V0.1
  高度一致（soldier 亲读信 1.0/experienced、merchant 只有传闻 0.7/heard、
  owner 推测信已泄露 0.9、soldier 认定 owner_hiding_something 0.65）。
  说明移植是忠实的，V0.2 rule 对照组与 V0.1 可比。
- `main.py --mode mock --ticks 20 --seed 42` → runs_v02/mock_seed42：
  70 个事件，链路完整；mock 决策器是刻意简化的，其 soldier 未能找到信——
  这是 mock 的预期行为，不作为涌现证据。
- `main.py --mode llm ...`（无 Key）→ 退出码 2，明确报错，未启动任何运行，
  没有用任何东西冒充 LLM 结果。OpenAI Provider 本身已实现并有单元测试
  覆盖"无 Key 时抛 ProviderError"路径；真实调用待 Key 提供后验收。
- 决策审计：rule 运行在 acp.sqlite3 中落库 60 条 agent_decisions
  （20 tick × 3 人，含 context_summary / parsed_output / execution_result）、
  91 条 world_events、60 条 event_deliveries。
- 信息隔离（测试断言）：三人各自 perceived_event_ids ⊆ Broker 实际投递集合
  （含 muffled 存在性通知），零越界——测试第一轮曾因我漏算 muffled 而误报，
  核对后修正了测试口径，机制本身无泄漏。

## 已知差距（进入下一阶段）

1. 真实 LLM 运行未验收（等 Key）。
2. 调度仍是 tick 循环；asyncio 事件唤醒、会话内局部队列属 Phase B。
3. Prediction Engine / Prediction Error / 完整 Plan Manager / Memory
   Consolidation 属 Phase C；Phase A 只有计划的原因门禁与 soldier 读信后的
   固定步骤计划。
4. 数据库只有 3 张表；其余表（beliefs/plans/snapshots/predictions）随 Phase C 落地。
