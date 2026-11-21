# V0.3 Phase 1 阶段报告（Story Foundation）

> 任务书 §19 Phase 1 + `V03_MIGRATION_PLAN.md` §6。Hu 于 2026-11-21
> 批准按迁移计划建议开工（Alembic 押后、外部人物不做 Agent）。
> 本阶段全部为新增代码（`v03/` 包），未改动任何 V0.2 文件；
> 冻结规范轨迹 md5 复核仍为 `cbf84d263ad01e50fca711328c1e78e2`。

## 1. 交付物

| 类别 | 文件 |
|---|---|
| 模型 | `v03/story/models.py`：StoryThread（§5.2 全字段 + 匹配钩子）、CharacterConflict（§6.3）、CharacterGoalView（§6.1）、StoryConfig / StoryState（Pydantic） |
| Architect | `v03/story/architect.py`：故事配置 → 初始 StoryState；从各人物 `goals.yaml` 构建目标视图（weight→priority、条件树→success/failure 信念集）；依赖自动登记为双向关联 |
| Thread Manager | `v03/story/thread_manager.py`：创建/依赖/共享事件标记；状态机 active/dormant/escalating/blocked/resolved + 重开；知情追踪（只认投递记录 + 行动者本人）；停滞查询 |
| Conflict Engine | `v03/story/conflict_engine.py`：三种确定性检测（目标信念拉锯、资源争夺、信息不对称）+ 配置声明冲突；升级由对峙/敏感断言驱动，静默期自动冷却；随关联线索解决而解决 |
| Story Engine | `v03/story/story_engine.py`：observe 级 tick 钩子（迁移计划 §3 第 1 级）+ world_log 回放驱动；停滞信号计数（距上个 plot 事件/线索推进的 tick 数） |
| 持久化 | `v03/persistence.py`：独立 `story.sqlite3`（stories / story_threads / thread_dependencies / character_conflicts / schema_version），SQLAlchemy 2.x；冻结的 acp.sqlite3 零改动 |
| 故事配置 | `stories/tavern_secret.json`：酒馆五线（主线/秘密/利益/关系/危机）+ 声明冲突 1 项 + 目标资源元数据 + 初始知情（老板知信的存在与指控） |
| 产物 | `v03_runs/rule_v02c4_seed42/`：story_state.json / story_summary.json / story.sqlite3（规范轨迹回放结果） |
| 测试 | `tests_v03/test_phase1.py` **8/8 通过**；全量回归 277 过 + 1 红（冻结时即存在的停放项 P-C4-1，与本阶段无关，v03 与 v02 无任何 import 关系） |

## 2. 关键口径：传闻不能「解决」问题

实施中发现并修正的一个语义问题，值得单独记录：初版里线索问题
只要被任何断言「提到」就算 answered——于是酒馆主线会被士兵的
暗示性发言判成 resolved，等于把「有人指控镇长」当成「镇长有罪
已坐实」。修正后引入证据分级：

- **direct**（直接证据）：搜查者亲手找到物品的 fact_belief；
  以亲历知识（scenario claims 的 speaker_kind=experienced）
  发言的断言。只有 direct 能 answered 问题。
- **hearsay**（传闻）：其余断言与物品 reveals——只把问题标为
  addressed（已被提出/谈论），不计进度。

这与叙事层 N2 的教训同源：结构闭合 ≠ 剧情落定。

## 3. 规范轨迹回放结果（酒馆 20 tick，观察级）

| 线索 | 终局状态 | 进度 | 说明 |
|---|---|---|---|
| 主线·镇长贪腐 | escalating | 0.5 | 信的存在被士兵搜得（direct，已答）；贪腐本身只有暗示与否认（addressed，未答）——如实 |
| 秘密·写信人 | dormant | 0.0 | 全程仅 1 个相关事件，长期无发展被判 dormant |
| 利益·商人牟利 | escalating | 0.0 | 31 个相关事件但交易从未达成——与 V0.2-B 已知事实（商人商业目标未完成）一致 |
| 关系·老板与士兵 | active | —（无问题集） | 5 个相关事件 |
| 危机·调查泄露 | escalating | 0.0 | 随主线话题扩散持续被推进 |

冲突共检出/登记 6 项：目标冲突（士兵×商人，信念 mayor_embezzled
拉锯——士兵的调查目标以确立该信念为成功，商人的护财目标以该
信念坐实为失败，纯从 goals.yaml 条件树机械检出）、资源冲突
（商人×老板，酒馆渠道）、信息不对称 3 项（按线索敏感事实的知情
差）、声明利益冲突 1 项。回放全程确定性：两次回放状态逐字段
相同；持久化往返无损。

## 4. 本阶段未做（按计划留给后续 Phase）

- Story Engine 的「提议」级接入（候选生成/双验证/提交）属
  Phase 2；本阶段的 observe 钩子是库级 API + 回放验证，runner
  实时挂钩随 Phase 2 一并落地（候选要消费钩子才有意义）。
- 信念变化停滞计数（ticks_since_belief_change）字段已留，
  数据源在 runner 集成时接 goal/belief 变更流。
- LLM 语义冲突分析未启用：Phase 1 三检测器全确定性；
  LLM 补充按 §16 规范在后续 Phase 以 Pydantic 校验接入。
- 外部人物（镇长等）按拍板结论未做 Agent。
