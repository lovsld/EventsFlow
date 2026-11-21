# V0.3 Phase 4 阶段报告（Narrative Event Graph + Scene Packages）

> 任务书 §10.5/§11/§19 Phase 4。新增 `v03/graph/`（models /
> builder / packages）与格式文档 `docs/EVENT_GRAPH_SCHEMA.md`；
> V0.2 零改动（world.py 补丁为 Phase 3b 已批准件），轨迹不变。

## 1. 交付物

| 类别 | 内容 |
|---|---|
| 事件图谱 | `EventGraph`：节点 = 已提交事件（不增不删），边 = 9 类关系，每条边带推导规则编号与证据字典 |
| 边推导 | `GraphBuilder`：8 条确定性规则（G-ENABLE/CAUSE/REVEAL/ESCALATE/CONTRA/CONCEAL/PREVENT/RESOLVE/DEPENDS），全部以投递记录、搜得事件、线索账本、候选 causal_basis 为据 |
| 认识论分类 | 节点级 epistemic：objective / observed / reported / rumored / prior_knowledge / unverified / false_statement（§11 的事实层级区分） |
| 场景包 | `build_scene_packages`：每包含参与者进出时刻、事件列表、每人场景起止已知事实、关联线索终态、传播进出、戏剧功能标签 |
| 产物 | 规范场与 ON 场各一份 `event_graph.json` + `scene_packages.json`（存 v03_runs 对应目录） |
| 测试 | `tests_v03/test_phase4.py` 新增 4 项；v03 合计 **27/27**；全量回归 304 过 + 停放红 P-C4-1 |

## 2. 酒馆规范场图谱（73 节点）

立边 8 条：reveals 2、enables 1、escalates 5。主线在图上一眼
可读：E0008 搜信 —enables→ E0022 士兵首次说出贪腐（同时
—reveals→ 把它披露给新听众）；随后 E0030→E0032→E0036→
E0038 与 E0061→E0063→E0064 两段对峙升级链。认识论分布：
objective 37、rumored 24（信的 reveals 本就是传闻级）、
reported 10、unverified 2。

三类边在本场为 **0，且是数据决定的 0**：contradicts /
conceals 需要带命题的否认，而本场景 denial claim 的命题为
null（与叙事层 N2 当年 E5 零样本同一事实）；prevents 需要
refuse 事件，本场没有。规则本身由合成测试覆盖（含知情者
否认 → false_statement 节点标记）。causes 同理为 0：老板的
贪腐知识是初始私有知识（prior_knowledge），商人只问不述，
士兵是源头——没有「经投递学到再复述」的实例，不是推导遗漏。

推导过程中修掉两个真问题：① reveals 一度对同一事实的每次
复述都立边（24 条噪声）——改为只对「首次披露给新听众」立
边；② 说话人会出现在自己的投递名单里，导致「从自己处学到
自己创造的提议」的假 causes——已排除自投递。resolves 在本
场为 0 的原因也已查明并写入格式文档：信件问题由开线事件
E0008 本身回答，自边不立，答案记于线索状态。

## 3. ON 场图谱（76 节点，Phase 3b 的注入场）

比规范场多 5 条 depends_on：3 条注入事件逐条连回其
causal_basis（如代理人抵达 E0041 依赖 E0023/E0031/E0033
三条贪腐断言）——「故事压力从哪来」在图上可审计，这是
§7 因果链要求在叙事层的落点。

## 4. 场景包抽查

back_room 包：士兵知识 at_start 为空、at_end 为
{anonymous_letter_exists, mayor_embezzled}，戏剧功能
discovery，propagations_out 10 条（他把事实带进 bar 场景）
——与 Phase 3 传播账本、N2 提取结果三方一致。全场事件
覆盖无重复：一事件至多一包（测试断言）。
