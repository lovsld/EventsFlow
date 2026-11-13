"""Regression tests for the plan-transition audit-chain drop found by
the step-1 consistency audit (STEP1_AUDIT_REPORT.md, finding 1 / M5).

Root cause: CharacterAgent.receive() built chain updates as
{"kind": "plan_transition", **tr, "decision_key": None} — the
transition dict's own "kind" ("set"/"replace"/...) spread *after* the
envelope kind and overwrote it, so the Scheduler's chain-update
dispatch (which routes on kind == "plan_transition") silently dropped
every interpretation-driven plan transition. Behaviour was never
affected; the audit trail was. The fix keeps the envelope kind and
carries the transition's own kind as "transition_kind", which
Store.record_plan_transition stores in the row's kind column.

V0.2-B adjudication (parked item P-2): the interpretation layer no
longer produces plan transitions at all (interpretation creates no
plans), so the two receive-side tests below were re-pointed at the
surviving receive-side producer — StepEvaluator judgements drained
through the same envelope plumbing — keeping the M5 invariant
under test. See docs/V0.2-B_REPORT.md, adjudication record.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from v02.agent import CharacterAgent
from v02.config import load_all
from v02.protocol import PlanStep
from v02.providers import make_provider
from v02.runner import run_simulation

ROOT = Path(__file__).resolve().parent.parent


def make_agent(cid: str) -> CharacterAgent:
    cfg = load_all()[cid]
    return CharacterAgent(cfg, make_provider("mock", cfg, seed=42))


def perc(tick: int, loc: str) -> dict:
    return {"tick": tick, "time": "21:00", "location": loc,
            "colocated": [], "events": [], "muffled": [],
            "visible_objects": [], "agent_id": "x",
            "world_public": {"weather": "clear"}}


def test_receive_plan_transition_keeps_envelope_kind():
    """V0.2-B adjudication (parked item P-2): the original version
    drove receive() with a found-letter result and expected the
    INTERPRETATION layer to set a plan, whose transition then had
    to survive the chain plumbing. The interpretation layer no
    longer sets plans anywhere (V0.2-B design: interpretation
    creates no plans), so that producer is gone by design. The M5
    invariant itself — a plan transition produced during receive()
    must stay dispatchable as kind == "plan_transition" while the
    transition's OWN kind travels separately as transition_kind —
    is re-tested here through the surviving receive-side producer:
    the StepEvaluator judging a pending step when its action result
    arrives in receive().

    Setup mirrors decide()'s bookkeeping: the soldier holds a plan
    whose current step is fulfilled by finding the letter, and the
    pending step check from his previous (tick-9) decision is
    stashed; receive() then ingests the search result and evaluates
    the step against its success condition."""
    agent = make_agent("soldier")
    locate = PlanStep(
        step_id="locate:anonymous_letter",
        objective="locate anonymous_letter",
        action_candidates=[{"type": "search"}],
        success_condition={"type": "object_found",
                           "object": "anonymous_letter"})
    plan = agent.plan_manager.set_plan(
        agent.state, "locate the letter", [locate], 9)
    agent._pending_step_check = {
        "plan_id": plan.plan_id, "step_index": 0,
        "step_id": "locate:anonymous_letter", "tick": 9,
        "action": {"type": "search", "target": None,
                   "target_location": "back_room", "object": None,
                   "claim": None, "topic": None}}
    agent.receive(perc(10, "back_room"),
                  {"ok": True, "found": ["anonymous_letter"]})
    updates = agent.drain_chain_updates()
    pts = [u for u in updates if u.get("kind") == "plan_transition"]
    assert pts, "receive-side plan transition lost its envelope kind"
    for u in pts:
        # the envelope kind survived AND the transition's own kind
        # is carried separately — the M5 bug collapsed these two
        assert u["transition_kind"]
        assert u["transition_kind"] != "plan_transition"
    kinds = [u["transition_kind"] for u in pts]
    assert "step_evaluated" in kinds
    # receive-side transitions are keyed to the decision whose
    # action they evaluate (the tick-9 decision), not to the
    # receiving tick and not to None
    se = next(u for u in pts if u["transition_kind"] == "step_evaluated")
    assert se["decision_key"] == "soldier:9"


@pytest.fixture(scope="module")
def fixed_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    return asyncio.run(run_simulation(mode="rule", ticks=20, seed=42,
                                      run_name="rule_chainfix_test",
                                      out_root=out))


def test_receive_side_transitions_reach_the_db(fixed_run):
    """V0.2-B adjudication (parked item P-2): the original asserted
    interpretation-driven set/replace rows with decision_key NULL.
    Both halves of that signature are gone by design — the
    interpretation layer sets no plans, and the current plumbing
    keys every row to a decision. The M5 invariant at the DB level
    is what must still hold: transitions produced on the RECEIVE
    side are actually recorded, under their own kind. The receive
    side's fingerprint in this run is the step_evaluated rows (36)
    and blocked rows (3), which no decision record can emit; each
    step_evaluated row is keyed to the decision whose action it
    judged, so its decision_key names an earlier tick than the
    row's own tick. Counts are the measured values of the canonical
    trajectory rule_v02b_seed42 (the sibling test below pins this
    fixture's world_log to it byte-for-byte).

    V0.2-C C1 retarget (pre-registered disposition C1-2): counts
    re-measured on the new canonical rule_v02c_seed42 —
    step_evaluated 33, blocked 1, recovery 1. The fingerprint
    properties themselves (receive-side rows exist, all keyed,
    prev-tick keying present) are unchanged.

    V0.2-C C2 retarget (stage disposition C2-2): counts re-measured
    on the new canonical rule_v02c2_seed42 — step_evaluated 33,
    blocked 1, recovery 1 (unchanged numbers; the C2 communication
    changes move utterances, not this run's step judgements).

    V0.2-C C3 retarget (stage disposition C3-2): counts re-measured
    on the new canonical rule_v02c3_seed42 — step_evaluated 33,
    blocked 1, recovery 1 (unchanged again).

    V0.2-C C4 retarget (pre-registered dispositions C4-1/C4-2):
    counts re-measured on the new canonical rule_v02c4_seed42 —
    step_evaluated 46, blocked 0, recovery 0. Two C4 mechanisms
    move these numbers BY DESIGN (design 5.1/5.2): the
    every-receive fulfilment pass adds receive-side step_evaluated
    rows (22 of the 46, each fulfilled), and the narrowed failure
    rule (c) removes the tavern's belief/event-leaf false blocks
    entirely — blocked/recovery existence evidence moves to the
    warehouse gate trajectory and the C4 unit fixtures, the same
    relocation this file's sibling test already accepted for plan
    completions in B3. The keying property splits by source, and
    both halves are asserted below: action-result judgements stay
    keyed to the decision whose action they judged (24 rows,
    prev-tick property intact); every-receive fulfilments are
    NULL-keyed, the truthful attribution for a perception-driven
    record (the interpretation layer's belief rows are NULL-keyed
    for the same reason) — keying them to a decision would be
    causally false, since receive precedes decide within a tick."""
    db = sqlite3.connect(f"file:{fixed_run}/acp.sqlite3?mode=ro",
                         uri=True)
    kinds = dict(db.execute(
        "select kind, count(*) from plan_transitions group by kind"))
    assert kinds.get("step_evaluated", 0) == 46
    assert kinds.get("blocked", 0) == 0
    assert kinds.get("recovery", 0) == 0
    # the envelope kind itself never lands in the kind column —
    # the column carries each transition's own kind (transition_kind)
    assert "plan_transition" not in kinds
    # decision-side rows are recorded too, not dropped: the plan
    # synthesizer's sets are in the table
    assert kinds.get("set", 0) > 0
    # receive-side fingerprint: a step_evaluated row whose
    # decision_key names the tick BEFORE the row's own tick
    rows = db.execute(
        "select tick, decision_key from plan_transitions "
        "where kind = 'step_evaluated'").fetchall()
    assert rows
    # C4 split (see the docstring): action-result judgements are
    # keyed, with the prev-tick property; every-receive fulfilment
    # rows are NULL-keyed perception records, and every one of
    # them is a fulfilment
    keyed = [(t, dk) for t, dk in rows if dk is not None]
    unkeyed = [(t, dk) for t, dk in rows if dk is None]
    assert len(keyed) == 24 and len(unkeyed) == 22
    assert all(tick == int(dk.split(":")[1]) + 1 for tick, dk in keyed)
    unkeyed_fulfilled = db.execute(
        "select count(*) from plan_transitions "
        "where kind = 'step_evaluated' and decision_key is null "
        "and after_json like '%\"fulfilled\": true%'").fetchone()[0]
    assert unkeyed_fulfilled == len(unkeyed)


def test_trajectory_matches_current_canonical(fixed_run):
    """Trajectory pin against the canonical seed-42 run.

    Originally this asserted equality with runs_v02/rule_fixed_seed42
    to prove the audit-chain fix changed no behaviour (verified at fix
    time: identical md5). The plan-layer upgrade then INTENTIONALLY
    changed behaviour — result-driven step advancement and the owner's
    probe candidates — and rule_planlayer_seed42 became the canonical
    trajectory (87 events; the 85-event run stays as the pre-upgrade
    baseline). V0.2-A Phase 3 changed behaviour again, by design:
    evidence-based belief recomputation (independent testimonies now
    corroborate instead of averaging away) and the commitment
    replacement gate both move decisions, so rule_v02a_seed42 (85
    events, world_log md5 035483fa…) is the canonical trajectory now
    and rule_planlayer_seed42 stays as the pre-V0.2-A baseline. The
    pin guards determinism against the new canonical; see
    V02A_PHASE3_REPORT.md.

    V0.2-B B3 retarget (pre-registered disposition #2): the
    canonical trajectory is now runs_v02/rule_v02b_seed42 (73
    events, world_log md5 0ab5c2c676cf3cf1e304fc0ba73ec6b7), built
    by the generic candidate generator + GoalEvaluator +
    StepEvaluator stack; rule_v02a_seed42 stays read-only as the
    V0.2-A baseline. See V0.2-B_REPORT.md.

    V0.2-C C1 retarget (pre-registered disposition C1-1): the
    Goal Scheduler (pursuit selection, pause policy, monotonic
    progress) intentionally changes the trajectory again; the
    canonical is now runs_v02/rule_v02c_seed42 (73 events,
    world_log md5 073083108602bbbc1864e682d4a03263), built under
    the calibrated scheduler coefficients (dep 0.4, wait 0.15,
    margin 0.10 — see docs/V0.2-C_IMPLEMENTATION_REPORT.md).
    rule_v02b_seed42 stays read-only as the V0.2-B baseline.

    V0.2-C C2 retarget (stage disposition C2-1): the Communication
    Generator (question queue, recipient-side answer pricing,
    proactive asserts, census) intentionally changes the
    trajectory again; the canonical is now
    runs_v02/rule_v02c2_seed42 (73 events, world_log md5
    4b5ee390d4bc8ad9054b7d6d0083224c), built under the calibrated
    answer coefficient 2.6 (see the C2 section of
    docs/V0.2-C_IMPLEMENTATION_REPORT.md). rule_v02c_seed42 stays
    read-only as the V0.2-C C1 baseline.

    V0.2-C C3 retarget (stage disposition C3-1): the unified
    Information Value (searched certainty 0.9 in place of the
    0.15 discount, derived searched-places) plus the two
    exploration generation expansions intentionally change the
    trajectory again; the canonical is now
    runs_v02/rule_v02c3_seed42 (73 events, world_log md5
    f0c69bbad8b6fdad33b14b5509175118). rule_v02c2_seed42 stays
    read-only as the V0.2-C C2 baseline.

    V0.2-C C4 retarget (pre-registered disposition C4-1): the
    every-receive step judgement, the narrowed failure rule and
    the replan gate intentionally change the trajectory again;
    the canonical is now runs_v02/rule_v02c4_seed42 (73 events,
    world_log md5 cbf84d263ad01e50fca711328c1e78e2).
    rule_v02c3_seed42 stays read-only as the V0.2-C C3 baseline."""
    log = json.loads((fixed_run / "world_log.json").read_text())
    canonical = json.loads(
        (ROOT / "runs_v02" / "rule_v02c4_seed42" / "world_log.json")
        .read_text())
    assert log == canonical
