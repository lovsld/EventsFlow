# NOVEL_PHASE3_REPORT — 人物弧线 + 线索/冲突视图

> 2026-11-22。产物：三场 upgrade/character_arcs.json 与
> upgrade/story_views.json。回归 316 过 + P-C4-1 挂起一红。

## 人物弧线（narrative/character_arc.py）

数据源分层：信念学习取 NarrativeEvent 层（客观）；置信度
变化与目标状态取 acp.sqlite3 的 belief_changes / goal_changes，
明确标 L-AUX 旁证，不混入事实层。

**转折点门槛**（本阶段核心纪律）：学到线索核心事实只是
「冲击」，必须再有下游使用——后续事件与该学习事件存在
因果祖先关系，或人物亲自转述该事实——才记 turning point；
未过门槛的记入 diagnostics 的 impact_without_followthrough，
不删除、不冒充。

| 场景 | 人物 | 弧线型 | 转折点（事实 → 下游使用数） |
|---|---|---|---|
| 酒馆 | soldier | unresolved/open | 匿名信存在 → 1；镇长侵吞 → 10 |
| 酒馆 | owner | unresolved/open | 商业机会 → 1；镇长侵吞 → 4 |
| 酒馆 | merchant | **flat_arc** | 无（学到 1 个事实，行为未围绕新知重组） |
| 灯塔 | 三人 | unresolved/open | 船期/雾号事实均有下游使用（协作型） |
| 诊所 | doctor_shen | unresolved/open | 病人将至 → 2；药品补齐 → 3 |
| 诊所 | nurse_lin | **flat_arc** | 无（学到 2 个事实均无下游使用——与其补药链断裂的处境一致） |
| 诊所 | porter_wu | unresolved/open | 病人将至 → 2 |

价态弧线型（positive/negative/tragic 等）规则层**不自动
判定**，全部保持 open——这是任务书「不能因为发生一件事
就认定人物完成成长」的执行结果，不是功能缺失。
emotional_changes / value_changes 留空并在产物 notes 中
声明原因：涌现层没有情绪轨迹记录，叙事输入无确定性来源。

## 线索视图（narrative/story_views.py）

- ThreadView 以 v03 线索为主干，合并 narrative 故事线状态，
  diagnostics 里双口径并列（v03 progress 与 narrative line
  state 各自保留，附 calibre_note 说明动作型闭环为何会
  让 progress 低估）——Phase 2 发现的分歧被制度化保存，
  不平均、不掩盖。
- climax_event 取相关事件中重要性最高的对峙/披露/发现类
  事件（推断级，来源可查）；turning_points 取重要性前三。
- 未答问题逐条保留在 unresolved_questions；任何状态都不
  自动 resolved（测试断言）。
- 线间关系只落 dependencies（v03 已有）与交集判定，
  其余关系宁缺勿滥。

## 冲突视图

- severity 沿用 v03；**tension 独立计算**（0.5 × v03
  escalation + 0.5 × 相关事件对峙密度），两数分开存——
  酒馆实例证明必要性：C003 severity 0.4 但 tension 0.0
  （声明过的冲突从未实际发作），C006 同理（0.39 / 0.01）。
- escalation_history 以对峙事件 tick 序列呈现（事件派生，
  非 severity 历史——v03 未存后者，如实标注）。
- resolution_conditions 由关联线索的未答问题生成
  （「问题 X 被直接证据回答」形式）。
- 灯塔、诊所冲突为 0：v03 检测器在这两份最小配置下未
  检出（无声明冲突、目标对立不足）——如实记录，不硬造。

## 未完成 / 后续

- 弧线的情绪/价值观维度需涌现层提供轨迹数据，属主聊天
  涌现层议题，本层不代劳。
- ThreadView 的 intersects/parallel 等关系只做了
  dependencies，其余留待 Phase 5 规划时按需补。
