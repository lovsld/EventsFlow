# Phase 3 Step 1 审计：人物表达差异化与叙事文本质量

日期：2026-11-26。方法：以当前代码与当日产物为准。
配套：PHASE1_FINAL_ACCEPTANCE_REPORT（含 §6 外部评审补记）、
PHASE2_INTEGRATION_REPORT、PHASE2_STEP1_AUDIT。

## 1. 八个问题（逐条回答，附证据）

**Q1 第二阶段人物状态是否真正进入 Writer？——是，已验证。**
character_state 视图 → planner 场景 character_state 字段 →
writer2 assemble_context → 提示词「人物状态与本场约束」段。
证据：tests_narrative/test_phase2_integration.py 6/6；酒馆
Phase 2 版真实生成（质量均分 3.92，事实/知识 5.0）。

**Q2 Writer 是否具备人物表达差异化机制？——基本没有。**
提示词里人物维度只有三样：voice_samples（每人 ≤3 条原始
台词样本）、弧线转折事实、Phase 2 的状态段（目标/情绪/
知识约束）。没有表达策略层：state.json 里现成的人格数值
（caution/curiosity/honesty/impulsiveness/sociability）、
principles、relationships 的 trust 值都不进 Writer——
character_state 视图未收录 personality/principles，
relationships 的 trust 虽在视图里但 writer2 不渲染。
外部评审的实证：人物差异不鲜明、句式跨人物雷同；
对话自然度 Phase 2 版仅 2.0。

**Q3 是否已有对话重复检测或语义去重？——有三层，但都在
「识别」层，生成层只有软指令。**
(1) semantic.py 合并组：逐字重复组（MG-V）+ 事实接力组
（MG-R），writer2 据此给事件标 role=repeat，提示词要求
「不要重写、只叙述一次」；(2) scene_records.py 事后检测
无价值重复段（前两场小说均为 0）；(3) quality 的
repetition 维（确定性重复率，Phase 2 版 4.95）。缺口：
没有「重复处置策略」——压缩为摘要、用指代、只保留关键
人物表达、保留戏剧性重复——全靠提示词一句软要求，模型
经常把重复确认写成完整对话（外部评审发现 1、2）。

**Q4 是否已有 voice profile？——没有。**
只有原始台词样本（voice_samples）。没有从设定与行为
归纳的表达倾向（长度、直接程度、言语行为分布、对不同
关系对象的变化）。可用的原料都在：state.json 人格数值 +
principles（L-CTX 静态设定）、observations 的实际言语
行为（可统计每人的 ask/assert/confront 分布、平均台词
长度、提问占比）、relationships 的 trust（可支撑关系
条件化）。归纳须标来源、样本不足不下结论（见 Step 2）。

**Q5 重复台词来自事件流还是 Writer？——主要是事件流，
Writer 放大其中一类。**
量化（semantic 统计）：酒馆 39 条言语事件中 36 条落入
重复组，去重后仅 15 条唯一；灯塔 34→16；诊所 16→9。
逐字重复组规模惊人：士兵的侵吞台词 ×10、守塔人的雾笛
询问 ×7、阿贵的船讯询问 ×6——这是涌现层罐头台词，是
事实（确实说了），不是 Writer 编的。Writer 侧的放大是
另一类：把重复确认写成完整往返对话、台词说完情绪后
旁白再复述一次（外部评审发现 1）。Phase 1 的 scene
records 证明小说段落级「无价值重复」为 0——问题不在
段落复制，在对话处理方式与情绪复述。

**Q6 哪些重复必须保留、哪些可压缩？——判据已可操作化。**
必须保留：首次出现；带来新信息/新立场的重复；造成
知识变化的重复（有人因此学到事实）；冲突升级中的
重复（confront 链、压力递增——戏剧性重复）；构成主线
的重复盘问（如士兵的例行询问是其调查行为本身）。
可压缩：同一事实的后续逐字重述且无人因此学到新东西、
立场无变化、关系无变化——压缩为叙事摘要或指代，但
event_ids 必须留在 trace 中（现行 trace 机制支持：
段落 event_ids 可为空时以场景 event_ids 兜底追溯；
压缩段应显式携带被压缩事件的 ids）。

