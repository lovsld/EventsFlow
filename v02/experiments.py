"""Phase D — experiments & analysis.

Three instruments, all built on the deterministic rule/mock stack so
every comparison is strictly reproducible:

1. Seed-group runner: the same configuration under a group of seeds,
   one run directory per seed, plus a comparison table (JSON/CSV/MD)
   with per-run outcome metrics pulled from summary.json and the
   run's SQLite audit store.

2. Diversity & consistency metrics (definitions are part of the API,
   not just the report):
   - Trajectory signature of a run: the SET of (tick, event_type,
     primary_actor) triples over its world_log events.
   - Trajectory diversity of a group: the mean Jaccard distance
     (1 - |A∩B| / |A∪B|) between the signature sets of every pair of
     runs. 0 = all trajectories identical as event sets; 1 = disjoint.
   - Key-outcome consistency: for a tracked emergent node (default:
     "the soldier ends the run having personally read the letter" —
     belief anonymous_letter_exists with kind 'experienced' and
     confidence >= 1.0), the fraction of runs in the group where it
     holds. Belief distributions are reported as the raw per-seed
     values, never averaged silently over missing beliefs.

3. Counterfactual runner: control vs treatment with the SAME seed and
   configuration; the treatment differs by exactly one thing — a
   Broker suppression rule that keeps one class of delivery from one
   recipient (see broker.match_suppression). The objective events are
   NOT touched: the utterance still happens, one agent simply never
   receives it, exactly like the information-isolation rules. The
   comparison verifies this from the artefacts (delivery diff at the
   target events; objective-event prefix identical up to the first
   downstream divergence) and then measures the downstream effect on
   the target agent's belief trajectory and actions.
"""
from __future__ import annotations

import asyncio
import csv
import io
import itertools
import json
from pathlib import Path

from .broker import match_suppression
from .runner import run_simulation

# ---------------------------------------------------------------- metrics


def event_signature_set(world_log: list[dict]) -> set[tuple]:
    """The trajectory signature: {(tick, type, primary_actor)}."""
    sig = set()
    for rec in world_log:
        for ev in rec["events"]:
            actors = ev.get("actors") or []
            sig.add((rec["tick"], ev["type"], actors[0] if actors else ""))
    return sig


def jaccard_distance(a: set, b: set) -> float:
    """1 - |A∩B|/|A∪B|; two empty sets have distance 0."""
    union = a | b
    if not union:
        return 0.0
    return 1.0 - len(a & b) / len(union)


def mean_pairwise_jaccard(sets: list[set]) -> dict:
    """Mean Jaccard distance over all unordered pairs, with the pairs."""
    pairs = []
    for (i, a), (j, b) in itertools.combinations(enumerate(sets), 2):
        pairs.append({"pair": [i, j], "distance": round(jaccard_distance(a, b), 4)})
    mean = round(sum(p["distance"] for p in pairs) / len(pairs), 4) if pairs else 0.0
    return {"mean": mean, "pairs": pairs}


def outcome_consistency(run_metrics: list[dict], outcome: str) -> dict:
    """Fraction of runs where a named boolean outcome column holds.
    `outcome` names a boolean field of the per-run metric dicts."""
    hits = sum(1 for m in run_metrics if m.get(outcome))
    return {"outcome": outcome, "hits": hits, "runs": len(run_metrics),
            "rate": round(hits / len(run_metrics), 4) if run_metrics else 0.0}


# ---------------------------------------------------------------- run data


def _belief(final_beliefs: dict, agent: str, key: str) -> dict | None:
    b = (final_beliefs.get(agent) or {}).get(key)
    return {"confidence": b["confidence"], "kind": b["kind"]} if b else None


DIALOGUE_TYPES = ("speak", "ask", "confront")


