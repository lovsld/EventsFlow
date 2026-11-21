# 叙事层 Phase 1 设计稿（Narrative Observer / Storyline Extractor / Script Writer）

> 本稿是 ACP Story Simulator「第二步：故事流 / 叙事层」的 Phase 1 设计，
> 由故事流专责对话撰写。大纲要点已于 2026-11-13 经 Hu 确认，2026-11-14
> Hu 批准按大纲写全稿。本稿只做设计，不含实现；实现须在本稿获 Hu 批准后
> 按 §8 分阶段启动，每阶段停工汇报。
>
> 事实来源：`docs/V0.2-C_EXPERIMENT_REPORT.md`、`docs/V0.2-C_ARCHITECTURE.md`、
> 以及对 `runs_v02/` 产物的第一手抽查（2026-11-13，抽查范围见 §2.2）。
> 本稿所有数字与结构描述均可按 §2.2 的方法复核；未核实处明确标注为待核。

---

## 0. 门控状态（如实记录，不作宣布）

任务书原定纪律：Narrative Observer 在 V0.2-C「通过」后解阻。

V0.2-C 的终局状态是：

- §十一计分：**达成 3 / 部分达成 5 / 未达成 2**（共 10 项）。
- 未达成两项（C3 阶段门、诊所主链全环合龙判据）共享同一底层判据，
  即同一个实质缺口的两次记账。
- 诊所全链判据已经 Hu 裁决**登记为未达成**（定案，非待办）；
  P-FIX-1 / P-FIX-2 为登记在案的已知问题，不再开诊所修复轮。
- 冻结基线：32 个决策文件 sha256 清单；冻结时测试 243（242 过，
  唯一红为停放项 P-C4-1）；全量快照已推送 GitHub（lovsld/EventsFlow，
  HEAD `4d53b32d`，1,893 文件）。

**V0.2-C 未全过 §十一，本稿不宣布、也不暗示其「已通过」。**
第二步由 Hu 于 2026-11-13 指示新建本专责对话而启动——这是以指示方式
启动，不是对门控的追认。Hu 批准本设计稿时，视为对「以 §十一 3/5/2、
诊所判据登记未达的现状启动叙事层」这一事实状态的一并确认；若 Hu 不
确认，本稿其余部分不生效。

三项停放裁决（P-C2-1、P-C2-2、P-C4-1）属主对话与 Hu 的 Reminders，
与叙事层无关，本稿不引用其结论、不等待其裁决。

## 1. 定位与红线

### 1.1 两步路线图中的位置

Hu 于 2026-11-11 定版的两步路线图：

- **第一步（涌现层，已收官）**：多智能体自主决策产生事件流。只按三条
  判据评判——事件流正常运行、角色行为合理、动机与行为一致；**不按
  故事质量评判**。
- **第二步（本稿，叙事层）**：把事件流加工成引人入胜的故事。故事质量
  在这一步是正当判据，但必须按 §6.4 的可操作标准评判，且永远不得
  以牺牲事实保真为代价换取（见 §1.2 红线 3）。

### 1.2 红线（本稿一切设计的约束，任何阶段不得突破）

1. **只读下游**：叙事层只能读取已完成 run 的产物。它不存在任何写回
   通道：不写 `runs_v02/`、不写涌现层代码与数据、不调用 runner、
   不触发新 run（§7 隔离复跑是验收手段，由 Hu 在场批准后另行执行，
   不属于叙事层程序的职能）。
2. **只叙述已发生的客观事件**：成稿中的事实性内容，其唯一权威来源是
   `world_log.json` 中已提交的事件（以 `event_id` 为锚）。不得虚构
   事件、不得补写没发生的事、不得把未送达的言语写成已送达、不得把
   角色的主观信念写成客观事实。
3. **可追溯优先于故事质量**：当「好看」与「可指回事件 id」冲突时，
   先保追溯。成稿的每个事实段落都必须能映射到事件 id 集合（§6.3）；
   映射不上即视为缺陷，不以文学理由豁免。
4. **不反向干预**：叙事层的输出（观察、故事线、成稿、评分）不得回流
   为任何 Agent 的输入，不得影响任何未来 run 的决策。涌现层将来若要
   消费叙事产物，须另立设计、另经 Hu 批准，不在本稿范围内。
5. **因果不重排**：叙事加工可以选材、聚焦、分场、调节节奏，不得改变
   事件的时序与因果方向（§6.2 的形态约束）。

### 1.3 范围与非目标

**在范围内**（三件套，源自 V0.2-A 任务界定，当时明确推迟、从未启动）：

1. **Narrative Observer**：只读观察事件流，把客观事件整理成叙事可用
   的观察记录（§4）。
2. **Storyline Extractor**：从观察记录中抽取故事线——线索、因果链、
   人物弧（§5）。
3. **Script Writer**：把故事线写成成稿文本（§6）。

**非目标**：

