"""Writer v2 (upgrade Phase 6; design §6): outline-driven,
scene-by-scene writing with F1/F2/F3 content levels.

F1 factual — must map to event ids, quotes verbatim.
F2 narrative presentation — connective action/staging, no new
     objective facts.
F3 creative addition — psychology, atmosphere, literary detail;
     every F3 paragraph is registered in the trace's creative
     register so a reader can strip them all.

Repetition is killed structurally: merge-group primaries are
written once; repeat members are handed to the model explicitly
marked as repeats to be passed over in at most one clause.
The model is Gemini (gemini_client) per Hu's instruction.
"""
from __future__ import annotations

import json
from pathlib import Path

from narrative.gemini_client import generate as _gemini_generate

CAP_IN = 200_000
CAP_OUT = 60_000

SYSTEM = """你是一名严谨的小说作者，基于结构化大纲与事件记录逐场创作。铁律：
1. 内容分三级，每段必须标注：F1=客观事实（只能来自给定的事件记录，引文逐字照录含人名）；F2=叙事衔接（动作调度、场景过渡，不得新增客观事实：不得出现新的人名、物件、数字、地点、事件结果）；F3=创作补充（心理、氛围、环境与文学细节，允许虚构但不得与事实矛盾，且不得把虚构写成事实）。
2. 视角人物只能知道他在本场开始时实际掌握的信息（清单已给），不得让他引用未学到的事实。
3. 标记为「复述」的事件不要重写：同一事实只叙述一次，最多用半句话带过其重复发生。
4. 未解决的问题保持未解决：不得替人物查明真相、不得安排和解或结局。
5. 人物语言要贴合各自的说话样本与身份，避免人人同腔。
6. 素材分级铁律：每条事件素材都标了 material_class 与 quotable。只有 quotable=true 的台词素材可以逐字引用；quotable=false 的动作/状态素材是叙述者描述，只能转述进叙事句，禁止给它加引号、禁止写成任何人物说过的话，禁止原样抄写其中的英文标识符串。
7. 全书级别比例（软约束）：F1 不低于三成，F3 不超过两成，其余为 F2；不要为了凑比例硬造事实或硬删好的创作段，只在有选择时优先把可锚定事件的内容写成 F1/F2。
7. 只输出 JSON：{"paragraphs":[{"level":"F1|F2|F3","event_ids":["E0001"],"text":"..."}],"scene_end_state":"本场结束时局面一句话"}。F1 段必须给 event_ids；F2/F3 段 event_ids 可为空数组。"""


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _names(run_dir: Path) -> dict[str, str]:
    """Character display names from their state files' utterances
    is unreliable; use scenario speech prefixes via observations
    text (「人名：」) — fall back to the character id."""
    return {}


