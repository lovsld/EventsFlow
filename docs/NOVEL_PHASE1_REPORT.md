# NOVEL_PHASE1_REPORT — NarrativeEvent 数据模型（升级 Phase 1）

> 2026-11-22。设计：docs/NARRATIVE_UPGRADE_DESIGN.md（全阶段）。
> 本阶段范围：Schema 定型 + 确定性投影参考实现。语义解释
> （合并、LLM 标注）是 Phase 2，不在本报告冒充完成。

## 交付

1. **docs/NARRATIVE_UPGRADE_DESIGN.md**：六阶段总设计，本阶段
   模型为详设，后续阶段为要点（含每阶段验收口径）。
2. **narrative/models/narrative_event.py**：NarrativeEvent 全量
   字段（任务书第五节逐项）：source_event_ids 可多源（合并的
   结构前提）、dramatic_function 16 类多标签可空、
   knowledge/character/relationship/conflict 四类变化记录、
   causal_links（统一词表 11 类枚举）、evidence_level 四级、
   interpretation_confidence。事实与推测分离：NE 本体恒为
   objective，解释性字段各自带 source 与 confidence。
3. **narrative/models/story.py**：配套 Schema 一并定型——
   CausalEdge（统一边结构）、CharacterArc、ConflictView
   （severity 与 tension 分开存）、ThreadView、SetupPayoffRecord
   （五态）、StoryOutline / ChapterOutline / SceneOutline、
   QualityReport（evaluator 字段强制声明评分者，结构上禁止
   Writer 自评无标注）。
4. **narrative/models/build.py**：确定性投影（observations →
   NarrativeEvent，1:1）。戏剧功能规则保守：search 首次发现
   → discovery；事实首次向新听众断言 → revelation；confront
   → confrontation；已断言命题的后续断言 → escalation；
   ask → setup；session 开场 → introduction；move/observe/
   ambient 不标（场景素材）。knowledge_changes 只来自投递与
   搜得（learned/reinforced + via），信念类 character_change
   同步记录。importance 为文档化启发式（类型基值 + 线成员
   +0.2 + 新知 +0.2 + 发现/披露 +0.1，封顶 1.0）。

## 验证（三场实测）

| run | NE 数 | 有功能标注 | knowledge_changes | 功能分布要点 |
|---|---|---|---|---|
| 酒馆 | 73 | 39 | 53 | discovery 1（信件搜得）、revelation 3、confrontation 6、setup 10 |
| 灯塔 | 104 | 42 | 18 | setup 20、discovery 2、revelation 2 |
| 诊所 | 107 | 29 | 12 | discovery 2、introduction 13 |

- 测试：新增 tests_narrative/test_models.py 4 个（Schema 强制
  source 可追溯、三场投影数与 observations 一致、酒馆 E0008
  的 discovery 与信念学习、投影确定性字节一致、world_log
  哈希不变）全过；全量回归 307 过 + 挂起的 P-C4-1 一红
  （与升级前一致）。
- 产物：三场 narrative_runs/<run>/upgrade/narrative_events.json。

## 已知粗糙处（Phase 2 处理，不掩盖）

- **escalation 过标**：酒馆 18 个——「已断言命题的后续断言」
  把罐头台词的转述也算了升级。转述≠升级，Phase 2 语义层
  要区分（转述应归合并/重复处理，对峙语境才算升级）。
- character/relationship/conflict changes 投影阶段只填信念
  学习一项，其余留空（Schema 已定型，Phase 3 补全）——
  空列表是设计内的合法状态。
- event_summary 目前是原文截断（标 source_text）；LLM 摘要
  是 Phase 2 的可选装饰，需另行报预算。

## 未完成 / 待 Hu

- Phase 2 开工前置：灯塔/诊所补跑 v03 只读分析（设计 §7 已
  建议，确定性零额度）——随 Phase 2 一并做即可，无需单独批准。
- Phase 2 的 LLM 语义标注若要启用，另行报预算；规则版因果图
  不依赖它。