- 不修改涌现层任何决策代码、场景数据与角色数据；设计若认为必须改，
  须另行提出、经 Hu 批准后由主对话执行，不在本对话动手。
- 不生成新 run（§7 的隔离复跑除外，且只为验收）。
- 不做发布、朗读、多媒体化；不做跨 run 的「世界编年史」拼接（多 run
  之间无因果关系，拼接即虚构——如将来要做，另立设计）。
- 不使用 Hu 向朋友借的 Gemini 钥池；叙事层如需 LLM，用 DeepSeek
  （vault surrogate），且每次真实调用前先在本对话报预算量级（§6.5）。

## 2. 可用资产与第一手事实

### 2.1 资产清单

| 资产 | 位置 | 用途 |
|---|---|---|
| 酒馆规范轨迹 | `runs_v02/rule_v02c4_seed42/`（73 事件，world_log md5 `cbf84d26…`） | Observer/Extractor 首要验证对象；金标准之一 |
| C5 灯塔双 seed | `runs_v02/c5_lighthouse_seed42/`（93 事件）、`c5_lighthouse_seed7/`（104 事件） | seed7 全链闭合，Extractor 正例金标准；双 seed 对照 |
| 诊所修复轮双 seed | `runs_v02/clinic_v02cfix_seed42/seed7/` | Extractor 负例：seed42 主链物理性断裂，必须报断不许补完（§5.5） |
| 仓库真实 LLM 双 seed | `runs_v02/llm10_deepseek_warehouse_seed42/seed7/`（81 / 73 事件量级） | 验证叙事层对 LLM 模式 run 同样适用 |
| 场景数据 | `scenarios/{tavern_letter, warehouse_key_world, scenario_c_clinic, lighthouse_fog}.json` | claims/object 定义；run_meta 内嵌同版本快照，以快照为准（§3.2） |
| 文档 | `docs/V0.2-C_EXPERIMENT_REPORT.md`、`docs/V0.2-C_ARCHITECTURE.md`、`docs/V0.2-B_ARCHITECTURE.md` | 事实与契约依据 |

### 2.2 world_log 结构（2026-11-13 第一手抽查）

`world_log.json` 是按 tick 的数组（酒馆 20 条、灯塔 24 条）。每条含
`tick / time / positions / events / deliveries`：

- `events[]`：事件对象，字段随类型而异。全集（遍历全部 `runs_v02/`
  统计）共 15 种类型，字段如下（`event_id / tick / type / actors /
  location` 为公共字段）：

  | 类型 | 专有字段 | 叙事角色（初步分类，§4.3 定版） |
  |---|---|---|
  | `speak` | `claim, topic, text, interrupted, session_location` | 情节承载 |
  | `ask` | `claim, topic, text, interrupted, session_location` | 情节承载 |
  | `confront` | `claim, topic, text, interrupted, session_location` | 情节承载 |
  | `search` | `found[]` | 情节承载 |
  | `show` | `object` | 情节承载 |
  | `refuse` | `target, topic` | 情节承载 |
  | `speak_failed` | `target, topic, reason` | 情节承载（失败尝试，§4.4） |
  | `move` | `from, to` | 结构 |
  | `session_start` / `session_end` | `session` | 结构 |
  | `ambient` | `topic, text` | 纹理（文本为世界层原文，可引用） |
  | `observe` / `listen` | `target, topic` | 纹理 |
  | `work` | `target, topic` | 纹理 |
  | `wait` | `target, topic` | 纹理 |

- `deliveries[]`：每 tick 每 Agent 一条，含 `delivered_event_ids` 与
  `muffled_event_ids`。**这是「谁在何时实际收到什么」的客观记录**，
  是叙事层推导信息传递的唯一合法依据（§4.5）。
- `speak` 事件的 `text` 是世界层已渲染的完整台词（含说话人名前缀），
  引用时逐字为准，不得改写后冒充原话（§6.2 尺度 L1）。
- 目标/信念/计划的变化**不在** world_log 里，只在 sqlite（见 §3.3）。
  world_log 是纯客观事件流——这正是它能作唯一权威源的原因。

### 2.3 既有 Narrator 的定位

每个 run 目录已有一份 `narrative.md`，由 `v02/runner.py` 的
`render_narrative()` 在 run 结束时生成：逐 tick 罗列客观事件的清单式
文本。它是涌现层的附属产物，定位为叙事层的**基线对照**（§6.4 的
对照组）：叙事层的成稿必须在可读性上显著超过它，同时在保真度上不
低于它。它不是三件套的任何一件，本稿不修改、不复用其代码。

## 3. 输入契约

### 3.1 权威源分层

叙事层的数据源严格分三层，层级即权限：

