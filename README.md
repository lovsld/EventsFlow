# Multi-Agent Emergent Story Simulation — V0.1

最小可运行的多 Agent 故事模拟系统。剧情不由中央脚本安排：三个角色是
**三个独立的操作系统进程**，各自持有私人记忆、信念、情绪与计划，只能通过
World Engine 的消息队列感知和影响世界。

## 运行

```bash
cd ~/workspace/multi-agent-story-sim
python3 run.py --ticks 20 --seed 42            # 主实验
python3 -m unittest discover -s tests -v       # 全部测试
```

产物在 `runs/run_seed<seed>/`：
- `world_log.json` — 每周期的客观世界状态、位置、意图、已提交事件、投递记录
- `narrative.md` — Narrator 仅依据客观日志生成的叙事（事后生成，不参与决策）
- `summary.json` — 最终位置、信念与记忆条数汇总
- `characters/<A|B|C>/` — 每个 Agent 的私人文件（state.json / memory.md / beliefs.md）
- `ipc/` — 进程间消息队列的原始文件（可审计每一次感知与意图）

## 架构

```
                ┌────────────────────────────────────────────┐
                │              World Engine（主进程）           │
                │  客观状态 · 时间 · 物品 · 客观事件日志          │
                │  Action Resolver（合法性/冲突/打断判定）      │
                │  Interaction Manager（会话与记录）            │
                │  Event Broker + Perception Engine           │
                │   （同房间=完整事件 / 相邻=只有模糊声 / 否则无）  │
                └──────▲───────────────────────▲─────────────┘
              intent 文件│            perception 文件│
        ┌───────────────┴───┐   ┌────────┴────────┐   ┌───┴───────────────┐
        │ Agent A 进程       │   │ Agent B 进程     │   │ Agent C 进程       │
        │ 奥伦·老板          │   │ 米拉·商人        │   │ 凯尔·士兵          │
        │ 私有 profile/state │   │ 私有 profile/state│  │ 私有 profile/state │
        │ 私有 MemoryManager │   │ 私有 MemoryManager│  │ 私有 MemoryManager │
        │ 独立 RNG           │   │ 独立 RNG          │   │ 独立 RNG           │
        └───────────────────┘   └─────────────────┘   └───────────────────┘
```

模块对照（规格 → 代码）：
- World Engine → `src/world_engine.py`（统一提交，含世界侧环境事件）
- Event Broker → `src/event_broker.py`（唯一的事件→Agent 通道，产出投递审计记录）
- Perception Engine → `src/perception.py`（位置/感官过滤，相邻房间只有 muffled）
- Action Resolver → `src/action_resolver.py`（主动性排序、目标离开导致发言失败、
  同会话第二次发言标记为打断、物品发现只认客观位置）
- Interaction Session → `src/interaction.py`（同处一室自动成会话，含完整记录）
- Memory Manager → `src/memory_manager.py`（Agent 侧；事实/传闻/推测分开记账）
- Agent 大脑 → `src/agent_brain.py`（DecisionProvider 接口，见下）
- 进程入口/编排 → `src/agent_process.py` / `src/orchestrator.py`

## 独立性是怎么保证的

1. 每个 Agent 是单独进程，只打开自己 `characters/<id>/` 目录与自己的收件箱；
   代码路径上没有任何地方读取世界日志或其他 Agent 的文件。
2. Agent 的全部输入是 Event Broker 投递的 perception JSON；它的全部输出是
   一个 intent JSON。世界不替它说话，它也不替世界记账。
3. 客观历史（world_log）与个人记忆（memory.md）是两套文件、两个所有者。
   一个事件发生，不代表任何人知道它——知道与否由投递记录决定，可逐条审计。

## 关于"多模型并行调用"（重要限制）

本沙箱没有可用的模型 API 凭据，因此 V0.1 的决策器是
`RuleBasedProvider`：按人格五维、目标权重、信念与情绪做效用评分，
每进程一个实例、一套独立状态与 RNG。它**不是**"一个中央 AI 依次扮演三人"：
决策发生在三个进程内部，编排进程从不生成台词或行动。