def assemble_context(run_dir: Path, narrative_dir: Path,
                     scene: dict, plan: dict, products: dict,
                     prior_outcomes: list[str]) -> dict:
    """The nine pre-writing inputs (task book §11), each either
    assembled or explicitly declared missing in the manifest."""
    events = products["events"]
    ne_by_id = {e["narrative_event_id"]: e for e in events}
    obs_by_id = {o["obs_id"]: o
                 for o in products["observations"]}
    chapter = next((c for c in plan["chapters"]
                    if scene["scene_id"] in
                    _chapter_scene_ids(plan, c)), None)
    # knowledge at scene start
    start_tick = scene["time_range"][0]
    known: dict[str, set[str]] = {}
    from narrative.initial_knowledge import seed_initial_knowledge
    for c, facts in seed_initial_knowledge(run_dir).items():
        known[c] = set(facts)
    for e in events:
        if e["tick"] >= start_tick:
            continue
        for k in e.get("knowledge_changes", []):
            if k["change"] == "learned":
                known.setdefault(k["character"], set()).add(
                    k["fact"])
    # voice samples: distinct utterances per participant
    voices: dict[str, list[str]] = {}
    for o in products["observations"]:
        if o.get("text") and o["type"] in ("speak", "confront",
                                           "ask"):
            speaker = (o.get("actors") or [""])[0]
            line = o["text"].strip()
            if line and line not in voices.setdefault(speaker, []):
                voices[speaker].append(line)
    for c in voices:
        voices[c] = voices[c][:6]
    # merge-group roles for this scene's events
    groups = products["semantic"].get("groups", [])
    role: dict[str, str] = {}
    for g in groups:
        for m in g["member_ne_ids"]:
            role[m] = "primary" if m == g["primary_ne_id"] \
                else f"repeat of {g['primary_ne_id']}"
    from narrative.material import adapt_event
    from narrative.observer import resolve_names
    all_actors = sorted({a for o in products["observations"]
                         for a in (o.get("actors") or [])})
    names = {cid: rec["name"] for cid, rec in
             resolve_names(run_dir.parent.parent, all_actors,
                           []).items()}
    scene_events = []
    for n in scene["event_ids"]:
        e = ne_by_id[n]
        obs = obs_by_id[e["source_event_ids"][0]]
        m = adapt_event(e, obs, names)
        if m["provenance"] == "QUARANTINED":
            continue  # system/debug material never reaches Writer
        m["role"] = role.get(n, "primary")
        scene_events.append(m)
    arcs = {a["character_id"]: a
            for a in products["arcs"].get("arcs", [])}
    # information increment (Phase 1 task 3): facts revealed by
    # earlier scenes must not be re-revealed as new information
    scene_order = [s["scene_id"] for s in plan["scenes"]]
    idx = scene_order.index(scene["scene_id"]) \
        if scene["scene_id"] in scene_order else 0
    already_revealed: set[str] = set()
    for prev in plan["scenes"][:idx]:
        for n in prev["event_ids"]:
            e = ne_by_id[n]
            for k in e.get("knowledge_changes", []):
                if k.get("change") == "learned":
                    already_revealed.add(k["fact"])
            o = obs_by_id[e["source_event_ids"][0]]
            if o.get("proposition") and o.get("stance") == "assert":
                already_revealed.add(o["proposition"])
    ctx = {
        "story_outline": plan["story"],
        "chapter_outline": chapter,
        "scene_outline": scene,
        "character_profiles": {
            c: {"voice_samples": voices.get(c, []),
                "arc": arcs.get(c, {})}
            for c in scene.get("participants", [])},
        "knowledge_at_start": {c: sorted(f) for c, f in
                               known.items()
                               if c in scene.get("participants",
                                                 [])},
        "related_conflicts": [c for c in
                              products["views"].get("conflicts", [])
                              if set(c["participants"]) &
                              set(scene.get("participants", []))],
        "prior_outcomes": prior_outcomes[-3:],
        "setup_payoff_open": [r for r in
                              products["setup"].get("records", [])
                              if r["status"] != "payoff"],
        "already_revealed": sorted(already_revealed),
        # Phase 2: character state + scene expectations from the
        # planner's state enrichment (goals / entry knowledge /
        # must-not-use / entering emotion / expected changes).
        "character_state": scene.get("character_state"),
        "scene_events": scene_events}
    manifest = {k: (v is not None and v != {} and v != [])
                for k, v in ctx.items()}
    return {"context": ctx, "manifest": manifest}


def _chapter_scene_ids(plan: dict, chapter: dict) -> list[str]:
    ids = set(chapter.get("included_events", []))
    return [s["scene_id"] for s in plan["scenes"]
            if set(s["event_ids"]) & ids]