| 层 | 来源 | 权限 |
|---|---|---|
| **L-OBJ（客观层，唯一权威）** | `world_log.json` 全文；`run_meta.json` 的出处字段与内嵌场景快照；`summary.json` 的统计字段 | 成稿事实、故事线边的**唯一**合法来源 |
| **L-AUX（旁证层）** | `acp.sqlite3` 的 `goal_changes / belief_changes / plan_transitions / predictions` 四表 | 只许 Extractor 用作因果解释与断裂检测的旁证；每处使用必须在产物中标注 `aux` 来源与表名/行 id；**永不以事实身份进入成稿** |
| **L-CTX（背景层）** | 场景 JSON 与角色 profile（经 run_meta 快照对齐版本） | 只提供静态定义（claim→命题/立场、object→关联信念、角色名与身份）；不提供任何「发生了什么」的信息 |

三条硬规则：

1. sqlite 一律以 `mode=ro` 打开（URI 参数），且叙事层进程对 run 目录
   只持有读权限路径（实现上以只读方式打开全部输入文件）。
2. L-AUX 与 L-CTX 的内容若与 L-OBJ 冲突，以 L-OBJ 为准并把冲突记入
   产物的问题清单——例如 sqlite 的 `world_events` 表与 world_log 应
   一致，若不一致，记问题、以 world_log 为准，不静默取舍。
3. 角色的私有记忆（run 目录 `characters/*/memory.md`、`beliefs.md`、
   `state.json`）**不读**。它们是 Agent 的主观状态，既非客观事实，
   读取它们写「他心里想」正是红线 2 要禁止的全知视角。人物内心在
   成稿中的合法呈现方式只有 §6.2 的外化规则。

### 3.2 出处与版本对齐

每个 run 的处理开始前，Observer 先写出处记录（provenance）：

- `run_id / scenario_id / seed / mode / engine_version / ticks`，
  取自 `run_meta.json` 与 `summary.json`，两处不一致即停并报问题；
- 输入文件清单及各自 sha256（`world_log.json`、`acp.sqlite3`、
  `summary.json`、`run_meta.json`、`narrative.md`）——同一清单在处理
  结束后复算，用于隔离验证（§7）；
- 场景定义以 `run_meta.json` 内嵌的场景快照为准（它含 `claims` 的
  `proposition / stance / base_reliability` 与 `objects` 的
  `fact_belief / reveals` 等定义）；`scenarios/` 下的同名文件只作
  交叉核对，不一致时以快照为准并记问题。这样保证叙事层解释的永远
  是「这个 run 运行时生效的那一版数据」。

角色显示名（如 owner→奥伦）取自角色 profile 的 `name` 字段；具体
目录（`characters_v02/` 等）随场景而异，N1 开工第一件事是核对每个
验证 run 的名称来源路径并写进出处记录；核不到的角色就用
`character_id` 原样呈现，不猜名。

### 3.3 事件粒度

- **原子粒度 = 单个事件**。观察记录与事件 1:1，不合并、不拆分、
  不丢弃；「不重要」只能以标注表达，不能以删除表达（§4.3）。
- **场景段是聚合视图，不是新事实**：Observer 在原子记录之上给出
  场景段划分（§4.6），段只引用成员事件 id 集合，不产生事件之外的
  内容。
- tick 不是叙事单位：同一 tick 可含多个场景，同一场景可跨多个
  tick。一切引用以 `event_id` 为准，tick 只作排序与定位辅助。

### 3.4 speak_failed 与 interrupted 的定性

抽查实证：失败尝试会进 world_log（灯塔 seed7 有 4 次 `speak_failed`，
全库共 65 次）。定性如下，Observer 与 Writer 共同遵守：

- `speak_failed`：**尝试发生过，内容未送达**。可叙述「X 试图开口，
  但 Y 已经离开」（其 `reason` 字段支持这一粒度），禁止叙述其
  `topic` 所指的内容已被任何人听到——它连 `text` 字段都没有，
  结构上也无从引用。
- `interrupted=true` 的 `speak/ask/confront`：事件已提交、台词文本
  存在；**是否被某人实际接收，一律以 deliveries 为准**：
  在某 Agent 的 `delivered_event_ids` 中 → 该 Agent 听到了；
  只在 `muffled_event_ids` 中 → 该 Agent 只听到动静、未听清内容，
  叙事不得让其「知道」内容；两者皆无 → 未接收。
- 这与涌现层修复轮的契约收紧（「未送达不算发生」，V0.2-C 架构 §1.3）
  是同一原则在叙事侧的对应物：**送达性是事实的一部分，不是可以
  为了顺滑而抹平的细节**。人物在成稿中表现出「知道某事」，其依据
  必须是该人物的 delivered 记录，而不是「这件事在场发生过」。

## 4. Narrative Observer

### 4.1 职责与形态

Observer 是纯确定性程序（无 LLM、无随机）：输入一个 run 目录，
输出该 run 的观察记录集。它做三件事：**转写**（把事件逐条转成统一
结构的观察记录）、**标注**（给每条记录打上确定性可算的特征标注）、
**分段**（给出场景段与信息前沿两个派生视图）。它不判断「故事好不好」，
不做任何文学加工。

### 4.2 观察记录 schema（`observations.json`）

