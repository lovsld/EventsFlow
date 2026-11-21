# ACP Story Simulator V0.3 迁移计划（Phase 0 审查输出）

> 输入：V0.3 构建任务书（Hu，2026-11-21）、V0.2 冻结基线
> （EventsFlow `4d53b32d` + 叙事层推送 `eac31d54`）。
> 本计划只做审查与映射，不改任何已有代码。结论先行：
> **V0.2 的地基比任务书假设的更完整——World/人物/DB/LLM 四层大部
> 分是「复用或小幅扩展」，真正的新建集中在 Story Engine 与多场景
> 调度两块。最大风险不在代码，而在两个口径问题（§5），需 Hu 在
> Phase 1 开工前拍板。**

## 1. 现状盘点（第一手核查，2026-11-21）

| 层 | 现状 | 证据 |
|---|---|---|
| Agent Runtime | `v02/agent.py`（975 行）：预测加工认知、独立记忆/信念、私有文件隔离 | V0.2-C 冻结验证 |
| World Engine | `v02/world.py`：唯一提交者；地点为扁平列表 + adjacency 图，移动按邻接校验；ambient 事件已有场景数据先例（rain_starts 等） | `scenarios/tavern_letter.json` |
| Event Broker | `v02/broker.py` + `comm_candidates.py`：投递/可闻性（delivered/muffled）完备 | world_log deliveries |
| Plan Manager | `v02/plan_manager.py`（748 行）：多步计划、挂起/恢复、指纹去重 | PLAN_LAYER_REPORT |
| Goal Engine | `v02/goal_engine.py`：条件式激活/成功/失败、depends_on（同人物内）、goal_changes 全量落库 | V0.2-C §十一 |
| Scheduler | `v02/scheduler.py`：asyncio 多 Agent 并行循环 + SubmissionGate，逐 tick 世界提交 | — |
| Database | **已是 SQLAlchemy 2.x ORM**（`v02/persistence.py`，10 张表：decisions/events/deliveries/belief_changes/plan_transitions/goal_changes 等），含 rollback_after 支持 resume；**无 Alembic**，建表走 create_all + 轻量补丁 | requirements.txt 含 SQLAlchemy 2.1.7 |
| LLM Provider | rule/mock/gemini/openai-compatible 四后端 + vault surrogate 取钥 + Pydantic 校验（`v02/protocol.py`）+ 429 池轮换/配额等待 | V0.2-C 收尾包 |
| Narrative（离线） | `narrative/` Observer/Extractor/Writer + 保真审计已交付（N1–N3）：事件图边（E1–E7）、场景切分、故事线判定、trace 审计 | NARRATIVE_N1/N2/N3_REPORT |
| 测试 | tests_v02 + tests_narrative 共 **268 项**（含冻结回归；唯一红为已泊车的 P-C4-1） | pytest collect |
| 冻结基线 | 酒馆规范轨迹 world_log md5 `cbf84d26…`（73 事件）；32 个决策文件 sha256 清单 | V0.2-C_IMPLEMENTATION_REPORT |

## 2. 模块映射（任务书 §3 → 处置）

图例：**复用** = 不动或仅配置；**扩展** = 在现有模块上加字段/钩子，
保持旧行为可回归；**新建** = 新模块；**重构** = 改内部结构（本计划
原则上避免，出现即单独报 Hu）。