`src/agent_brain.py` 中的 `LLMProvider` 是预留的同接口插槽：接入模型时，
每个 Agent 进程各持一个独立会话/密钥，输入仍只有自己的感知与私人状态，
其余机制（世界、投递、解决）无需改动。V0.2 的首要工作就是接这一层。

---

# V0.2 / V0.2-A / V0.2-B 现状（2026-11-12 更新）

V0.1 的三进程实现保留在 `src/` 作对照基线，不再改动。现行系统是
`v02/` 包：单进程 asyncio 事件驱动、Scheduler 每 tick 统一提交一次
世界步进，角色仍各自持有私有状态与记忆、只能感知被投递的事件——
独立性约束与 V0.1 相同，机制全部换代。

- **决策**：rule（确定性、零外部依赖）/ mock / 真实 LLM 多后端
  （Gemini 默认；DeepSeek/Kimi/Qwen/OpenAI 同一兼容层，`ACP_*`
  环境变量选后端、模型与密钥池）。LLM 只提意图：协议严格校验、
  运行时校验目标归属与行动成员资格，世界层最终裁定。rule 路径是
  **通用候选生成器**（V0.2-B）：候选自目标条件叶反向模板 + 计划步
  模板 + 社交回应 + 基线行动，六因子效用打分且逐候选落盘可审计；
  代码里没有任何按角色或剧情的分支。
- **认知**：结构化预测 → 预测误差 → 信念/信任更新的闭环全程落盘
  可复原（SQLite）；信念带证据列表（来源类型/可靠度/事件 id），
  kind 与置信度由证据派生；任何置信度变化都有审计行。言语感知走
  三条通用通道（命题证据 / 言语行为效应 / 矛盾推断），解释层不
  设立计划。
- **场景即数据**（V0.2-B）：Scenario Definition（地点图、物品能力
  位、claims、台词、目标条件）全部声明在场景/角色数据里，经
  `v02/scenario.py` 校验加载并贯通 World/Broker/Context/Decision/
  Plan；可用行动由 CapabilityRegistry 派生。仓库变体中 keeper 会
  目标驱动地找钥匙（移动到可搜查地点并搜查、找到后目标完成）。
- **目标与计划**（V0.2-B）：四层严格分离——Action 成功 ≠ Step
  完成 ≠ Plan 完成 ≠ Goal 完成。Goal 由 GoalEvaluator 按声明的
  激活/成功/失败条件判定（progress = 条件满足度，优先级真实影响
  效用：交换士兵两目标权重使行为分布整体翻转）；Plan 步由
  StepEvaluator 按 success_condition 判定，连续失败进入 blocked，
  五种恢复策略（retry/replan/suspend/abandon/skip）由 PlanManager
  状态机执行，不再依赖 provider 开口。旧 `plan_steps.py` 标签谓词
  表已删除。
- **实验工具**：种子组、反事实（投递抑制）、STEP1 一致性审计
  （M1 已切换为模板一致性口径）、§6 十五指标聚合
  （`collect_run_metrics()["v02b"]`）；中断可续跑（`--resume`，
  校验 engine_version），LLM 额度耗尽可等待重试（`--loop`）。

运行（V0.2）：

```bash
.venv/bin/python main.py --mode rule --ticks 20 --seed 42
.venv/bin/python -m pytest tests_v02 tests -q
# 173 个测试：166 通过；7 项为预登记停放冲突
# （钉住已废除旧机制的断言，待裁决，见 V0.2-B 报告 §7）
```

规范轨迹：`runs_v02/rule_v02b_seed42`（73 事件，V0.2-B 规范；
V0.2-A 的 `rule_v02a_seed42` 只读保留为上一代基线）。

文档：`docs/V0.2-B_REPORT.md`（B1–B3 实施 + B4 场景 A/B 评测与
决策代码冻结清单）、`docs/V0.2-B_ARCHITECTURE.md`（落地架构与
数据契约、与设计差异逐条）、`docs/V0.2-B_DESIGN.md`（设计）、
`docs/V0.2-A_ARCHITECTURE.md`、`docs/V0.2-A_EXPERIMENT_REPORT.md`、
`docs/V0.2-A_PHASE1_REVIEW.md`、`PHASE_*_REPORT.md`、
`V02A_PHASE3_REPORT.md`、`LOCAL_DEPLOY.md`（本地部署与多后端）。