每条记录对应一个事件，字段如下（`*` 为必填）：

| 字段 | 来源 | 说明 |
|---|---|---|
| `obs_id*` | = `event_id` | 与事件 id 同值，不另编号，保证引用零翻译 |
| `tick* / time*` | world_log | 定位与排序 |
| `type*` | 事件 `type` | 15 种类型原样保留 |
| `actors*` | 事件 `actors` | `character_id` 原样；显示名在渲染层按 §3.2 映射 |
| `location*` | 事件 `location` | |
| `claim / proposition / stance` | 事件 `claim` + L-CTX 的 claims 定义 | 言语事件才有；`proposition=null` 的社交性 claim（如 greeting）如实记 null |
| `text` | 事件 `text` | 逐字保存（speak/ask/confront/ambient）；无此字段的类型记 null，不生成替代文本 |
| `found / object` | 事件 `found[]` / `object` | search/show 才有 |
| `delivered_to* / muffled_for*` | 本 tick `deliveries` 反查 | 该事件被哪些 Agent 完整接收 / 模糊接收；事件自身的 actor 视为在场知晓并单独标记 `self` 来源 |
| `narrative_role*` | §4.3 分类表 | `plot / structure / texture` 三档之一，仅为标注 |
| `features*` | §4.3 确定性特征 | 见下 |
| `raw_ref*` | 固定为 `world_log.json#<event_id>` | 回指原文位置 |

`features` 只含可机械判定的布尔/计数值，例如：`opens_session`、
`closes_session`、`first_mention_of_claim`、`first_find_of_object`、
`has_text`、`is_failed_attempt`、`addressee_present`（ask/speak 的
第二 actor 是否同场——由同 tick positions 判定）。**禁止**出现
`important`、`dramatic` 之类需要判断的字段；重要性判断是 Extractor
与 Writer 的事，且它们必须给出依据（§5、§6）。

### 4.3 事件分类（narrative_role）

按 §2.2 表的初步分类定版：`speak / ask / confront / search / show /
refuse / speak_failed` 为 `plot`；`move / session_start / session_end`
为 `structure`；`ambient / observe / listen / work / wait` 为
`texture`。分类只影响 Writer 的选材默认权重（§6.2），不影响记录
的存废与 Extractor 抽边的资格——texture 事件同样可以被边引用
（如 ambient 的天气变化被人物提及）。分类表如需调整，属设计变更，
须经 Hu 批准并在本稿修订记录中登记。

### 4.4 信息前沿（knowledge frontier）

Observer 的核心派生视图：对每个角色、每个 tick，计算其**客观可知
集合** = 截至该 tick、该角色自身行动产生的事件 ∪ 其
`delivered_event_ids` 累积集合（muffled 只记「知有其事」标记，
不计入内容可知）。输出 `frontier.json`：每角色一份按 tick 的事件 id
前缀清单及增量。

用途与边界：

- 它回答「在 tick t 之后，角色 A 有客观渠道知道哪些事件」——这是
  Writer 写人物认知、Extractor 判信息传递边的**上限依据**：任何
  超出前沿的「知道」都是虚构。
- 它**不**回答「A 实际上相信了什么」——那是 L-AUX 的 belief_changes
  的事，只能作旁证（§3.1）。前沿是「可知」，信念是「实信」，两者
  不得混写。

### 4.5 场景段划分（scenes）

确定性规则，优先级从高到低：

1. 有 `session` id 的事件按 session 聚合为一段（session_start 至
   对应 session_end；未闭合的 session 段如实标记 `open=true`）；
2. 不属于任何 session 的 plot 事件，按（tick, location）就近归入
   同 tick 同地点的段；无同伴者自成一段；
3. structure/texture 事件不独立成段，作为段的附属引用挂在时间上
   最近的段上（move 事件若构成 session 参与者的进出场，则挂入
   该 session 段并标注）。

输出 `scenes.json`：每段含 `scene_id`（如 `SC-###`)、起止 tick、
地点、参与者、成员 `event_id[]`（保持时序）、附属引用
`context_event_ids[]`。段划分是组织手段：段内事件的原始顺序与
内容不因分段而改变，段与段之间也不产生任何新关系断言。

### 4.6 Observer 产物与验收

产物（全部落在叙事层自己的目录，§7.1）：`provenance.json`、
`observations.json`、`frontier.json`、`scenes.json`、`issues.json`
（处理过程中发现的源数据问题清单，无问题时为空数组，不省略文件）。

N1 验收门（详见 §8）：事件覆盖 1:1（记录数 = world_log 事件总数，
逐 id 对齐）；delivered/muffled 反查与 world_log 原文逐 tick 一致
（程序化校验）；同一输入重复运行两次，产物逐字节相同（确定性）；
输入文件哈希处理前后零变化（§7）。

## 5. Storyline Extractor

### 5.1 什么算一条线

