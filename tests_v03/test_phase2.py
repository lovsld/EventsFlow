"""V0.3 Phase 2 acceptance tests (task book §19 Phase 2):
events need causal basis; unreasonable candidates are rejected;
reasonable ones pass; the Story Engine cannot mutate world state;
no repeated candidates; no random filler.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from v03.story.architect import load_config  # noqa: E402
from v03.story.eventgen import (CausalValidator, WorldRuleValidator,  # noqa: E402
                                WorldSnapshot, to_world_event)
from v03.story.models import EventCandidate, StoryConfig  # noqa: E402
from v03.story.story_engine import StoryEngine  # noqa: E402

RUN = PROJECT / "runs_v02" / "rule_v02c4_seed42"


def cand(**kw) -> EventCandidate:
    base = dict(candidate_id="ECX", source_type="story_driven",
                kind="external_arrival", description="x",
                proposed_location="door", causal_basis=["E0001"],
                fingerprint="fp1")
    base.update(kw)
    return EventCandidate(**base)


def test_baseless_candidate_rejected():
    cfg = StoryConfig(story_id="s", title="t")
    eng = StoryEngine(cfg)
    v = CausalValidator(eng.state, {"E0001"})
    ok, why = v.validate(cand(causal_basis=[]))
    assert not ok and "no causal basis" in why
    ok, why = v.validate(cand(causal_basis=["E9999"]))
    assert not ok and "not in history" in why


def test_world_rule_rejections():
    snap = WorldSnapshot(locations=["door"], characters=["a"])
    v = WorldRuleValidator(snap, seen_fingerprints=set())
    ok, why = v.validate(cand(proposed_location="moon"))
    assert not ok and "unknown location" in why
    ok, why = v.validate(cand(involved_characters=["ghost"]))
    assert not ok and "unknown character" in why
    v2 = WorldRuleValidator(snap, seen_fingerprints={"fp1"})
    ok, why = v2.validate(cand())
    assert not ok and "duplicate" in why


def test_valid_candidate_becomes_world_event_dict():
    c = cand()
    ev = to_world_event(c, "E1000")
    assert ev["type"] == "ambient" and ev["source"] == "story_engine"
    assert ev["causal_basis"] == ["E0001"]
    assert ev["location"] == "door" and ev["actors"] == []


def test_shadow_replay_proposals_have_basis_and_no_repeats():
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    before = hashlib.md5((RUN / "world_log.json").read_bytes()).hexdigest()
    eng = StoryEngine.replay_with_proposals(RUN, cfg,
                                            PROJECT / "characters_v02")
    passed = [c for c in eng.candidates_log if c.validation["passed"]]
    assert len(passed) >= 2  # trace + arrival at least
    fps = [c.fingerprint for c in passed]
    assert len(fps) == len(set(fps))  # no repeat proposals
    known = eng.known_event_ids
    for c in passed:
        assert c.causal_basis and all(e in known for e in c.causal_basis)
        assert c.evaluation and c.evaluation["causality"] > 0
    # shadow mode never touches the run
    after = hashlib.md5((RUN / "world_log.json").read_bytes()).hexdigest()
    assert before == after == "cbf84d263ad01e50fca711328c1e78e2"


def test_no_triggers_no_proposals():
    cfg = StoryConfig(story_id="quiet", title="q",
                      characters=["a", "b"], proposal_cooldown=1)
    eng = StoryEngine(cfg)
    snap = WorldSnapshot(locations=["door"], characters=["a", "b"])
    total = 0
    for n in range(1, 21):
        eng.observe_tick({"tick": n, "time": "21:00", "events": [],
                          "deliveries": []}, {})
        total += len(eng.propose(snap))
    assert total == 0  # a quiet world gets no filler events


def test_cooldown_restraint():
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    eng = StoryEngine.replay_with_proposals(RUN, cfg,
                                            PROJECT / "characters_v02")
    passed_ticks = sorted({c.created_at for c in eng.candidates_log
                           if c.validation["passed"]})
    for a, b in zip(passed_ticks, passed_ticks[1:]):
        assert b - a >= cfg.proposal_cooldown or a == b
