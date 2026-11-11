"""Step-1 closing audit — motivation/behaviour consistency.

Hu's two-step roadmap (2026-11-11): step 1 is the emergence layer ONLY.
Its acceptance criteria are exactly three: (1) the event stream runs
normally, (2) characters act reasonably, (3) motivation matches
behaviour. Whether the resulting story is gripping is step 2's problem
(event-stream adaptation) and is NOT judged here.

This module computes a fixed set of metrics over FINISHED runs, from
their persisted artefacts only (acp.sqlite3 + world_log.json +
summary.json). It never runs a simulation and never calls a provider.

Metric definitions (fixed BEFORE the numbers; the report quotes them):

M1  Plan-action alignment. Population: decisions whose context carries
    an active plan (context.plan_step is not null). A step label is
    judged by a published predicate over (action type, target,
    target_location, claim, agent location, owner location); see
    step_consistent(). Two variants:
      M1a — the action is consistent with the plan's RECORDED current
            step (context.plan_step.step);
      M1b — the action is consistent with ANY step of the active plan
            (context.current_plan.steps), the deliberation placeholder
            "decide_next_action" excluded (it has no action signature).
    Decisions whose current step is a placeholder, an unknown label,
    or already satisfied at the agent's location ("moot", e.g.
    return_to_front while already at the front) are excluded from both
    denominators and counted separately. Plan coverage (share of all
    decisions with an active plan) is reported alongside.

M2  Knowledge grounding (no knowledge leaks). An action whose claim
    ASSERTS a proposition must be supported by the agent's own beliefs
    in that same decision's context:
      letter_discussion      -> anonymous_letter_exists, kind
                                'experienced', confidence >= 0.9
      mayor_hint (confront)  -> mayor_embezzled >= 0.55
      mayor_hint (speak/ask) -> mayor_embezzled >= 0.45
      letter_question        -> anonymous_letter_exists present
      mayor_late_payment     -> mayor_business_deal present
    M2 = supported / asserting actions; every violation is listed.
    Two claim types are observational only (NOT in the denominator):
    bluff_hint (a designed bluff — the character asserts what he does
    not know; we record whether a letter belief was indeed absent) and
    answer_mayor (a hedged deflection; we record the speaker's own
    mayor_business_deal / mayor_embezzled beliefs descriptively).
    M2b (reverse check): a non-owner agent may hold
    anonymous_letter_exists with kind 'experienced' in a decision
    context only if an earlier own search actually found the letter or
    a `show` of the letter was delivered to him before that context.

M3  Post-refusal adaptation. Cases: predictions whose error contains a
    refusal mismatch with predicted=False, actual=True (an UNEXPECTED
    refusal). The post window is the same agent's decisions with
    tick >= the prediction's evaluated_tick (the evaluation happens in
    receive(), before that tick's decision). An "exact repeat" is a
    post-window decision whose action signature
    (type:target-or-location:topic-or-object — the agent's own _sig
    format) equals the refused action's. Per case we report the repeat
    count/ticks, each repeat's own refusal_expected and whether it was
    refused again, and the first post-window prediction toward the
    same target whose refusal_expected flipped to True.

M4  Illegal-action attempts. Submissions = agent_decisions rows;
    rejected = execution_result.ok is False (rate + reason histogram;
    provider-error rows counted separately). Committed legality is
    checked independently by replaying world_log.json: every move must
    be to an adjacent (or same) location from the actor's replayed
    position, every speech event's target must be co-located with the
    speaker at that point in the tick's event order, every search must
    happen where the searcher stands, every `show` must come from the
    letter's holder, and replayed end-of-tick positions must equal the
    recorded positions. M4c: every chosen action must be a member of
    its own context's available_actions.

M5  Plan-change justification. From plan_transitions: abandon/replace
    rows carrying a non-empty reason / all abandon/replace rows. If the
    corpus contains no abandon/replace rows the rate is UNDEFINED and
    reported as such. Companion reconstruction from decision contexts:
    distinct plan ids per run and plan-id switches between consecutive
    decisions of an agent — interpretation-driven plan creations are
    counted there, so a gap between "plans that demonstrably existed"
    and "set/replace rows in plan_transitions" exposes audit-trail
    loss.

M6  Prediction-basis truthfulness. For every prediction, four checks
    against independent records of the same decision:
      location       basis.location == context.location
      colocated      basis.colocated == the context perception's
                     co-located agent ids
      trust          basis.trust_in_target == context.trust[target]
                     (None when the action has no target)
      perceived_ids  basis.perceived_ids == an independent
                     reconstruction from event_deliveries: the union,
                     over delivery rows of that agent with row tick <=
                     decision tick, of delivered+muffled event ids,
                     keeping only events whose type the agent's
                     _perceive_event actually memorises
                     (move/speak/ask/confront/show/search/refuse/
                     ambient) and whose first actor is not the agent
                     himself.
    Reported per field and as an all-fields rate, over the full
    population (no sampling).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

# V0.2-B: the audit is a tavern-scoped analysis tool; it reads the
# graph from world_config's single definition (the module-level
# context_builder.ADJACENCY constant no longer exists).
from .world_config import adjacency_of

ADJACENCY = adjacency_of()
# V0.2-B B3: plan_steps.py (the tavern step-label predicate table)
# is deleted; M1's consistency definition is now the design's
# template definition, shared with the runtime's StepEvaluator.
from .plan_evaluator import action_matches_templates
from .plan_evaluator import action_signature as action_sig

AGENTS = ("owner", "merchant", "soldier")
START_POSITIONS = {"owner": "bar", "merchant": "tables", "soldier": "door"}
PERCEIVED_TYPES = {"move", "speak", "ask", "confront", "show", "search",
                   "refuse", "ambient"}


# ---------------------------------------------------------------- loading


def load_run(run_dir: Path) -> dict:
    """Load one finished run's artefacts into plain dicts."""
    from .persistence import (DecisionRow, DeliveryRow, EventRow,
                              PlanTransitionRow, PredictionRow, Store)
    run_dir = Path(run_dir)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    store = Store(summary["run_id"], sqlite_path=str(run_dir / "acp.sqlite3"))
    with store.Session() as s:
        decisions = []
        for r in s.query(DecisionRow).filter_by(run_id=summary["run_id"]) \
                .order_by(DecisionRow.id).all():
            decisions.append({
                "character_id": r.character_id, "tick": r.tick,
                "decision_key": r.decision_key, "error": r.error,
                "context": json.loads(r.context_json) if r.context_json else {},
                "decision": json.loads(r.parsed_output) if r.parsed_output else {},
                "execution_result": (json.loads(r.execution_result)
                                     if r.execution_result else None),
            })
        predictions = {}
        for r in s.query(PredictionRow).filter_by(run_id=summary["run_id"]).all():
            predictions[r.decision_key] = {
                "character_id": r.character_id, "tick": r.tick,
                "action_type": r.action_type,
                "prediction": json.loads(r.prediction_json) if r.prediction_json else {},
                "error": json.loads(r.error_json) if r.error_json else None,
                "evaluated_tick": r.evaluated_tick,
            }
        transitions = [{"tick": r.tick, "character_id": r.character_id,
                        "decision_key": r.decision_key, "plan_id": r.plan_id,
                        "kind": r.kind, "reason": r.reason}
                       for r in s.query(PlanTransitionRow)
                       .filter_by(run_id=summary["run_id"]).all()]
        deliveries = [{"tick": r.tick, "agent_id": r.agent_id,
                       "delivered": json.loads(r.delivered_event_ids or "[]"),
                       "muffled": json.loads(r.muffled_event_ids or "[]")}
                      for r in s.query(DeliveryRow)
                      .filter_by(run_id=summary["run_id"]).all()]
        events = {}
        for r in s.query(EventRow).filter_by(run_id=summary["run_id"]).all():
            events[r.event_id] = json.loads(r.payload) if r.payload else {}
    world_log = json.loads((run_dir / "world_log.json").read_text(encoding="utf-8"))
    return {"run_dir": str(run_dir), "summary": summary,
            "run_id": summary["run_id"], "seed": summary.get("seed"),
            "decisions": decisions, "predictions": predictions,
            "plan_transitions": transitions, "deliveries": deliveries,
            "events": events, "world_log": world_log}


