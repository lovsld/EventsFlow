"""Thread and conflict views (upgrade Phase 3; design §3).

ThreadView merges the two existing calibres WITHOUT averaging
them away: the v03 thread state (information-evidence progress)
and the narrative storyline state (chain completeness) are both
recorded, because Phase 2 showed they legitimately disagree on
action-completed scenarios. A thread is never auto-resolved:
status comes from the sources, and an unresolved question stays
listed until direct evidence answers it.

ConflictView keeps severity (v03) and tension (derived here)
as separate numbers, per the task book.
"""
from __future__ import annotations

import json
from pathlib import Path

from narrative.models.story import ConflictView, ThreadView

_TYPE_MAP = {"interest": "interest_conflict",
             "information": "information_conflict",
             "goal": "goal_conflict", "moral": "moral_conflict",
             "resource": "resource_conflict",
             "relationship": "relationship_conflict",
             "internal": "internal_conflict",
             "survival": "survival_conflict"}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_story_views(run_dir: Path, narrative_dir: Path,
                      v03_dir: Path | None) -> dict:
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    observations = _load(narrative_dir / "observations.json")
    storylines = _load(narrative_dir / "storylines.json")
    obs_by_id = {o["obs_id"]: o for o in observations}
    ne_by_id = {e["narrative_event_id"]: e for e in events}

    state = None
    if v03_dir is not None and (v03_dir / "story_state.json").exists():
        state = _load(v03_dir / "story_state.json")
    v03_threads = (state or {}).get("threads") or {}
    v03_conflicts = (state or {}).get("conflicts") or {}

    def related_events(props: set[str], objects: set[str]) -> list[str]:
        out = []
        for o in observations:
            if o.get("proposition") in props or \
                    (o.get("found") and
                     set(o["found"]) & objects):
                out.append(f"NE-{o['obs_id']}")
        return out

    threads: list[dict] = []
    diagnostics: dict[str, dict] = {}
    covered_lines: set[str] = set()
    for tid, th in v03_threads.items():
        props = set(th.get("known_facts", []))
        for q in th.get("questions", []):
            props.add(q.get("answered_by"))
        props.discard(None)
        objects = set()
        rel = related_events(props, objects)
        # narrative lines sharing propositions with this thread
        line_states = []
        for line in storylines.get("lines", []):
            if props & set(line.get("propositions", [])):
                covered_lines.add(line["line_id"])
                line_states.append({"line_id": line["line_id"],
                                    "state": line["state"]})
        nes = [ne_by_id[i] for i in rel if i in ne_by_id]
        dramatic = [e for e in nes if any(
            f in ("confrontation", "revelation", "discovery")
            for f in e.get("dramatic_function", []))]
        climax = max(dramatic, key=lambda e: e["narrative_importance"],
                     default=None)
        top = sorted(nes, key=lambda e: -e["narrative_importance"])[:3]
        unresolved = [q["text"] for q in th.get("questions", [])
                      if q.get("answered_at") is None]
        confront_n = sum(1 for e in nes if "confrontation" in
                         e.get("dramatic_function", []))
        tension = round(min(1.0, 0.3 * confront_n / max(1, len(nes))
                              * 3 + 0.4 * (len(unresolved) /
                                           max(1, len(th.get(
                                               "questions", []))))),
                        3) if nes else 0.0
        relations = [{"other_thread": d, "relation": "dependent"}
                     for d in th.get("dependencies", [])]
        view = ThreadView(
            thread_id=tid, title=th.get("title", ""),
            central_question=th.get("central_question", ""),
            involved_characters=th.get("involved_characters", []),
            related_events=rel,
            opening_event=rel[0] if rel else "",
            turning_points=[e["narrative_event_id"] for e in top],
            climax_event=climax["narrative_event_id"] if climax
            else None,
            progress=th.get("progress", 0.0), tension=tension,
            status=th.get("status", "open"),
            unresolved_questions=unresolved, relations=relations)
        threads.append(view.model_dump(mode="json"))
        diagnostics[tid] = {
            "v03_status": th.get("status"),
            "v03_progress": th.get("progress"),
            "narrative_line_states": line_states,
            "calibre_note": "progress is information-evidence "
                            "based (v03); a narrative line may be "
                            "closed by action completion while "
                            "progress stays low — both are kept."}
    for line in storylines.get("lines", []):
        if line["line_id"] in covered_lines:
            continue
        rel = [f"NE-{i}" for i in line.get("event_ids", [])]
        nes = [ne_by_id[i] for i in rel if i in ne_by_id]
        top = sorted(nes, key=lambda e: -e["narrative_importance"])[:3]
        view = ThreadView(
            thread_id=f"narrative-{line['line_id']}",
            title=", ".join(line.get("propositions", [])),
            related_events=rel,
            opening_event=rel[0] if rel else "",
            turning_points=[e["narrative_event_id"] for e in top],
            status=line.get("state", "open"))
        threads.append(view.model_dump(mode="json"))
        diagnostics[view.thread_id] = {
            "source": "narrative storyline only (no v03 thread)"}

    conflicts: list[dict] = []
    for cid, c in v03_conflicts.items():
        rel = [f"NE-{i}" for i in c.get("related_events", [])]
        nes = [ne_by_id[i] for i in rel if i in ne_by_id]
        confront_ticks = sorted(
            e["tick"] for e in nes
            if "confrontation" in e.get("dramatic_function", []) or
            e["event_type"] == "confront")
        density = len(confront_ticks) / max(1, len(nes))
        escalation = c.get("escalation")
        tension = round(0.5 * (escalation or 0.0) + 0.5 * density, 3)
        history = [{"tick": t, "confront_count_so_far": i + 1}
                   for i, t in enumerate(confront_ticks)]
        conditions = []
        for tid in c.get("related_threads", []):
            th = v03_threads.get(tid) or {}
            for q in th.get("questions", []):
                if q.get("answered_at") is None:
                    conditions.append(
                        f"问题「{q['text']}」被直接证据回答")
        view = ConflictView(
            conflict_id=cid,
            participants=c.get("participants", []),
            conflict_type=_TYPE_MAP.get(c.get("conflict_type", ""),
                                        c.get("conflict_type", "")),
            core_issue=c.get("basis", ""),
            stakes="", severity=c.get("severity", 0.0),
            tension=tension, escalation_history=history,
            triggering_events=rel[:1],
            resolution_conditions=conditions,
            status=c.get("status", "active"))
        conflicts.append(view.model_dump(mode="json"))
    return {"threads": threads, "conflicts": conflicts,
            "diagnostics": diagnostics}


def write_story_views(run_dir: Path, narrative_dir: Path,
                      v03_dir: Path | None, out_dir: Path) -> Path:
    result = build_story_views(run_dir, narrative_dir, v03_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "story_views.json"
    path.write_text(json.dumps(result, ensure_ascii=False,
                               indent=1), encoding="utf-8")
    return path