def _v02a_metrics(run_dir: Path, summary: dict) -> dict:
    """V0.2-A experiment metrics (task book section 14), computed only
    from artefacts the run itself persisted. Anything a historical run
    never recorded is reported as None — never reconstructed or
    invented (latency, pre-V0.2-A repair counts)."""
    from .persistence import (DecisionRow, PlanTransitionRow, Store)
    out: dict = {}
    # ---- world-log level: dialogue volume ----
    dialogue = {t: 0 for t in DIALOGUE_TYPES}
    world_log_path = run_dir / "world_log.json"
    if world_log_path.exists():
        for snap in json.loads(world_log_path.read_text(encoding="utf-8")):
            for ev in snap.get("events", []):
                if ev.get("type") in dialogue:
                    dialogue[ev["type"]] += 1
    out["dialogue_by_type"] = dialogue
    out["dialogue_count"] = sum(dialogue.values())
    # ---- information propagation coverage (tavern key object) ----
    fb = summary.get("final_beliefs", {})
    know = experienced = 0
    for beliefs in fb.values():
        b = (beliefs or {}).get("anonymous_letter_exists")
        if b:
            know += 1
            if b["kind"] == "experienced":
                experienced += 1
    out["letter_knowledge_coverage"] = {
        "agents": len(fb), "know": know, "experienced": experienced,
        "heard_or_inferred_only": know - experienced}
    # ---- goal completion (final character states) ----
    goals_by_status: dict[str, int] = {}
    states_dir = run_dir / "characters"
    if states_dir.exists():
        for state_path in sorted(states_dir.glob("*/state.json")):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            for g in state.get("goals", []):
                status = g.get("status", "unspecified_pre_lifecycle")
                goals_by_status[status] = goals_by_status.get(status, 0) + 1
    total_goals = sum(goals_by_status.values())
    out["goals_by_status"] = goals_by_status
    # pre-V0.2-A artefacts carry no lifecycle status at all: the rate
    # is then not measurable (None), not zero
    out["goal_completion_rate"] = (
        round(goals_by_status.get("completed", 0) / total_goals, 3)
        if total_goals and "unspecified_pre_lifecycle" not in goals_by_status
        else None)
    # ---- decision / plan level (the run's DB) ----
    db_path = run_dir / "acp.sqlite3"
    decisions = successes = fallbacks = 0
    tokens_in = tokens_out = 0
    llm_calls = 0
    sigs_by_agent: dict[str, list[str]] = {}
    plan_advances: dict[str, int] = {}
    plan_completed: set[str] = set()
    plans_seen: set[str] = set()
    if db_path.exists():
        store = Store(summary["run_id"], sqlite_path=str(db_path))
        with store.Session() as s:
            rows = s.query(DecisionRow).filter_by(
                run_id=summary["run_id"]).order_by(
                DecisionRow.character_id, DecisionRow.tick).all()
            for r in rows:
                if r.provider not in ("rule", "mock"):
                    llm_calls += 1
                    tokens_in += r.input_tokens or 0
                    tokens_out += r.output_tokens or 0
                if r.error:
                    fallbacks += 1
                    continue
                if not r.parsed_output:
                    continue
                decisions += 1
                try:
                    result = json.loads(r.execution_result) \
                        if r.execution_result else {}
                except json.JSONDecodeError:
                    result = {}
                if result.get("ok"):
                    successes += 1
                try:
                    action = json.loads(r.parsed_output).get("action", {})
                except json.JSONDecodeError:
                    action = {}
                sig = "|".join(str(action.get(k)) for k in
                               ("type", "target", "target_location",
                                "claim", "topic"))
                sigs_by_agent.setdefault(r.character_id, []).append(sig)
            for plan_id, kind, after_json in s.query(
                    PlanTransitionRow.plan_id, PlanTransitionRow.kind,
                    PlanTransitionRow.after_json).filter_by(
                    run_id=summary["run_id"]).all():
                plans_seen.add(plan_id)
                if kind == "advance":
                    plan_advances[plan_id] = \
                        plan_advances.get(plan_id, 0) + 1
                # some transition kinds snapshot a scalar (commitment
                # decay stores the float), not a plan dict
                after = json.loads(after_json) if after_json else None
                if isinstance(after, dict) \
                        and after.get("status") == "completed":
                    plan_completed.add(plan_id)
    total_actions = sum(len(v) for v in sigs_by_agent.values())
    repeats = sum(1 for sigs in sigs_by_agent.values()
                  for a, b in zip(sigs, sigs[1:]) if a == b)
    persistent = {p for p in plans_seen
                  if plan_advances.get(p, 0) >= 2 or p in plan_completed}
    out.update({
        "decisions_recorded": decisions,
        "action_success_rate": (round(successes / decisions, 3)
                                if decisions else None),
        "repeat_action_rate": (round(repeats / total_actions, 3)
                               if total_actions else None),
        "fallback_decisions": fallbacks,
        "plans_total": len(plans_seen),
        "plan_persistence_rate": (round(len(persistent) / len(plans_seen), 3)
                                  if plans_seen else None),
        "llm": {
            "calls": llm_calls,
            "input_tokens": tokens_in, "output_tokens": tokens_out,
            # repair retries were only counted from V0.2-A runs on;
            # older summaries simply lack the aggregate
            "repairs_total": summary.get("llm_repairs_total"),
            "json_repair_rate": (
                round(summary["llm_repairs_total"] / llm_calls, 3)
                if summary.get("llm_repairs_total") is not None
                and llm_calls else None),
            # DecisionRow never persisted per-call latency; historical
            # runs cannot yield it and it is reported missing, not faked
            "latency_ms": None,
            "latency_note": "not recorded in persisted artefacts",
        },
    })
    return out


