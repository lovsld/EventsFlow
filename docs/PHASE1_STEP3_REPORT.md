# Phase 1 步骤 3 中期报告：决策质量（任务一，P0）

日期：2026-11-24。状态：**评估/判断/记录层已完成并验证；
实时干预实验待预算批准（见 §4）。**

## 已完成：Story Progress Judge（`v03/decision_quality.py`）

只读评估模块，挂接在 tick 边界之外（回溯模式），不写世界状态、
不替角色决策。输入：run 的 world_log + scenario + story config
（+ 可选 acp.sqlite3 的 goal_changes；缺失时 goal 维度如实报
unknown，不猜）。

- **故事状态评估**（§3.2）：每 tick 末快照——各角色已知事实
  （种子知识 = 配置 initial_knowledge + 角色 state 文件中
  `seed:` 来源的信念）、开放/已提出/已回答问题、故事进度、
  人物位置。
- **六维推进判断**（§3.3）：信息（断言被投递给尚不知情者 /
  搜寻发现）、目标（acp goal_changes）、冲突（confront / deny
  投递）、关系（首次投递接触对）、状态（首次到访地点 / 发现）、
  悬念（主线命题被提及）。每项推进必须引用 event id（goal 维
  度引用 acp 日志本身，不伪造 event id）。
- **问题状态机与 v03 同一诚实口径**：搜寻发现物的 fact_belief
  是直接证据 → 问题「已回答」；reveals 信念带 reliability<1，
  属传闻级 → 只标「已提出」，永不标已回答。（初版曾把 reveals
  误判为已回答，测试前自查发现并修正——酒馆贪腐问题在规则
  run 里是 addressed 而非 answered，与 V0.3 结论一致。）
- **决策记录**（§3.5）：每 tick 一条，含 decision_id、状态
  摘要、六类结果型（new_event / advance_conflict /
  character_choice / reveal_information / hold_observe /
  trigger_diagnosis）、六维证据、理由串；回溯模式下 expected
  与 validation 如实标注 n/a。
- **诊断**：连续 ≥3 个 tick「有事件但零维度推进」→
  ZERO_PROGRESS_STREAK 诊断（含未决问题清单）；整场另有
  mainline_zero_engagement 标志（问题状态零推进）。

## 验证（V0.3 Phase 6 既有 20-tick 实验回溯判定）

| run | 推进 tick | 问题推进 tick | 最终进度 | 诊断 |
|---|---|---|---|---|
| llm_on_seed42 (DeepSeek) | 6/20 | **0** | **0.0** | 2 次 ZERO_PROGRESS_STREAK |
| llm_on_seed7 (DeepSeek) | 6/20 | **0** | **0.0** | 2 次 |
| rule_on_seed42 | 20/20 | 2 | 0.4 | 0 |
| rule_on_seed7 | 20/20 | 2 | 0.4 | 0 |

LLM 两场：73 个事件、32 次发言，但没有任何一个故事问题被
「已提出」以上推进——外围维度（首次接触/首次到访）在动，
主线纹丝不动。这正是任务书 §3.1 的病症，现在它会被系统
**自动诊断**，而不是混在「正常运行」里。新增
`tests_v03/test_decision_quality.py` 3 项全过；判定产物已写入
两场 seed42 的 run 目录（decision_quality.json）。全量回归
见收尾数据。

进度口径说明：本判定器的进度 = 全部 5 个问题（answered 1.0 /
addressed 0.5 / open 0）的均值；V0.3 的 0.5 是主线单 thread
口径。双口径并存、各自标注，不混用。

## 未完成：实时干预实验（待批准）

判定层只解决「能识别」。任务书验收还要求一轮**实时** 20-tick
实验：Judge 在 tick 边界在线运行，诊断触发时向下一 tick 注入
压力/机会（沿 V0.3 Story Engine 的既有通道，不改 decide()
核心），验证零推进能被打破或至少被实时拦截并记录。这需要
真实 LLM 调用（DeepSeek，矩阵规模参照 V0.3 Phase 6：LLM 组
4 场 20 tick 约 86 万入 / 4.3 万出；本轮可缩减为 2 场对照
（ON 干预 vs OFF 基线）约一半量级）。**开工前等 Hu 批准
预算与场次**。

另：provider 层 20–25% 决策解析失败是独立病灶（与推进判断
解耦），建议在实时实验前顺带修结构化输出重试/降级，属
provider 层小改，不动 decide() 核心——一并等批准。

## 补记（2026-11-24 晚）：实时实验的执行与终止

Hu 于 16:51 批准实时实验（DeepSeek 2 场对照 + provider 解析
修复）。执行结果如下：

- **provider 解析修复已完成**：Phase 6 LLM run 的 14/60（23%）
  决策失败全部源于同一类错误——模型在 plan_update 内多塞
  schema 未声明的字段（desc / step / actor），被 extra=forbid
  严格校验拒绝。已在 openai / gemini 两个 provider 的
  `_sanitize` 中加入未声明字段剥离（顶层 / action /
  plan_update / recovery / new_steps 五处；校验本身仍严格，
  语义错误照样失败）。`tests_v02/test_provider_sanitize.py`
  4 项通过。
- **干预机制已离线验证**：`v03/intervention.py` +
  `v03/live.py` 的 extra_hook 接点（默认 None，旧行为不变）。
  以 Phase 6 两场历史回放喂 hook：LLM 场如期触发经双验证的
  干预事件（≤3 次），规则场零触发。`tests_v03/
  test_intervention.py` 2 项通过。
- **实时对照未完成**：OFF 臂跑到第 11/20 tick 时被运行时
  重启杀掉（已花 109k 入 / 7.6k 出）；ON 臂未开始。半成品
  保存在 `v03_runs/phase1_live/off_seed42/`（11 tick，
  不作对照数据）。副产物证据：这 11 tick 的 33 次决策
  **失败 0 次**（无修复时按 23% 应有约 7 次）——解析修复
  在真实流量上初步有效，但样本小，不作统计结论。
- **Hu 于 18:56 决定就此停**：不再重跑实时对照。本任务的
  最终状态为——判定/诊断层完成并回溯验证；解析修复完成
  并初步验证；干预机制完成并离线验证；实时 A/B 对照
  **未验证**（中断后经 Hu 决定终止）。驱动脚本
  `scripts/run_phase1_live.py` 已修正完整性判断（world_log
  满 20 tick 才算完成），日后如需重跑可直接使用。