# ---------------------------------------------------------------- helpers


def _belief(ctx: dict, key: str) -> dict | None:
    for b in ctx.get("beliefs", []):
        if b.get("key") == key:
            return b
    return None


def positions_at(run: dict, tick: int) -> dict:
    """Objective positions in force when the envelope of `tick` was
    built: start positions at tick 0, else the world_log record of
    that tick (positions after that tick's commit)."""
    if tick <= 0:
        return dict(START_POSITIONS)
    for rec in run["world_log"]:
        if rec["tick"] == tick:
            return dict(rec["positions"])
    return dict(START_POSITIONS)


# ---------------------------------------------------------------- M1
# V0.2-B B3 (design 5.2): the consistency definition CHANGED with
# the architecture, by the design's own instruction. An action is
# consistent with a step iff it matches one of the step's declared
# action_candidates templates (field-level — the same match the
# runtime's StepEvaluator uses). The old label-predicate values
# (M1a 0.6932 / M1b 0.7727 on the V0.2-A seed group) are NOT
# recomputed here: plan_steps.py is deleted, and V0.2-B_REPORT.md
# presents old and new values side by side without mixing them.
# Steps without templates (legacy free-text steps) are unjudgeable
# under the template definition and counted separately.


def m1_plan_alignment(run: dict) -> dict:
    decs = run["decisions"]
    with_plan = judgeable = legacy_or_unmeasurable = 0
    a_hits = b_hits = 0
    per_step: dict[str, Counter] = defaultdict(Counter)
    step_index_dist: Counter = Counter()
    plan_update_dist: Counter = Counter()
    for d in decs:
        ctx = d["context"]
        plan_update_dist[(d["decision"].get("plan_update") or {}).get("status")] += 1
        ps = ctx.get("plan_step")
        if not ps:
            continue
        with_plan += 1
        step_index_dist[ps.get("step_index")] += 1
        action = (d["decision"] or {}).get("action") or {}
        templates = ps.get("action_candidates") or []
        label = ps.get("step_id") or ps.get("step")
        if not templates:
            legacy_or_unmeasurable += 1
            per_step[label]["excluded"] += 1
            continue
        judgeable += 1
        cur = action_matches_templates(action, templates)
        a_hits += bool(cur)
        per_step[label]["judged"] += 1
        per_step[label]["m1a_consistent"] += bool(cur)
        plan = ctx.get("current_plan") or {}
        all_templates = [t for s in plan.get("steps", [])
                         for t in (s.get("action_candidates") or [])]
        any_hit = action_matches_templates(action, all_templates) \
            if all_templates else False
        b_hits += bool(any_hit)
        per_step[label]["m1b_consistent"] += bool(any_hit)
    total = len(decs)
    return {
        "decisions": total, "with_active_plan": with_plan,
        "plan_coverage": round(with_plan / total, 4) if total else 0.0,
        "judgeable": judgeable,
        "excluded_legacy_or_unmeasurable": legacy_or_unmeasurable,
        "m1a_current_step": {"consistent": a_hits, "judged": judgeable,
                             "rate": round(a_hits / judgeable, 4) if judgeable else None},
        "m1b_any_plan_step": {"consistent": b_hits, "judged": judgeable,
                              "rate": round(b_hits / judgeable, 4) if judgeable else None},
        "step_index_distribution": {str(k): v for k, v in sorted(
            step_index_dist.items(), key=lambda kv: str(kv[0]))},
        "plan_update_status_distribution": dict(plan_update_dist),
        "unknown_step_labels": {},
        "per_step": {k: dict(v) for k, v in sorted(per_step.items())},
    }