**定义**：一条故事线是观察记录的一个子集，其成员由 §5.2 的强边
连通，且满足：成员 ≥ 3 个事件、时间跨度 ≥ 2 个 tick、且至少包含
一个 `plot` 事件。不满足的强边连通分量记为 `fragment`（片段），
如实输出、不升级为线。每条线输出：成员事件 id（时序）、边清单
（每条边含类型与判定依据）、参与者、涉及的 claim/物件、状态
（`closed / open / broken`，§5.3）、以及供 Writer 使用的
确定性摘要素材（成员事件的原文摘录集合——不是 LLM 生成的摘要）。

**人物弧**在本设计中不是独立抽取物：它定义为「某角色参与的全部
故事线 + 其信息前沿的增量序列」的视图（`arcs.json` 按角色组织），
不引入 world_log 之外的心理断言。人物弧回答「他经历了什么、他的
可知范围如何扩大」，不回答「他变成了什么样的人」——后者若写入
成稿，必须由 Writer 以事件为据呈现，不由 Extractor 下结论。

### 5.2 因果边类型（全部确定性判定）

边是有向的（早 → 晚），每类边给出可机械复核的判定规则；规则之外
不连边。LLM 不参与边的生成与删除（§5.4）。

| 边 | 名称 | 判定规则（全部条件须同时满足） |
|---|---|---|
| E1 | 同题言语链 | 两言语事件（speak/ask/confront）`claim` 相同或 `proposition` 相同（非 null），tick 递增。仅连相邻提及（前一次→后一次），不全连接 |
| E2 | 信息传递 | 事件 X（speak/ask/show）被角色 A 接收（X ∈ A 的 delivered 集），其后 A 发出言语事件 Y 且 Y 与 X 同 claim/同 proposition，或 A 作出以 X 的 `found/object` 为对象的 show/search 后续动作。边注记中必须写明传递所凭的 delivered 记录 |
| E3 | 问答 | ask 事件 Q 与其后同 session 内的言语事件 R：R 的发出者是 Q 的被问方（actors[1] 或同场其他参与者），且 R 的 claim 的 proposition 与 Q 的 proposition 相同、stance ∈ {assert, deny}。无同 session 证据时，允许 tick 差 ≤ 3 的跨 session 配对并标记 `weak_window=true`（3 tick 取自涌现层待答队列 TTL 的既有语义，非新拍常数） |
| E4 | 搜查—发现—使用 | search 事件 F（`found` 含物件 O）与其后事件 U：U 为 show（`object=O`）、或 U 的 claim 对应的 proposition 等于 O 在 L-CTX 中的 `fact_belief` 或 `reveals[].belief`、或 U 是持有 O 的角色对 O 的再次 search/show |
| E5 | 冲突 | 言语事件 D 的 stance = deny（或 type = confront）且其 proposition 与更早的 assert 事件 A 的 proposition 相同，D 的发出者 ≠ A 的发出者 |
| E6 | 使能（弱边） | move/session_start 使两名角色首次同场，其后 1 tick 内两人之间发生言语事件。弱边只用于场景组织与 Writer 的衔接提示，**不计入**故事线的连通性判定与因果评分 |

E1–E5 为强边。所有边的判定只许使用 L-OBJ + L-CTX（定义映射）；
用 L-AUX 才能成立的边不许生成，只许在 §5.3 的断裂检测中作旁证
注记出现。

### 5.3 线的状态与断裂判定

- `closed`：线内存在终止性事件——E3 的问答已配对完成、E4 的发现
  已被使用、或线内最后一个言语事件是同题链中最终的 assert/deny
  且其后无未答的 ask。判据逐项写进产物，不用「感觉完整」。
- `open`：线未终止，但 run 结束时其末端事件距结尾 ≤ 3 tick——
  链还在进行中就没时间了，如实记 open（例：灯塔 seed42 的回报
  候选已生成而 tick 耗尽——那是涌现层内部状态，叙事层能合法说的
  只有「链未及闭合」）。
- `broken`：线内存在一个**有据的期待落空**：存在未获 E3 配对的
  ask（跨越弱窗口仍无回应）、或 E2 传递在中间角色处中断（收到
  但此后再未发出同题言语/动作直至 run 结束）、且末端事件距结尾
  > 3 tick。可选旁证：L-AUX 显示对应目标已激活而未完成
  （goal_changes），出现时以 `aux` 注记加强断裂判定，但**没有
  旁证时仅凭 L-OBJ 的期待落空亦可判 broken**。

**断裂是合法且必须如实呈现的结论**。Extractor 报断、Writer 写断：
一条在 run 里断掉的线，成稿里就应呈现为悬而未决或戛然而止，
补完它即虚构（红线 2）。这是本设计与「故事好看」之间最硬的
一条接缝，验收时以诊所负例专门检查（§5.5）。

### 5.4 LLM 在 Extractor 中的位置

只许两件事，且都在边与线已由规则确定之后：

1. 给每条线起标题（一行，不含事件外信息）；
2. 给每条线写 ≤ 3 句摘要，输入只许是该线成员事件的观察记录，
   输出每句必须在提交时附其依据的 event_id 集合，无依据句整句
   丢弃并计数。

