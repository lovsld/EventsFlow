"""Decision quality (Phase 1 / V1.0, task 1): story-state
assessment, per-tick progress judgement and decision records.

Design constraints (task book §3 + the project's red lines):
- This module only OBSERVES. It attaches at tick boundaries,
  reads world_log / scenario / story config (and optionally the
  run's acp.sqlite3 goal changes), and produces assessments,
  judgements and diagnoses. It never writes world state and
  never decides for a character.
- Every judgement cites event ids. A dimension that cannot be
  assessed from the available data is reported as unknown
  (None), never silently treated as advanced.
- "Events happened" is not "the story progressed": a tick whose
  events advance none of the six dimensions is recorded as
  motion without progress, and a streak of such ticks raises a
  ZERO_PROGRESS diagnosis instead of passing as normal operation.

Progress dimensions (task book §3.3): information, goal,
conflict, relationship, state, suspense.
Decision outcome types (task book §3.4): new_event,
advance_conflict, character_choice, reveal_information,
hold_observe, trigger_diagnosis.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DIMENSIONS = ("information", "goal", "conflict", "relationship",
              "state", "suspense")
ZERO_STREAK_LIMIT = 3


def _flatten(world_log: list[dict]):
    by_tick: dict[int, list[dict]] = {}
    by_id: dict[str, dict] = {}
    deliveries: dict[int, list[dict]] = {}
    positions: dict[int, dict] = {}
    for rec in world_log:
        tick = rec["tick"]
        by_tick[tick] = rec.get("events", [])
        deliveries[tick] = rec.get("deliveries", [])
        positions[tick] = rec.get("positions", {})
        for e in rec.get("events", []):
            by_id[e["event_id"]] = e
    return by_tick, by_id, deliveries, positions


def _seed_knowledge(run_dir: Path,
                    initial_knowledge: dict) -> dict[str, set]:
    """Initial beliefs per character: story config initial
    knowledge plus `seed:`-sourced beliefs in the run's own
    character state files (same rule as the narrative layer's
    initial-knowledge derivation)."""
    known: dict[str, set] = {}
    for cid, beliefs in (initial_knowledge or {}).items():
        known.setdefault(cid, set()).update(beliefs)
    chars = run_dir / "characters"
    if chars.is_dir():
        for state in sorted(chars.glob("*/state.json")):
            try:
                data = json.loads(state.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            cid = data.get("character_id") or state.parent.name
            for b in data.get("beliefs", []):
                ev = str(b.get("evidence", ""))
                if ev.startswith("seed:"):
                    known.setdefault(cid, set()).add(
                        ev.split(":", 1)[1])
    return known


def _goal_changes(run_dir: Path) -> dict[int, list[dict]]:
    """Goal changes per tick from the run's acp.sqlite3, if the
    table exists; {} when unavailable (goal dimension then
    reports unknown rather than guessing)."""
    db = run_dir / "acp.sqlite3"
    if not db.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cols = [r[1] for r in con.execute(
            "PRAGMA table_info(goal_changes)")]
        if not cols:
            return {}
        rows = con.execute("SELECT * FROM goal_changes").fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    out: dict[int, list[dict]] = {}
    for row in rows:
        rec = dict(zip(cols, row))
        tick = rec.get("tick")
        if isinstance(tick, int):
            out.setdefault(tick, []).append(rec)
    return out


class ProgressJudge:
    """Stateful per-run judge: feed it a run, get per-tick
    decision records, snapshots and diagnoses."""

    def __init__(self, scenario: dict, story_cfg: dict):
        self.claims = scenario.get("claims") or {}
        self.objects = scenario.get("objects") or {}
        self.initial_knowledge = story_cfg.get(
            "initial_knowledge") or {}
        self.threads = story_cfg.get("initial_threads") or []
        self.questions: dict[str, dict] = {}
        for th in self.threads:
            for q in th.get("questions", []):
                self.questions[q["id"]] = {
                    "thread_id": th["thread_id"],
                    "answered_by": q["answered_by"],
                    "status": "open"}
        self.thread_props = {p for th in self.threads
                             for p in th.get("propositions", [])}

    def _progress(self) -> float:
        if not self.questions:
            return 0.0
        val = {"answered": 1.0, "addressed": 0.5, "open": 0.0}
        return round(sum(val[q["status"]]
                         for q in self.questions.values())
                     / len(self.questions), 3)

    def judge(self, run_dir: Path) -> dict:
        world_log = json.loads(
            (run_dir / "world_log.json").read_text(encoding="utf-8"))
        known = _seed_knowledge(run_dir, self.initial_knowledge)
        goals = _goal_changes(run_dir)
        return self.judge_world(world_log, known, goals)

    def judge_world(self, world_log: list[dict],
                    seed_known: dict, goal_changes: dict) -> dict:
        """Judge an in-memory world_log (list of per-tick records
        with events / deliveries / positions). Used by judge()
        for finished runs and by the live intervention hook for
        runs in progress. A judge instance is single-use: its
        question states mutate as it walks the ticks."""
        by_tick, by_id, deliveries, positions = _flatten(world_log)
        known = {c: set(v) for c, v in seed_known.items()}
        goals = goal_changes
        contacted: set[tuple[str, str]] = set()
        visited: dict[str, set] = {}
        records, snapshots, diagnoses = [], [], []
        streak = 0
        progress_ticks = 0
        question_progress_ticks = 0
        for tick in sorted(by_tick):
            events = by_tick[tick]
            status_before = {qid: q["status"] for qid, q in
                             self.questions.items()}
            dims: dict[str, dict] = {
                d: {"advanced": False, "evidence": []}
                for d in DIMENSIONS}
            if not goals:
                dims["goal"] = {"advanced": None, "evidence": []}
            # --- deliveries: information / conflict / suspense /
            #     relationship
            for d in deliveries.get(tick, []):
                agent = d["agent_id"]
                for eid in d.get("delivered_event_ids", []):
                    e = by_id.get(eid)
                    if not e or e.get("type") not in (
                            "speak", "ask", "confront"):
                        continue
                    speaker = (e.get("actors") or ["?"])[0]
                    if speaker != agent:
                        pair = (speaker, agent)
                        if pair not in contacted:
                            contacted.add(pair)
                            dims["relationship"]["advanced"] = True
                            dims["relationship"]["evidence"].append(
                                eid)
                    cdef = self.claims.get(e.get("claim") or "", {})
                    prop = cdef.get("proposition")
                    stance = cdef.get("stance")
                    if not prop:
                        continue
                    if stance == "deny":
                        dims["conflict"]["advanced"] = True
                        dims["conflict"]["evidence"].append(eid)
                    if stance == "assert" and \
                            prop not in known.get(agent, set()):
                        known.setdefault(agent, set()).add(prop)
                        dims["information"]["advanced"] = True
                        dims["information"]["evidence"].append(eid)
                    if prop in self.thread_props:
                        dims["suspense"]["advanced"] = True
                        dims["suspense"]["evidence"].append(eid)
                    for q in self.questions.values():
                        if q["answered_by"] == prop and \
                                q["status"] == "open":
                            q["status"] = "addressed"
            # --- events: finds (reveal), confront, state changes
            reveal = False
            for e in events:
                eid = e["event_id"]
                if e.get("type") == "confront":
                    dims["conflict"]["advanced"] = True
                    dims["conflict"]["evidence"].append(eid)
                if e.get("type") == "search" and e.get("found"):
                    reveal = True
                    dims["information"]["advanced"] = True
                    dims["information"]["evidence"].append(eid)
                    dims["state"]["advanced"] = True
                    dims["state"]["evidence"].append(eid)
                    finder = (e.get("actors") or ["?"])[0]
                    for obj in e["found"]:
                        odef = self.objects.get(obj, {})
                        # The object's fact_belief is direct
                        # evidence: finding it ANSWERS the
                        # question it is keyed to. A `reveals`
                        # belief is hearsay-grade (it carries a
                        # reliability < 1): the finder learns it
                        # and the question becomes ADDRESSED,
                        # never answered — the same honesty rule
                        # the v03 thread tracker applies.
                        fb = odef.get("fact_belief")
                        if fb:
                            known.setdefault(finder, set()).add(fb)
                            for q in self.questions.values():
                                if q["answered_by"] == fb:
                                    q["status"] = "answered"
                        for r in odef.get("reveals") or []:
                            belief = r.get("belief")
                            if belief:
                                known.setdefault(finder,
                                                   set()).add(belief)
                                for q in self.questions.values():
                                    if q["answered_by"] == belief \
                                            and q["status"] == \
                                            "open":
                                        q["status"] = "addressed"
                actor = (e.get("actors") or [None])[0]
                loc = e.get("location")
                if actor and loc:
                    seen = visited.setdefault(actor, set())
                    if loc not in seen:
                        seen.add(loc)
                        dims["state"]["advanced"] = True
                        dims["state"]["evidence"].append(eid)
            # --- goal changes (when data exists; the acp log is
            #     the evidence, so no event id is cited)
            if goals and goals.get(tick):
                dims["goal"] = {
                    "advanced": True, "evidence": [],
                    "detail": f"{len(goals[tick])} goal change(s) "
                              "in acp log"}
            advanced = [d for d in DIMENSIONS
                        if dims[d]["advanced"]]
            rank = {"open": 0, "addressed": 1, "answered": 2}
            if any(rank[q["status"]] > rank[status_before[qid]]
                   for qid, q in self.questions.items()):
                question_progress_ticks += 1
            if advanced:
                progress_ticks += 1
                streak = 0
            elif events:
                streak += 1
            # --- outcome classification (task book §3.4 order of
            #     specificity: diagnosis > reveal > conflict >
            #     choice > new event > hold)
            if streak >= ZERO_STREAK_LIMIT and events:
                outcome = "trigger_diagnosis"
                if streak == ZERO_STREAK_LIMIT:
                    diagnoses.append({
                        "kind": "ZERO_PROGRESS_STREAK",
                        "tick": tick,
                        "streak": streak,
                        "detail": "events occurred but no story "
                                  "dimension advanced for "
                                  f"{streak} consecutive ticks",
                        "open_questions": [
                            qid for qid, q in self.questions.items()
                            if q["status"] != "answered"]})
            elif reveal:
                outcome = "reveal_information"
            elif "conflict" in advanced:
                outcome = "advance_conflict"
            elif "goal" in advanced:
                outcome = "character_choice"
            elif advanced:
                outcome = "new_event"
            else:
                outcome = "hold_observe"
            evidence = sorted({eid for d in dims.values()
                               for eid in d["evidence"]})
            rationale = (
                f"advanced: {', '.join(advanced) or 'none'}; "
                f"events this tick: {len(events)}")
            if outcome == "hold_observe" and events:
                rationale += ("; motion without progress — events "
                              "changed no assessed dimension")
            if not events:
                rationale += "; no events this tick"
            snap = {
                "tick": tick,
                "open_questions": [qid for qid, q in
                                   self.questions.items()
                                   if q["status"] == "open"],
                "addressed_questions": [qid for qid, q in
                                        self.questions.items()
                                        if q["status"] ==
                                        "addressed"],
                "answered_questions": [qid for qid, q in
                                       self.questions.items()
                                       if q["status"] == "answered"],
                "story_progress": self._progress(),
                "known_facts": {c: sorted(v)
                                for c, v in known.items() if v},
                "positions": positions.get(tick, {})}
            snapshots.append(snap)
            records.append({
                "decision_id": f"D{tick:03d}",
                "tick": tick, "mode": "retrospective",
                "state_summary": {
                    "open_questions": snap["open_questions"],
                    "story_progress": snap["story_progress"]},
                "outcome_type": outcome, "dimensions": dims,
                "evidence_event_ids": evidence,
                "rationale": rationale,
                "expected": None, "actual": outcome,
                "validation": "n/a (retrospective)"})
        return {
            "snapshots": snapshots, "decisions": records,
            "diagnoses": diagnoses,
            "summary": {
                "ticks": len(records),
                "progress_ticks": progress_ticks,
                "question_progress_ticks": question_progress_ticks,
                "zero_engagement": progress_ticks == 0,
                "mainline_zero_engagement":
                    question_progress_ticks == 0,
                "final_story_progress": self._progress(),
                "diagnosis_count": len(diagnoses)}}

def judge_run(run_dir: Path, scenario_path: Path,
              story_cfg_path: Path) -> dict:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    cfg = json.loads(story_cfg_path.read_text(encoding="utf-8"))
    return ProgressJudge(scenario, cfg).judge(run_dir)
