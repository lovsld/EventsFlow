# NARRATIVE_UPGRADE_DESIGN — 小说自动生成升级设计

> 依据 docs/NOVEL_ADAPTATION_AUDIT.md（Phase 0 审查）。
> 总原则：新增模块只读现有产物；narrative/ 与 v03/ 既有代码
> 零修改；事实与推测物理分离；每个 Phase 独立验收。

## 0. 分层与数据流

```text
observations / storylines（narrative 既有）
v03 story_state / event_graph / scene_packages（只读证据，可缺省降级）
        │
        ▼
L1 NarrativeEvent 语义层（narrative/models/）      Phase 1–2
L2 统一因果图（narrative/causal_graph.py）          Phase 2
L3 人物弧线 / 线索冲突视图（character_arc.py 等）   Phase 3
L4 伏笔管理（setup_payoff.py）                      Phase 4
L5 三级大纲（narrative/planner/）                   Phase 5
L6 Writer 升级（F1/F2/F3）+ 质量评价（quality.py）  Phase 6
```

每层的产物落 `narrative_runs/<run>/upgrade/`，与旧产物并存，
旧读取方不受影响。

## 1. Phase 1：NarrativeEvent 模型（本阶段详设）

### 1.1 粒度与标识

默认与 observation 1:1：`narrative_event_id = "NE-" + obs_id`，
`source_event_ids = [obs_id 对应的原始 event_id]`。Schema 允许
一对多（source_event_ids 为列表）——Phase 2 的语义合并（同一
事实的重复台词合并为一个 NE、保留全部源 id）依赖这一点，
这是治重复的结构前提。本阶段只做 1:1 投影。

### 1.2 字段（任务书第五节全量）

见 `narrative/models/narrative_event.py`。要点：

- `evidence_level`：objective / subjective / interpretation /
  creative —— 事件本体的事实等级。投影产出的 NE 本体恒为
  objective（它是对已提交事件的投影）；interpretation 只出现
  在标注字段（dramatic_function 的来源、summary）上，二者分开。
- `event_summary`：投影阶段 = 原始 text 的截断或确定性模板句，
  标 `summary_source="source_text"`；LLM 改写摘要是 Phase 2
  的事，必须标 interpretation 并给 confidence。
- `dramatic_function`：多标签，可空。投影规则（保守，宁缺勿滥）：
  search 且 found 非空 → discovery；某事实首次向新听众断言 →
  revelation；confront → confrontation；同命题对峙链后续断言 →
  escalation；ask → setup（提问开启信息缺口）；回答了悬置问题
  的断言 → payoff；session 首个剧情事件 → introduction；
  move/observe/ambient → 空（场景素材）。
- `narrative_importance`：0–1 确定性启发式（剧情事件类型、
  Extractor 强边度数、是否回答问题、是否事实首现），公式写入
  代码注释，可复算；它只是排序信号，不是事实。
- `knowledge_changes`：由 delivered_to / found / frontier 确定性
  推导：{character, fact, change: learned|reinforced, via,
  source_event_id}。人物实际知道什么，以这一层为准。
- `character_changes` / `relationship_changes` /
  `conflict_changes`：Schema 先行；投影阶段只填有确定性证据
  的条目（信念学习同时记一条 belief 类 character_change；
  其余留空），Phase 3 补全。**空列表是合法状态，不是缺失。**
- `causal_links`：统一词表的边（见 §2），投影阶段为空，
  Phase 2 填充。
- `interpretation_confidence`：规则标注为 1.0（规则即依据）；
  无标注时 dramatic_function 为空、confidence 不适用（null）。

### 1.3 配套 Schema（本阶段一并定型，后续阶段实现）

`narrative/models/` 同时定型：CausalEdge（统一边结构）、
CharacterArc、ConflictView、ThreadView、SetupPayoffRecord、
StoryOutline / ChapterOutline / SceneOutline、QualityReport。
字段按任务书对应章节全量落齐，避免后续阶段再改 Schema。

## 2. Phase 2：因果图增强（设计要点）

- 统一词表 11 类。映射：E2 投递传递 → CAUSED_BY（信息因果）/
  REVEALED_BY（以披露事件为视角）；E4 搜得→使用 → ENABLED_BY；
  E3 问答 → RESPONDED_TO；E5 否认 → CONTRADICTS；v03 escalates
  → ESCALATED_BY；v03 resolves → RESOLVED_BY；v03 enables →
  ENABLED_BY；v03 depends_on（注入）→ CAUSED_BY（带 candidate
  证据）。MOTIVATED_BY 只允许有目标/计划证据时立（goals.yaml
  条件与事件匹配），否则标 inferred 且 confidence ≤0.5。
  FORESHADOWS / PAYOFF_OF 由 Phase 4 的伏笔登记反哺。