标题与摘要是**可丢弃的装饰层**：删除它们，storylines.json 的
事实部分（成员、边、状态）完整无损。Extractor 的验收（§5.5、
§8）只验事实部分；摘要的无据句计数作为质量指标报告，不作门。

### 5.5 验证计划（金标准对照）

验证在实现阶段（N2）执行，本稿预注册其方法与判据，不事后调：

1. **正例一（灯塔 seed7，`c5_lighthouse_seed7`）**：终局报告 §3.7
   已给出全链的事件级证据——守塔人断言 `E0026@6`（ship_notice）
   → 维修工同 tick 形成信念并激活 → `@11` 搜得 spare_belt
   （E0044）→ `@19` 维修完成 → horn_report `E0080@20` 回流闭合。
   Extractor 必须抽出一条包含上述关键事件的线并判 `closed`；
   关键事件漏检即不通过（主线召回按关键事件计，须 100%）。
2. **正例二（酒馆规范轨迹）**：匿名信链——`E0008@3` 士兵搜查发现
   anonymous_letter 为起点，其后 letter 相关 claim 的言语链
   （letter_discussion / letter_question / denial 等）应连成主线；
   商业提案链（business_proposal）应成第二条线或片段，不得与
   信链误连（proposition 不同，E1 不应跨连）。
3. **负例（诊所 seed42，`clinic_v02cfix_seed42`）**：终局报告 §4
   的逐环账目——传讯经护工链迟到送达、护士 `@23` 搜到已被医生
   取走药的空柜、restock 物理上不可能完成。Extractor 必须把该线
   判为 `broken` 并把断点定位在搜查落空处附近；判 closed 或抽出
   一条「补货成功」的线即一票否决。
4. **精度抽查**：对正例一、二的全部强边，由人工（实现者逐条 +
   Hu 抽查）核对判定规则是否被正确应用；误边率（人工判不成立
   的边 / 总边数）目标 ≤ 10%。
5. 金标准的人工标注在 N2 开工时先行落盘（标注文件进叙事层目录、
   带标注人注记），**先于** Extractor 的输出生成，保证对照独立。

### 5.6 Extractor 产物

`storylines.json`（线与片段：成员、边及依据、状态及判据）、
`arcs.json`（按角色的参与线与前沿增量索引）、`titles.json`
（LLM 装饰层，独立文件、可整体删除）、`issues.json`。

## 6. Script Writer

### 6.1 成稿形态（三选，附推进次序）

| 形态 | 定义 | 约束 |
|---|---|---|
| A 忠实编年叙事 | 按时序把观察记录写成连贯散文，最小加工 | 与既有 narrative.md 同为编年，但成文而非罗列；用作保真基线与对照组 |
| **B 场景化短篇（主形态）** | 按 scenes 分场，场内保时序，可选材、可聚焦、可调节场间节奏 | **场间与场内均不重排时序**；省略只许省略 texture 与重复 structure，plot 事件的省略须逐个列入省略清单并说明理由；主线（Extractor 判定的线）成员事件不得省略 |
| C 分场脚本 | 剧本格式（场景头、对白、舞台提示） | 舞台提示只许转写事件字段（地点、进出、动作类型），不许发明动作细节 |

推进次序：先实现 A 并通过保真审计（证明管线不丢真、不造假），
再实现 B 并做质量评分；C 不在本轮实现，Hu 点名后另行批准。
三形态共用同一套可追溯附录与审计（§6.3）。

### 6.2 改编尺度（定版：L1）

大纲推荐并经 Hu 批准的大纲包含此项：默认尺度 **L1**。

- **L0**：台词只许逐字引用原文 `text`；叙述只许同义转写事件字段。
- **L1（定版）**：台词可逐字引用（引用须逐字，引号内不得改一字），
  也可转述（转述不得改变 claim 的 proposition 与 stance——肯定
  不得转成否定、试探不得转成断言）；叙述可加入**不承载新事实的
  连接文字**（时间过渡、场景定位复述、已交代信息的指代）。
- **L2（禁用）**：新造台词、新造心理活动、新造细节（天气、表情、
  动作细节等事件字段之外的任何具体描写）。L2 不是「以后放宽」
  的预留档，本稿明确排除；将来若要，须推翻红线 2 重写本稿。

L1 的三条具体禁令（审计可查）：

1. **禁止全知心理**：不得写任何角色的内心活动。角色「知道/不知
   道」某事，只许写到其信息前沿支持的程度，且以行为或言语呈现
   （「他还不知道信的事」是合法的——若信的事件不在其前沿内；
   「他心里盘算着」不合法——无事件依据）。
2. **禁止补动机**：角色为何做某事，只许引用其已说出口的话或
   前后事件的可见因果；L-AUX 的目标/信念可以提示 Writer「此处
   可能有动机」，但成稿不得把旁证写成事实（如「他为了完成补货
   任务而……」不合法；「护士径直去了药房，翻找那个柜子」合法）。