def _prompt(ctx: dict) -> str:
    c = ctx
    lines = [f"【故事大纲】主题：{c['story_outline'].get('theme','')}"
             f"；中心问题：{c['story_outline'].get('central_question','')}"
             f"；结局状态（如实）：{c['story_outline'].get('ending_state','')}"]
    if c["chapter_outline"]:
        lines.append(f"【本章】{c['chapter_outline']['chapter_id']}"
                     f" 目的：{c['chapter_outline'].get('narrative_purpose','')}"
                     f"；本章未答问题：{c['chapter_outline'].get('unresolved_questions',[])}")
    so = c["scene_outline"]
    lines.append(f"【本场大纲】地点 {so['location']}，tick "
                 f"{so['time_range']}，视角人物 "
                 f"{so['viewpoint_character']}，场景目标："
                 f"{so['scene_goal']}；对立：{so['opposition']}；"
                 f"转折点事件：{so.get('turning_point')}")
    lines.append("【人物（说话样本与弧线摘要）】")
    for char, prof in c["character_profiles"].items():
        arc = prof.get("arc") or {}
        lines.append(f"- {char}：样本 {prof['voice_samples'][:3]}"
                     f"；已学事实转折 "
                     f"{[t.get('fact') for t in arc.get('turning_points', [])]}")
    lines.append(f"【本场开始时各人实际知道的事实】"
                 f"{json.dumps(c['knowledge_at_start'], ensure_ascii=False)}")
    if c["related_conflicts"]:
        lines.append(f"【相关冲突】" + "；".join(
            f"{x['conflict_id']}({x['conflict_type']}, "
            f"tension {x['tension']})" for x in
            c["related_conflicts"]))
    if c["prior_outcomes"]:
        lines.append(f"【前情】{'；'.join(c['prior_outcomes'])}")
    open_sp = [f"{r['description']}({r['status']})"
               for r in c["setup_payoff_open"][:4]]
    if open_sp:
        lines.append(f"【未回收伏笔（保持开放）】{'；'.join(open_sp)}")
    if c.get("already_revealed"):
        lines.append("【已揭示事实（前场已交代，本场不得再次当作"
                     "新信息揭示，只能简短带过）】" +
                     "；".join(c["already_revealed"]))
    cs = c.get("character_state")
    if cs:
        lines.append("【人物状态与本场约束（Phase 2）】")
        for cid, p in cs.get("participants", {}).items():
            emo = p.get("emotion_entering") or {}
            lines.append(
                f"- {cid}：当前目标 {p.get('goals_active', [])}；"
                f"入场情绪（推断）{emo.get('emotion', 'unknown')}"
                f"（置信度 {emo.get('confidence', 0)}）；"
                f"尚未掌握、不得当作已知使用的事实："
                f"{p.get('must_not_use_at_entry', [])}")
        gaps = cs.get("information_gaps", [])
        if gaps:
            lines.append("信息差：" + "；".join(
                f"{g['pair'][0]} 与 {g['pair'][1]} 掌握的信息不同"
                for g in gaps))
        exp = cs.get("expected_state_changes", [])
        if exp:
            lines.append("本场预期状态变化（由本场事件产生，"
                         "须在文本中自然呈现）：" + "；".join(
                             f"{e['character_id']}·{e['field']}"
                             for e in exp))
        lines.append(
            "心理呈现规则：禁止把心理写成说明书式旁白（不得出现"
            "「他感到恐惧/愤怒/犹豫，因此决定……」这类直接解释）；"
            "必须通过动作、对话、停顿、回避、语气和选择性注意"
            "让读者自己看出。人物只能使用其入场已知与本场事件中"
            "学到的信息；对尚未掌握的事实，人物至多猜测，且文本"
            "必须表现为猜测（如「莫非……」），不得写成确定事实。")
    lines.append("【本场事件（按序；role=repeat 的不要重写；"
                 "quotable=true 的台词才可逐字引用，"
                 "quotable=false 的只能转述）】")
    for e in c["scene_events"]:
        lines.append(f"- {e['event_id']} t{e['tick']} "
                     f"{e['material_class']} "
                     f"quotable={e['quotable']} "
                     f"重要性{e['importance']} "
                     f"功能{e['functions']} role={e['role']}："
                     f"{e['text']}")
    lines.append("请写这一场。篇幅与事件重要性成比例；重复台词"
                 "只叙述一次。")
    return "\n".join(lines)


REWRITE_SYSTEM = """你是一名小说改稿编辑。下面是某章中被标为
F3（创作补充）的段落。你的任务是把它们改写为 F2（叙事呈现）：
用人物动作、对话、场景信息来表达同样的内容，减少纯解释、
纯描写和作者补充。硬性规则：
1. 不得新增任何客观事实、人物、物件或引语；不得删除段落中
   已有的事件信息与因果关系。
2. 段落数量、顺序、event_ids 必须与输入完全一致。
3. 只输出 JSON：{"paragraphs":[{"text":"...","level":"F2",
   "event_ids":[...]}]}。"""