# ---------------------------------------------------------------- M2


ASSERTION_RULES = {
    # claim -> (required belief key, kind or None, min confidence)
    "letter_discussion": ("anonymous_letter_exists", "experienced", 0.9),
    "letter_question": ("anonymous_letter_exists", None, None),
    "mayor_late_payment": ("mayor_business_deal", None, None),
}


def _assertion_check(action: dict, ctx: dict) -> tuple[str, bool] | None:
    """(rule description, supported) for fact-asserting actions, else
    None when the action asserts no auditable proposition."""
    claim = action.get("claim")
    if claim == "mayor_hint":
        b = _belief(ctx, "mayor_embezzled")
        need = 0.55 if action.get("type") == "confront" else 0.45
        return (f"mayor_embezzled >= {need}",
                bool(b and b["confidence"] >= need))
    rule = ASSERTION_RULES.get(claim or "")
    if rule is None:
        return None
    key, kind, conf = rule
    b = _belief(ctx, key)
    ok = bool(b) and (kind is None or b["kind"] == kind) \
        and (conf is None or b["confidence"] >= conf)
    return (f"{key}" + (f"/{kind}" if kind else "")
            + (f">={conf}" if conf else " present"), ok)


def m2_knowledge_grounding(run: dict) -> dict:
    checked = supported = 0
    violations = []
    observational: Counter = Counter()
    for d in run["decisions"]:
        action = (d["decision"] or {}).get("action") or {}
        ctx = d["context"]
        chk = _assertion_check(action, ctx)
        if chk:
            checked += 1
            if chk[1]:
                supported += 1
            else:
                violations.append({
                    "agent": d["character_id"], "tick": d["tick"],
                    "action": action.get("type"), "claim": action.get("claim"),
                    "rule": chk[0]})
        claim = action.get("claim")
        if claim == "bluff_hint":
            has = _belief(ctx, "anonymous_letter_exists") is not None
            observational[f"bluff_hint with letter belief "
                          f"{'present' if has else 'absent'}"] += 1
        elif claim == "answer_mayor":
            deal = _belief(ctx, "mayor_business_deal") is not None
            emb = _belief(ctx, "mayor_embezzled") is not None
            observational[f"answer_mayor speaker mayor_business_deal="
                          f"{deal} mayor_embezzled={emb}"] += 1
    # M2b: experienced letter belief must trace to an own find or a show
    events = run["events"]
    shown_to: dict[str, set[int]] = defaultdict(set)
    for row in run["deliveries"]:
        for eid in row["delivered"]:
            ev = events.get(eid) or {}
            if ev.get("type") == "show" and ev.get("object") == "anonymous_letter":
                shown_to[row["agent_id"]].add(row["tick"])
    found_by: dict[str, set[int]] = defaultdict(set)
    for d in run["decisions"]:
        er = d["execution_result"] or {}
        if "anonymous_letter" in (er.get("found") or []):
            found_by[d["character_id"]].add(d["tick"])
    reverse_violations = []
    seen_agents = set()
    for d in sorted(run["decisions"], key=lambda x: (x["character_id"], x["tick"])):
        aid = d["character_id"]
        if aid == "owner" or aid in seen_agents:
            continue
        b = _belief(d["context"], "anonymous_letter_exists")
        if b and b["kind"] == "experienced":
            seen_agents.add(aid)
            traced = any(t < d["tick"] for t in found_by.get(aid, set())) or \
                any(t <= d["tick"] for t in shown_to.get(aid, set()))
            if not traced:
                reverse_violations.append({
                    "agent": aid, "first_experienced_context_tick": d["tick"]})
    return {
        "asserting_actions": checked, "supported": supported,
        "rate": round(supported / checked, 4) if checked else None,
        "violations": violations,
        "observational": dict(observational),
        "reverse_check": {"agents_checked": sorted(seen_agents),
                          "violations": reverse_violations},
    }


