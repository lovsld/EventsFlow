"""Chapter grouping + story outline (upgrade Phase 5; design §5).

Chapters close after a climax/payoff scene, when the dominant
thread switches, or after four scenes — scene integrity is
never broken. The story outline's ending state is assembled
from thread statuses only; an open thread stays open in the
outline, verbatim.
"""
from __future__ import annotations

import json
from pathlib import Path

from narrative.models.story import (ChapterOutline, StoryOutline)

MAX_SCENES_PER_CHAPTER = 4


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_story_outline(run_dir: Path, narrative_dir: Path,
                        views: dict, arcs: dict,
                        config_path: Path | None) -> dict:
    cfg = _load(config_path) if config_path and \
        config_path.exists() else {}
    threads = views.get("threads", [])
    main = next((t for t in threads if t["thread_id"].startswith(
        "main")), threads[0] if threads else {})
    turning = set()
    for a in arcs.get("arcs", []):
        for t in a.get("turning_points", []):
            turning.add(t["learned_at"])
    for t in threads:
        if t.get("climax_event"):
            turning.add(t["climax_event"])
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    dramatic = [e for e in events if any(
        f in ("confrontation", "revelation", "discovery")
        for f in e.get("dramatic_function", []))]
    climax_candidates = [e["narrative_event_id"] for e in sorted(
        dramatic, key=lambda e: -e["narrative_importance"])[:3]]
    open_threads = [t for t in threads
                    if t["status"] != "resolved"]
    ending = "；".join(
        f"线索「{t['title'] or t['thread_id']}」状态 {t['status']}，"
        f"未答问题 {len(t.get('unresolved_questions', []))} 个"
        for t in threads) or "无线索记录"
    outline = StoryOutline(
        title=cfg.get("title", ""),
        theme=cfg.get("theme", ""),
        central_question=cfg.get("central_question", "") or
        main.get("central_question", ""),
        main_thread=main.get("thread_id", ""),
        sub_threads=[t["thread_id"] for t in threads
                     if t is not main],
        main_characters=cfg.get("characters", []),
        core_conflicts=[c["conflict_id"]
                        for c in views.get("conflicts", [])],
        key_turning_points=sorted(turning),
        climax_candidates=climax_candidates,
        ending_state=ending)
    return outline.model_dump(mode="json")


def build_chapters(narrative_dir: Path, scenes: list[dict],
                   views: dict, setup: dict) -> list[dict]:
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    ne_by_id = {e["narrative_event_id"]: e for e in events}
    payoff_events = set()
    for r in setup.get("records", []):
        if r["status"] == "payoff":
            payoff_events |= set(r.get("actual_payoff_event_ids",
                                       []))
    threads = {t["thread_id"]: t for t in views.get("threads", [])}
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_thread = None
    for sc in scenes:
        thread = sc.get("conflict", "")
        has_climax = sc.get("turning_point") in payoff_events or \
            any(ne_by_id[n]["narrative_importance"] >= 0.9
                for n in sc["event_ids"] if n in ne_by_id)
        if current and (thread != current_thread or
                        len(current) >= MAX_SCENES_PER_CHAPTER):
            groups.append(current)
            current = []
        current.append(sc)
        current_thread = thread
        if has_climax:
            groups.append(current)
            current = []
            current_thread = None
    if current:
        groups.append(current)
    chapters: list[dict] = []
    known_so_far: set[str] = set()
    learn_at: dict[str, list[str]] = {}
    for e in events:
        for k in e.get("knowledge_changes", []):
            if k["change"] == "learned":
                learn_at.setdefault(e["narrative_event_id"],
                                    []).append(k["fact"])
    for i, group in enumerate(groups, 1):
        event_ids = [n for sc in group for n in sc["event_ids"]]
        opening_facts = len(known_so_far)
        for n in event_ids:
            known_so_far |= set(learn_at.get(n, []))
        turning = None
        best = -1.0
        for n in event_ids:
            e = ne_by_id.get(n)
            if e and e["narrative_importance"] > best:
                best = e["narrative_importance"]
                turning = n
        thread_ids = sorted({sc.get("conflict", "")
                             for sc in group if sc.get("conflict")})
        unresolved = []
        for tid in thread_ids:
            unresolved += (threads.get(tid) or {}).get(
                "unresolved_questions", [])
        curve = [round((threads.get(sc.get("conflict", "")) or {})
                       .get("tension", 0.0), 3) for sc in group]
        chapter = ChapterOutline(
            chapter_id=f"CH{i:02d}",
            chapter_title=f"第{i}章",
            narrative_purpose=(threads.get(thread_ids[0]) or {})
            .get("central_question", "") if thread_ids else "",
            opening_state={"known_facts_total": opening_facts},
            ending_state={"known_facts_total": len(known_so_far)},
            included_threads=thread_ids,
            included_events=event_ids, turning_point=turning,
            unresolved_questions=sorted(set(unresolved)),
            emotional_curve=curve)
        chapters.append(chapter.model_dump(mode="json"))
    return chapters
