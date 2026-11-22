"""Upgrade Phase 2: semantic merge groups + unified causal graph
+ independent verifier + chain tracing."""
from __future__ import annotations

import json
from pathlib import Path

from narrative.causal_graph import (build_causal_edges,
                                    trace_causes,
                                    verify_causal_edges,
                                    write_causal_graph)
from narrative.models.build import build_narrative_events
from narrative.models.story import CausalEdge
from narrative.semantic import build_merge_groups

ROOT = Path(__file__).resolve().parent.parent


def _dirs(name: str):
    return (ROOT / "runs_v02" / name, ROOT / "narrative_runs" / name,
            ROOT / "v03_runs" / name)


def test_merge_groups_tavern_finds_the_repeated_line():
    _, ndir, _ = _dirs("rule_v02c4_seed42")
    obs = json.loads((ndir / "observations.json")
                     .read_text(encoding="utf-8"))
    result = build_merge_groups(obs)
    verbatim = [g for g in result["groups"]
                if g["kind"] == "verbatim_repeat"]
    assert verbatim, "the tavern draft's 14x repeated line must " \
                     "form a verbatim group"
    biggest = max(verbatim, key=lambda g: len(g["member_ne_ids"]))
    assert len(biggest["member_ne_ids"]) >= 5
    assert result["stats"]["unique_after_merge"] < \
        result["stats"]["speech_events"]


def test_escalation_no_longer_fires_on_relays():
    run_dir, ndir, _ = _dirs("rule_v02c4_seed42")
    events = build_narrative_events(run_dir, ndir)
    esc = [e for e in events if any(
        f.value == "escalation" for f in e.dramatic_function)]
    # Phase 1 rule produced 18; relays must not count any more
    assert len(esc) < 18


def test_causal_graph_tavern_chain_and_verification():
    run_dir, ndir, v03 = _dirs("rule_v02c4_seed42")
    edges, stats = build_causal_edges(ndir, v03)
    assert edges and stats["extractor_used"] > 0
    obs = json.loads((ndir / "observations.json")
                     .read_text(encoding="utf-8"))
    evs = [e.model_dump(mode="json")
           for e in build_narrative_events(run_dir, ndir)]
    edges, report = verify_causal_edges(edges, obs, evs)
    assert report["rejected"] == 0
    # the letter find must be a causal ancestor of the soldier's
    # later assertions that use it
    assert "NE-E0008" in trace_causes(edges, "NE-E0022")


def test_verifier_rejects_proposition_only_and_time_travel():
    obs = [{"obs_id": "E0001", "tick": 1, "type": "speak",
            "proposition": "p", "stance": "assert",
            "delivered_to": []},
           {"obs_id": "E0002", "tick": 2, "type": "speak",
            "proposition": "p", "stance": "assert",
            "delivered_to": []}]
    evs = [{"narrative_event_id": "NE-E0001", "tick": 1},
           {"narrative_event_id": "NE-E0002", "tick": 2}]
    bad = CausalEdge(**{"from": "NE-E0002", "to": "NE-E0001"},
                     relation="CAUSED_BY",
                     evidence_event_ids=["E0001", "E0002"])
    out, report = verify_causal_edges([bad], obs, evs)
    assert out[0].verification_status == "rejected"
    assert report["rejected"] == 1
    backwards = CausalEdge(**{"from": "NE-E0001", "to": "NE-E0002"},
                           relation="CAUSED_BY",
                           evidence_event_ids=["E0001", "E0002"])
    out2, _ = verify_causal_edges([backwards], obs, evs)
    assert out2[0].verification_status == "rejected"


def test_write_products_all_scenarios(tmp_path):
    for name in ("rule_v02c4_seed42", "c5_lighthouse_seed7",
                 "clinic_v02cfix_seed42"):
        run_dir, ndir, v03 = _dirs(name)
        summary = write_causal_graph(run_dir, ndir, v03,
                                     tmp_path / name)
        assert summary["edges"] > 0
        graph = json.loads((tmp_path / name / "causal_graph.json")
                           .read_text(encoding="utf-8"))
        assert graph["verification"]["rejected"] == 0
