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
6. 只输出 JSON：{"paragraphs":[{"level":"F1|F2|F3","event_ids":["E0001"],"text":"..."}],"scene_end_state":"本场结束时局面一句话"}。F1 段必须给 event_ids；F2/F3 段 event_ids 可为空数组。"""


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
    scene_events = []
    for n in scene["event_ids"]:
        e = ne_by_id[n]
        obs = obs_by_id[e["source_event_ids"][0]]
        scene_events.append({
            "ne_id": n, "event_id": e["source_event_ids"][0],
            "tick": e["tick"], "type": e["event_type"],
            "text": obs.get("text") or e["event_summary"],
            "importance": e["narrative_importance"],
            "functions": e.get("dramatic_function", []),
            "role": role.get(n, "primary")})
    arcs = {a["character_id"]: a
            for a in products["arcs"].get("arcs", [])}
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
    lines.append("【本场事件（按序；role=repeat 的不要重写）】")
    for e in c["scene_events"]:
        lines.append(f"- {e['event_id']} t{e['tick']} "
                     f"{e['type']} 重要性{e['importance']} "
                     f"功能{e['functions']} role={e['role']}："
                     f"{e['text']}")
    lines.append("请写这一场。篇幅与事件重要性成比例；重复台词"
                 "只叙述一次。")
    return "\n".join(lines)


def write_novel(run_dir: Path, narrative_dir: Path,
                generate_fn=None, cap_in: int = CAP_IN,
                cap_out: int = CAP_OUT) -> dict:
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
                             "title": ch.get("chapter_title", ""),
                             "paragraphs": paras})
        if stopped:
            break
    return {"chapters": chapters_out, "usage": usage,
            "creative_register": creative_register,
            "stopped": stopped,
            "plan_validation": plan.get("validation", {})}


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
                      out_dir: Path, generate_fn=None) -> dict:
    novel = write_novel(run_dir, narrative_dir,
                        generate_fn=generate_fn)
    plan = _load(narrative_dir / "upgrade" / "plan.json")
    title = plan["story"].get("title") or run_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "novel_v2.md").write_text(
        render_markdown(novel, title), encoding="utf-8")
    (out_dir / "trace_v2.json").write_text(json.dumps(
        novel, ensure_ascii=False, indent=1), encoding="utf-8")
    return novel