3. **禁止环境造景**：场景描写只许用 ambient 事件的原文信息与
   事件的 location 字段；不得添加未记录的陈设、光线、神态。

### 6.3 可追溯附录与保真审计

Writer 的每次产出是两件套，缺一不交付：

1. `story.md`：成稿正文，按段落编号（如 `¶12`）。
2. `trace.json`：每段的映射——`{paragraph, event_ids[], aux_refs[],
   mode}`，`mode ∈ {quote, paraphrase, connective}`；`connective`
   段（纯连接文字）允许 event_ids 为空，但必须逐段声明，不许混入
   事实句。

保真审计是确定性程序（不是 LLM 自评），逐项检查：

- 每段非 connective 段至少映射 1 个 event_id；映射的 id 必须真实
  存在于该 run 的 world_log；
- 全部引号内文本与 world_log 对应事件 `text` 逐字比对，一致率
  须 100%；
- 主线覆盖率：Extractor 主线成员事件中被成稿段落映射覆盖的比例，
  B 形态须 = 100%（省略清单只许覆盖非主线 plot 事件）；
- 时序单调：段落映射的首事件 tick 序列不得逆序（分场形态下以
  场为单位检查，场间亦同）；
- 无据事实句：由审计标记出「含具体事实断言但映射为空」的段落，
  目标为 **0**；此项靠映射纪律而非语义理解——映射是 Writer 的
  义务，审计只验映射的存在与自洽，语义层的抽查由 Hu 评分时
  人工完成（§6.4）。

### 6.4 评判标准（故事质量在此步为正当判据）

| 维度 | 方法 | 通过线（提议，N3 开工前由 Hu 确认或改定） |
|---|---|---|
| 事实保真 | §6.3 程序化审计 | 无据事实段 = 0；引文一致率 100%；主线覆盖 100%（B） |
| 因果正确 | 审计时序单调 + Hu 抽查段落映射 | 时序逆序 = 0；抽查映射名实相符率 ≥ 90% |
| 可读性 | Hu 按量表对成稿打分：连贯 / 张力 / 人物区分度 / 整体可读，各 1–5 | 四项均 ≥ 3 且均值 ≥ 3.5 |
| 对照增益 | 同 run 的 A 形态成稿与既有 narrative.md 并列，Hu 盲评哪份「更像一个故事」 | B 形态须胜过 narrative.md；A 形态只须不劣于 |
| 断线呈现 | 负例 run 的成稿人工检查 | 断掉的线以未决呈现，无任何补完句 |

量表与通过线是本设计的提议值，写进本稿即为预注册：N3 验收时按
此判定，不事后放宽。若 Hu 批准本稿时改动数值，以批准值为准并
登记在本稿修订记录中。

### 6.5 LLM 使用与预算纪律

- 后端只用 DeepSeek（vault surrogate，`deepseek-flash` 系），
  经涌现层已有的 provider 抽象的同等机制接入；**叙事层代码不
  import 涌现层决策模块**（§7.3），LLM 调用走独立的最小客户端
  （同一 vault 取钥路径，钥不落盘、不进日志、不进产物）。
- 每次批量调用前在本对话报预算量级。量级预估方法：输入为观察
  记录/线素材的 JSON 体量，酒馆量级单 run 约 73–104 事件，
  预估单 run 全流程（Extractor 摘要 + Writer A/B 各一稿）输入
  ≤ 120k tokens、输出 ≤ 40k tokens；实际以调用回执为准、超
  预估 50% 即停并报告。此数字是预估不是实测，N3 开工时以
  第一个 run 的实测校准后续预算。
- 确定性部分（Observer、Extractor 抽边、审计）零 LLM、零成本，
  可任意复跑；LLM 产物（标题/摘要/成稿）必须记录所用模型与
  token 回执进出处文件。

## 7. 隔离验证

### 7.1 产物目录

叙事层的一切产物只写入新目录 `narrative_runs/<run_id>/`（含
`gold/` 子目录存人工金标准）。叙事层不向 `runs_v02/`、`docs/`
（本稿自身除外）、`v02/`、`scenarios/`、`characters*/` 写任何
字节。叙事层代码放在新包 `narrative/`（与 `v02/` 平级），测试
放 `tests_narrative/`。

### 7.2 哈希零变化

每个 run 处理前后，对 §3.2 出处清单中的全部输入文件复算
sha256 并比对：任一变化即该次处理作废、停工报告。此校验是
Observer 程序的内置步骤（处理前清单写入临时区、处理后比对、
比对结果写进产物），不是人工纪律。

### 7.3 依赖隔离

- `narrative/` 包**禁止 import `v02` 的任何模块**（解析 world_log
  只需要标准库 JSON；读 sqlite 只需要标准库 sqlite3）。以一条
  静态检查（grep import 图）作为 N1 验收项之一。
