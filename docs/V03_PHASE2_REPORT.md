# V0.3 Phase 2 阶段报告（Causal Event Generation）

> 任务书 §7/§8/§19 Phase 2。全部新增于 `v03/story/eventgen.py` 与
> Story Engine 的 propose 管线；V0.2 零改动，规范轨迹 md5 不变。
> 本阶段的提交路径做到「影子模式」为止（见 §4 的范围说明）。

## 1. 交付物

| 类别 | 内容 |
|---|---|
| 候选模型 | `EventCandidate`（§7.2 全字段：source_type / triggering_conditions / causal_basis / dramatic_functions / uncertainty / validity_requirements / fingerprint） |
| 生成器 | `EventGenerator`：三条确定性模板规则（见 §2），无随机源、无 LLM |
| 因果验证 | `CausalValidator`：基底事件必须全部存在于已提交历史；关联线索必须存在；触发条件对当前故事状态**复核**而非信任声明 |
| 世界规则验证 | `WorldRuleValidator`：地点/人物必须存在于世界快照；指纹去重（同候选永不重复提议） |
| 戏剧评估 | `DramaticEvaluator`：七维分开报告（causality / thread_progress / conflict_impact / goal_impact / information_change / novelty / coherence）；加权 rank 只用于排序，不删史、不改果、不作闸门 |
| 管线 | `StoryEngine.propose()`：生成→双验证→评估→排序；冷却期节制（默认 5 tick 至多一批）；通过与拒绝都进 change_log 并可落库（event_candidates 表，schema v2） |
| 产物 | `v03_runs/rule_v02c4_seed42/candidates.json`（影子回放全记录）+ story.sqlite3 已存候选 |
| 测试 | `tests_v03/test_phase2.py` 新增 6 项；v03 合计 **14/14**；全量回归 291 过 + 停放红 P-C4-1 一项（冻结既有） |

## 2. 三条生成规则（全部要求因果基底）

| 规则 | 来源 | 触发 | 候选 | 基底 |
|---|---|---|---|---|
| G-A 压力 | story_driven | 线索 escalating 且其敏感事实已被 ≥2 个不同说话人断言 + 线索配置声明了 pressure_event | external_arrival（如镇长代理人抵达酒馆门口） | 那些断言事件 |
| G-B 机会 | story_driven | 线索 dormant、有未答问题、且已有知情者 + 配置声明 opportunity_event | opportunity_notice（门口人来人往的场合） | 该线索的历史事件 |
| G-C 后果 | world_driven | 可移动物品被他人搜得且原持有者未获投递该发现 | trace_event（原地点留下被翻动痕迹） | 那次搜查事件 |

候选只提议「发生了什么环境事实」，从不提议「某人知道了什么」
或「某人将做什么」——人物如何回应由人物自己决定（§2.2）。

## 3. 影子回放结果（酒馆规范轨迹 20 tick）

共生成 10 个候选，**通过 3、拒绝 7（全部因指纹重复——同一压力
只提议一次，符合 §7.5 节制）**：

| 候选 | tick | 类型 | 基底 | rank |
|---|---|---|---|---|
| EC001 | 3 | trace_event（后屋被翻动的痕迹） | E0008 士兵搜得匿名信、老板未获投递 | 0.533 |
| EC002 | 8 | external_arrival（镇长代理人抵达） | E0022/E0030/E0032 三次贪腐断言、≥2 说话人 | 0.700 |
| EC004 | 13 | opportunity_notice（门口人流场合） | 秘密线 dormant，其历史事件 E0008 | 0.593 |

另有两项对照实证：20 tick 全静默的合成世界里提议数为 **0**
（无触发不注水）；world_log 在影子回放前后 md5 相同
（`cbf84d26…`）——Story Engine 结构上没有改世界的路径：
`to_world_event()` 只返回一个普通 ambient 事件 dict，提交与否
是 World Engine 一侧的事。

## 4. 范围说明（如实）

- **实时提交未做**：候选真正注入一场正在跑的模拟，需要
  runner/scheduler 的 tick 级回调，那正是 Phase 3（多场景
  运行时）要动调度层的地方。本阶段按 Phase 1 同样口径：
  管线完整、影子验证，实时挂钩随 Phase 3 落地。
- character_driven 来源本阶段没有独立生成器：人物驱动的事件
  本就由人物自己的行动产生（已在世界里），不需要 Story
  Engine 代拟——生成器只补 world/story 两源。此口径请 Hu
  在本门确认。
- LLM 候选生成未启用：模板规则覆盖了酒馆场景的三类触发；
  §16 的 LLM 通道（Pydantic 校验 + 同管线验证）可在后续
  Phase 作为第四条生成规则接入，验证与节制不变。
