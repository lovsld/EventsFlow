"""Scene narrative records + information-increment checking
(Phase 1 / V1.0, task 3).

For every scene of a written novel, record what it used and
what it added: event ids, facts revealed for the first time,
key dialogues, character knowledge changes, and its narrative
goal. Repetition is then measured on three layers:

1. same event_id narrated again in a later scene;
2. semantically similar sentences (character-set Jaccard) with
   no new event and no new fact attached — "valueless repeats";
3. re-narration WITH a new function (discovery -> recollection
   -> reinterpretation) is legitimate and is NOT counted; the
   deterministic layers only flag candidates, and layer 3 is
   reserved for human sampling (flagged records carry their
   reasons so a reviewer can overrule).

Scene Repetition Rate = valueless-repeat paragraphs / total
paragraphs. It must be read together with human sampling, not
as a pure text-similarity verdict (task book §5.4).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_SENT_SPLIT = re.compile(r"[。！？\n]")
SIMILARITY_THRESHOLD = 0.8
MIN_SENTENCE_LEN = 8


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "")
            if len(s.strip()) >= MIN_SENTENCE_LEN]


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_scene_records(narrative_dir: Path,
                        novel: dict) -> dict:
    upgrade = narrative_dir / "upgrade"
    plan = _load(upgrade / "plan.json")
    events = _load(upgrade / "narrative_events.json")
    observations = _load(narrative_dir / "observations.json")
    obs_by_id = {o["obs_id"]: o for o in observations}
    ne_by_id = {e["narrative_event_id"]: e for e in events}
    # facts an event can reveal: propositions it asserts plus
    # beliefs its knowledge changes teach
    event_facts: dict[str, set] = {}
    for e in events:
        facts = set()
        for k in e.get("knowledge_changes", []):
            if k.get("change") == "learned":
                facts.add(k["fact"])
        obs = obs_by_id[e["source_event_ids"][0]]
        if obs.get("proposition") and obs.get("stance") == "assert":
            facts.add(obs["proposition"])
        event_facts[e["source_event_ids"][0]] = facts
    paras_by_scene: dict[str, list[dict]] = {}
    for ch in novel["chapters"]:
        for p in ch["paragraphs"]:
            paras_by_scene.setdefault(p.get("scene_id", "?"),
                                      []).append(p)
    seen_events: set[str] = set()
    seen_facts: set[str] = set()
    seen_dialogue: set[str] = set()
    seen_sentences: list[str] = []
    records = []
    total_paras = 0
    total_valueless = 0
    for scene in plan["scenes"]:
        sid = scene["scene_id"]
        paras = paras_by_scene.get(sid, [])
        total_paras += len(paras)
        used_events: list[str] = []
        for p in paras:
            for eid in p.get("event_ids", []):
                if eid not in used_events:
                    used_events.append(eid)
        scene_facts: set[str] = set()
        for eid in used_events:
            scene_facts |= event_facts.get(eid, set())
        new_facts = sorted(scene_facts - seen_facts)
        repeated_events = [e for e in used_events
                           if e in seen_events]
        dialogues, repeated_dialogues = [], []
        for n in scene["event_ids"]:
            e = ne_by_id[n]
            obs = obs_by_id[e["source_event_ids"][0]]
            if obs.get("type") in ("speak", "ask", "confront") \
                    and (obs.get("text") or "").strip():
                text = obs["text"].strip()
                dialogues.append(text)
                if text in seen_dialogue:
                    repeated_dialogues.append(text)
                seen_dialogue.add(text)
        changes = []
        for n in scene["event_ids"]:
            for k in ne_by_id[n].get("knowledge_changes", []):
                changes.append({"character": k["character"],
                                "fact": k["fact"],
                                "change": k["change"]})
        valueless = []
        for p in paras:
            p_events = set(p.get("event_ids", []))
            p_facts: set[str] = set()
            for eid in p_events:
                p_facts |= event_facts.get(eid, set())
            similar = [s for s in _sentences(p.get("text", ""))
                       if any(_jaccard(s, old) >=
                              SIMILARITY_THRESHOLD
                              for old in seen_sentences)]
            if similar and not (p_events - seen_events) \
                    and not (p_facts - seen_facts):
                valueless.append({
                    "text_prefix": p.get("text", "")[:60],
                    "similar_sentences": similar[:2],
                    "reason": "similar wording, no new event, "
                              "no new fact"})
            seen_sentences.extend(_sentences(p.get("text", "")))
        total_valueless += len(valueless)
        records.append({
            "scene_id": sid,
            "narrative_goal": scene.get("scene_goal", ""),
            "used_event_ids": used_events,
            "repeated_event_ids": repeated_events,
            "revealed_facts": sorted(scene_facts),
            "new_facts": new_facts,
            "key_dialogues": dialogues,
            "repeated_dialogues": repeated_dialogues,
            "character_state_changes": changes,
            "paragraphs": len(paras),
            "valueless_repeat_paragraphs": valueless,
            "has_increment": bool(new_facts or
                                  (set(used_events) - seen_events)
                                  or changes),
        })
        seen_events |= set(used_events)
        seen_facts |= scene_facts
    return {
        "scenes": records,
        "summary": {
            "scenes": len(records),
            "paragraphs": total_paras,
            "valueless_repeat_paragraphs": total_valueless,
            "scene_repetition_rate": round(
                total_valueless / max(1, total_paras), 4),
            "repeated_event_ids": sum(
                len(r["repeated_event_ids"]) for r in records),
            "repeated_dialogues": sum(
                len(r["repeated_dialogues"]) for r in records),
            "scenes_without_increment": [
                r["scene_id"] for r in records
                if not r["has_increment"]]}}