def _v02b_metrics(run_dir: Path, summary: dict) -> dict:
    """V0.2-B metric family (design section 9 / B-design section 6),
    computed only from the run's own artefacts. Denominators follow
    the design exactly; anything a run never recorded is None.

    Goal metrics read final character states (status / progress /
    conditions) plus the goal_changes table (ever-activated etc.).
    Plan metrics read plan_transitions kinds — the B3 kinds
    (step_evaluated / blocked / recovery) simply do not occur in
    earlier runs, yielding honest zeros/Nones there."""
    from .persistence import (DecisionRow, GoalChangeRow,
                              PlanTransitionRow, Store)
    out: dict = {}
    states: dict[str, dict] = {}
    states_dir = run_dir / "characters"
    if states_dir.exists():
        for state_path in sorted(states_dir.glob("*/state.json")):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            states[state["character_id"]] = state

    db_path = run_dir / "acp.sqlite3"
    goal_change_kinds: dict[tuple[str, str], set] = {}
    decisions_by_agent: dict[str, list[dict]] = {}
    plan_kinds: dict[str, list[str]] = {}
    step_eval_rows: list[dict] = []
    recovery_rows: list[dict] = []
    plan_step_shapes: dict[str, list] = {}
    if db_path.exists():
        store = Store(summary["run_id"], sqlite_path=str(db_path))
        with store.Session() as s:
            for r in s.query(GoalChangeRow).filter_by(
                    run_id=summary["run_id"]).all():
                goal_change_kinds.setdefault(
                    (r.character_id, r.goal_key), set()).add(r.kind)
            for r in s.query(DecisionRow).filter_by(
                    run_id=summary["run_id"]).order_by(
                    DecisionRow.character_id, DecisionRow.tick).all():
                if r.error or not r.parsed_output:
                    continue
                try:
                    parsed = json.loads(r.parsed_output)
                    ctx = json.loads(r.context_json) if r.context_json \
                        else {}
                except json.JSONDecodeError:
                    continue
                decisions_by_agent.setdefault(r.character_id, []).append(
                    {"tick": r.tick, "parsed": parsed, "ctx": ctx})
                plan = ctx.get("current_plan")
                if plan and plan.get("plan_id"):
                    plan_step_shapes[plan["plan_id"]] = \
                        plan.get("steps") or []
            for r in s.query(PlanTransitionRow).filter_by(
                    run_id=summary["run_id"]).all():
                plan_kinds.setdefault(r.plan_id, []).append(r.kind)
                if r.kind == "step_evaluated":
                    try:
                        after = json.loads(r.after_json) \
                            if r.after_json else {}
                    except json.JSONDecodeError:
                        after = {}
                    step_eval_rows.append(
                        {"plan_id": r.plan_id, "after": after})
                elif r.kind == "recovery":
                    recovery_rows.append(
                        {"plan_id": r.plan_id, "reason": r.reason or ""})

    # ---------- goal family (metrics 1-5 + standing extras) ----------
    goals_with_condition = ever_active = pursued = completed = 0
    conditioned_ever_active = 0
    failed = abandoned = 0
    achievement_progress: list[float] = []
    maintenance_failed = 0
    linked_goals: set[tuple[str, str]] = set()
    for (cid, decs) in decisions_by_agent.items():
        for d in decs:
            plan = d["ctx"].get("current_plan")
            if plan and plan.get("goal_id"):
                linked_goals.add((cid, plan["goal_id"]))
    for cid, state in states.items():
        for g in state.get("goals", []):
            key = (cid, g["key"])
            kinds = goal_change_kinds.get(key, set())
            has_condition = g.get("activation_condition") is not None
            if has_condition:
                goals_with_condition += 1
            active_ever = (not has_condition) \
                or g.get("status") != "inactive" or "activated" in kinds
            if active_ever:
                ever_active += 1
                if has_condition:
                    conditioned_ever_active += 1
                was_pursued = key in linked_goals or any(
                    d["parsed"].get("selected_goal_id") == g["key"]
                    for d in decisions_by_agent.get(cid, []))
                if was_pursued:
                    pursued += 1
                if g.get("status") == "completed":
                    completed += 1
                if g.get("kind") == "achievement" \
                        and g.get("progress") is not None:
                    achievement_progress.append(g["progress"])
            if g.get("status") == "failed":
                failed += 1
                if g.get("kind") == "maintenance":
                    maintenance_failed += 1
            if g.get("status") == "abandoned":
                abandoned += 1
    total_goals = sum(len(st.get("goals", [])) for st in states.values())
    out["goal_activation_rate"] = (
        round(conditioned_ever_active / goals_with_condition, 3)
        if goals_with_condition else None)
    out["goal_activation_rate_all_goals"] = (
        round(ever_active / total_goals, 3) if total_goals else None)
    out["goal_pursuit_rate"] = (
        round(pursued / ever_active, 3) if ever_active else None)
    # metric 3: the NEW denominator is activated goals; the V0.2-A
    # denominator (all goals) is reported alongside, deprecated
    out["goal_completion_rate"] = (
        round(completed / ever_active, 3) if ever_active else None)
    out["goal_completion_rate_deprecated_denominator"] = (
        round(completed / total_goals, 3) if total_goals else None)
    out["goal_progress_mean"] = (
        round(sum(achievement_progress) / len(achievement_progress), 3)
        if achievement_progress else None)
    out["maintenance_goal_failures"] = maintenance_failed
    out["goal_failure_rate"] = (
        round(failed / ever_active, 3) if ever_active else None)
    out["goal_abandonment_count"] = abandoned
    out["unpursued_goal_count"] = ever_active - pursued
    # metric 5: goal switching per character
    switches = switch_base = 0
    for decs in decisions_by_agent.values():
        seq = [d["parsed"].get("selected_goal_id") for d in decs]
        for a, b in zip(seq, seq[1:]):
            switch_base += 1
            if a != b:
                switches += 1
    out["goal_switching_rate"] = (
        round(switches / switch_base, 3) if switch_base else None)

    # ---------- plan family (metrics 6-10; B3 kinds may be absent) ----------
    created = {pid for pid, kinds in plan_kinds.items()
               if "set" in kinds or "replace" in kinds}
    ever_blocked = {pid for pid, kinds in plan_kinds.items()
                    if "blocked" in kinds}
    recovered = {pid for pid, kinds in plan_kinds.items()
                 if "recovery" in kinds}
    replanned = {pid for pid, kinds in plan_kinds.items()
                 if "replaced" in kinds or "replace" in kinds}
    # B3: a replan recovery closes the old plan with an "abandon"
    # row (reason "replanned") plus a recovery row — count those too
    replanned |= {r["plan_id"] for r in recovery_rows
                  if "strategy=replan" in r["reason"]}
    out["plan_blocked_rate"] = (
        round(len(ever_blocked) / len(created), 3) if created else None)
    out["plan_recovery_rate"] = (
        round(len(recovered) / len(ever_blocked), 3)
        if ever_blocked else None)
    out["plan_recovery_abandon_after_blocked"] = len(
        {r["plan_id"] for r in recovery_rows
         if r["plan_id"] in ever_blocked
         and "strategy=abandon" in r["reason"]})
    out["replan_rate"] = (
        round(len(replanned) / len(created), 3) if created else None)
    out["plans_created"] = len(created)
    out["plans_ever_blocked"] = len(ever_blocked)
    # metric 6: step fulfilment over CONDITIONAL-step evaluations
    # (legacy evaluations are excluded from the denominator)
    cond_evals = [r for r in step_eval_rows
                  if not r["after"].get("legacy")]
    fulfilled_evals = [r for r in cond_evals
                       if r["after"].get("fulfilled")]
    out["plan_step_fulfillment_rate"] = (
        round(len(fulfilled_evals) / len(cond_evals), 3)
        if cond_evals else None)
    out["step_evaluations_conditional"] = len(cond_evals)
    out["step_evaluations_fulfilled"] = len(fulfilled_evals)
    # metric 9: retry recoveries + repeat evaluations (2nd and later
    # evaluations of the same (plan, step))
    retry_recoveries = sum(1 for r in recovery_rows
                           if "strategy=retry" in r["reason"])
    seen_steps: dict[tuple, int] = {}
    repeat_evals = 0
    for r in cond_evals:
        key = (r["plan_id"], r["after"].get("step_id"))
        seen_steps[key] = seen_steps.get(key, 0) + 1
        if seen_steps[key] >= 2:
            repeat_evals += 1
    out["plan_retry_count"] = retry_recoveries + repeat_evals
    out["recovery_strategies"] = {
        s: sum(1 for r in recovery_rows if f"strategy={s}" in r["reason"])
        for s in ("retry", "replan", "suspend", "abandon", "skip",
                  "reactivate")}
    # legacy step share over the plans visible in decision contexts
    total_steps = legacy_steps = 0
    for steps in plan_step_shapes.values():
        for stp in steps:
            total_steps += 1
            if not isinstance(stp, dict) \
                    or stp.get("success_condition") is None:
                legacy_steps += 1
    out["legacy_step_share"] = (
        round(legacy_steps / total_steps, 3) if total_steps else None)

    # ---------- metric 12: action-goal alignment (plan-serving arm) ----------
    aligned = alignable = 0
    for cid, decs in decisions_by_agent.items():
        for d in decs:
            sel = d["parsed"].get("selected_goal_id")
            plan = d["ctx"].get("current_plan")
            if not sel or not plan or plan.get("goal_id") != sel:
                continue
            steps = plan.get("steps") or []
            idx = plan.get("current_step", 0)
            if idx >= len(steps):
                continue
            templates = (steps[idx].get("action_candidates")
                         if isinstance(steps[idx], dict) else None)
            if not templates:
                continue
            alignable += 1
            action = d["parsed"].get("action", {})
            for tmpl in templates:
                if tmpl.get("type") == action.get("type") and all(
                        action.get(k) == v for k, v in tmpl.items()
                        if k != "type"):
                    aligned += 1
                    break
    out["action_goal_alignment"] = (
        round(aligned / alignable, 3) if alignable else None)

    # ---------- metric 13: behavioural diversity ----------
    sig_sets: dict[str, set] = {}
    total_decisions = distinct = 0
    for cid, decs in decisions_by_agent.items():
        sigs = set()
        for d in decs:
            a = d["parsed"].get("action", {})
            sigs.add("|".join(str(a.get(k)) for k in
                              ("type", "target", "target_location",
                               "claim", "topic")))
        sig_sets[cid] = sigs
        total_decisions += len(decs)
        distinct += len(sigs)
    out["behavioral_diversity"] = (
        round(distinct / total_decisions, 3) if total_decisions else None)
    jaccards = []
    ids = sorted(sig_sets)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            union = sig_sets[a] | sig_sets[b]
            if union:
                jaccards.append(1 - len(sig_sets[a] & sig_sets[b])
                                / len(union))
    out["behavioral_diversity_jaccard_mean"] = (
        round(sum(jaccards) / len(jaccards), 3) if jaccards else None)
    return out


