# Phase 1 / V1.0 — 现有架构与问题定位报告

日期：2026-11-24。依据：《EventsFlow 第一阶段开发需求文档》（Phase 1 / V1.0）
§9.1。本报告只做审查与定位，不改任何代码。基线已按 §8.1 保存（见 §1）。

## 1. 基线保存（§8.1）

三场 run 的输入、配置、事件流与全部叙事产物已复制到 `phase1_baseline/`
（102 个文件，约 1.15 MB），每文件记录 md5 与来源路径于
`phase1_baseline/manifest.json`：runs_v02 三场的 world_log / run_meta /
summary；narrative_runs 三场的 observations、storylines、story_B.md 与
upgrade/ 全部产物（narrative_events、semantic、causal_graph、
character_arcs、story_views、setup_payoff、foreshadow_edges、plan、
novel_v2、trace_v2、narrative_quality_report、comparison）；v03_runs
三场的 JSON 产物；stories/ 三份场景配置。原文件只读复制、未覆盖。
Phase 1 全部前后对比以此基线为准。

## 2. 当前数据流（总图）

```
v02 涌现层：scenario → CharacterAgent.decide(perception) [agent.py]
  → World Engine 提交 → world_log.json（按 tick 分组：positions + events）
  → narrative/observer.py → observations.json（type/text/claim/features）
  → models/build.py → NarrativeEvent[]（event_summary / knowledge_changes）
  → semantic.py（合并组）/ causal_graph.py（统一因果边+验证器）
  → character_arc.py / story_views.py / setup_payoff.py
  → planner/ → plan.json（三级大纲 + 连续性校验）
  → writer2.py（九项输入装配 → Gemini 逐场写作，F1/F2/F3 段落）
  → quality.py（6 确定性 + 6 评审 = 12 维）
v03 故事引擎：StoryEngine 在 tick 边界以 replay/live 两种方式挂接
  （live.py 的 hook 只在 tick 边界运行），产物独立存 story.sqlite3 /
  v03_runs/，不写 acp.sqlite3。
```

## 3. 八个问题逐项定位

### 3.1 事件流决策质量（任务一，P0）

- **发生模块**：`v02/agent.py` 的 `CharacterAgent.decide()`（感知→目标
  引擎→计划→provider 调用）+ `v02/providers/`（rule / openai 兼容 /
  gemini / mock）。v03 实验矩阵（`v03/experiment.py`）以 llm 模式
  （DeepSeek）跑 20 tick 时故事机制零触发：agent 从未选择 search，
  信件从未被发现，主线进度 0.0；且 20–25% 的 LLM 决策输出解析失败
  （provider 层注释已记录各家的 json_object 怪癖）。
- **数据流**：决策只看当前感知与自身目标/计划，没有任何「故事级」
  状态输入——系统里存在故事状态（v03 StoryEngine 的 threads /
  progress），但它只在 tick 之后被动统计，从不反馈给决策者。
  「事件发生 ≠ 故事推进」在代码上的对应物就是：**推进信号没有回路**。
- **改法（新增，不重写）**：新增 Story State Assessor + Progress Judge
  模块，在 tick 边界（复用 v03 live hook 的挂接点）评估六维推进，
  产出 §3.3 的六类决策建议与完整决策记录（ID/状态摘要/理由/预期/
  实际/事件 ID/验证结果）。建议以「压力/机会」形态注入（与 V0.3
  Story Engine 既有红线一致：引擎提议、角色决定、World Engine 提交），
  不直接改写 `CharacterAgent.decide` 的核心选择逻辑；解析失败率问题
  在 provider 层单独修（结构化输出重试/降级），与推进判断解耦。
- **迁移**：无数据结构迁移；新增 `decision_log` 产物（JSON/场）。
- **兼容**：OFF 时必须逐字节复现既有 run（沿用 V0.3 的 OFF 基线纪律）。

### 3.2 动作日志污染（任务二，P0）

