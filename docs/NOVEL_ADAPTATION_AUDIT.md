# NOVEL_ADAPTATION_AUDIT — 小说自动生成升级 · Phase 0 审查报告

> 审查对象为实际代码与运行产物（非文档转述）。审查日 2026-11-22。
> 结论先行：现有叙事层的**事实保真**是扎实的，但它本质上是
> 「事件流的忠实转述机」，不是「理解故事的创作系统」——任务书
> 列的七个问题全部成立，且大部分有量化证据。升级所需的一半
> 地基已在 v03 存在（不同形状），另一半（人物弧线、NarrativeEvent
> 语义层、F1–F3 创作分级、质量评价）确实从零开始。

## 1. 当前数据结构与处理流程（实测）

```text
world_log.json ──► narrative/observer.py ──► observations.json
（冻结）             （473 行，确定性）          scenes.json / frontier.json
                                                    │
                   narrative/extractor.py ──► storylines.json（E1–E7 边）
                     （526 行，确定性）          titles.json（确定性回退标题）
                                                    │
                   narrative/writer.py ─────► story_A.md / story_B.md
                     （204 行，单次 LLM 调用）    trace_*.json（段落↔事件映射）
                                                    │
                   narrative/audit.py ──────► audit_*.json（保真审计）
                     （125 行，确定性）
```

- **Observer**：逐事件产 observation（obs_id、tick、actors、claim、
  proposition、stance、delivered_to、muffled_for、found、text、
  narrative_role 等 17 个字段）；场景按 session/地点切分；frontier
  记录每人每事实的知情前沿。输入哈希校验，world_log 只读——
  这是全系统最扎实的一层。
- **Extractor**：E1 同题话语链、E2 投递信息传递（含经 Hu 裁定的
  五条口径：动作条款限 assert+addressee、最近源归属等）、E3 问答、
  E4 搜得→使用、E5 否认冲突（酒馆零样本，数据决定）、E6 弱使能、
  E7 搜查序列。强边连通分量 = 故事线，状态 closed/open/broken
  （B1–B3 判据）。**边上没有 confidence、没有统一关系词表、
  没有动机类关系**——E1–E7 是「提取规则编号」，不是语义关系。
- **Writer**：把全部 observations 的文本清单 + 故事线清单拼成
  一个 prompt，单次 LLM 调用产出 {title, paragraphs[{id, mode,
  event_ids, text}]}。mode 只有 quote/paraphrase/connective 三种
  （即任务书的 F1 雏形 + 连接段），**没有 F2/F3 分级、没有大纲
  输入、没有人物档案/弧线/知情状态输入、没有分场景写作**。
- **Audit**：只查保真——引文逐字、主线覆盖、无据段落、时序。
  **没有任何质量维度**（连贯、人物区分、重复率都不测）。
- **v03 侧（并行存在的另一套下游）**：story/（线索状态机、冲突
  severity、候选）、graph/（9 类带证据的边 + 节点认识论分类、
  场景包）、planning/（弧线阶段、章节段落、伏笔登记、连续性
  校验、小说素材导出）。形状与任务书目标高度重叠，但建在
  world event 粒度上，且**从未与 narrative/ 的 Writer 打通**：
  Writer 至今只吃 observations + storylines。

## 2. 已实现的能力（核对属实）

事件观测与知情前沿 ✓｜投递为据的信息传播边 ✓｜故事线提取与
断裂判定 ✓｜引文级保真审计 ✓｜形态 A/B 双稿 ✓｜v03 侧：线索
状态机、冲突 severity 追踪、因果图（证据边）、场景包、弧线
阶段、伏笔登记（问题/物件两类）、连续性校验、素材导出 ✓。

## 3. 尚未实现的能力（对照任务书）

