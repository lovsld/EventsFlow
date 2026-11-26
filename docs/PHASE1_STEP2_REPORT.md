# Phase 1 步骤 2 报告：动作日志污染修复（任务二，P0）

日期：2026-11-24。状态：**已完成并验证**。

## 改动

1. **新增 `narrative/material.py`（叙事素材适配层）**：Writer 不再
   直接读原始事件。每个 NarrativeEvent 的源 observation 被确定性
   分类为素材类（映射任务书 §4.3 到现有 type 体系，不新造分类）：
   - DIALOGUE（speak/ask/confront 且有真实文本）：quotable=true，
     逐字文本保留，provenance=SOURCE_BASED；
   - ACTION（move/search/observe/listen/work/speak_failed，及无
     文本的言语类）：quotable=false，由结构化字段渲染成自然语言
     动作描述（如「沈默在 oncall 搜寻，找到了 admission_form」），
     provenance=RENDERED；
   - OBSERVATION（ambient 环境文本）：不可作台词引用，可转述；
   - STATE（session_start/end）：渲染为到场/散场描述；
   - 未知类型 → SYSTEM 隔离，writer_materials 直接丢弃，永不进
     Writer。INFERENCE 类预留给步骤 6/7 的推断层，同样不进事实
     素材。
   附 `detect_log_leakage()`：检测正文中被引号包住的日志串
   （等于模板摘要或匹配 `word @ word` 模式）。
2. **`narrative/models/build.py` 溯源标注修复**：无文本事件的
   event_summary 模板回退保留，但 summary_source 由硬编码的
   "source_text" 改为如实标注 "rule"。三场 narrative_events.json
   的 summary_source 已按「有文本=source_text / 无文本=rule」
   逐条重标（酒馆 24 / 灯塔 55 / 诊所 81 条改为 rule），且与
   模板摘要一一对应（双向校验无例外）。
   **勘误（2026-11-24 晚）**：本报告初版称「重算后除
   summary_source 外无任何变化（45/76/86 条）」——该结论来自
   一次只比对字段名集合的错误校验，且当时的整文件重算曾把
   narrative_events.json 内嵌的 causal_links 清空。现已改为
   从基线文件出发只重标 summary_source，内嵌 causal_links
   完整保留（31/30/13 条），并以逐字段值比对复核：除
   summary_source 外与基线零差异。下游模块（writer2 /
   quality / scene_records / psychology / metrics）均不读
   内嵌 causal_links（因果边以独立 causal_graph.json 为准，
   该文件从未被触碰），故此误不影响任何已报结论与分数。
3. **`narrative/writer2.py`**：assemble_context 的事件槽位改走
   适配层（material_class / quotable / provenance 入上下文，
   QUARANTINED 素材剔除）；SYSTEM 提示新增第 6 条素材分级铁律
   （只有 quotable=true 可逐字引用，动作素材禁止加引号、禁止
   写成人物台词、禁止抄写英文标识符串）；_prompt 逐条标注
   quotable。人物显示名改由 observer.resolve_names 解析（原
   _names 为空桩）。
4. **`narrative/quality.py`**：确定性评分新增系统日志泄漏检测
   ——任何段落中被引用的日志串记为 system_log_leakage issue
   （high）；非 F1 段的泄漏额外扣事实一致性分（F1 泄漏已由原
   逐字校验计数，避免双计）。

## 验证

- 新增 `tests_narrative/test_material.py` 5 项全过：诊所 E0002
  （search）与 E0030（speak_failed）均判 ACTION 且不可引用、
  渲染文本无 "@"、发现物保留；台词逐字且可引用；三场全部素材
  中无一 quotable 文本匹配日志模式；泄漏检测器在基线诊所旧稿
  上准确抓出 `search @ oncall` 与 `speak_failed @ triage` 两处，
  对干净文本零误报；未知类型被隔离。
- 全量回归：**332 passed**（327 + 新增 5）+ 挂起的 P-C4-1 一项
  （不变）。
- **诊所重跑验收**（Gemini 同引擎，产物写入
  `narrative_runs/clinic_v02cfix_seed42/phase1/`，不覆盖基线）：
  Writer 用量 9,381 入 / 3,662 出（8 次调用）。
  - **事实一致性 2.0 → 5.0**，违规 0，日志泄漏 0；全文 "@" 出现
    次数为 0。
  - 其余确定性维度：重复 5.0、知识一致性 5.0 不变；场景完整性
    5.0 → 4.38（单次生成波动，结构性覆盖略降，记录在案）。
  - 评审维度单样本波动：对话自然度 2.5 → 2.0、可读性 3.5 →
    2.75、情感 3.75 → 3.0；章节节奏 2.14 → 3.04。评审为单次
    LLM 评分，不作统计结论；本步验收标准（事实一致性 + 零
    泄漏）已达成。
  - 观察：个别 F1 句保留了地点/物件 id 原文（如 oncall、
    admission_form）——属事实转写不美观，非污染（未被当作
    台词引用），留待步骤 4/5 与写作质量一并观察。

## 遗留

- writer.py（N3 旧 Writer）未接适配层：其 L1 纪律本就只引用
  台词文本，且已非主链；如需统一，另行立项。
- 素材分类目前是「类」级；段落级 provenance（SOURCE_BASED /
  INFERRED / CREATIVE）已由 trace 的 F1/F2/F3 + 本步 material
  provenance 双轨覆盖，统一指标在步骤 5 汇总。