def collect_run_metrics(run_dir: Path) -> dict:
    """Per-run outcome row for the comparison table, from the run's own
    artefacts only (summary.json + acp.sqlite3)."""
    from .persistence import (ConsolidationRow, PredictionRow, Store)
    run_dir = Path(run_dir)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    fb = summary["final_beliefs"]
    falsified = promotions = 0
    db_path = run_dir / "acp.sqlite3"
    if db_path.exists():
        store = Store(summary["run_id"], sqlite_path=str(db_path))
        with store.Session() as s:
            for (err_json,) in s.query(PredictionRow.error_json).filter_by(
                    run_id=summary["run_id"]).all():
                if err_json and json.loads(err_json).get("mismatches"):
                    falsified += 1
            for (cons_json,) in s.query(ConsolidationRow.consolidated_json).filter_by(
                    run_id=summary["run_id"]).all():
                if cons_json:
                    promotions += len(json.loads(cons_json))
    counts = summary["db_counts"]
    soldier_letter = _belief(fb, "soldier", "anonymous_letter_exists")
    return {
        "run_id": summary["run_id"], "seed": summary["seed"], "mode": summary["mode"],
        "ticks": summary["ticks"], "total_events": summary["total_events"],
        "final_positions": summary["final_positions"],
        "soldier_letter": soldier_letter,
        "soldier_read_letter": bool(soldier_letter
                                    and soldier_letter["kind"] == "experienced"
                                    and soldier_letter["confidence"] >= 1.0),
        "merchant_letter": _belief(fb, "merchant", "anonymous_letter_exists"),
        "owner_letter_known_by_others": _belief(fb, "owner", "letter_known_by_others"),
        "soldier_mayor_embezzled": _belief(fb, "soldier", "mayor_embezzled"),
        "predictions": counts.get("predictions", 0),
        "predictions_falsified": falsified,
        "belief_changes": counts.get("belief_changes", 0),
        "long_term_promotions": promotions,
        "counterfactual_suppressions": counts.get("counterfactual_suppressions", 0),
        "run_dir": str(run_dir),
        "v02a": _v02a_metrics(run_dir, summary),
        "v02b": _v02b_metrics(run_dir, summary),
    }