| 任务书模块 | 处置 | 落点与说明 |
|---|---|---|
| agents/character_agent 等 | **复用** | `v02/agent.py` 等整套，不动 |
| world/world_engine、event_broker、perception、action_resolver | **复用** | `v02/world.py`、`broker.py`、感知与解析均已存在且冻结验证 |
| infrastructure/llm_provider | **复用** | 四后端 + vault + Pydantic 校验已满足 §16 大半要求；Story 侧调用走同一 provider 抽象 |
| infrastructure/database | **扩展** | 见 §4：新表进独立模块 + 独立 sqlite 文件，冻结的 acp.sqlite3 结构零改动 |
| story/story_architect | **新建** | `v03/story/architect.py`：读故事配置（theme/central_question/初始线与冲突）→ 初始化 Story State；只建初始条件，不写大纲 |
| story/thread_manager | **新建** | `v03/story/threads.py`：StoryThread 按任务书 §5.2 落字段；状态机 active/dormant/escalating/blocked/resolved/abandoned；进度由已提交事件驱动更新（订阅 tick 提交结果），不由 LLM 自由判定 |
| story/conflict_engine | **新建（半确定性）** | `v03/story/conflicts.py`：目标对偶分析先用确定性规则（同资源争夺、同命题立场相反、目标条件互斥——数据都在 goal/belief 表里）；LLM 只做语义补充且须过 Pydantic 校验 |
| story/event_generator | **新建** | `v03/story/eventgen.py`：三源候选（人物/世界/故事驱动）；候选只是提议，必须过 §3 的双验证才进世界 |
| story/dramatic_evaluator | **新建** | 确定性维度优先（线程推进、信息变化、目标影响都可从事件与 frontier 机械算）；主观维度（Novelty 等）可 LLM 辅助，但评分只记录、不删史、不改果（任务书 §8 红线） |
| story/arc_manager | **新建** | 阶段判定用可审计触发条件（不可逆代价已发生、多线汇聚、核心冲突被正面回应等），禁止按 tick 强制转换；转换必记依据 |
| scenes/scene_manager 等 | **扩展 + 新建** | V0.2 已有「多地点 + session」：Observer 的场景切分是事后视角；V0.3 需要**运行时**场景对象——在 scheduler/world 之上新建 `v03/scenes/`，场景 = (地点 × 时间窗 × 参与者) 的运行时登记，仍由 World Engine 统一提交，禁止场景自带一套状态 |
| narrative/event_graph | **扩展** | `narrative/extractor.py` 的 E1–E7 边体系即事件图的离线版；V0.3 的 NarrativeEvent 在其上补 caused_by/enabled_by/knowledge_changes 等字段，数据源仍是 world_log + deliveries + sqlite 旁证，口径与 N2 裁量保持一致 |
| narrative/scene_package | **扩展** | Observer scenes + Extractor lines 已备齐大半字段；ScenePackage 按任务书 §12 补 turning_point/outcome/unresolved_questions，生成走确定性汇总 + 可选 LLM 润色（润色不得添事实，沿用 N3 审计） |
| narrative/planner、chapter_planner、novel_exporter、continuity_validator | **新建（Phase 5）** | 建在 event graph 与 scene packages 之上；连续性校验复用 N3 保真审计的机械检查（引语逐字、覆盖、时序） |
| evaluation/* | **新建** | §6 指标大部分可从既有产物机械算（frontier、goal_changes、deliveries）；「Story Engine 直接改变人物决定次数 = 0」靠架构保证（Story Engine 无写人物状态的代码路径）+ 审计断言 |

**目录处置**：任务书 §3 的树形图是职责图，不是物理目录令。
按本项目既有先例（V0.2 与 V0.1 并存、narrative 与 v02 并存），
V0.3 新代码进新包 `v03/`（内含 story/、scenes/、evaluation/ 子包），
不搬动 `v02/` 与 `narrative/` 的任何文件。

## 3. Story Engine 接入方式（自主性红线的工程落法）

唯一接入点：**tick 边界钩子**，且分三级权限——

1. **观察**（默认开）：每 tick 提交后，Story Engine 只读本 tick
   事件、线程与冲突状态，更新自己的账本（线程进度、停滞计数、
   冲突演化）。不产生任何世界影响。
2. **提议**（Phase 2 开）：停滞或冲突信号触发时候选生成；候选过
   Causal Validation（因果依据字段必须能指回已提交事件/世界状态）
   与 World Rule Validation（地点邻接、人物在场、物品状态等由
   World Engine 的既有规则判），通过后**以普通世界事件的身份**
   在下一 tick 经 World Engine 提交（如「镇长代理人来到镇上」
   这类环境事实），人物是否理会完全由其自身决策决定。
3. **永不**：Story Engine 没有写人物记忆/信念/目标/计划的接口；
   没有绕过 World Engine 提交事件的路径；没有删除或改写历史
   事件的路径。此条以代码结构保证，并以测试断言（§17.3 末项
   恒为 0）。

**回归门**：Story Engine 关闭时，rule 模式同 seed 运行必须逐字节
复现冻结规范轨迹（md5 `cbf84d26…`）——沿用叙事层 §7.4 隔离实验
的做法，每个 Phase 都跑。

## 4. 数据库迁移策略

- 冻结的 `acp.sqlite3`（每 run 一份）**结构零改动**，保证旧 run
  可复现、新旧产物可对照。
- V0.3 新实体（stories/story_threads/character_conflicts/
  event_candidates/scenes/narrative_events/event_relations/
  scene_packages/narrative_arcs/narrative_plans/setup_payoff_links
  等，按任务书 §15）进新模块 `v03/persistence.py`，落在同 run
  目录的 **`story.sqlite3`**，同为 SQLAlchemy 2.x ORM 模型。
- 迁移方式：新库 create_all + `schema_version` 表起步；
  **Alembic 暂不引入**——单文件实验库 + 实体仍在快速演化期，
  Alembic 的收益在多人协作/生产升级时才显现；若 Hu 要求按任务书
  字面执行，可在 Phase 1 单独加（成本约半天，纯增量）。此项列入
  §5 待拍板。
- 事件主权不变：客观事实仍以 world_log + acp.sqlite3 为准；
  story.sqlite3 里的 narrative_events 是**派生索引**（可由前者
  重建），不是第二事实源——与叙事层 L-OBJ/L-AUX 分层一致。

## 5. 待 Hu 拍板的口径问题（2 个）

1. **Alembic**：按任务书字面引入，还是按 §4 的务实方案
   （create_all + schema_version，Alembic 押后）？推荐后者。
2. **外部人物**（镇长、代理人、写信人）：任务书 §18 把他们列为
   「外部人物」。V0.2 里他们只是 claims/objects 指向的离场存在，
   不是 Agent。推荐 Phase 1–2 **不新增完整 Agent**：外部人物只
   能通过 world/story 驱动的候选事件（经双验证）产生在场事实
   （如代理人抵达、镇长下令），不做自主决策；是否在后续 Phase
   升级为完整 Agent，等多场景跑通后另议。这样 Phase 1 的范围
   可控，也不动人物自主性的既有验证。

其余口径已在任务书内自洽，按任务书执行：停滞检测先用确定性
信号（连续 K tick 无 plot 事件/无线程推进/无信念变化），LLM
只在候选生成与语义分析处介入且输出必过 Pydantic 校验；实验
对照（Story Engine 开/关）按 §19 Phase 6 预注册执行。

## 6. 阶段映射与验收（对齐任务书 §19）

| Phase | 内容 | 本计划的验收锚点 |
|---|---|---|
| 0 | 本计划 | Hu 批准 + §5 两项拍板 |
| 1 | Story State / Architect / Thread Manager / Goal 字段扩展 / Conflict Engine | 任务书 Phase 1 五项测试 + 关闭 Story Engine 时规范轨迹逐字节复现 |
| 2 | Event Candidate / Generator / 双验证 / Dramatic Evaluator | 候选无因果依据必拒；Story Engine 写人物状态路径不存在（结构测试）；同候选不重复生成（指纹去重，复用 plan_fingerprint 思路） |
| 3 | Scene Manager / Runtime / Scheduler 扩展 / 跨场景传播 | 同人物同时两地不可能（世界校验）；跨场景信息必有传递途径与时间（deliveries 口径扩展） |
| 4 | Narrative Event Graph / Scene Package / Causal Analyzer | 每重要事件可溯源；认知变化可追踪（frontier 已有底座） |
| 5 | Arc Manager / Setup-Payoff / Planner / Chapter / Exporter / Validator | 伏笔必有来源事件；导出素材与历史零冲突（N3 审计复用） |
| 6 | 酒馆多 seed × 目标组合 × Provider × 开/关对照实验 | 产出任务书五件套；指标 + 人工报告并行（§17.4） |

每 Phase 沿用既有纪律：独立报告、测试全绿才算完、冻结基线
回归必跑、LLM 花费先报预算、阶段间等 Hu 放行。

## 7. 主要风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 范围膨胀 | 六个 Phase 是 V0.2 一整个版本的量级 | 阶段门纪律；每 Phase 只做任务书列明项，衍生想法进泊车清单 |
| 停滞检测误报 | 平静期是合法状态（任务书 §7.5），误判会逼出注水事件 | 确定性信号 + 冷却期 + 候选有人物拒绝权；开/关对照实验量化注水率 |
| LLM 进回路毁复现 | Story 侧用 LLM 后同 seed 不再逐字节复现 | rule 模式下 Story Engine 全确定性（LLM 只在显式 llm 模式启用）；复现性验收只对 rule 模式 + 关闭态提出 |
| 线程账本与事实脱节 | 实时线程（Story Engine 账本）与事后派生线（Extractor）可能不一致 | 两者定位不同：前者是动力学状态，后者是事实核算；Phase 4 输出两者差异报告，分歧即信号不是 bug |
| 外部人物诱惑 | 给镇长加 Agent 会牵动场景/调度/目标一大片 | §5 第 2 项的范围冻结 |

## 8. 本计划未做的事

- 未改任何代码、未动冻结基线、未跑任何 LLM。
- 未细化各 Phase 的内部设计——每 Phase 开工前按惯例先出该
  Phase 的设计要点，经 Hu 过目后实施（或按 Hu 指示直接实施）。
