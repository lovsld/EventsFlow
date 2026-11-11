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
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from v02.agent import CharacterAgent
from v02.config import load_all
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
    """Finding the letter makes the soldier set a new plan during
    receive(); the drained chain update must stay dispatchable as a
    plan_transition while preserving the transition's own kind."""
    agent = make_agent("soldier")
    agent.receive(perc(10, "back_room"), {"found": ["anonymous_letter"]})
    updates = agent.drain_chain_updates()
    pts = [u for u in updates if u.get("kind") == "plan_transition"]
    assert pts, "interpretation-driven plan transition lost its envelope kind"
    for u in pts:
        assert u["transition_kind"] in ("set", "replace")


@pytest.fixture(scope="module")
def fixed_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    return asyncio.run(run_simulation(mode="rule", ticks=20, seed=42,
                                      run_name="rule_chainfix_test",
                                      out_root=out))


def test_interpretation_transitions_reach_the_db(fixed_run):
    db = sqlite3.connect(f"file:{fixed_run}/acp.sqlite3?mode=ro",
                         uri=True)
    kinds = dict(db.execute(
        "select kind, count(*) from plan_transitions group by kind"))
    # interpretation-driven set/replace rows exist at all (pre-fix: 0)
    assert kinds.get("set", 0) + kinds.get("replace", 0) > 0
    # and at least some arrived via the receive-side chain, whose
    # records carry decision_key NULL (pre-fix: none could)
    n = db.execute(
        "select count(*) from plan_transitions "
        "where kind in ('set', 'replace') and decision_key is null"
    ).fetchone()[0]
    assert n > 0


def test_trajectory_matches_current_canonical(fixed_run):
    """Trajectory pin against the canonical seed-42 run.

    Originally this asserted equality with runs_v02/rule_fixed_seed42
    to prove the audit-chain fix changed no behaviour (verified at fix
    time: identical md5). The plan-layer upgrade then INTENTIONALLY
    changed behaviour — result-driven step advancement and the owner's
    probe candidates — and rule_planlayer_seed42 became the canonical
    trajectory (87 events; the 85-event run stays as the pre-upgrade
    baseline). The pin now guards determinism against the new
    canonical; see PLAN_LAYER_REPORT.md."""
    log = json.loads((fixed_run / "world_log.json").read_text())
    canonical = json.loads(
        (ROOT / "runs_v02" / "rule_planlayer_seed42" / "world_log.json")
        .read_text())
    assert log == canonical