**Q7 F 预算执行器对第三阶段的影响？——有一个真实风险。**
执行环的转化目标是「每章前 N 个 F3 段」，纯位置顺序，
不看价值：可能把高价值 F3 转成 F2，却留下一段复述型
F3。Phase 3 §5.5 的担忧成立。修法很小：转化目标按
冗余度排序（与本章 F1 段的相似度、情绪复述模式），
最冗余者先转，独特 F3 保留。软目标 0.05 与豁免规则
（2026-11-26 已上线）不受影响。

**Q8 N3 的 L1 审计是否允许本阶段的表达优化？——允许，
边界清晰。**
L1（NARRATIVE_LAYER_DESIGN §6.2）：引语须逐字；转述须
保持 proposition 与 stance；三禁：全知内心、虚构动机、
虚构场景。任务书 §4.3 的十条与 L1 完全同向（去标签、
自然转述、压缩重复确认、以摘要呈现重复行为、保留必要
逐字引语）。注意：audit.py 审计的是旧 writer.py 的 A/B
稿，不覆盖 writer2；writer2 的保真由 quality 确定性维
（事实一致性含引语/泄漏检查）+ material 隔离保障，
Phase 3 须在回归中继续跑这两条线，并给压缩后的段落
保留 event_ids 以维持追溯 100%。

## 2. 模块现状映射

| 能力 | 现有模块 | 现状 | Phase 3 缺口 |
|---|---|---|---|
| 人物状态 | character_state.py | 已进 Writer（目标/知识/情绪/冲突） | 未含人格、principles、trust 的表达层信息 |
| 表达样本 | writer2 voice_samples | 原始台词 ≤3 条 | 无倾向归纳、无关系条件化 |
| 重复识别 | semantic.py / scene_records.py / quality.repetition | 识别完备 | 无处置策略层（压缩/指代/保留的判定与指令） |
| 对话生成 | writer2 _prompt | 状态段 + 软性「只叙述一次」 | 无逐事件处置指令、无承接/回避/打断的表达策略 |
| 心理呈现 | writer2 规则 + scene_verification 启发式 | 禁说明书旁白（提示词）+ 事后计数 | 情绪复述（台词后旁白再说一遍）无专门抑制 |
| 节奏/密度 | writer2 一句软要求 | 「篇幅与重要性成比例」 | Planner 无信息密度信号，场景长度平均化 |
| F 协同 | fbudget + writer2 执行环 | 比例达标、软目标/豁免已上线 | 转化目标不按价值/冗余排序 |
| 质量评估 | quality.py 13 维 | 对话自然度是评审维 | 无「人物表达差异」独立度量（需新增，不改 13 维均分口径） |

## 3. Step 2 最小改动方案（待 Hu 批准）

修改文件（不新增模块）：
1. character_state.py：新增 voice 段——来源三路并标注：
   配置人格/principles（L-CTX）、observations 言语行为
   统计（样本 ≥5 才给倾向结论）、trust 关系表。仍是只读
   视图，不新建系统。
2. planner/scene_planner.py：场景增加 density 信号
   （新事实数+知识变化数+冲突事件数 → 高/中/低）与逐事件
   重复处置指令（keep / compress / refer），判定用 Q6
   判据 + semantic 合并组 + already_revealed。
3. writer2.py：提示词渲染 voice 段与处置指令；对话规则
   增补（去「人名：」标签式输出、承接/回避/试探的策略
   示例、台词已表达的情绪旁白不得复述）；场景篇幅按
   density 分级；执行环转化目标改按冗余度排序。
4. scene_verification.py：新增两项确定性检查——文本层
   逐字重复台词计数、情绪复述模式计数（扩展现有启发式）。
5. quality.py：新增独立报告的 voice_differentiation
   度量（确定性：人物间台词长度/句式分布的区分度 +
   评审维单列），不并入 13 维均分，保证与 Phase 2 可比。

新增文件：仅实验脚本（scripts/ 下），无新模块。

## 4. 实验设计与成本估计（Step 4，Gemini）

基线：酒馆 Phase 2 版已有；灯塔、诊所 Phase 2 版需补
生成（2 部）。对照：三场 Phase 3 版各 1 部；酒馆 Phase 3
再重复 1 部报方差；§6.4 反事实约 4 组单场配对。
估计总量 ≈ 130k 入 / 46k 出（与 Novel Upgrade 的
200k/60k 批准量级相当），Step 3 开工前再确认。

## 5. 结论

缺口集中在「表达层」而非「状态层」：状态已接通，缺的
是 voice 归纳、重复处置策略、密度信号与 F3 价值排序。
全部可在现有模块内完成，不需要新的人物系统。