- 独立验证器：逐边复核证据（deliveries 存在性、时间序、
  同命题不足以立因果——CAUSED_BY 必须有投递或使能证据，
  仅同 proposition 的对归入 E1 组织关系，不进因果图）。
- 时间先后 ≠ 因果：无证据的先后事件不连边（允许无边）。

## 3. Phase 3：人物弧线与线索/冲突视图（设计要点）

- CharacterArc 数据源：observations（行为与话语）+ frontier
  （认知）+ v02 beliefs_trace（若该 run 有；没有则在输出中
  标注证据等级降级）。turning_points 的认定门槛：同一维度
  ≥2 个独立证据事件且其后行为模式发生可测变化（同类行动
  频率/对象改变），达不到门槛只记 candidate，不记 turning
  point。arc_type 判定保守：证据不足 → unresolved_arc /
  arc_status=open，**禁止单事件定成长**。
- ConflictView：在 v03 severity 之外补 tension（由对峙频率、
  未答问题龄、参与人数合成的确定性指标，与 severity 分开
  存）、escalation_history（severity 变化点列表）、
  resolution_conditions（由关联线索问题生成）。
- ThreadView：合并 narrative 故事线与 v03 线索：补
  central_question（取自主问题）、climax_event（关联事件中
  importance 最高且处对峙/披露类功能者，标注为推断）、
  turning_points、线间关系（dependencies 来自 v03；
  intersects = 共享参与者且事件集相交；其余关系宁缺勿滥）。

## 4. Phase 4：伏笔管理（设计要点）

五态：setup / reinforcement / misdirection / payoff /
unresolved。条目来源：线索问题（setup）、物件发现（setup）、
被 concealed 的事实（setup）；同一伏笔被再次提及且未推进
→ reinforcement；bluff 类 claim 指向的假线索 → misdirection
（必须有 bluff 证据）；payoff 只认三级证据链的第三级——
**发现（setup）≠ 证实（reinforcement/证实事件）≠ 解决
（问题被直接证据回答）**，payoff 仅当关联问题 answered
（直接证据）时成立，与 Phase 1 起一贯的诚实口径一致。

## 5. Phase 5：三级规划（设计要点）

- StoryOutline：由 ThreadView + 弧线 + 冲突合成，确定性组装，
  标题/主题句可 LLM 装饰（标 interpretation）。
- ChapterOutline：按「戏剧功能段落 + 故事线进展 + 场景完整性」
  切分——复用 Phase 5（V0.3）的弧线阶段段落为骨架，但章节
  边界允许跨阶段微调以保场景完整；每章带 emotional_curve
  （由 tension 序列采样，确定性）。
- SceneOutline：**不按 tick/地点机械切**——以「参与者集合变化
  + 地点变化 + 目标转移」三信号的合取切分戏剧场景；每场定
  viewpoint（该场知识变化最大者）、scene_goal（由主导线索
  问题生成）、opposition、turning_point（场内 importance
  峰值事件）、next_scene_hook（未答问题或未解决冲突）。

## 6. Phase 6：Writer 与质量（设计要点）

- 内容分级：F1 事实（必须映射 event_ids，沿用现 Audit 查）；
  F2 叙事表现（衔接动作/场景调度，不新增事实，段落标 F2）；
  F3 创作补充（心理/环境/文学细节，段落标 F3 且在 trace 中
  逐段登记，成稿附「创作补充清单」）。旧 Writer 的
  quote/paraphrase/connective 保留为 F1 内部模式。
- 写作流程：按 SceneOutline 逐场写（Scene Writer），人物
  台词过 Character Voice 检查（人物档案 + 该人物历史台词
  样本为据），逐章过 Continuity Reviewer（确定性：知情、
  时序、重复）与 Literary Reviewer（LLM，评价者与 Writer
  不同 prompt 角色且不得同稿自评——用独立调用 + 固定量表）。
- 写作前九项输入按任务书清单逐项装配，缺项在 trace 中声明。
- Quality：12 维——确定性维度（事实一致性、重复率、伏笔
  回收、知情一致性、时序）由代码算；文学维度（连贯、对话
  自然度、可读性、人物弧线合理性、冲突推进、情绪变化、
  场景完整性、章节节奏）由 LLM 按量表评，输出
  narrative_quality_report.json（分项 + 问题 + 关联 id）。
- LLM 预算：Phase 2 语义标注、Phase 6 写作与评价各自先报
  量级再跑（沿用本项目惯例）。

## 7. 验收场景与回归

三场（酒馆/灯塔/诊所）全程贯穿；灯塔/诊所缺 v03 产物，
Phase 2 前补跑 v03 只读分析（确定性、零额度）统一口径。
每 Phase：新模块单测 + tests_narrative / tests_v02 /
tests_v03 全回归 + world_log 哈希不变断言。
