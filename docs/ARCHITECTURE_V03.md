# ARCHITECTURE_V03 — ACP Story Simulator V0.3 架构总览

V0.3 在 V0.2 之上增加故事动力与叙事整理，三个引擎的职责边界
不变（任务书 §2.1）：

```text
World Simulation Engine (v02/, 冻结基线 + Phase 3b 一处批准补丁)
  世界状态 / 行动验证 / 事件提交 —— 唯一的事实提交者
        │  已提交事件流 (world_log)
        ▼
Story Engine (v03/story/ + v03/scenes/ + v03/live.py)
  observe: 线索/冲突/弧线信号记账（只读）
  propose: 候选 → 因果验证 → 世界规则验证 → 戏剧评估
        │  queue_external_event（世界盖号提交）
        ▼  （人物如何回应由人物自己决定）
Narrative Engine (narrative/ N1–N3 + v03/graph/ + v03/planning/)
  事件图谱 / 场景包 / 章节规划 / 小说素材导出（只读下游）
```

## 包地图

| 包 | 职责 | 阶段 |
|---|---|---|
| `v03/story/` | Story Architect、线索状态机、冲突检测、候选生成与双验证、戏剧评估、Story Engine 门面 | Phase 1–2 |
| `v03/scenes/` | 场景登记（地点占用）、跨场景传播账本、一致性校验、SceneRuntime | Phase 3 |
| `v03/live.py` | 实时驱动：与 runner 同构构造 + 链式 tick 钩子 + 注入排队（2 tick 管线延迟） | Phase 3b |
| `v03/graph/` | 叙事事件图谱（9 类带证据的边、节点认识论分类）、场景包 | Phase 4 |
| `v03/planning/` | 弧线管理（事件结构触发）、章节规划、伏笔登记、连续性校验、小说导出 | Phase 5 |
| `v03/persistence.py` | story.sqlite3（故事派生索引，可由 world_log 重建） | Phase 1–2 |
| `v03/experiment.py` | Phase 6 对照实验矩阵与指标 | Phase 6 |

## 不变式

1. 只有 World Engine 提交客观事实；Story Engine 的全部输出
   是候选，提交权在世界（`queue_external_event` 由世界盖号）。
2. Story Engine 关闭 ⇒ 规范轨迹逐字复现（md5 回归门，Phase 3b
   起每阶段复验）。
3. 叙事层只读：图谱、场景包、规划、导出全部由 world_log
   派生，可重算、可审计，不回写世界与人物。
4. 人物自主性：Story Engine 改人物决定次数恒为 0——结构上
   没有路径（无人物状态写入口、无决策回调）。
5. 数据先行：每条推导（线索问题答复、图谱边、弧线转换、
   传播记录）都带证据 id；无据不立，零值如实报告。

## 迁移要点

详见 `V03_MIGRATION_PLAN.md`：新代码独立 `v03/` 包；故事表存
独立 story.sqlite3（create_all + schema_version，Alembic 押后）；
外部人物（镇长/代理人/写信人）不建全 Agent，只经验证后的
候选事件入场。