| 任务书要求 | 现状 |
|---|---|
| NarrativeEvent 语义模型（dramatic_function、importance、knowledge/character/relationship/conflict changes） | **无**。observation 是事件的事实投影，没有叙事语义字段 |
| 统一因果词表（CAUSED_BY…PAYOFF_OF）+ 独立因果验证器 | **无**。两套边（E1–E7 与 v03 的 9 类）互不兼容，均无 confidence/verification_status，MOTIVATED_BY / FORESHADOWS / PAYOFF_OF 不存在 |
| 人物弧线（arc type、turning points、OPEN 允许） | **完全没有**。任何模块都不产人物弧线；beliefs_trace 在 v02 侧有原始数据但叙事层未消费 |
| 冲突 tension 与 severity 分离、escalation_history、resolution_conditions | v03 只有 severity 单值 + 状态；tension、历史、解决条件均无 |
| StoryThread 升级（climax_event、turning_points、线间关系 dependent/intersects/…） | v03 线索有 dependencies（单向）与 progress；无 climax/turning_points 记录，无 intersects/parallel 等关系；narrative 侧故事线是连通分量，无问题/质询字段 |
| 伏笔 reinforcement / misdirection 状态，发现≠证实≠解决的区分 | v03 登记只有 question/object 两类与 paid_off/open 两态；无 reinforcement/misdirection；narrative 侧无伏笔概念 |
| 故事/章节/场景三级大纲（Scene Outline 含 viewpoint、scene_goal、opposition、next_scene_hook；场景不按 tick/地点切） | **无**。v03 章节 = 弧线阶段对长驻场景的 tick 切片（Phase 5 自认的近似）；Observer 场景 = session/地点切分——两者都正是任务书明说不够的切法 |
| Writer F1/F2/F3 分级 + Scene/Character Voice/Dialogue/Emotion 分工 + 写作前九项输入 | **无**。单次调用、两种输入、mode 三分只是 F1 雏形 |
| 质量评价 12 维 + narrative_quality_report.json + 评价者与 Writer 分离 | **无**。只有保真审计；N3 的 1–5 分人工评分（Hu）至今未给，质量门实际悬空 |

## 4. 当前事件流不能满足小说创作的具体原因（带证据）

1. **重复是数据与写法的乘积**：酒馆 story_B.md 全文 41 行，
   士兵的同一句台词出现 **14 次**——rule 模式罐头台词经 E1 链
   反复入选，Writer 按事件逐段转述必然复读。没有「同一事实
   只叙述一次」的合并层，也没有重复率指标拦截。
2. **Writer 看不见「谁知道什么」**：frontier 数据存在，但
   build_messages 只拼事件清单与故事线清单——人物认知错误
   与人物区分度无从谈起，因为输入里根本没有知情状态、
   人物档案与弧线。
3. **没有叙事重要性排序**：73 个事件在 prompt 里等权排列，
   高潮与过渡同等篇幅，节奏平淡是结构必然。
4. **因果只到「信息传递」为止**：动机、伏笔、回收没有边
   类型，Writer 无法沿因果链组织情节，只能按时间排队。
5. **场景切分与戏剧单元错位**：session/地点切分把吧台 20 tick
   切成一个场景，场景内无目标、无对立、无转折点标注，
   Scene Writer 无从写起（任务书第十条的担忧属实）。

## 5. 建议的目标架构

```text
world_log ─► Observer（不动）─► observations
                              ├─► [新] narrative/models/ NarrativeEvent 语义层
                              │      （确定性规则标注 + LLM 语义补充，Pydantic 校验）
                              ├─► [新] narrative/causal_graph.py 统一词表边 + 验证器
                              │      （适配 E1–E7 与 v03 边为输入证据，不推翻）
                              ├─► [新] narrative/character_arc.py 人物弧线
                              ├─► [扩] 线索/冲突视图（读 v03 story_state 为据，
                              │      在 narrative 侧建 ThreadView/ConflictView）
                              ├─► [新] narrative/setup_payoff.py 伏笔五态
                              ├─► [新] narrative/planner/ 三级大纲
                              └─► [扩] Writer：大纲驱动、分场景、F1/F2/F3 标记
                                     └─► [扩] Audit：保真（不动）+ [新] quality.py 12 维评价
```

原则：全部新增模块只读现有产物；narrative/ 与 v03/ 两套既有
代码零修改（仅允许新增适配读取）；旧产物格式不变、仍可读。

## 6. 复用 / 扩展 / 新建判定

- **直接复用**：observer.py、extractor.py、audit.py、llm_client.py、
  v03 全部产物（story_state、event_graph、scene_packages、
  novel_export）作输入证据。
- **扩展（新增文件、原文件不动）**：Writer（新增 writer2 或
  creative 模式模块，旧 write_form 保留）、Extractor 边（新增
  统一词表映射层，不改 E1–E7 本身）。
- **新建**：narrative/models/、causal_graph.py（含验证器）、
  character_arc.py、setup_payoff.py、planner/（story/chapter/
  scene/continuity）、quality.py。
- **不做**：不改 v02、不改 v03 代码、不重跑模拟、不动冻结轨迹。

## 7. 数据迁移风险

- 旧 narrative_runs 产物字段稳定，新增模块按「缺字段降级」
  读取（老 run 没有 v03 产物时，ThreadView/ConflictView 退化
  为仅由 observations 推导，并在输出中标注证据等级下降）。
- LLM 语义标注必须带 interpretation 标记与 confidence，
  与 objective fact 物理分开存储（任务书 3.2），审计器要能
  按 evidence_level 过滤。
- 三个验收场景中灯塔/诊所没有 v03 产物（只有酒馆有），
  Phase 2 起需先为这两场补跑 v03 只读分析（零额度、确定性），
  或接受降级口径——建议补跑，成本低且口径统一。