- **发生模块（已定位到行）**：`narrative/models/build.py:151-152`——
  observation 无台词文本时，event_summary 回退为模板串
  `f"{type} @ {location}"`（如 `search @ oncall`、`speak_failed @
  triage`）；且 `summary_source` 被硬编码为 `"source_text"`，模板串
  被错误标注为源文本（溯源标注 bug，一并修）。
  `narrative/writer2.py` 的 assemble_context 以
  `"text": obs.text or event_summary` 把该串放进事件的 text 槽位，
  模型遂将其当作可引用台词写进正文（诊所事实一致性 2.0 的 3 处违规
  中 2 处源于此，另 1 处为引文不逐字）。
- **现有可复用资产**：observation 已有结构化字段可支撑分类——
  `type`（speak/confront/ask/search/move/observe/speak_failed…）、
  `features.has_text / is_failed_attempt`、`fields`（found/reason/
  target）、`claim/proposition/stance`。任务书 §4.3 的八类分类可
  **映射**到现有 type 体系，无需新造一套（FACT/ACTION/DIALOGUE/
  OBSERVATION/SYSTEM 等作为素材层分类叠加在 NE 之上）。
- **改法（新增层）**：新增 Narrative Material Adapter（
  `narrative/material.py`）：输入 NE + observation，输出分类后的
  叙事素材——DIALOGUE 素材保留逐字文本（可引用）；ACTION 素材由
  模板渲染成自然语言描述（「沈医生在值班室搜寻」）且标记
  quotable=False；SYSTEM/DEBUG 类不进入素材。writer2 改为只消费
  adapter 输出（assemble_context 的 text 槽位换源），writer.py
  （N3 旧 Writer）不动。
- **迁移**：NE 增加 material_class / quotable 派生字段（可由现有
  字段确定性推导，旧产物可回填，不破坏 schema）。
- **兼容**：adapter 是纯函数层，旧 run 可重算；基线保留原产物。

### 3.3 跨场景重复叙述（任务三，P0）

- **发生模块**：`narrative/writer2.py`——逐场独立装配上下文，
  `prior_outcomes` 只带最近 3 场的 scene_end_state 一句话，没有
  「已揭示事实 / 已用事件 / 已说过的关键对话」的结构化叙述历史；
  同一场内靠 semantic 合并组压重复（已解决），跨场无任何机制。
- **改法（新增）**：Scene Narrative Record（每场落盘：用过的
  event_id、已揭示事实、关键对话指纹、叙事目标、结束问题）+
  Information Increment Checker（新场生成前做三层检测：同 id 复用 /
  语义相似 / 叙事价值判断），检测结果作为场景目标约束注入 prompt，
  并产出 Scene Repetition Rate 指标（§8.2）。检测只标注与约束，
  不硬禁复用（回忆/伏笔/强调是合法重述）。
- **迁移**：新增 `upgrade/scene_records.json` 产物；trace_v2 增补字段。
- **兼容**：writer2 的 write_novel 循环内增量接入，旧 trace 可读。

### 3.4 情绪与动机缺失（任务四，P1）

- **现状**：`narrative/character_arc.py` 的 emotional_changes /
  value_changes 诚实为空——涌现数据（world_log + acp.sqlite3 的
  belief/goal_changes）没有情感轨迹，这不是代码缺陷而是数据缺位。
  MOTIVATED_BY 在统一词表里已定义（Phase 1 schema），因果图构建器
  从不产生它：叙事输入里没有确定性动机证据，当时的裁决是宁缺勿造。
- **改法（新增）**：`narrative/psychology.py`——Emotional State 记录
  （character/tick/scene/emotion/intensity/confidence/
  evidence_event_ids/source_type）以**推断层**身份单独落盘
  （evidence_level=interpretation，物理上不进事实层）；证据来源
  限定为可溯源信号：对峙/拒绝事件（anger/fear 候选）、发现事件
  （surprise）、目标达成/受阻（goal_changes 的 before/after）、
  台词中的显式情绪词。无证据则 unknown，不强行生成。
  MOTIVATED_BY 以多假设形态产生（§6.4 的四种 inference_status），
  动机候选来自角色 goal 栈与事件的因果下游，confidence ≤0.5 起评、
  有佐证才升 SUPPORTED；边写入独立的 inference 图文件，不混入
  已验证因果图（保持「已验证」语义纯净）。