def seed_group_metrics(run_metrics: list[dict], world_logs: list[list[dict]]) -> dict:
    """Diversity & consistency over one seed group (definitions in the
    module docstring)."""
    sigs = [event_signature_set(wl) for wl in world_logs]
    tracked = {}
    for label, field in (("soldier_letter", "soldier_letter"),
                         ("merchant_letter", "merchant_letter"),
                         ("owner_letter_known_by_others", "owner_letter_known_by_others"),
                         ("soldier_mayor_embezzled", "soldier_mayor_embezzled")):
        tracked[label] = [
            (m[field]["confidence"] if m[field] else None) for m in run_metrics]
    return {
        "trajectory_diversity_mean_jaccard": mean_pairwise_jaccard(sigs),
        "soldier_read_letter_consistency": outcome_consistency(
            run_metrics, "soldier_read_letter"),
        "total_events": {
            "values": [m["total_events"] for m in run_metrics],
            "min": min(m["total_events"] for m in run_metrics),
            "max": max(m["total_events"] for m in run_metrics),
            "mean": round(sum(m["total_events"] for m in run_metrics)
                          / len(run_metrics), 2),
        },
        "belief_confidence_by_seed": tracked,
    }


# ---------------------------------------------------------------- seed group


def _seed_group_markdown(summary: dict) -> str:
    runs, metrics = summary["runs"], summary["metrics"]
    lines = ["# 种子组对照表（rule/mock 确定性轨迹）", "",
             f"模式：{summary['mode']}；tick 数：{summary['ticks']}；种子："
             f"{', '.join(str(s) for s in summary['seeds'])}", "",
             "| run | 种子 | 事件 | 士兵读信 | 商人听闻信 | 老板知信外泄 | 士兵信镇长贪腐 | "
             "预测 | 证伪 | 信念变化 | 长期记忆晋升 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    def cell(b):
        return f"{b['confidence']:.2f}/{b['kind']}" if b else "—"
    for m in runs:
        lines.append(
            f"| {m['run_id']} | {m['seed']} | {m['total_events']} | "
            f"{cell(m['soldier_letter'])} | {cell(m['merchant_letter'])} | "
            f"{cell(m['owner_letter_known_by_others'])} | "
            f"{cell(m['soldier_mayor_embezzled'])} | {m['predictions']} | "
            f"{m['predictions_falsified']} | {m['belief_changes']} | "
            f"{m['long_term_promotions']} |")
    div = metrics["trajectory_diversity_mean_jaccard"]
    cons = metrics["soldier_read_letter_consistency"]
    lines += ["",
              f"- 轨迹多样性（两两 Jaccard 距离均值，(tick,类型，主体） 事件集合）："
              f"**{div['mean']}**",
              f"- 关键结果一致性（士兵最终亲读信）：**{cons['hits']}/{cons['runs']}**"
              f"（{cons['rate']}）",
              f"- 总事件数：min {metrics['total_events']['min']} / "
              f"mean {metrics['total_events']['mean']} / max {metrics['total_events']['max']}",
              ""]
    return "\n".join(lines)


def _seed_group_csv(summary: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["run_id", "seed", "mode", "ticks", "total_events",
                "soldier_letter_confidence", "soldier_letter_kind",
                "merchant_letter_confidence", "merchant_letter_kind",
                "owner_letter_known_confidence", "soldier_mayor_embezzled_confidence",
                "predictions", "predictions_falsified", "belief_changes",
                "long_term_promotions", "counterfactual_suppressions",
                "final_positions"])
    for m in summary["runs"]:
        def conf(b):
            return b["confidence"] if b else ""
        def kind(b):
            return b["kind"] if b else ""
        w.writerow([m["run_id"], m["seed"], m["mode"], m["ticks"], m["total_events"],
                    conf(m["soldier_letter"]), kind(m["soldier_letter"]),
                    conf(m["merchant_letter"]), kind(m["merchant_letter"]),
                    conf(m["owner_letter_known_by_others"]),
                    conf(m["soldier_mayor_embezzled"]),
                    m["predictions"], m["predictions_falsified"],
                    m["belief_changes"], m["long_term_promotions"],
                    m["counterfactual_suppressions"],
                    ";".join(f"{a}@{loc}" for a, loc
                             in sorted(m["final_positions"].items()))])
    return buf.getvalue()


def run_seed_group(mode: str = "rule", seeds=(42, 7, 101, 2024, 555),
                   ticks: int = 20, out_root: Path | None = None) -> dict:
    """Run one configuration under each seed; write the runs plus
    seed_group_summary.json / seed_group_table.csv / seed_group_table.md
    into out_root and return the summary dict."""
    out_root = Path(out_root) if out_root else Path("runs_v02") / "exp_seedgroup"
    out_root.mkdir(parents=True, exist_ok=True)
    run_metrics, world_logs = [], []
    for seed in seeds:
        run_dir = asyncio.run(run_simulation(
            mode=mode, ticks=ticks, seed=seed,
            run_name=f"{mode}_seed{seed}", out_root=out_root))
        run_metrics.append(collect_run_metrics(run_dir))
        world_logs.append(json.loads(
            (run_dir / "world_log.json").read_text(encoding="utf-8")))
    summary = {"kind": "seed_group", "mode": mode, "ticks": ticks,
               "seeds": list(seeds), "runs": run_metrics,
               "metrics": seed_group_metrics(run_metrics, world_logs)}
    (out_root / "seed_group_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_root / "seed_group_table.csv").write_text(
        _seed_group_csv(summary), encoding="utf-8")
    (out_root / "seed_group_table.md").write_text(
        _seed_group_markdown(summary), encoding="utf-8")
    return summary


# ---------------------------------------------------------------- counterfactual


def _deliveries_by_event(world_log: list[dict]) -> dict[str, dict[str, str]]:
    """event_id -> {agent_id: 'full'|'muffled'} from a run's world_log."""
    out: dict[str, dict[str, str]] = {}
    for rec in world_log:
        for d in rec.get("deliveries", []):
            for eid in d["delivered_event_ids"]:
                out.setdefault(eid, {})[d["agent_id"]] = "full"
            for eid in d["muffled_event_ids"]:
                out.setdefault(eid, {})[d["agent_id"]] = "muffled"
    return out


def _events_by_tick(world_log: list[dict]) -> dict[int, list[tuple]]:
    per_tick: dict[int, list[tuple]] = {}
    for rec in world_log:
        sigs = sorted((ev["type"], tuple(ev.get("actors") or []),
                       ev.get("location") or "", ev.get("claim") or "",
                       ev.get("topic") or "") for ev in rec["events"])
        per_tick[rec["tick"]] = sigs
    return per_tick


def belief_series(trace: list[dict], agent: str, key: str) -> list[dict]:
    """[{tick, phase, confidence|None, kind|None}] for one belief."""
    out = []
    for entry in trace:
        b = entry["agents"][agent]["beliefs"].get(key)
        out.append({"tick": entry["tick"], "phase": entry["phase"],
                    "confidence": b["confidence"] if b else None,
                    "kind": b["kind"] if b else None})
    return out


def emotion_series(trace: list[dict], agent: str, key: str) -> list[dict]:
    return [{"tick": e["tick"], "phase": e["phase"],
             "value": e["agents"][agent]["emotions"].get(key)}
            for e in trace]


def agent_action_stats(world_log: list[dict], agent: str) -> dict:
    """Action counts for one agent from the objective world_log:
    questions/confrontations/searches, and the first tick of each
    investigation-relevant milestone."""
    stats = {"ask": 0, "confront": 0, "speak": 0, "search": 0, "move": 0,
             "confront_mayor_accusation": 0,
             "first_ask_tick": None, "first_confront_tick": None,
             "first_backroom_search_tick": None, "first_letter_found_tick": None}
    for rec in world_log:
        for ev in rec["events"]:
            if not ev.get("actors") or ev["actors"][0] != agent:
                continue
            t = ev["type"]
            if t in stats:
                stats[t] += 1
            if t == "ask" and stats["first_ask_tick"] is None:
                stats["first_ask_tick"] = rec["tick"]
            if t == "confront":
                if stats["first_confront_tick"] is None:
                    stats["first_confront_tick"] = rec["tick"]
                if ev.get("claim") == "mayor_hint":
                    stats["confront_mayor_accusation"] += 1
            if t == "search":
                if ev.get("location") == "back_room" \
                        and stats["first_backroom_search_tick"] is None:
                    stats["first_backroom_search_tick"] = rec["tick"]
                if "anonymous_letter" in (ev.get("found") or []) \
                        and stats["first_letter_found_tick"] is None:
                    stats["first_letter_found_tick"] = rec["tick"]
    return stats


def _suppression_rows(run_dir: Path, run_id: str) -> list[dict]:
    from .persistence import CounterfactualSuppressionRow, Store
    db_path = Path(run_dir) / "acp.sqlite3"
    if not db_path.exists():
        return []
    store = Store(run_id, sqlite_path=str(db_path))
    with store.Session() as s:
        rows = s.query(CounterfactualSuppressionRow).filter_by(
            run_id=run_id).order_by(CounterfactualSuppressionRow.id).all()
        return [{"tick": r.tick, "agent_id": r.agent_id, "event_id": r.event_id,
                 "kind": r.kind, "rule": json.loads(r.rule)} for r in rows]


def _counterfactual_markdown(comp: dict) -> str:
    rule = comp["rule"]
    lines = ["# 反事实对照：士兵是否听到商人的镇长付款发言", "",
             f"种子 {comp['seed']}，{comp['ticks']} tick，模式 {comp['mode']}。"
             f"唯一差别：实验组在 Broker 层抑制投递 {json.dumps(rule, ensure_ascii=False)}，"
             "客观事件本身不变。", "",
             "## 目标发言的出现与投递（按组各自认定）", "",
             "对照组中该发言的出现：", "",
             "| 事件 | tick | 投递 |", "|---|---|---|"]
    def fmt(d):
        return ", ".join(f"{a}:{k}" for a, k in sorted(d.items())) or "—"
    for t in comp["target_events_control"]:
        lines.append(f"| {t['event_id']}（{t['claim']}） | {t['tick']} | {fmt(t['deliveries'])} |")
    lines += ["", "实验组中该发言的出现（事件照常发生，仅士兵未收到）：", "",
              "| 事件 | tick | 投递 |", "|---|---|---|"]
    for t in comp["target_events_treatment"]:
        lines.append(f"| {t['event_id']}（{t['claim']}） | {t['tick']} | {fmt(t['deliveries'])} |")
    v = comp["verification"]
    lines += ["",
              f"- 对照组首次出现士兵完整听到："
              f"{v['control_first_target_heard_by_recipient']}；"
              f"对照组全部出现中士兵的投递分布："
              f"{v['control_target_deliveries_to_recipient']}；"
              f"实验组每次出现均被抑制且有审计："
              f"{v['treatment_targets_all_suppressed_for_recipient']}",
              f"- 客观事件前缀一致到 tick {v['objective_prefix_identical_through_tick']}"
              f"（首个分歧 tick：{v['first_objective_divergence_tick']}）",
              f"- 实验组抑制审计记录：{len(comp['suppressions'])} 条；对照组抑制："
              f"{comp['control_suppressions']} 条",
              "",
              "## 事件总数",
              "",
              f"对照组 {comp['control_total_events']}；实验组 "
              f"{comp['treatment_total_events']}；差 {comp['total_events_delta']:+d}",
              "",
              "## 士兵行动对照", "",
              "| 指标 | 对照组 | 实验组 |", "|---|---|---|"]
    for k in comp["soldier_actions"]["control"]:
        lines.append(f"| {k} | {comp['soldier_actions']['control'][k]} | "
                     f"{comp['soldier_actions']['treatment'][k]} |")
    lines += ["", "## 士兵信念终值对照", "",
              "| 命题 | 对照组 | 实验组 |", "|---|---|---|"]
    for key, pair in comp["soldier_final_beliefs"].items():
        def fmt(b):
            return f"{b['confidence']:.2f}/{b['kind']}" if b else "（无）"
        lines.append(f"| {key} | {fmt(pair['control'])} | {fmt(pair['treatment'])} |")
    lines.append("")
    return "\n".join(lines)


def run_counterfactual(seed: int = 42, ticks: int = 20, rule: dict | None = None,
                       out_root: Path | None = None, mode: str = "rule") -> dict:
    """Control vs treatment for one suppression rule. Writes both runs
    plus comparison.json / comparison.md into out_root."""
    rule = rule or {"recipient": "soldier", "actor": "merchant",
                    "claim": ["mayor_late_payment", "answer_mayor"]}
    recipient = rule["recipient"]
    out_root = Path(out_root) if out_root else Path("runs_v02") / "exp_counterfactual"
    out_root.mkdir(parents=True, exist_ok=True)
    control_dir = asyncio.run(run_simulation(
        mode=mode, ticks=ticks, seed=seed, run_name="control",
        out_root=out_root, trace_beliefs=True))
    treatment_dir = asyncio.run(run_simulation(
        mode=mode, ticks=ticks, seed=seed, run_name="treatment",
        out_root=out_root, suppression_rules=[rule], trace_beliefs=True))

    logs, traces, summaries = {}, {}, {}
    for name, d in (("control", control_dir), ("treatment", treatment_dir)):
        logs[name] = json.loads((d / "world_log.json").read_text(encoding="utf-8"))
        traces[name] = json.loads((d / "beliefs_trace.json").read_text(encoding="utf-8"))
        summaries[name] = json.loads((d / "summary.json").read_text(encoding="utf-8"))

    # ---- verification: the pair differs by the suppressed delivery ----
    # Rule-matching occurrences are identified per run: after the first
    # suppressed delivery the trajectories may legitimately diverge, so
    # a later occurrence in the treatment is a DIFFERENT objective event
    # (different id/tick) — what must hold is that every occurrence in
    # the treatment was suppressed for the recipient and never reached
    # him, while in the control every occurrence reached him in full.
    deliveries = {name: _deliveries_by_event(logs[name])
                  for name in ("control", "treatment")}

    def occurrences(name: str) -> list[dict]:
        out = []
        for rec in logs[name]:
            for ev in rec["events"]:
                if match_suppression(rule, recipient, ev):
                    out.append({"event_id": ev["event_id"], "tick": ev["tick"],
                                "claim": ev.get("claim"), "actors": ev.get("actors"),
                                "deliveries": deliveries[name].get(ev["event_id"], {})})
        return out

    target_control = occurrences("control")
    target_treatment = occurrences("treatment")
    per_tick = {name: _events_by_tick(logs[name]) for name in ("control", "treatment")}
    first_divergence = None
    for tick in sorted(set(per_tick["control"]) | set(per_tick["treatment"])):
        if per_tick["control"].get(tick) != per_tick["treatment"].get(tick):
            first_divergence = tick
            break
    suppressions = _suppression_rows(treatment_dir, summaries["treatment"]["run_id"])
    suppressed_ids = {s["event_id"] for s in suppressions}
    treatment_targets_suppressed = all(
        t["event_id"] in suppressed_ids and recipient not in t["deliveries"]
        for t in target_treatment) and \
        suppressed_ids == {t["event_id"] for t in target_treatment}
    # What makes the pair a valid hearing-vs-not-hearing contrast is
    # the FIRST occurrence: it is the same pre-divergence event in
    # both runs, and in the control it must actually reach the
    # recipient in full. Later occurrences are post-divergence (not
    # even the same events across runs) and perception physics decides
    # what reaches him — since the plan-layer upgrade the claim class
    # also occurs in conversations he takes no part in (the merchant
    # answering the owner's probe questions), which reach him in full
    # when he happens to be co-located, muffled from next door, or
    # not at all. The old all-occurrences-must-be-full check encoded
    # the old trajectories, where the class only ever occurred in
    # conversations addressed to him; the delivery breakdown is
    # therefore reported descriptively instead of asserted.
    control_first_heard = bool(target_control) and \
        target_control[0]["deliveries"].get(recipient) == "full"
    delivery_breakdown = {"full": 0, "muffled": 0, "absent": 0}
    for t in target_control:
        delivery_breakdown[t["deliveries"].get(recipient, "absent")] = \
            delivery_breakdown.get(
                t["deliveries"].get(recipient, "absent"), 0) + 1

    soldier_keys = ["mayor_embezzled", "investigation_widening",
                    "anonymous_letter_exists", "owner_hiding_something"]
    comp = {
        "kind": "counterfactual", "mode": mode, "seed": seed, "ticks": ticks,
        "rule": rule,
        "control_run": summaries["control"]["run_id"],
        "treatment_run": summaries["treatment"]["run_id"],
        "target_events_control": target_control,
        "target_events_treatment": target_treatment,
        "suppressions": suppressions,
        "control_suppressions": summaries["control"]["db_counts"].get(
            "counterfactual_suppressions", 0),
        "verification": {
            "objective_prefix_identical_through_tick":
                (first_divergence - 1) if first_divergence is not None else ticks,
            "first_objective_divergence_tick": first_divergence,
            "first_target_event_tick":
                target_control[0]["tick"] if target_control else None,
            "control_first_target_heard_by_recipient": control_first_heard,
            "control_target_deliveries_to_recipient": delivery_breakdown,
            "treatment_targets_all_suppressed_for_recipient":
                treatment_targets_suppressed,
        },
        "control_total_events": summaries["control"]["total_events"],
        "treatment_total_events": summaries["treatment"]["total_events"],
        "total_events_delta": summaries["treatment"]["total_events"]
                              - summaries["control"]["total_events"],
        "soldier_actions": {
            "control": agent_action_stats(logs["control"], recipient),
            "treatment": agent_action_stats(logs["treatment"], recipient)},
        "soldier_belief_series": {
            key: {"control": belief_series(traces["control"], recipient, key),
                  "treatment": belief_series(traces["treatment"], recipient, key)}
            for key in soldier_keys},
        "soldier_suspicion_series": {
            "control": emotion_series(traces["control"], recipient, "suspicion"),
            "treatment": emotion_series(traces["treatment"], recipient, "suspicion")},
        "soldier_final_beliefs": {
            key: {"control": _belief(summaries["control"]["final_beliefs"], recipient, key),
                  "treatment": _belief(summaries["treatment"]["final_beliefs"], recipient, key)}
            for key in soldier_keys},
    }
    (out_root / "comparison.json").write_text(
        json.dumps(comp, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_root / "comparison.md").write_text(
        _counterfactual_markdown(comp), encoding="utf-8")
    return comp
