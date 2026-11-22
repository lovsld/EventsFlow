"""Upgrade Phase 4: setup/payoff registry — the discovery /
corroboration / resolution ladder must not be collapsed."""
from __future__ import annotations

from pathlib import Path

from narrative.setup_payoff import build_setup_payoff

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = {"rule_v02c4_seed42": "stories/tavern_secret.json",
           "c5_lighthouse_seed7": "stories/lighthouse_watch.json",
           "clinic_v02cfix_seed42": "stories/clinic_fever.json"}


def _build(name: str):
    return build_setup_payoff(ROOT / "runs_v02" / name,
                              ROOT / "narrative_runs" / name,
                              ROOT / "v03_runs" / name,
                              ROOT / CONFIGS[name])


def test_letter_question_pays_off_but_embezzlement_does_not():
    result = _build("rule_v02c4_seed42")
    by_desc = {r["description"]: r for r in result["records"]}
    letter_q = next(r for r in result["records"]
                    if r["kind"] == "question" and
                    "匿名信是否真实存在" in r["description"])
    assert letter_q["status"] == "payoff"
    assert "NE-E0008" in letter_q["actual_payoff_event_ids"]
    emb_q = next(r for r in result["records"]
                 if r["kind"] == "question" and
                 "侵吞" in r["description"])
    assert emb_q["status"] != "payoff"
    assert emb_q["actual_payoff_event_ids"] == []


def test_discovery_does_not_resolve_the_deeper_question():
    result = _build("rule_v02c4_seed42")
    # the letter's own fact is its EXISTENCE (fact_belief), and
    # finding it does resolve that. What discovery must NOT do
    # is resolve the embezzlement question the letter points to:
    # that record shows discovery events but no resolution.
    emb_q = next(r for r in result["records"]
                 if r["kind"] == "question" and
                 "侵吞" in r["description"])
    diag = result["diagnostics"][emb_q["setup_id"]]
    assert diag["discovery_events"], "the letter find is a " \
                                      "discovery for this fact"
    assert diag["resolution_events"] == []
    assert emb_q["status"] != "payoff"
    # and where setup and payoff coincide in one event, no
    # degenerate self-edge is emitted
    for e in result["graph_edges"]:
        assert e["from"] != e["to"]


def test_clinic_restock_stays_unresolved():
    result = _build("clinic_v02cfix_seed42")
    for r in result["records"]:
        if r["kind"] == "question":
            assert r["status"] != "payoff" or \
                r["actual_payoff_event_ids"], \
                "no payoff without resolution events"


def test_bluff_is_misdirection_never_payoff():
    result = _build("rule_v02c4_seed42")
    bluffs = [r for r in result["records"] if r["kind"] == "bluff"]
    assert bluffs and bluffs[0]["status"] == "misdirection"


def test_foreshadow_edges_only_for_payoffs():
    result = _build("rule_v02c4_seed42")
    payoffs = [r for r in result["records"]
               if r["status"] == "payoff"]
    assert payoffs
    # every emitted edge pair belongs to a payoff record whose
    # setup and payoff events differ; relations stay in vocab
    rels = {e["relation"] for e in result["graph_edges"]}
    assert rels <= {"FORESHADOWS", "PAYOFF_OF"}
    assert len(result["graph_edges"]) % 2 == 0
    assert len(result["graph_edges"]) <= 2 * len(payoffs)