- **迁移**：新增产物文件，不改既有 schema。
- **兼容**：因果图验证器与既有边集合不变。

### 3.5 F3 占比 ~40% 无预算（任务五，P1）

- **现状**：F1/F2/F3 没有独立分类器模块——等级由 Writer 在输出时
  自我标注（writer2 的 paragraphs[].level），quality.py 只统计
  分布、不设预算、不告警。三场 F3 占比 40%/40%/41%。
- **改法（新增配置 + 校验）**：外置预算配置（如
  `narrative/f_budgets.json`：F1≥30%、F2 40–55%、F3≤20% 为第一阶段
  实验初始值）；quality.py 增加预算维度（逐章 + 全书统计、超限
  产出诊断 issue 并列出可降级 F3→F2 的候选段）；writer2 的 prompt
  注入当前累计占比作为软约束。**不机械删段**：超预算只触发警告与
  改写建议，例外由人工批准（任务书 §7.3）。
- **迁移**：无；配置外置。
- **兼容**：旧 trace 的 F 分布可直接重算对照。

### 3.6 章节标题仍为序号

- **现状**：planner 的 chapter title 为「第N章」占位（Phase 5 已
  声明文学标题属 LLM 层）。本期任务书未单列任务，仅在问题表中
  提及；处理方式：随任务五/六的 LLM 调用顺带生成候选标题（基于
  章节大纲与高潮事件），或在步骤 5 指标完成后单列一个小任务。
  **本报告建议**：不单独立项，随 Phase 1 收尾时用一次小额调用生成
  并写入 plan 的 title 字段（保留 ordinal 字段不覆盖）。

## 4. 复用 / 修改 / 新增 总表

| 类别 | 内容 |
|---|---|
| 直接复用 | observer、semantic 合并组、causal_graph 及验证器、character_arc 门槛逻辑、story_views 双口径、setup_payoff 证据梯、planner + 连续性校验、quality 12 维框架、gemini_client 号池 |
| 小改 | models/build.py（summary 回退与 summary_source 标注）、writer2（素材换源、叙述历史注入、F 预算软约束）、quality.py（新增指标与预算维度） |
| 新增 | material.py（素材适配层）、scene_records + 增量检测、psychology.py（情绪+动机推断层）、Story State Assessor + Progress Judge（tick 边界）、f_budgets.json 配置、phase1 专项指标模块 |
| 不动 | v02 涌现核心（agent 决策主体、World Engine）、v03 既有模块、narrative/writer.py、全部既有 run 与基线 |

## 5. 实施顺序与依赖（按任务书 §10，结合定位结论）

1. 本报告 + 基线（已完成，待 Hu 确认）。
2. 任务二（素材适配层）：依赖最少、收益最直接（诊所 2.0），且为
   任务三/五提供干净的素材分类基础。
3. 任务一（决策质量）：依赖对 v02/v03 挂接点的审查（本报告 §3.1）；
   需要 LLM 实验预算（DeepSeek 20 tick × 矩阵），开工前单独报量级。
4. 任务三（场景增量）：依赖任务二的素材分类（对话指纹需要 quotable
   标记）。
5. 统一指标（§8.2 九项）：与 2–4 并行落点，收尾统一计算。
6. 任务四（情绪+动机）：独立推断层，随时可插。
7. 任务五（F 预算）：依赖 quality 框架扩展。
8. 全量回归 + 12 项交付物。

## 6. 风险与待确认

- 任务一触及 v02/v03 决策链路：本报告按任务书授权将其设计为
  **tick 边界新增模块 + provider 解析修复**，不改 agent 决策主体；
  若实施中发现必须动 decide() 核心，将先停工报 Hu。
- 步骤 2 的验收要重跑诊所 Writer + 评价（Gemini，量级约 11k 入 /
  7k 出，与 Phase 6 诊所一轮相当）；步骤 3 的 20 tick 实验矩阵
  用量在开工前单独报批。
- 章节标题（§3.6）的处理方式请 Hu 一并确认。