def _enforce_budget(novel: dict, gen, usage: dict,
                    cap_in: int = CAP_IN) -> dict:
    """F-budget enforcement pass (Phase 2 acceptance §2.2):
    measure -> one targeted F3->F2 rewrite round per chapter ->
    re-measure -> explicit diagnosis if still over budget.
    Never deletes paragraphs, never touches F1, preserves
    event_ids (traceability). Returns the enforcement record;
    the novel dict is modified in place."""
    from narrative.fbudget import analyze
    before = analyze(novel)
    record = {"before": before["book"]["ratios"],
              "issues_before": [i["kind"] for i in before["issues"]],
              "rewritten_paragraphs": 0, "chapters": []}
    if not before["over_budget"]:
        record["status"] = "within_budget"
        record["after"] = before["book"]["ratios"]
        novel["budget_enforcement"] = record
        return record
    # Convert only as many F3 paragraphs as the book-level
    # budget calls for: enough to bring F3 under its cap AND
    # to lift F2 to its floor, but never so many that F2 is
    # pushed over its own cap.
    import math
    counts = before["book"]["counts"]
    total = max(1, before["book"]["total"])
    b = before["budgets"]
    n_for_f3 = counts["F3"] - math.floor(b["F3_max"] * total)
    n_for_f2_floor = math.ceil(b["F2_min"] * total) - counts["F2"]
    n_for_f2_cap = math.floor(b["F2_max"] * total) - counts["F2"]
    # F3 soft target (external review, 2026-11-26): conversions
    # for the F3 cap stop at the target floor; conversions for
    # the F2 HARD floor may go below it, with the waiver and
    # its reason recorded -- never silent, never filler.
    target = b.get("F3_target_min")
    n_above_target = counts["F3"] if target is None else max(
        0, counts["F3"] - math.ceil(target * total))
    n_convert = max(0, min(max(min(n_for_f3, n_above_target),
                               n_for_f2_floor),
                           n_for_f2_cap, counts["F3"]))
    if target is not None and n_convert > n_above_target:
        record["f3_target_waived"] = {
            "target_min": target,
            "reason": "F2 hard floor takes precedence over the "
                      "F3 soft target; the conversions F2 "
                      "requires push F3 below target"}
    record["planned_conversions"] = n_convert
    by_id = {ch["chapter_id"]: ch for ch in novel["chapters"]}
    for ch_rep in before["chapters"]:
        if n_convert <= 0:
            break
        ch = by_id[ch_rep["chapter_id"]]
        targets = [(i, p) for i, p in enumerate(ch["paragraphs"])
                   if p.get("level") == "F3"][:n_convert]
        if not targets or usage["prompt_tokens"] >= cap_in:
            continue
        n_convert -= len(targets)
        payload = [{"n": n, "text": p.get("text", ""),
                    "event_ids": p.get("event_ids", [])}
                   for n, (_, p) in enumerate(targets)]
        text, u = gen(REWRITE_SYSTEM,
                      json.dumps({"paragraphs": payload},
                                 ensure_ascii=False),
                      max_tokens=8192)
        usage["prompt_tokens"] += u.get("prompt_tokens", 0)
        usage["candidates_tokens"] += u.get(
            "candidates_tokens", 0) + u.get("thoughts_tokens", 0)
        usage["calls"] += 1
        entry = {"chapter_id": ch["chapter_id"],
                 "targeted": len(targets), "applied": 0}
        try:
            data = json.loads(text[text.find("{"):
                                   text.rfind("}") + 1])
            new_paras = data["paragraphs"]
            if len(new_paras) != len(targets):
                raise ValueError("paragraph count changed")
            for (idx, old), new in zip(targets, new_paras):
                if new.get("event_ids") != old.get("event_ids"):
                    raise ValueError("event_ids changed")
                ch["paragraphs"][idx] = {
                    **old, "text": new["text"], "level": "F2",
                    "budget_rewritten": True}
                entry["applied"] += 1
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            entry["error"] = f"rewrite rejected: {e}"
        record["rewritten_paragraphs"] += entry["applied"]
        record["chapters"].append(entry)
    after = analyze(novel)
    record["after"] = after["book"]["ratios"]
    record["issues_after"] = [i["kind"] for i in after["issues"]]
    record["status"] = ("within_budget_after_rewrite"
                        if not after["over_budget"]
                        else "over_budget_after_rewrite")
    novel["budget_enforcement"] = record
    return record


