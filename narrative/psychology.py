"""Character psychology inference layer (Phase 1 / V1.0,
task 4): emotional states and MOTIVATED_BY hypotheses.

PHYSICAL SEPARATION: everything produced here is INFERENCE.
It is written to upgrade/psychology.json and
upgrade/inference_edges.json — never into the verified causal
graph, never into the fact layer. Every record carries
evidence_event_ids, a confidence, and source_type=
"rule_inference"; emotions may be "unknown" when a scene gives
no usable signal for a character, and motivation relations
carry an inference_status (HYPOTHESIS by default; SUPPORTED
only with >=2 independent evidence events; the null alternative
"routine behaviour, no story motivation" is always listed).
"""
from __future__ import annotations

import json
from pathlib import Path

EMOTIONS = ("fear", "anger", "sadness", "joy", "surprise",
            "disgust", "trust", "anticipation", "unknown")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _emotion_candidates(obs: dict, ne: dict) -> list[tuple]:
    """(character, emotion, intensity, confidence) candidates
    from one event, each grounded in that event alone."""
    out = []
    actors = obs.get("actors") or []
    t = obs.get("type")
    if t == "confront" and actors:
        out.append((actors[0], "anger", 0.6, 0.4))
        out.append((actors[0], "anticipation", 0.5, 0.3))
    if t == "search" and obs.get("found") and actors:
        out.append((actors[0], "surprise", 0.7, 0.6))
        out.append((actors[0], "joy", 0.4, 0.4))
    if t == "speak_failed" and actors:
        out.append((actors[0], "sadness", 0.4, 0.3))
        out.append((actors[0], "anticipation", 0.5, 0.3))
    if obs.get("stance") == "deny" and actors:
        out.append((actors[0], "anger", 0.4, 0.3))
    for k in ne.get("knowledge_changes", []):
        if k.get("change") == "learned" and \
                k.get("evidence_level") == "subjective":
            out.append((k["character"], "anticipation", 0.5, 0.35))
    return out


def build_psychology(narrative_dir: Path) -> dict:
    upgrade = narrative_dir / "upgrade"
    plan = _load(upgrade / "plan.json")
    events = _load(upgrade / "narrative_events.json")
    observations = _load(narrative_dir / "observations.json")
    views = _load(upgrade / "story_views.json")
    obs_by_id = {o["obs_id"]: o for o in observations}
    ne_by_id = {e["narrative_event_id"]: e for e in events}
    # --- emotional states, aggregated per character x scene
    emotions = []
    for scene in plan["scenes"]:
        best: dict[str, dict] = {}
        for n in scene["event_ids"]:
            ne = ne_by_id[n]
            obs = obs_by_id[ne["source_event_ids"][0]]
            for cid, emo, inten, conf in _emotion_candidates(
                    obs, ne):
                cur = best.get(cid)
                rec = {"character_id": cid,
                       "scene_id": scene["scene_id"],
                       "tick": ne["tick"], "emotion": emo,
                       "intensity": inten, "confidence": conf,
                       "evidence_event_ids":
                           [ne["source_event_ids"][0]],
                       "source_type": "rule_inference",
                       "evidence_level": "interpretation"}
                if cur is None or conf > cur["confidence"]:
                    if cur is not None:
                        rec["evidence_event_ids"] = sorted(
                            set(cur["evidence_event_ids"]) |
                            set(rec["evidence_event_ids"]))
                    best[cid] = rec
                elif cur["emotion"] == emo:
                    cur["evidence_event_ids"] = sorted(
                        set(cur["evidence_event_ids"]) |
                        {ne["source_event_ids"][0]})
        for cid in scene.get("participants", []):
            if cid in best:
                emotions.append(best[cid])
            else:
                emotions.append({
                    "character_id": cid,
                    "scene_id": scene["scene_id"],
                    "tick": scene["time_range"][0],
                    "emotion": "unknown", "intensity": 0.0,
                    "confidence": 0.0, "evidence_event_ids": [],
                    "source_type": "rule_inference",
                    "evidence_level": "interpretation"})
    # --- MOTIVATED_BY hypotheses for key actions
    threads = views.get("threads", [])
    thread_of_event: dict[str, dict] = {}
    for th in threads:
        for n in th.get("related_events", []):
            thread_of_event[n] = th
    learned_at: dict[str, list[tuple[int, str]]] = {}
    for e in events:
        for k in e.get("knowledge_changes", []):
            if k.get("change") == "learned":
                learned_at.setdefault(k["character"], []).append(
                    (e["tick"], k["fact"]))
    motivated = []
    key_actions = 0
    for e in events:
        if e["narrative_importance"] < 0.5:
            continue
        obs = obs_by_id[e["source_event_ids"][0]]
        if obs.get("type") not in ("search", "confront", "ask",
                                   "speak"):
            continue
        key_actions += 1
        th = thread_of_event.get(e["narrative_event_id"])
        actor = (obs.get("actors") or [None])[0]
        if not th or not actor or \
                actor not in th.get("involved_characters", []):
            continue
        facts = {k["fact"] for k in e.get("knowledge_changes", [])}
        prop = obs.get("proposition")
        related_facts = {fact for (tick, fact) in
                         learned_at.get(actor, [])
                         if tick < e["tick"] and
                         (fact in facts or fact == prop)}
        evidence_ids = sorted({
            k["source_event_id"]
            for ne2 in events for k in
            ne2.get("knowledge_changes", [])
            if k.get("character") == actor
            and k.get("change") == "learned"
            and k["fact"] in related_facts})
        conf = round(min(0.6, 0.3 + 0.1 * len(evidence_ids)), 2)
        motivated.append({
            "character_id": actor,
            "action_event_id": e["source_event_ids"][0],
            "motivation": f"推进线索「{th['title']}」："
                          f"{th['central_question']}",
            "thread_id": th["thread_id"],
            "confidence": conf,
            "evidence_event_ids": evidence_ids,
            "alternative_explanations": [
                "日常行为，无故事动机（零假设）",
                "执行他人指令或履行职责"],
            "inference_status": "SUPPORTED"
            if len(evidence_ids) >= 2 else "HYPOTHESIS",
            "evidence_level": "interpretation"})
    return {"emotions": emotions, "motivated_by": motivated,
            "diagnostics": {
                "key_actions": key_actions,
                "motivated_actions": len(motivated),
                "emotion_records": len(emotions),
                "unknown_emotions": sum(
                    1 for r in emotions
                    if r["emotion"] == "unknown")}}


