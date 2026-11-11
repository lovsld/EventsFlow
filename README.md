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
