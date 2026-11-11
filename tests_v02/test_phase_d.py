"""Phase D tests for ACP V0.2 (pytest).

Covered: diversity/consistency metric definitions against hand-checked
values; Broker counterfactual suppression (only the target recipient
loses the delivery, full and muffled forms, suppression audited, the
committed event object untouched); seed-group runner completeness and
per-seed reproducibility; and a full counterfactual pair at seed 42.

The pair's question changed with the dynamics (V0.2-B adjudication,
parked item P-5): the merchant's mayor-payment utterance class no
longer occurs at all under the generic candidate generator, so the
suppression rule now targets the mayor-class assertions that DO
occur — the owner's mayor_hint utterances delivered to the soldier —
and the test asserts what that experiment actually measures.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

from v02.broker import EventBroker, match_suppression
from v02.experiments import (belief_series, event_signature_set,
                             jaccard_distance, mean_pairwise_jaccard,
                             outcome_consistency, run_counterfactual,
                             run_seed_group)
from v02.runner import run_simulation

ROOT = Path(__file__).resolve().parent.parent
RULE = {"recipient": "soldier", "actor": "merchant",
        "claim": ["mayor_late_payment", "answer_mayor"]}
# V0.2-B adjudication (P-5): the counterfactual pair's rule. The
# merchant mayor-payment class above never occurs under the current
# dynamics (the mayor testimony is the owner's), which left the old
# experiment spinning with nothing to suppress. The rule is
# re-pinned to the mayor-class assertions that actually occur: the
# owner's mayor_hint utterances, delivered to the soldier. Measured
# on the standing evidence run runs_v02/exp_counterfactual_v02b_owner
# (seed 42, 20 ticks); the assertions in
# test_counterfactual_pair_seed42 pin those measured facts.
OWNER_RULE = {"recipient": "soldier", "actor": "owner",
              "claim": ["mayor_hint"]}


# ---------- metrics: hand-checkable small samples ----------

def test_jaccard_distance_known_values():
    a = {(1, "speak", "merchant"), (2, "move", "soldier")}
    b = {(1, "speak", "merchant"), (3, "move", "soldier")}
    # intersection 1, union 3 -> distance 2/3
    assert abs(jaccard_distance(a, b) - 2 / 3) < 1e-9
    assert jaccard_distance(a, a) == 0.0
    assert jaccard_distance(a, {(9, "wait", "owner")}) == 1.0
    assert jaccard_distance(set(), set()) == 0.0


def test_mean_pairwise_and_consistency_known_values():
    s1 = {(1, "speak", "a")}
    s2 = {(1, "speak", "a"), (2, "move", "b")}
    s3 = {(5, "wait", "c")}
    out = mean_pairwise_jaccard([s1, s2, s3])
    # pairs: (s1,s2)=1-1/2=0.5, (s1,s3)=1, (s2,s3)=1 -> mean 2.5/3
    assert abs(out["mean"] - round(2.5 / 3, 4)) < 1e-9
    assert len(out["pairs"]) == 3
    runs = [{"hit": True}, {"hit": False}, {"hit": True}, {"hit": True}]
    cons = outcome_consistency(runs, "hit")
    assert cons == {"outcome": "hit", "hits": 3, "runs": 4, "rate": 0.75}


def test_event_signature_set_shape():
    wl = [{"tick": 1, "events": [{"type": "speak", "actors": ["merchant", "soldier"]},
                                 {"type": "ambient", "actors": []}]},
          {"tick": 2, "events": [{"type": "move", "actors": ["soldier"]}]}]
    assert event_signature_set(wl) == {(1, "speak", "merchant"),
                                       (1, "ambient", ""),
                                       (2, "move", "soldier")}


# ---------- broker suppression: unit level ----------

def _world(locations: dict[str, str]) -> dict:
    return {
        "tick": 5, "time": "21:25", "weather": "rain",
        "agents": {aid: {"id": aid, "name": aid, "role": "x", "location": loc}
                   for aid, loc in locations.items()},
        "locations": {loc: {"objects": []} for loc in
                      ("bar", "tables", "door", "back_room", "outside")},
    }


def _utterance() -> dict:
    return {"event_id": "E0019", "tick": 5, "type": "speak",
            "actors": ["merchant", "soldier"], "location": "bar",
            "claim": "answer_mayor", "topic": "answer_mayor",
            "text": "米拉：镇长的钱袋子紧不紧……只能说，不太好看。",
            "interrupted": False}


def test_suppression_rule_matching():
    ev = _utterance()
    assert match_suppression(RULE, "soldier", ev)
    assert not match_suppression(RULE, "owner", ev)          # wrong recipient
    assert not match_suppression(RULE, "soldier", {**ev, "claim": "greeting"})
    assert not match_suppression(RULE, "soldier", {**ev, "actors": ["owner", "soldier"]})
    # the literal design-doc claim tag matches the same rule (claim class)
    assert match_suppression(RULE, "soldier", {**ev, "claim": "mayor_late_payment"})


def test_broker_suppression_only_target_recipient_full():
    world = _world({"owner": "bar", "merchant": "bar", "soldier": "bar"})
    events = [_utterance()]
    pristine = copy.deepcopy(events)
    broker = EventBroker(["owner", "merchant", "soldier"], suppression_rules=[RULE])
    broker.publish(world, events)
    perc = broker.pending_perceptions()
    assert [e["event_id"] for e in perc["soldier"]["events"]] == []
    assert [e["event_id"] for e in perc["owner"]["events"]] == ["E0019"]
    assert [e["event_id"] for e in perc["merchant"]["events"]] == ["E0019"]
    sup = broker.drain_counterfactual_suppressions()
    assert len(sup) == 1
    assert sup[0]["agent_id"] == "soldier" and sup[0]["event_id"] == "E0019"
    assert sup[0]["kind"] == "full"
    assert broker.dedup_drops == []          # suppression is not dedup
    assert events == pristine                # objective event untouched


def test_broker_suppression_muffled_form():
    world = _world({"owner": "bar", "merchant": "bar", "soldier": "tables"})
    broker = EventBroker(["owner", "merchant", "soldier"], suppression_rules=[RULE])
    broker.publish(world, [_utterance()])
    perc = broker.pending_perceptions()
    assert perc["soldier"]["events"] == [] and perc["soldier"]["muffled"] == []
    sup = broker.drain_counterfactual_suppressions()
    assert len(sup) == 1 and sup[0]["kind"] == "muffled"


def test_broker_without_rules_delivers_everything():
    world = _world({"owner": "bar", "merchant": "bar", "soldier": "bar"})
    broker = EventBroker(["owner", "merchant", "soldier"])
    broker.publish(world, [_utterance()])
    assert [e["event_id"] for e in
            broker.pending_perceptions()["soldier"]["events"]] == ["E0019"]
    assert broker.drain_counterfactual_suppressions() == []


# ---------- seed group: completeness + reproducibility ----------

def test_seed_group_complete_and_reproducible(tmp_path):
    out = tmp_path / "group"
    summary = run_seed_group(mode="rule", seeds=[42, 7], ticks=20, out_root=out)
    assert summary["kind"] == "seed_group" and len(summary["runs"]) == 2
    for m in summary["runs"]:
        d = Path(m["run_dir"])
        assert (d / "summary.json").exists() and (d / "acp.sqlite3").exists()
        assert m["total_events"] > 0 and m["predictions"] == 60
    assert (out / "seed_group_summary.json").exists()
    assert (out / "seed_group_table.csv").exists()
    assert (out / "seed_group_table.md").exists()
    # each group run reproduces independently: same seed, fresh directory
    again = asyncio.run(run_simulation(mode="rule", ticks=20, seed=42,
                                       run_name="again42", out_root=tmp_path))
    group42 = next(m for m in summary["runs"] if m["seed"] == 42)
    assert (again / "world_log.json").read_bytes() == \
        (Path(group42["run_dir"]) / "world_log.json").read_bytes()
    # seed 42 is the canonical trajectory — 85 events since V0.2-A
    # Phase 3 (evidence-based beliefs + commitment gate; was 87 on
    # the plan-layer canonical asserted until 2026-11-11 — see
    # V02A_PHASE3_REPORT.md)
    # V0.2-B B1 (pre-registered disposition #1): re-pinned to the
    # measured B1 trajectory, 77 events (the generic candidate
    # generator replaced the ported rule branches wholesale).
    # V0.2-B B2 (disposition #1): re-pinned to the measured 73.
    assert group42["total_events"] == 73
    assert group42["soldier_read_letter"] is True


# ---------- counterfactual: the design doc's question, full pair ----------

def test_counterfactual_pair_seed42(tmp_path):
    """The pair, re-pinned by adjudication P-5 to OWNER_RULE and to
    measured facts (standing evidence: runs_v02/
    exp_counterfactual_v02b_owner, seed 42, 20 ticks, rule mode —
    deterministic, reproduced exactly by this test's fresh pair).

    Measured profile: the owner's mayor_hint occurs 8 times, all 8
    delivered in full to the soldier in the control; all 8 are
    suppressed for him in the treatment, and the objective
    trajectories NEVER diverge (73 events in both arms) — his
    behaviour is driven by the first-hand letter evidence, and the
    mayor testimony is corroboration, not a behavioural trigger.
    The belief layer DOES diverge: both soldiers form
    mayor_embezzled at 0.6 from the letter's reveals (tick 4); from
    tick 9 the control soldier's testimony channel corroborates it
    to 0.704 while the treatment soldier stays at 0.6."""
    comp = run_counterfactual(seed=42, ticks=20, rule=OWNER_RULE,
                              out_root=tmp_path)
    recipient = OWNER_RULE["recipient"]

    # the target utterance class occurs in the control run (8 times,
    # measured) and every occurrence reaches the soldier in full
    assert len(comp["target_events_control"]) == 8
    assert comp["verification"]["control_first_target_heard_by_recipient"]
    assert comp["verification"][
        "control_target_deliveries_to_recipient"] == \
        {"full": 8, "muffled": 0, "absent": 0}
    # in the treatment the same utterances still occur objectively,
    # but every one is suppressed for the soldier and audited as such
    assert comp["verification"]["treatment_targets_all_suppressed_for_recipient"]
    assert comp["target_events_treatment"], "utterance still occurs in treatment"
    for t in comp["target_events_treatment"]:
        assert recipient not in t["deliveries"]
    # the first occurrence is the same pre-divergence event in both
    # runs: identical id/tick, and the owner still receives it in full
    c0, t0 = comp["target_events_control"][0], comp["target_events_treatment"][0]
    assert (c0["event_id"], c0["tick"]) == (t0["event_id"], t0["tick"]) == \
        ("E0026", 7)
    assert t0["deliveries"].get("owner") == c0["deliveries"].get("owner") == "full"
    assert comp["control_suppressions"] == 0
    assert len(comp["suppressions"]) == 8
    assert all(s["agent_id"] == recipient for s in comp["suppressions"])
    assert {s["event_id"] for s in comp["suppressions"]} == \
        {t["event_id"] for t in comp["target_events_treatment"]}

    # measured finding, asserted as such: NO objective divergence.
    # The suppressed testimony was corroboration for a belief the
    # soldier already held from first-hand evidence, so his actions
    # never change; the prefix is identical through the whole run.
    v = comp["verification"]
    assert v["first_objective_divergence_tick"] is None
    assert v["objective_prefix_identical_through_tick"] == 20
    assert comp["control_total_events"] == comp["treatment_total_events"] == 73

    # the belief layer, snapshot by snapshot: identical through
    # tick 8 (0.6 in both arms — the letter's reveals alone, formed
    # at tick 4, before the first hint could matter), diverging
    # from tick 9 (post_step): control 0.704 (testimony channel
    # corroborating), treatment 0.6 (reveals only).
    series_c = belief_series(comp_trace(tmp_path, "control"), recipient,
                             "mayor_embezzled")
    series_t = belief_series(comp_trace(tmp_path, "treatment"), recipient,
                             "mayor_embezzled")

    def snap(series, tick):
        return next(e for e in series
                    if e["tick"] == tick and e["phase"] == "post_step")
    assert snap(series_c, 8)["confidence"] == 0.6
    assert snap(series_t, 8)["confidence"] == 0.6
    assert snap(series_c, 9)["confidence"] == 0.704
    assert snap(series_t, 9)["confidence"] == 0.6

    # headline outcome under the new dynamics: the corroboration gap
    # IS the measured effect of the suppressed deliveries (finals:
    # control 0.704 heard vs treatment 0.6 heard).
    final = comp["soldier_final_beliefs"]["mayor_embezzled"]
    assert final["control"] == {"confidence": 0.704, "kind": "heard"}
    assert final["treatment"] == {"confidence": 0.6, "kind": "heard"}
    # the old headline belief, investigation_widening, was a product
    # of the deleted _interpret table and has no generic writer;
    # its assertion is deleted (adjudication P-5). What is asserted
    # instead is its measured absence in BOTH arms, alongside the
    # other _interpret-only product, owner_hiding_something.
    for key in ("investigation_widening", "owner_hiding_something"):
        assert comp["soldier_final_beliefs"][key] == \
            {"control": None, "treatment": None}
    assert (tmp_path / "comparison.json").exists()
    assert (tmp_path / "comparison.md").exists()


def comp_trace(out_root: Path, group: str) -> list[dict]:
    return json.loads((out_root / group / "beliefs_trace.json")
                      .read_text(encoding="utf-8"))
