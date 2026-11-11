"""Phase D tests for ACP V0.2 (pytest).

Covered: diversity/consistency metric definitions against hand-checked
values; Broker counterfactual suppression (only the target recipient
loses the delivery, full and muffled forms, suppression audited, the
committed event object untouched); seed-group runner completeness and
per-seed reproducibility; and a full counterfactual pair at seed 42 —
the design doc's question: does the soldier hear the merchant's
mayor-payment utterance, and what changes if he does not?
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
    # seed 42 is the canonical trajectory — 87 events since the
    # plan-layer upgrade (was 85 on the pre-upgrade baseline, which
    # this test asserted until 2026-11-11; see PLAN_LAYER_REPORT.md)
    assert group42["total_events"] == 87
    assert group42["soldier_read_letter"] is True


# ---------- counterfactual: the design doc's question, full pair ----------

def test_counterfactual_pair_seed42(tmp_path):
    comp = run_counterfactual(seed=42, ticks=20, rule=RULE, out_root=tmp_path)
    recipient = RULE["recipient"]

    # the target utterance occurs in the control run and its FIRST
    # occurrence — the same pre-divergence event in both runs — reaches
    # the soldier in full; in the treatment the same class of utterance
    # still occurs objectively, but never reaches the soldier.
    # (Plan-layer update: the old all-occurrences-full check encoded
    # the old trajectories, where the claim class only ever occurred
    # in conversations addressed to the soldier. It now also occurs in
    # merchant–owner conversations, which reach him by ordinary
    # perception physics — full when co-located, muffled or not at
    # all otherwise — so the verification asserts the first occurrence
    # and reports the delivery breakdown descriptively. The pair's
    # substantive profile below is asserted as before.)
    assert comp["target_events_control"], "utterance must occur in control"
    assert comp["verification"]["control_first_target_heard_by_recipient"]
    assert comp["verification"][
        "control_target_deliveries_to_recipient"]["full"] >= 1
    assert comp["verification"]["treatment_targets_all_suppressed_for_recipient"]
    assert comp["target_events_treatment"], "utterance still occurs in treatment"
    for t in comp["target_events_treatment"]:
        assert recipient not in t["deliveries"]
    # the first occurrence is the same pre-divergence event in both
    # runs: identical id/tick, and the owner still receives it in full
    c0, t0 = comp["target_events_control"][0], comp["target_events_treatment"][0]
    assert (c0["event_id"], c0["tick"]) == (t0["event_id"], t0["tick"])
    assert t0["deliveries"].get("owner") == c0["deliveries"].get("owner") == "full"
    assert comp["control_suppressions"] == 0
    assert comp["suppressions"], "treatment must audit its suppressions"
    assert all(s["agent_id"] == recipient for s in comp["suppressions"])
    assert {s["event_id"] for s in comp["suppressions"]} == \
        {t["event_id"] for t in comp["target_events_treatment"]}

    # objective events identical through the first target event's tick;
    # divergence, when it comes, is downstream of the suppressed delivery
    v = comp["verification"]
    assert v["objective_prefix_identical_through_tick"] >= \
        v["first_target_event_tick"]

    # the hearing itself, at the first snapshot after the first delivery:
    # control soldier holds mayor_embezzled (heard), treatment does not
    first_tick = comp["target_events_control"][0]["tick"]
    series_c = belief_series(comp_trace(tmp_path, "control"), recipient,
                             "mayor_embezzled")
    series_t = belief_series(comp_trace(tmp_path, "treatment"), recipient,
                             "mayor_embezzled")
    snap_c = next(e for e in series_c
                  if e["tick"] == first_tick + 1 and e["phase"] == "post_step")
    snap_t = next(e for e in series_t
                  if e["tick"] == first_tick + 1 and e["phase"] == "post_step")
    assert snap_c["confidence"] is not None and snap_c["confidence"] >= 0.5
    assert snap_t["confidence"] is None

    # headline outcome: investigation_widening exists only if he heard it
    final = comp["soldier_final_beliefs"]["investigation_widening"]
    assert final["control"] is not None and final["control"]["confidence"] == 0.6
    assert final["treatment"] is None
    assert (tmp_path / "comparison.json").exists()
    assert (tmp_path / "comparison.md").exists()


def comp_trace(out_root: Path, group: str) -> list[dict]:
    return json.loads((out_root / group / "beliefs_trace.json")
                      .read_text(encoding="utf-8"))