def write_novel(run_dir: Path, narrative_dir: Path,
                generate_fn=None, cap_in: int = CAP_IN,
                cap_out: int = CAP_OUT,
                enforce_budget: bool = False) -> dict:
    gen = generate_fn or _gemini_generate
    upgrade = narrative_dir / "upgrade"
    plan = _load(upgrade / "plan.json")
    products = {
        "events": _load(upgrade / "narrative_events.json"),
        "observations": _load(narrative_dir / "observations.json"),
        "semantic": _load(upgrade / "semantic.json"),
        "arcs": _load(upgrade / "character_arcs.json"),
        "views": _load(upgrade / "story_views.json"),
        "setup": _load(upgrade / "setup_payoff.json")}
    usage = {"prompt_tokens": 0, "candidates_tokens": 0, "calls": 0}
    chapters_out: list[dict] = []
    prior_outcomes: list[str] = []
    creative_register: list[dict] = []
    stopped = None
    scene_chapter = {}
    for ch in plan["chapters"]:
        for sid in _chapter_scene_ids(plan, ch):
            scene_chapter[sid] = ch["chapter_id"]
    for ch in plan["chapters"]:
        paras: list[dict] = []
        for scene in plan["scenes"]:
            if scene_chapter.get(scene["scene_id"]) != \
                    ch["chapter_id"]:
                continue
            if usage["prompt_tokens"] >= cap_in or \
                    usage["candidates_tokens"] >= cap_out:
                stopped = f"budget cap reached before " \
                          f"{scene['scene_id']}"
                break
            assembled = assemble_context(run_dir, narrative_dir,
                                         scene, plan, products,
                                         prior_outcomes)
            prompt = _prompt(assembled["context"])
            data = None
            for attempt in range(2):
                text, u = gen(SYSTEM, prompt, max_tokens=8192)
                usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                usage["candidates_tokens"] += u.get(
                    "candidates_tokens", 0) + u.get(
                    "thoughts_tokens", 0)
                usage["calls"] += 1
                try:
                    data = json.loads(text[text.find("{"):
                                           text.rfind("}") + 1])
                    break
                except json.JSONDecodeError:
                    if attempt == 1:
                        raise ValueError(
                            f"unparseable writer output for "
                            f"{scene['scene_id']}: "
                            f"{text[:200]!r}")
            for p in data.get("paragraphs", []):
                p["scene_id"] = scene["scene_id"]
                p["chapter_id"] = ch["chapter_id"]
                paras.append(p)
                if p.get("level") == "F3":
                    creative_register.append(
                        {"chapter_id": ch["chapter_id"],
                         "scene_id": scene["scene_id"],
                         "text": p.get("text", "")[:80]})
            if data.get("scene_end_state"):
                prior_outcomes.append(data["scene_end_state"])
            scene["_context_manifest"] = assembled["manifest"]
        chapters_out.append({"chapter_id": ch["chapter_id"],
                             "title": ch.get("literary_title")
                             or ch.get("chapter_title", ""),
                             "paragraphs": paras})
        if stopped:
            break
    novel = {"chapters": chapters_out, "usage": usage,
             "creative_register": creative_register,
             "stopped": stopped,
             "plan_validation": plan.get("validation", {})}
    if enforce_budget and not stopped:
        _enforce_budget(novel, gen, usage,
                        cap_in=cap_in)
    return novel


def render_markdown(novel: dict, story_title: str) -> str:
    out = [f"# {story_title}（升级版 Writer 生成）", ""]
    for ch in novel["chapters"]:
        out.append(f"## {ch['chapter_id']} {ch['title']}")
        out.append("")
        for p in ch["paragraphs"]:
            out.append(p.get("text", ""))
            out.append("")
    if novel["creative_register"]:
        out.append("---")
        out.append(f"创作补充（F3）共 {len(novel['creative_register'])}"
                   f" 段，已在 trace_v2.json 中逐段登记。")
    return "\n".join(out)


def write_novel_files(run_dir: Path, narrative_dir: Path,
                      out_dir: Path, generate_fn=None,
                      enforce_budget: bool = False) -> dict:
    novel = write_novel(run_dir, narrative_dir,
                        generate_fn=generate_fn,
                        enforce_budget=enforce_budget)
    plan = _load(narrative_dir / "upgrade" / "plan.json")
    title = plan["story"].get("title") or run_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "novel_v2.md").write_text(
        render_markdown(novel, title), encoding="utf-8")
    (out_dir / "trace_v2.json").write_text(json.dumps(
        novel, ensure_ascii=False, indent=1), encoding="utf-8")
    return novel
