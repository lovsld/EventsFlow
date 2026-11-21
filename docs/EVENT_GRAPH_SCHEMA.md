# EVENT_GRAPH_SCHEMA — 叙事事件图谱格式（V0.3 Phase 4）

产物：每场运行一份 `event_graph.json`（`v03/graph/`）。

## 节点 GraphNode

节点 = 已提交事件，一一对应 world_log，不增不删。

| 字段 | 含义 |
|---|---|
| event_id / tick / type / location / actors | 世界原样 |
| proposition / stance | 经场景 claims 富化的话语命题与姿态（无则 null） |
| epistemic | 认识论分类（见下） |
| scene_id | 该事件所属场景（Phase 3 登记，无则空） |

认识论分类（§11 要求区分事实层级）：
`objective` 世界提交的客观事件 ｜ `observed` 说话人亲历/亲见
（搜得、experienced 断言）｜ `reported` 经他人投递转述 ｜
`rumored` 内容本身是传闻级信念（物品 reveals 的 hearsay）｜
`prior_knowledge` 初始私有知识 ｜ `unverified` 无来源断言 ｜
`false_statement` 明知为真却否认的陈述。

## 边 GraphEdge

`source`/`target` 均为 event_id；每条边必带 `rule`（推导规则
编号）与 `evidence`（证据字典）。无证据不立边。

| relation | rule | 语义与证据 |
|---|---|---|
| enables | G-ENABLE | 搜得事件使搜得者后来能说出该事实（首次断言） |
| causes | G-CAUSE | A 的断言经投递被 B 学到，B 后来首次复述（投递记录为据） |
| reveals | G-REVEAL | 断言把事实首次披露给至少一名新听众；source 为事实的建立事件 |
| escalates | G-ESCALATE | 同命题的对峙链：confront 与其前/后的异方断言相连 |
| contradicts | G-CONTRA | 带命题的否认指向先前异方断言（deny → assert） |
| conceals | G-CONCEAL | 知情者的否认/拒绝答复掩盖其知识来源事件 |
| prevents | G-PREVENT | 拒绝事件阻断 2 tick 内未答的 ask（refuse → ask） |
| resolves | G-RESOLVE | 线索问题被直接证据回答：回答事件 → 该线索首个相关事件；若回答事件本身就是首个相关事件（问题由配置提出、开线即答），不立自边，答案记于线索状态 |
| depends_on | G-DEPENDS | Story Engine 注入事件依赖其 candidate 的 causal_basis 事件 |

## 场景包 ScenePackage（`scene_packages.json`）

每包对应 Phase 3 登记的一个场景：地点、起止 tick、状态、
参与者及加入/离开时刻、事件 id 列表、每位参与者在场景
开始/结束时已知的事实集合、关联线索及其终态、跨场景传播
的进/出记录、戏剧功能标签（由场景内事件与边推导：
discovery / confrontation / revelation / escalation 等）、
结局摘要。任一事件至多属于一个场景包。