# ---------------------------------------------------------------- M3


def m3_refusal_adaptation(run: dict) -> dict:
    decs = {(d["character_id"], d["tick"]): d for d in run["decisions"]}
    cases = []
    complementary = 0
    for key, p in sorted(run["predictions"].items()):
        err = p["error"]
        if not err or "refusal" not in (err.get("mismatches") or []):
            continue
        det = (err.get("details") or {}).get("refusal") or {}
        if det.get("predicted") is True and det.get("actual") is False:
            complementary += 1
            continue
        if not (det.get("predicted") is False and det.get("actual") is True):
            continue
        aid, pred_tick, eval_tick = p["character_id"], p["tick"], p["evaluated_tick"]
        refused = decs.get((aid, pred_tick))
        if refused is None:
            continue
        refused_action = (refused["decision"] or {}).get("action") or {}
        rsig = action_sig(refused_action)
        target = refused_action.get("target")
        repeats = []
        flip = None
        for (a2, t2), d2 in sorted(decs.items()):
            if a2 != aid or t2 < eval_tick:
                continue
            a2ct = (d2["decision"] or {}).get("action") or {}
            p2 = run["predictions"].get(d2["decision_key"]) or {}
            pj = p2.get("prediction") or {}
            if flip is None and pj.get("target") == target \
                    and pj.get("refusal_expected") is True:
                flip = {"tick": t2, "action_type": pj.get("action_type")}
            if action_sig(a2ct) == rsig:
                err2 = p2.get("error") or {}
                det2 = (err2.get("details") or {}).get("refusal") or {}
                repeats.append({
                    "tick": t2,
                    "refusal_expected": pj.get("refusal_expected"),
                    "refused_again": det2.get("actual") if "refusal" in (
                        err2.get("details") or {}) else None})
        cases.append({
            "agent": aid, "prediction_tick": pred_tick,
            "evaluated_tick": eval_tick, "target": target,
            "refused_signature": rsig, "exact_repeats": repeats,
            "repeat_count": len(repeats),
            "expectation_flipped_to_refusal": flip})
    return {
        "unexpected_refusal_cases": cases,
        "case_count": len(cases),
        "cases_with_exact_repeat": sum(1 for c in cases if c["repeat_count"]),
        "total_exact_repeats": sum(c["repeat_count"] for c in cases),
        "repeats_refused_again": sum(
            1 for c in cases for r in c["exact_repeats"] if r["refused_again"]),
        "expected_refusal_but_engaged_cases": complementary,
    }


