# V0.3 Phase 5 阶段报告（Narrative Planning & Export）

> 任务书 §12/§19 Phase 5。新增 `v03/planning/`（arc_manager /
> planner / continuity / exporter）与 `docs/NARRATIVE_EXPORT_GUIDE.md`；
> V0.2 零改动，轨迹不变。规范场与 ON 场各导出一份完整素材包。

## 1. 交付物

| 类别 | 内容 |
|---|---|
| Arc Manager | 六阶段弧线（setup/development/escalation/climax/aftermath/resolution），转换全部由事件结构触发并记录证据：线索发展过开场/首次传播 → 发展；线索 escalating 或冲突 severity ≥0.6 → 升级；主线对峙阶梯（图谱 escalates 边）≥3 → 高潮；高潮后 4 tick 无发展 → 余波；主线 resolved → 收束。**不用 tick 计数定阶段** |
| Narrative Planner | 章节 = 弧线阶段段落，对长驻场景做 tick 切片（场景是地点登记不是戏剧单元——初版按场景起始 tick 分章把全场压成「铺垫」，已按段落切片重做）；关键事件 = 图谱边端点 |
| Setup/Payoff | 问题型伏笔（线索问题，planted/payoff tick）+ 物件型（发现 → 首次披露）；未回收如实 open |
| Continuity Validator | 四类校验：时间线完整性（tick/事件号单调）、知识使用先于学习（已知事实的 unverified 断言 = 错误；basis-free 命题豁免）、物件重复发现、线索依赖倒置 |
| Novel Exporter | `novel_export.json`（全量包）+ `novel_export.md`（可读渲染，事件文本逐字引用） |

## 2. 酒馆规范场的弧线（实测）

t1–4 铺垫 → t5 发展（主线事件数过开场）→ t6 升级（危机线
escalating）→ t10 高潮（主线对峙阶梯满 3）→ 直到 t20 仍在
高潮段。**余波与收束未抵达**——这场 20 tick 的模拟本就没有
走到结局，弧线如实停在高潮，不硬凑完整结构。

章节四章与阶段一一对应，关键事件逐章在案（升级章含 E0022
士兵首次当众说出资金问题）。伏笔 6 条：信件存在问题 t3 回收、
信件物件 t6 经披露回收；其余 4 条（贪腐真伪、写信人、交易、
镇长是否知情）全部 open——与线索终态（主线 escalating
0.5）一致。连续性校验：**0 错误 0 警告**（两处 unverified
断言均为 basis-free 的提议/唬人，按口径豁免）。

ON 场（Phase 3b 注入场）导出结果同构，高潮章关键事件多 2 条
（注入的抵达事件进入图谱边集），弧线转换点与规范场相同——
rule 模式下注入未改变行为，弧线自然不变，与 3b 结论一致。

## 3. 测试

`tests_v03/test_phase5.py` 新增 4 项：弧线转换事件驱动且
未抵达阶段如实、章节 tick 覆盖完整无缺口、连续性校验能抓
四类埋设违规（事件号倒序/重复发现/未学先用/依赖倒置）、
导出包完整（73 节点、4 场景包、0 错误）。v03 合计 **31/31**；
全量回归 308 过 + 停放红 P-C4-1（冻结既有）。

## 4. 剩余事项

- Phase 6（对照实验）是任务书的收官：多 seed × 目标组合 ×
  provider × Story Engine 开/关，产出 V03_EXPERIMENT_REPORT
  与 STORY_METRICS 等五件套。其中 LLM 场次（人物对注入压力
  的真实回应）需要 DeepSeek 额度决定——报告里会先给预算
  量级再跑。
- 尚缺的 V0.3 文档：ARCHITECTURE_V03.md、STORY_ENGINE_DESIGN.md、
  SCENE_SYSTEM_DESIGN.md（可随 Phase 6 一并收官，或单独一轮
  文档补齐）。
