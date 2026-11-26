"""Live intervention (Phase 1 / V1.0, task 1 — live half).

The Progress Judge, running at tick boundaries over the run in
progress. When it raises a ZERO_PROGRESS_STREAK diagnosis, the
intervention proposes ONE pressure/opportunity event for the
stalest open thread that has a config template — built as an
ordinary EventCandidate, passed through the existing causal +
world-rule validators, converted with the existing
to_world_event, and handed to the World Engine's external
queue. The World Engine still commits or drops it; no character
decision is touched, and no event is ever fabricated outside
the config's own templates.

Restraint: at most `max_interventions` per run, at least
`cooldown` ticks apart, one per distinct diagnosis, fingerprints
never repeat. Every attempt (fired / rejected / skipped) is
logged with its reason.
"""
from __future__ import annotations

import json
from pathlib import Path

from v03.decision_quality import ProgressJudge, _seed_knowledge
from v03.story.eventgen import (CausalValidator, WorldRuleValidator,
                                WorldSnapshot, fingerprint,
                                to_world_event)
from v03.story.models import EventCandidate


class LiveIntervention:
    def __init__(self, run_dir: Path, scenario: dict,
                 story_cfg: dict, story_state,
                 max_interventions: int = 3, cooldown: int = 3):
        self.run_dir = Path(run_dir)
        self.scenario = scenario
        self.cfg = story_cfg
        self.state = story_state
        self.max_interventions = max_interventions
        self.cooldown = cooldown
        self.log: list[dict] = []
        self._seen_fps: set[str] = set()
        self._last_fired_tick = -10 ** 9
        self._diagnoses_seen = 0
        self._counter = 0

    # -- candidate construction ---------------------------------
    def _candidate(self, judge_result: dict, assembled: list,
                   tick: int) -> EventCandidate | None:
        snap = judge_result["snapshots"][-1]
        open_q = set(snap["open_questions"]) | set(
            snap["addressed_questions"])
        all_ids = [e["event_id"] for tr in assembled
                   for e in tr["events"]]
        basis = all_ids[-3:]
        if not basis:
            return None
        for th in self.cfg.get("initial_threads", []):
            tmpl = th.get("opportunity_event") or \
                th.get("pressure_event")
            if not tmpl:
                continue
            qs = [q for q in th.get("questions", [])
                  if q["id"] in open_q]
            if not qs:
                continue
            kind = tmpl.get("kind", "opportunity_notice")
            fp = fingerprint(kind, th["thread_id"], qs[0]["id"])
            if fp in self._seen_fps:
                continue
            self._counter += 1
            return EventCandidate(
                candidate_id=f"INT{self._counter:03d}",
                source_type="story_driven", kind=kind,
                description=tmpl.get("description", ""),
                proposed_location=tmpl.get("location", ""),
                triggering_conditions=[
                    f"judge_zero_progress_streak:{th['thread_id']}"],
                causal_basis=basis,
                related_threads=[th["thread_id"]],
                expected_world_changes={
                    "gathering": tmpl.get("location", "")},
                dramatic_functions=["setup", "escalation"],
                uncertainty=0.5,
                validity_requirements=["location exists",
                                       "judge diagnosis active"],
                fingerprint=fp, created_at=tick)
        return None

    # -- the hook ------------------------------------------------
    def hook(self, tick_records, deliveries_by_tick, world):
        if not tick_records:
            return
        assembled = [{
            "tick": tr["tick"],
            "events": tr.get("events", []),
            "positions": tr.get("positions", {}),
            "deliveries": deliveries_by_tick.get(tr["tick"], []),
        } for tr in tick_records]
        judge = ProgressJudge(self.scenario, self.cfg)
        known = _seed_knowledge(
            self.run_dir, self.cfg.get("initial_knowledge") or {})
        result = judge.judge_world(assembled, known, {})
        diags = result["diagnoses"]
        if len(diags) <= self._diagnoses_seen:
            return
        self._diagnoses_seen = len(diags)
        tick = assembled[-1]["tick"]
        entry = {"tick": tick, "diagnosis": diags[-1]["kind"],
                 "open_questions": diags[-1]["open_questions"]}
        fired = [e for e in self.log if e.get("fired")]
        if len(fired) >= self.max_interventions:
            entry["fired"] = False
            entry["reason"] = "intervention budget exhausted"
            self.log.append(entry)
            return
        if tick - self._last_fired_tick < self.cooldown:
            entry["fired"] = False
            entry["reason"] = "cooldown"
            self.log.append(entry)
            return
        cand = self._candidate(result, assembled, tick)
        if cand is None:
            entry["fired"] = False
            entry["reason"] = "no templated open thread"
            self.log.append(entry)
            return
        known_ids = {e["event_id"] for tr in assembled
                     for e in tr["events"]}
        ok_c, why_c = CausalValidator(self.state, known_ids) \
            .validate(cand)
        snap = WorldSnapshot(
            locations=list(self.scenario.get("locations") or []),
            characters=list(self.cfg.get("characters") or []))
        ok_w, why_w = WorldRuleValidator(snap, self._seen_fps) \
            .validate(cand)
        entry["candidate"] = cand.candidate_id
        entry["kind"] = cand.kind
        entry["thread"] = cand.related_threads[0]
        if not (ok_c and ok_w):
            entry["fired"] = False
            entry["reason"] = f"validation failed: {why_c}; {why_w}"
            self.log.append(entry)
            return
        self._seen_fps.add(cand.fingerprint)
        world.queue_external_event(to_world_event(cand, "PENDING"))
        self._last_fired_tick = tick
        entry["fired"] = True
        entry["reason"] = "queued via World Engine external queue"
        self.log.append(entry)

    def write_log(self, path: Path):
        path.write_text(json.dumps(
            {"interventions": self.log,
             "fired": sum(1 for e in self.log if e.get("fired"))},
            ensure_ascii=False, indent=1), encoding="utf-8")