# ---------------------------------------------------------------- M4


def m4_legality(run: dict) -> dict:
    total = rejected = provider_errors = 0
    reasons: Counter = Counter()
    available_checked = available_ok = 0
    for d in run["decisions"]:
        total += 1
        if d["error"]:
            provider_errors += 1
        er = d["execution_result"]
        if er is not None and er.get("ok") is False:
            rejected += 1
            reasons[er.get("reason")] += 1
        # M4c: the chosen action must be offered by its own context
        ctx = d["context"]
        action = (d["decision"] or {}).get("action") or {}
        offered = ctx.get("available_actions") or []
        if offered and action:
            available_checked += 1
            hit = any(
                o.get("type") == action.get("type")
                and ("target" not in o or o.get("target") == action.get("target"))
                and ("target_location" not in o
                     or o.get("target_location") == action.get("target_location"))
                and ("object" not in o or o.get("object") == action.get("object"))
                for o in offered)
            available_ok += bool(hit)
    # committed-event legality: replay the objective log
    pos = dict(START_POSITIONS)
    violations = []
    for rec in run["world_log"]:
        for ev in rec["events"]:
            t = ev["type"]
            actors = ev.get("actors") or []
            actor = actors[0] if actors else None
            if t == "move" and actor:
                if ev.get("to") != ev.get("from") and \
                        ev.get("to") not in ADJACENCY.get(ev.get("from"), set()):
                    violations.append({"tick": rec["tick"], "event_id": ev["event_id"],
                                       "check": "move_adjacency"})
                if pos.get(actor) != ev.get("from"):
                    violations.append({"tick": rec["tick"], "event_id": ev["event_id"],
                                       "check": "move_origin_mismatch"})
                pos[actor] = ev.get("to")
            elif t in ("speak", "ask", "confront") and len(actors) > 1:
                if pos.get(actors[1]) != pos.get(actor):
                    violations.append({"tick": rec["tick"], "event_id": ev["event_id"],
                                       "check": "speech_target_not_colocated"})
            elif t == "search" and actor:
                if ev.get("location") != pos.get(actor):
                    violations.append({"tick": rec["tick"], "event_id": ev["event_id"],
                                       "check": "search_not_at_actor_location"})
            elif t == "show" and actor:
                if actor != "owner":
                    violations.append({"tick": rec["tick"], "event_id": ev["event_id"],
                                       "check": "show_by_non_holder"})
        if rec["positions"] != pos:
            violations.append({"tick": rec["tick"], "event_id": None,
                               "check": "end_of_tick_positions_mismatch"})
    return {
        "submissions": total, "rejected": rejected,
        "rejection_rate": round(rejected / total, 4) if total else 0.0,
        "rejection_reasons": dict(reasons),
        "provider_error_rows": provider_errors,
        "available_action_check": {
            "checked": available_checked, "member": available_ok,
            "rate": round(available_ok / available_checked, 4)
            if available_checked else None},
        "committed_legality": {"events_replayed": sum(
            len(rec["events"]) for rec in run["world_log"]),
            "violations": violations},
    }


