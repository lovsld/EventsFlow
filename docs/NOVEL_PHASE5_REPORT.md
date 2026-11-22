# NOVEL_PHASE5_REPORT — 故事/章节/场景三级规划

> 2026-11-22。产物：三场 upgrade/plan.json（story + chapters
> + scenes + validation）。回归见正文末。

## 实现（narrative/planner/）

- **scene_planner**：戏剧场景切分三信号——地点变化、在场
  人物集合变化（world_log 逐 tick positions）、剧情静默
  间隔 >3 tick；外加转折断点：当前场景已积累 ≥6 个剧情
  事件时，一个重要性 ≥0.8 的发现/披露/对峙事件开启新场。
  <2 个剧情事件的微场景并入前场。环境/移动事件是间质
  材料，不成场。每场定 viewpoint（该场知识变化最多者）、
  scene_goal（主导线索的中心问题）、opposition（关联冲突
  的 core_issue）、turning_point、next_scene_hook。
- **chapter_planner**：章节在「高潮/回收场之后、主导线索
  切换时、满 4 场时」收束，场景完整性不破。每章带
  opening/ending 的已知事实计数、turning_point、未答问题
  与 emotional_curve（主导线索 tension 序列）。
- **story outline**：主题/标题/中心问题取自故事配置（不
  发明）；key_turning_points = 人物弧线转折点 ∪ 线索
  climax；ending_state 由线索状态逐条拼装——开放的线索
  在大纲里保持开放，原文如此。
- **continuity_validator**：四项确定性检查——章节事件全序
  单调、人物不得断言未学到的事实（知识前沿检查）、
  重要性 ≥0.8 的事件恰好被一个场景覆盖、大纲不得把开放
  线索写成已解决。

## 本阶段最重要的发现：种子信念缺口（已修复）

灯塔校验初版报出一条错误：守塔人在 t6 断言「今晚有船」
却无学习记录。追查到人物 state.json：该信念的证据是
`seed:ship_passing_tonight`（置信 1.0、亲历类）——它是
**开局种子信念**，叙事层此前完全没有消费这份数据。修复：

- 新增 narrative/initial_knowledge.py：从各 run 的
  characters/*/state.json 提取 seed 证据信念（三场都有：
  酒馆商人的 mayor_business_deal、士兵的 investigation_order
  等此前也漏记），校验器与人物弧线统一改用「配置 ∪ 种子」
  的初始知识。
- 我为灯塔/诊所写的故事配置同步补齐 initial_knowledge
  并重跑 v03 补跑（酒馆配置与 v03 产物属 V0.3 封存范围，
  不动）。补齐后三场校验 **0 错误**。

## 实测

| run | 场景 | 章节 | 校验错误 | 备注 |
|---|---|---|---|---|
| 酒馆 | 5（2/9/7/13/9） | 5 | 0 | 吧台长场被转折断点切开（初版是一场 38 事件） |
| 灯塔 | 10 | 4 | 0 | 种子信念修复后通过 |
| 诊所 | 8 | 4 | 0 | |

全量回归 324 过 + P-C4-1 挂起一红（与升级前一致）。

## 已知近似（如实）

- scene_goal 的主导线索按「相关事件重叠数」选，酒馆吧台
  场景选中了商人利益线而非主线——重叠计数对高频闲谈线
  有偏好；Phase 6 写作时以 ThreadView 的 main 标记校正
  即可，本阶段不为此引入加权魔法数。
- emotional_curve 是 tension 的采样，不是情绪测量；
  章节标题目前是序号（第 N 章），文学标题属 Phase 6 的
  LLM 装饰层，需另行报预算。
- 章节收束规则中的「满 4 场」是篇幅护栏，不是戏剧规律。
