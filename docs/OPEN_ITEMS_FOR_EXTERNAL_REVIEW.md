# 待外部评审的两个遗留项（供其他 AI 判断）

背景：EventsFlow 第一阶段验收有两项需要判断，项目所有者
本人不做判断，转交外部评审。本文件自带全部所需材料指针；
仓库内证据文件均已上传。评审结论请按文末格式返回，将被
原样记录进验收报告（并标注评审来源）。

## 遗留项一：叙事文本抽样评分（原「人工抽样」）

材料：
- docs/PHASE1_HUMAN_SAMPLING_PACK.md（15 段抽样说明与评分表）
- narrative_runs/human_sampling_pack.json（15 段全文，
  每段含出处：run / 章节 / 场景 / 段序 / F 等级 / event_ids，
  scores 字段为空待填）
- 段落来源小说全文（可对照上下文）：
  narrative_runs/rule_v02c4_seed42/phase1/trace_v2.json、
  narrative_runs/c5_lighthouse_seed7/upgrade/trace_v2.json、
  narrative_runs/clinic_v02cfix_seed42/phase1/trace_v2.json

请对每段按 1–5 分评五维：自然度、人物一致性、情节推进、
信息重复、事实一致性；并标注问题类型（机械复述 / 对话不
自然 / 日志·标签泄漏 / 无依据事实 / 心理说明书式旁白 /
其他）与简短说明。最后给出总体观察：高频问题有哪些。

诚实声明（评审者须知）：原验收要求为人工评分。外部 AI 的
评分将以「外部 AI 抽样评审」名义记录，不会冒充人工评分；
该验收项的状态由所有者据评审结果再定。

## 遗留项二：F3（创作补充）是否应设下限

背景：小说段落分三级——F1 事实复述、F2 叙事呈现、F3 创作
补充。现行预算（narrative/f_budgets.json）：F1 ≥ 0.30、
F2 ∈ [0.40, 0.55]、F3 ≤ 0.20，无 F3 下限。

事实：Step 2a 的预算执行环 A/B（同事件流，证据在
narrative_runs/rule_v02c4_seed42/phase2_accept/
f_ab_summary.json 与 docs/PHASE1_FINAL_ACCEPTANCE_REPORT.md
§2）中，ON v2 为达标把 F3 全部转成 F2，结果 F3 = 0.0——
合规，但意味着成书没有任何创作补充段落。

问题：F3 是否应设下限（如 0.05）？请给出建议值或「不设」
的结论，并说明理由（文学功能与系统目标的权衡）。注意：
F3 段落受铁律约束——不得新增事实、不得冒充事件记录，
其作用是氛围、衔接与可读性。

## 返回格式

遗留项一：逐段 scores（五维）+ problem_tags + note，
外加一段总体观察。
遗留项二：结论（设下限及数值 / 不设）+ 理由。