# ---------------------------------------------------------------- M5


def m5_plan_changes(run: dict) -> dict:
    trs = run["plan_transitions"]
    changes = [t for t in trs if t["kind"] in ("abandon", "replace")]
    justified = sum(1 for t in changes if (t.get("reason") or "").strip())
    kinds = Counter(t["kind"] for t in trs)
    # reconstruction from decision contexts
    per_agent: dict[str, list] = defaultdict(list)
    plan_ids: set[str] = set()
    for d in sorted(run["decisions"], key=lambda x: (x["character_id"], x["tick"])):
        plan = d["context"].get("current_plan")
        pid = plan.get("plan_id") if plan else None
        if pid:
            plan_ids.add(pid)
        per_agent[d["character_id"]].append(pid)
    switches = 0
    for aid, seq in per_agent.items():
        switches += sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    table_plan_ids = {t["plan_id"] for t in trs if t.get("plan_id")}
    return {
        "plan_transitions_rows": len(trs),
        "transition_kinds": dict(kinds),
        "abandon_or_replace_rows": len(changes),
        "with_reason": justified,
        "rate": (round(justified / len(changes), 4) if changes else None),
        "reconstruction": {
            "distinct_plan_ids_in_contexts": len(plan_ids),
            "plan_id_switches_between_decisions": switches,
            "set_or_replace_rows_in_table": kinds.get("set", 0) + kinds.get("replace", 0),
            "distinct_plan_ids_in_table": len(table_plan_ids),
        },
    }


# ---------------------------------------------------------------- M6