- 反向同样成立：涌现层不 import `narrative/`，本稿不提议任何
  涌现层改动。

### 7.4 开关不改变 run（终极隔离证据）

N1 验收包含一次对照实验（须 Hu 在场或事先批准，因为要动
runner）：同一命令复跑酒馆规范轨迹参数（rule 模式、seed 42、
20 tick）两次，叙事层在场处理与完全不处理各一，两个新 run 的
world_log 须逐字节相同（且与规范轨迹 md5 `cbf84d26…` 一致——
修复轮后已实证该轨迹稳定）。新 run 只作验收用，验收后按 Hu
指示保留或删除，不进 `runs_v02/` 正式序列则改放
`narrative_runs/_isolation/` 并标注用途。

## 8. 分阶段计划与验收

| 阶段 | 内容 | LLM | 验收门（不过不进下一阶段） |
|---|---|---|---|
| N0 | 本设计稿 | 无 | Hu 批准本稿（含 §0 门控状态的一并确认、§6.4 通过线的确认或改定） |
| N1 | Observer 实现 + 酒馆/灯塔两 run 的观察产物 | 无 | §4.6 全部：1:1 覆盖、deliveries 反查一致、同输入双跑逐字节相同、输入哈希零变化、import 隔离检查过、§7.4 对照实验过 |
| N2 | Extractor 实现 + 金标准对照（§5.5） | 仅标题/摘要装饰层，预算先报 | 主线关键事件召回 100%（灯塔）、酒馆双链不误连、诊所负例正确报断且断点定位合理、误边率 ≤ 10% |
| N3 | Writer 实现（A 后 B）+ 保真审计 + Hu 量表评分 | DeepSeek，预算先报、实测校准 | §6.4 表全部维度达线；负例成稿无补完 |

每阶段交付：代码、该阶段产物、阶段报告（含失败与未达项的如实
记录）。每阶段停工汇报 Hu，得到明确继续指示后才进下一阶段——
与涌现层的阶段门纪律相同。

## 9. 风险与未决问题

1. **C5 文档截断的连带影响**：V0.2-C 终局报告 §9 记录了 C5 预注册
   与判定两份文件的落盘截断。本稿 §5.5 的灯塔金标准因此只引用
   终局报告与 run 产物（K12 已由主代理以 run 产物复核），不引用
   两份截断文件的缺失部分；金标准标注时以 run 产物逐事件核对
   为准。
2. **ask 的被问方判定**：言语事件 `actors` 为 [说话人, 受话人]
   是抽查所见的一般形态，但不保证全部历史 run 一致；E3 判定在
   N2 实现时须先对验证集做 actors 语义抽查，若有例外形态，
   先修设计再写代码。
3. **proposition 为 null 的社交 claim**（greeting/smalltalk 等）
   大量存在：E1 不连 null proposition（已在规则中排除）；它们在
   成稿中的作用是人物区分度素材，Writer 可用但不计入主线覆盖。
4. **长 run 的扩展性**：本稿的验证集都是 ≤ 104 事件的短 run；
   叙事层算法均为线性或近线性，可扩展性风险主要在 Writer 的
   上下文长度。超出单次上下文的 run 须按 scenes 分批写作并保持
   trace 完整——该情形不在本轮验证集内，遇到时先报 Hu 再处理，
   不自行降级保真要求。
5. **「好看」的最终裁判是 Hu**：§6.4 的量表只是把判断结构化，
   不替代 Hu 的阅读感受。若量表达线而 Hu 读后认为不成立，以
   Hu 的判断为准，并把分歧记入 N3 报告，作为下一步修订设计的
   输入。

## 10. 修订记录

| 日期 | 内容 | 批准 |
|---|---|---|
| 2026-11-13 | 大纲要点报 Hu，Hu 确认大纲 | Hu（对话确认） |
| 2026-11-14 | 全稿按大纲写成，提交 Hu 批准（N0 门） | 待 Hu 批准 |
| 2026-11-21 | Hu 批准本稿（N0 门通过；§0 门控状态经此批准一并确认），指示开始 N1 Observer；§6.4 通过线未作改动，按稿内提议值生效 | Hu（对话批准） |
| 2026-11-21 | N1 完成（NARRATIVE_N1_REPORT.md，验收全过）；N2 完成待追认：实施裁量五项（E2 行动分句收紧至 assert+受话人、新增 E7 搜查序列边、B2 不作断裂判据、E2 最近源归因、E4 限发现者本人断言），详见 NARRATIVE_N2_REPORT.md §5 | 待 Hu 在 N2 门追认 |
| 2026-11-21 | Hu 追认 N2 五项裁量全部，指示开始 N3 Writer | Hu（对话批准） |
| 2026-11-21 | N3 完成保真侧：四稿（酒馆 A/B、灯塔 B、诊所 B）审计全过、人工抽查无虚构，见 NARRATIVE_N3_REPORT.md；质量量表待 Hu 评分，N3 门未关闭 | 实施记录 |
