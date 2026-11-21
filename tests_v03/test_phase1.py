"""V0.3 Phase 1 acceptance tests (task book §19 Phase 1):
multiple threads coexist, dependencies, conflicting goals,
conflict escalation/cooling, thread status updates — plus the
honesty rule that hearsay never resolves a question.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from v03.persistence import StoryStore  # noqa: E402
from v03.story.architect import StoryArchitect, load_config  # noqa: E402
from v03.story.models import (CharacterGoalView, InitialConflict,  # noqa: E402
                              StoryConfig, StoryThread, ThreadQuestion)
from v03.story.story_engine import StoryEngine  # noqa: E402


def make_config(**kw) -> StoryConfig:
    base = dict(story_id="t1", title="t", characters=["a", "b", "c"])
    base.update(kw)
    return StoryConfig(**base)


def thread(tid, props, questions=None, **kw):
    return StoryThread(thread_id=tid, title=tid, propositions=props,
                       questions=questions or [], **kw)


def speech(eid, actor, target, prop, stance="assert", direct=False,
           etype="speak"):
    e = {"event_id": eid, "type": etype, "actors": [actor, target],
         "proposition": prop, "stance": stance, "claim": "x"}
    if direct:
        e["_direct"] = True
    return e


def tick(n, events, delivered=None):
    deliveries = [{"agent_id": a, "delivered_event_ids": ids}
                  for a, ids in (delivered or {}).items()]
    return {"tick": n, "time": "21:00", "events": events,
            "deliveries": deliveries}


def test_multiple_threads_coexist_and_dependencies():
    cfg = make_config(initial_threads=[
        thread("t_main", ["p1"]),
        thread("t_side", ["p2"], dependencies=["t_main"]),
        thread("t_third", ["p3"])])
    eng = StoryEngine(cfg)
    assert set(eng.state.threads) == {"t_main", "t_side", "t_third"}
    assert eng.state.threads["t_side"].dependencies == ["t_main"]
    assert "t_main" in eng.state.threads["t_side"].related_threads


def test_hearsay_addresses_but_never_answers():
    q = ThreadQuestion(id="q1", answered_by="p1")
    cfg = make_config(initial_threads=[
        thread("t_main", ["p1"], questions=[q],
               involved_characters=["a", "b"])])
    eng = StoryEngine(cfg)
    eng.observe_tick(tick(1, [speech("E1", "a", "b", "p1")],
                          {"b": ["E1"]}), {})
    t = eng.state.threads["t_main"]
    assert t.questions[0].addressed_at == 1
    assert t.questions[0].answered_at is None
    assert t.status != "resolved" and t.progress == 0.0
    # direct evidence answers and resolves
    eng.observe_tick(tick(2, [speech("E2", "a", "b", "p1", direct=True)],
                          {"b": ["E2"]}), {})
    assert t.questions[0].answered_at == 2
    assert t.status == "resolved" and t.progress == 1.0


def test_knowledge_only_from_deliveries():
    cfg = make_config(initial_threads=[
        thread("t_main", ["p1"], involved_characters=["a", "b", "c"])])
    eng = StoryEngine(cfg)
    eng.observe_tick(tick(1, [speech("E1", "a", "b", "p1")],
                          {"b": ["E1"]}), {})  # c not delivered
    t = eng.state.threads["t_main"]
    assert "p1" in t.known_by["a"] and "p1" in t.known_by["b"]
    assert "p1" not in t.known_by.get("c", [])


def test_dormant_and_reactivation():
    cfg = make_config(dormant_after_ticks=3, initial_threads=[
        thread("t_main", ["p1"], involved_characters=["a", "b"])])
    eng = StoryEngine(cfg)
    for n in range(1, 5):
        eng.observe_tick(tick(n, []), {})
    assert eng.state.threads["t_main"].status == "dormant"
    eng.observe_tick(tick(5, [speech("E9", "a", "b", "p1")],
                          {"b": ["E9"]}), {})
    assert eng.state.threads["t_main"].status == "active"


def test_goal_conflict_detection_from_conditions():
    cfg = make_config()
    state = StoryArchitect().build(cfg)
    state.goals = [
        CharacterGoalView(character_id="soldier", goal_id="investigate",
                          success_beliefs=["mayor_embezzled"]),
        CharacterGoalView(character_id="merchant", goal_id="protect",
                          failure_beliefs=["mayor_embezzled"]),
        CharacterGoalView(character_id="owner", goal_id="unrelated",
                          success_beliefs=["something_else"])]
    from v03.story.conflict_engine import ConflictEngine
    eng = ConflictEngine(state)
    found = eng.detect(tick=0)
    goal_conflicts = [c for c in found if c.conflict_type == "goal"]
    assert len(goal_conflicts) == 1
    assert set(goal_conflicts[0].participants) == {"soldier", "merchant"}
    # detection is idempotent
    assert eng.detect(tick=1) == []


def test_conflict_escalates_and_cools():
    cfg = make_config(
        initial_threads=[thread("t_main", ["secret"],
                                sensitive_facts=["secret"],
                                involved_characters=["a", "b"])],
        initial_conflicts=[InitialConflict(
            conflict_type="interest", participants=["a", "b"],
            related_threads=["t_main"], severity=0.3)])
    eng = StoryEngine(cfg)
    cid = next(iter(eng.state.conflicts))
    for n in range(1, 6):
        eng.observe_tick(tick(n, [speech(f"E{n}", "a", "b", "secret")],
                              {"b": [f"E{n}"]}), {})
    c = eng.state.conflicts[cid]
    assert c.escalation > 0.3 and c.status in ("active", "escalating")
    esc_peak = c.escalation
    for n in range(6, 26):
        eng.observe_tick(tick(n, []), {})
    assert c.escalation < esc_peak  # cooled over quiet ticks


def test_persistence_roundtrip(tmp_path):
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    eng = StoryEngine.replay(PROJECT / "runs_v02" / "rule_v02c4_seed42",
                             cfg, PROJECT / "characters_v02")
    store = StoryStore(tmp_path / "story.sqlite3")
    store.save(eng.state)
    loaded = store.load(cfg.story_id)
    assert loaded is not None
    assert loaded.model_dump() == eng.state.model_dump()


def test_tavern_replay_truthful_states():
    cfg = load_config(PROJECT / "stories" / "tavern_secret.json")
    eng = StoryEngine.replay(PROJECT / "runs_v02" / "rule_v02c4_seed42",
                             cfg, PROJECT / "characters_v02")
    main = eng.state.threads["main_embezzlement"]
    # the letter's existence was directly evidenced (soldier's find);
    # embezzlement itself was only ever hinted — never resolved
    assert main.status != "resolved"
    assert main.progress == 0.5
    # a goal conflict soldier-vs-merchant on mayor_embezzled exists
    assert any(c.conflict_type == "goal"
               and set(c.participants) == {"soldier", "merchant"}
               for c in eng.state.conflicts.values())
    # replay is deterministic
    eng2 = StoryEngine.replay(PROJECT / "runs_v02" / "rule_v02c4_seed42",
                              cfg, PROJECT / "characters_v02")
    assert eng.state.model_dump() == eng2.state.model_dump()