def m6_basis_truth(run: dict) -> dict:
    events = run["events"]
    # cumulative perceived reconstruction per agent: tick -> set
    recon: dict[str, dict[int, set]] = {}
    for aid in AGENTS:
        rows = sorted((r for r in run["deliveries"] if r["agent_id"] == aid),
                      key=lambda r: r["tick"])
        cum: dict[int, set] = {}
        seen: set = set()
        for row in rows:
            for eid in row["delivered"] + row["muffled"]:
                ev = events.get(eid) or {}
                actors = ev.get("actors") or []
                if ev.get("type") in PERCEIVED_TYPES \
                        and (not actors or actors[0] != aid):
                    seen.add(eid)
            cum[row["tick"]] = set(seen)
        recon[aid] = cum
    decs = {(d["character_id"], d["tick"]): d for d in run["decisions"]}
    fields: Counter = Counter()
    totals: Counter = Counter()
    bad_examples = []
    for key, p in run["predictions"].items():
        d = decs.get((p["character_id"], p["tick"]))
        if d is None:
            continue
        ctx = d["context"]
        basis = (p["prediction"] or {}).get("basis") or {}
        target = (p["prediction"] or {}).get("target")
        checks = {
            "location": basis.get("location") == ctx.get("location"),
            "colocated": basis.get("colocated") == [
                c["id"] for c in (ctx.get("perception") or {}).get("colocated", [])],
            "trust": basis.get("trust_in_target") == (
                (ctx.get("trust") or {}).get(target) if target else None),
        }
        seen = set()
        for rt, s in recon[p["character_id"]].items():
            if rt <= p["tick"]:
                seen = s
        checks["perceived_ids"] = set(basis.get("perceived_ids") or []) == seen
        for f, ok in checks.items():
            totals[f] += 1
            fields[f] += bool(ok)
            if not ok and len(bad_examples) < 5:
                bad_examples.append({"agent": p["character_id"], "tick": p["tick"],
                                     "field": f})
        totals["all_fields"] += 1
        fields["all_fields"] += all(checks.values())
    return {
        "predictions_checked": totals["all_fields"],
        "per_field": {f: {"match": fields[f], "total": totals[f],
                          "rate": round(fields[f] / totals[f], 4) if totals[f] else None}
                      for f in ("location", "colocated", "trust",
                                "perceived_ids", "all_fields")},
        "mismatch_examples": bad_examples,
    }


# ---------------------------------------------------------------- top level


def audit_run(run_dir: Path) -> dict:
    run = load_run(Path(run_dir))
    return {
        "run_id": run["run_id"], "seed": run["seed"],
        "run_dir": run["run_dir"],
        "decisions": len(run["decisions"]),
        "events": run["summary"].get("total_events"),
        "m1_plan_alignment": m1_plan_alignment(run),
        "m2_knowledge_grounding": m2_knowledge_grounding(run),
        "m3_refusal_adaptation": m3_refusal_adaptation(run),
        "m4_legality": m4_legality(run),
        "m5_plan_changes": m5_plan_changes(run),
        "m6_basis_truth": m6_basis_truth(run),
    }


def _sum_rates(reports: list[dict], path: tuple[str, ...],
               num_key: str, den_key: str) -> dict:
    num = den = 0
    for r in reports:
        node = r
        for k in path:
            node = node[k]
        num += node[num_key]
        den += node[den_key]
    return {"numerator": num, "denominator": den,
            "rate": round(num / den, 4) if den else None}


def aggregate(reports: list[dict]) -> dict:
    """Corpus-level aggregation: rates are recomputed from summed
    numerators/denominators, never averaged from per-run rates."""
    agg = {
        "runs": len(reports),
        "decisions": sum(r["decisions"] for r in reports),
        "m1a": _sum_rates(reports, ("m1_plan_alignment", "m1a_current_step"),
                          "consistent", "judged"),
        "m1b": _sum_rates(reports, ("m1_plan_alignment", "m1b_any_plan_step"),
                          "consistent", "judged"),
        "m1_judgeable": sum(r["m1_plan_alignment"]["judgeable"] for r in reports),
        "m1_with_plan": sum(r["m1_plan_alignment"]["with_active_plan"] for r in reports),
        "m2": _sum_rates(reports, ("m2_knowledge_grounding",), "supported",
                         "asserting_actions"),
        "m2_violations": sum(len(r["m2_knowledge_grounding"]["violations"])
                             for r in reports),
        "m2_reverse_violations": sum(len(
            r["m2_knowledge_grounding"]["reverse_check"]["violations"])
            for r in reports),
        "m3_cases": sum(r["m3_refusal_adaptation"]["case_count"] for r in reports),
        "m3_cases_with_repeat": sum(
            r["m3_refusal_adaptation"]["cases_with_exact_repeat"] for r in reports),
        "m3_total_repeats": sum(
            r["m3_refusal_adaptation"]["total_exact_repeats"] for r in reports),
        "m3_repeats_refused_again": sum(
            r["m3_refusal_adaptation"]["repeats_refused_again"] for r in reports),
        "m4": _sum_rates(reports, ("m4_legality",), "rejected", "submissions"),
        "m4_committed_violations": sum(len(
            r["m4_legality"]["committed_legality"]["violations"]) for r in reports),
        "m5_abandon_replace_rows": sum(
            r["m5_plan_changes"]["abandon_or_replace_rows"] for r in reports),
        "m5_plans_in_contexts": sum(
            r["m5_plan_changes"]["reconstruction"]["distinct_plan_ids_in_contexts"]
            for r in reports),
        "m5_set_replace_rows": sum(
            r["m5_plan_changes"]["reconstruction"]["set_or_replace_rows_in_table"]
            for r in reports),
        "m6": _sum_rates(reports, ("m6_basis_truth", "per_field", "all_fields"),
                         "match", "total"),
    }
    return agg


