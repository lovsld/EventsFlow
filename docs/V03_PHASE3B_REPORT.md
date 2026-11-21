# V0.3 Phase 3b 阶段报告（真注入 A/B）

> 承 Phase 3 报告 §4 的提案，经 Hu 2026-11-21 批准实施。
> 补丁只动 `v02/world.py` 一处；A/B 均为 rule 模式、seed 42、
> 酒馆场景、20 tick。产物：`v03_runs/live_ab_on_seed42/`
> （ON 场完整 run 目录：world_log / run_meta / acp.sqlite3 /
> story_summary / scenes）。

## 1. 补丁（如提案原文实施）

- `WorldEngine.__init__` 增加 `_external_queue` 与公开方法
  `queue_external_event(event)`；
- `step()` 在既有 ambient 生成之后、写 log 之前消费队列：
  每条按世界自己的 event_seq 盖号、盖 tick，作为普通事件
  追加进本 tick 的提交列表；
- 不动 resolve 规则、不动 broker、不动任何决策代码。

**关闭态回归门（先行验证）**：补丁后、队列恒空地重跑规范
轨迹，world_log md5 = `cbf84d263ad01e50fca711328c1e78e2`，
与冻结基线**逐字相同**。全量回归 300 过 + 停放红 P-C4-1
一项（冻结既有），v03 测试 23/23（新增 Phase 3b 三项：
空队列无多余事件、排队事件以世界编号提交且不重复提交、
实跑注入且被感知）。

## 2. Live 驱动（`v03/live.py`）

构造与 runner 新场路径同构（同类、同序、含 checkpointer），
唯一增量是链式 `on_tick_committed` 钩子：记账照旧，另把
已提交 tick 交 SceneRuntime 处理，验证通过的候选经
`queue_external_event` 排队。时序如实：tick T 的投递记录要
到 T+1 才由 broker 算出，故管线有 **2 tick 延迟**（T 的状态
→ T+1 钩子提议排队 → T+2 的 step 提交）。

## 3. A/B 结果

| 对照项 | OFF（规范） | ON（Story Engine 开） |
|---|---|---|
| 总事件 | 73 | 76（+3 注入） |
| 注入提交 | — | t5 痕迹事件 E0022、t10 代理人抵达 E0041、t15 人流场合 E0060，均带 candidate_id 与 causal_basis |
| 注入被感知 | — | 抵达与人流（在 bar）投递到**全部三名人物**；痕迹事件在 back_room，提交次 tick 无人在场，未被投递（V0.2 既有感知语义，非注入问题） |
| 人物事件流 | 基线 | 剔除注入事件后与基线**逐条相同** |
| 发言计数 | 商15 / 兵15 / 老板9 | 完全相同 |
| 线索终态 | 主线 escalating 0.5 等 | 相同 |

第一轮 A/B 还抓出一个真问题并已修正：候选模板最初把抵达/
场合放在 `door`——那是无人站立的地点，事件提交了却无人能
感知（正如规范轨迹里 door 的打铃声同样投递不到任何人）。
已改为人物实际所在的 bar（配置层修正，描述同步改写）。

## 4. 结论（如实）

**注入全链路成立**：生成 → 双验证 → 排队 → World Engine
以自己的编号提交 → broker 按既有感知规则投递。人物自主性
零干预：Story Engine 改人物决定的次数为 0（结构上没有
路径）。

**rule 模式下人物行为不变**：人物感知到了代理人抵达，但
规则型人物的决策不依据 ambient 文本分支——这是 rule 模式
的性质，不是注入失败。要测「人物如何回应压力」，需要
LLM 模式 A/B（人物会读到抵达文本并自主决定理会或不理会），
那属于 Phase 6 对照实验的范畴，且要动 DeepSeek 额度——
留待 Hu 在 Phase 6 时一并决定。
