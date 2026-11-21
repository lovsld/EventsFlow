"""N2 Extractor acceptance tests (design §5.5 / §8 N2 gate).

Gold standards were hand-written BEFORE the extractor existed and live
in narrative_runs/<run>/gold/gold_standard.json. Edge precision is
audited by independent re-derivation: every strong edge must satisfy
its rule's preconditions recomputed here from the raw products.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from narrative.extractor import extract  # noqa: E402

RUNS = ["rule_v02c4_seed42", "c5_lighthouse_seed7", "clinic_v02cfix_seed42"]


def storylines(run_id):
    return json.loads((PROJECT / "narrative_runs" / run_id /
                       "storylines.json").read_text(encoding="utf-8"))


def observations(run_id):
    return json.loads((PROJECT / "narrative_runs" / run_id /
                       "observations.json").read_text(encoding="utf-8"))


def line_with(s, eid):
    for e in s["lines"]:
        if eid in e["event_ids"]:
            return e
    return None


@pytest.fixture(scope="module", autouse=True)
def produce():
    for run_id in RUNS:
        extract(PROJECT / "runs_v02" / run_id,
                PROJECT / "narrative_runs" / run_id)


# ---------- gold: lighthouse ----------
def test_gold_lighthouse_main_line():
    s = storylines("c5_lighthouse_seed7")
    main = line_with(s, "E0026")
    assert main is not None
    for eid in ("E0044", "E0080", "E0022", "E0086"):
        assert eid in main["event_ids"], eid
    assert main["state"] == "closed"
    assert "E0027" not in main["event_ids"]  # lantern branch stays out


def test_gold_lighthouse_no_edge_from_failed_attempts():
    s = storylines("c5_lighthouse_seed7")
    failed = {"E0032", "E0049", "E0087", "E0100"}
    for e in s["lines"] + s["fragments"]:
        for ed in e["edges"]:
            assert ed["from"] not in failed


# ---------- gold: tavern ----------
def test_gold_tavern_two_lines_not_crossed():
    s = storylines("rule_v02c4_seed42")
    mayor = line_with(s, "E0008")
    assert "E0022" in mayor["event_ids"]
    assert "business_proposal" not in mayor["claims"]
    business = line_with(s, "E0014")
    assert "E0055" in business["event_ids"]
    assert business["line_id"] != mayor["line_id"]
    assert "mayor_hint" not in business["claims"]
    assert "investigation_probe" not in business["claims"]


# ---------- gold: clinic (negative case) ----------
def test_gold_clinic_broken():
    s = storylines("clinic_v02cfix_seed42")
    line = line_with(s, "E0097")
    assert line is not None and line["state"] == "broken"
    assert line["break_event"] in ("E0098", "E0106")
    assert any(a["goal_key"] == "restock_medicine"
               for a in line["aux_notes"])
    # no closed line may contain the nurse's searches
    for e in s["lines"]:
        if "E0106" in e["event_ids"] or "E0098" in e["event_ids"]:
            assert e["state"] == "broken"
    # the failed attempt originates no edge
    for e in s["lines"] + s["fragments"]:
        for ed in e["edges"]:
            assert ed["from"] != "E0040"


# ---------- edge precision: independent re-derivation ----------
@pytest.mark.parametrize("run_id", RUNS)
def test_every_strong_edge_rederives(run_id):
    s = storylines(run_id)
    obs = observations(run_id)
    by_id = {o["obs_id"]: o for o in obs}
    order = [o["obs_id"] for o in obs]
    idx = {eid: i for i, eid in enumerate(order)}
    meta = json.loads((PROJECT / "runs_v02" / run_id /
                       "run_meta.json").read_text(encoding="utf-8"))
    objects_def = (meta.get("scenario") or {}).get("objects") or {}
    speech = [o for o in obs if o["type"] in ("speak", "ask", "confront")]
    searches = [o for o in obs if o["type"] == "search"]

    def key(o):
        return o["proposition"] or o["claim"]

    checked = 0
    for e in s["lines"] + s["fragments"]:
        for ed in e["edges"]:
            a, b = by_id[ed["from"]], by_id[ed["to"]]
            assert idx[ed["from"]] < idx[ed["to"]]
            for t in ed["types"]:
                if t == "E1":
                    assert key(a) and key(a) == key(b)
                    group = [o for o in speech if key(o) == key(a)]
                    pos = group.index(a)
                    assert group[pos + 1] is b  # adjacent mentions
                elif t == "E2":
                    if b["type"] == "search":
                        assert a["stance"] == "assert"
                        assert b["actors"][0] == a["actors"][1]
                        assert a["actors"][1] in a["delivered_to"]
                        first = next(y for y in searches
                                     if y["actors"][0] == a["actors"][1]
                                     and idx[y["obs_id"]] > idx[a["obs_id"]])
                        assert first is b
                    else:
                        agent = b["actors"][0]
                        assert agent in a["delivered_to"]
                        assert key(a) == key(b)
                        # nearest source: no other candidate source may
                        # sit between a and b
                        for x2 in speech:
                            if key(x2) != key(a) or x2 is a:
                                continue
                            if x2["actors"][0] == agent:
                                continue
                            if agent not in x2["delivered_to"]:
                                continue
                            if idx[a["obs_id"]] < idx[x2["obs_id"]] \
                                    < idx[b["obs_id"]]:
                                between = [z for z in speech
                                           if z["actors"][0] == agent
                                           and key(z) == key(a)
                                           and idx[x2["obs_id"]]
                                           < idx[z["obs_id"]]
                                           < idx[b["obs_id"]]]
                                assert between, \
                                    f"E2 {a['obs_id']}->{b['obs_id']} is " \
                                    f"not nearest: {x2['obs_id']} intervenes"
                elif t == "E3":
                    assert a["stance"] == "inquire"
                    assert b["stance"] in ("assert", "deny")
                    assert a["proposition"] == b["proposition"]
                elif t == "E4":
                    assert a["type"] == "search" and a["found"]
                    targets = set()
                    for obj in a["found"]:
                        od = objects_def.get(obj) or {}
                        if od.get("fact_belief"):
                            targets.add(od["fact_belief"])
                        targets |= {r["belief"] for r in od.get("reveals", [])}
                    if b["type"] == "show":
                        assert b.get("object") in a["found"]
                    else:
                        assert b["proposition"] in targets
                        assert b["actors"][0] == a["actors"][0]
                        assert b["stance"] == "assert"
                elif t == "E5":
                    assert b["stance"] == "deny"
                    assert a["stance"] == "assert"
                    assert a["proposition"] == b["proposition"]
                elif t == "E7":
                    assert a["type"] == b["type"] == "search"
                    assert a["actors"][0] == b["actors"][0]
                    group = [o for o in searches
                             if o["actors"][0] == a["actors"][0]]
                    assert group[group.index(a) + 1] is b
                else:
                    raise AssertionError(f"unknown edge type {t}")
                checked += 1
    assert checked > 0


@pytest.mark.parametrize("run_id", RUNS)
def test_extractor_determinism_and_hash(run_id, tmp_path):
    out = tmp_path / run_id
    import shutil
    src = PROJECT / "narrative_runs" / run_id
    for name in ("observations.json", "scenes.json", "frontier.json"):
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / name, out / name)
    r1 = extract(PROJECT / "runs_v02" / run_id, out)
    first = (out / "storylines.json").read_bytes()
    r2 = extract(PROJECT / "runs_v02" / run_id, out)
    assert (out / "storylines.json").read_bytes() == first
    assert r1["hash_check"] == r2["hash_check"] == "pass"


def test_arcs_cover_all_characters():
    for run_id in RUNS:
        arcs = json.loads((PROJECT / "narrative_runs" / run_id /
                           "arcs.json").read_text(encoding="utf-8"))
        frontier = json.loads((PROJECT / "narrative_runs" / run_id /
                               "frontier.json").read_text(encoding="utf-8"))
        assert set(arcs) == set(frontier)
        for cid, arc in arcs.items():
            assert arc["frontier_final_count"] == \
                frontier[cid][-1]["cumulative_count"]