def render_markdown(report: dict) -> str:
    runs = report["runs"]
    agg = report["aggregate"]
    lines = ["# 第一步收官审计汇总（工具生成）", "",
             "指标定义见 `v02/audit.py` 模块文档与 STEP1_AUDIT_REPORT.md。", "",
             "| run | 种子 | 决策 | 事件 | M1a 当前步 | M1b 任一步 | M2 知识支撑 | "
             "M3 被拒案例/重复 | M4 拒单率 | M4 已提交违规 | M6 依据真实 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        m1, m2, m3 = r["m1_plan_alignment"], r["m2_knowledge_grounding"], \
            r["m3_refusal_adaptation"]
        m4, m6 = r["m4_legality"], r["m6_basis_truth"]
        lines.append(
            f"| {r['run_id']} | {r['seed']} | {r['decisions']} | {r['events']} | "
            f"{m1['m1a_current_step']['rate']}（{m1['m1a_current_step']['consistent']}/"
            f"{m1['m1a_current_step']['judged']}） | "
            f"{m1['m1b_any_plan_step']['rate']}（{m1['m1b_any_plan_step']['consistent']}/"
            f"{m1['m1b_any_plan_step']['judged']}） | "
            f"{m2['rate']}（{m2['supported']}/{m2['asserting_actions']}） | "
            f"{m3['case_count']}/{m3['total_exact_repeats']} | "
            f"{m4['rejection_rate']}（{m4['rejected']}/{m4['submissions']}） | "
            f"{len(m4['committed_legality']['violations'])} | "
            f"{m6['per_field']['all_fields']['rate']} |")
    lines += ["",
              f"聚合（{agg['runs']} 个 run，{agg['decisions']} 次决策）："
              f"M1a {agg['m1a']['rate']}（{agg['m1a']['numerator']}/{agg['m1a']['denominator']}）；"
              f"M1b {agg['m1b']['rate']}；M2 {agg['m2']['rate']}（违例 "
              f"{agg['m2_violations']}，反向违例 {agg['m2_reverse_violations']}）；"
              f"M3 案例 {agg['m3_cases']}、重复 {agg['m3_total_repeats']}、"
              f"重复再被拒 {agg['m3_repeats_refused_again']}；"
              f"M4 拒单 {agg['m4']['rate']}、已提交违规 {agg['m4_committed_violations']}；"
              f"M5 abandon/replace 行 {agg['m5_abandon_replace_rows']}、"
              f"context 中计划 {agg['m5_plans_in_contexts']} 个 vs 表中 set/replace 行 "
              f"{agg['m5_set_replace_rows']}；M6 {agg['m6']['rate']}。",
              ""]
    return "\n".join(lines)


def audit_runs(run_dirs: list[Path]) -> dict:
    reports = [audit_run(d) for d in run_dirs]
    report = {"kind": "step1_consistency_audit", "runs": reports,
              "aggregate": aggregate(reports)}
    return report