def write_psychology(narrative_dir: Path, out_dir: Path) -> dict:
    psych = build_psychology(narrative_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "psychology.json").write_text(
        json.dumps(psych, ensure_ascii=False, indent=1),
        encoding="utf-8")
    edges = [{"from": m["action_event_id"],
              "relation": "MOTIVATED_BY",
              "to": m["motivation"],
              "character_id": m["character_id"],
              "confidence": m["confidence"],
              "evidence_event_ids": m["evidence_event_ids"],
              "inference_status": m["inference_status"],
              "evidence_level": "interpretation"}
             for m in psych["motivated_by"]]
    (out_dir / "inference_edges.json").write_text(
        json.dumps({"edges": edges,
                    "note": "INFERENCE ONLY — physically separate "
                            "from the verified causal graph"},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    return psych


# ------------------------------------------------------------------
# Phase 2 (task book 4.2): incremental state-update records.
# Every update names its triggering event, the before/after
# values, the reason, the evidence, a confidence, and the
# alternative explanations that were NOT chosen. Fact-level
# updates (recorded learnings) carry confidence 1.0 and no
# alternatives; inference/hypothesis updates always carry at
# least one alternative. Updates are derived from existing
# products (character arcs + this module's own output); they
# never write back to any fact-layer file.
# ------------------------------------------------------------------

def build_state_updates(narrative_dir: Path) -> dict:
    upgrade = narrative_dir / "upgrade"
    arcs = json.loads((upgrade / "character_arcs.json")
                      .read_text(encoding="utf-8"))["arcs"]
    psych = build_psychology(narrative_dir)
    updates: list[dict] = []
    for arc in arcs:
        cid = arc["character_id"]
        for ch in arc.get("belief_changes", []):
            updates.append({
                "event_id": ch["event_id"],
                "character_id": cid, "tick": ch.get("tick"),
                "field": "knowledge", "tier": "fact",
                "before": {"known": False},
                "after": {"known": True, "fact": ch["fact"]},
                "reason": f"learned via {ch.get('via')}",
                "evidence_event_ids": [ch["event_id"]],
                "confidence": 1.0,
                "alternative_explanations": []})
    by_char: dict[str, list[dict]] = {}
    for e in psych["emotions"]:
        by_char.setdefault(e["character_id"], []).append(e)
    for cid, records in by_char.items():
        records = sorted(records, key=lambda r: r.get("tick", 0))
        prev = None
        for r in records:
            if prev is not None and r["emotion"] != prev \
                    and r["evidence_event_ids"]:
                updates.append({
                    "event_id": (r["evidence_event_ids"] or [None])[0],
                    "character_id": cid, "tick": r.get("tick"),
                    "field": "emotion", "tier": "inference",
                    "before": {"emotion": prev},
                    "after": {"emotion": r["emotion"],
                              "intensity": r["intensity"]},
                    "reason": "scene evidence changed the "
                              "inferred emotional state",
                    "evidence_event_ids": r["evidence_event_ids"],
                    "confidence": r["confidence"],
                    "alternative_explanations": [
                        "情境性短暂反应，未形成稳定情绪变化",
                        "推断规则误判（证据不足）"]})
            prev = r["emotion"]
    for m in psych["motivated_by"]:
        updates.append({
            "event_id": m["action_event_id"],
            "character_id": m["character_id"], "tick": None,
            "field": "motivation", "tier": "hypothesis",
            "before": {"motivation_hypothesis": None},
            "after": {"motivation_hypothesis": m["motivation"],
                      "inference_status": m["inference_status"]},
            "reason": "key action inside a thread the character "
                      "is involved in",
            "evidence_event_ids": m["evidence_event_ids"],
            "confidence": m["confidence"],
            "alternative_explanations":
                m["alternative_explanations"]})
    updates.sort(key=lambda u: (u["tick"] is None,
                                u["tick"] or 0,
                                u["character_id"], u["field"]))
    return {"updates": updates,
            "note": "Incremental state updates derived from "
                    "character arcs + psychology inference; "
                    "tiers keep fact / inference / hypothesis "
                    "physically labelled."}


def write_state_updates(narrative_dir: Path,
                        out_dir: Path | None = None) -> Path:
    data = build_state_updates(narrative_dir)
    out = out_dir or (narrative_dir / "phase2")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "state_updates.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path
