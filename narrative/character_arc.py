"""Character arcs (upgrade Phase 3; design §3).

Built from the NarrativeEvent layer (objective) plus clearly
labelled L-AUX traces from the run's sqlite (belief confidence
changes, goal states). Turning points are gated: learning a
thread-central fact counts only when the character later USES
it (asserts it, or acts in an event causally downstream of the
learning). One event never makes an arc: valence types
(positive/negative/tragic/...) are not auto-assigned by rules —
the default is unresolved_arc / open, and flat_arc is assigned
only when beliefs demonstrably moved while behaviour did not.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from narrative.causal_graph import trace_causes
from narrative.models.story import CharacterArc


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _aux_traces(run_dir: Path) -> tuple[dict, dict]:
    """(belief confidence changes, final goal states) per
    character from acp.sqlite3 — L-AUX corroboration only."""
    db = run_dir / "acp.sqlite3"
    beliefs: dict[str, list[dict]] = {}
    goals: dict[str, dict] = {}
    if not db.exists():
        return beliefs, goals
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for tick, char, key, kind, before, after in con.execute(
                "SELECT tick, character_id, key, kind, before, "
                "after FROM belief_changes ORDER BY tick"):
            beliefs.setdefault(char, []).append(
                {"tick": tick, "fact": key, "kind": kind,
                 "before": before, "after": after})
        for char, gkey, after_json in con.execute(
                "SELECT character_id, goal_key, after_json FROM "
                "goal_changes ORDER BY id"):
            try:
                g = json.loads(after_json)
            except (TypeError, json.JSONDecodeError):
                continue
            goals.setdefault(char, {})[gkey] = {
                "status": g.get("status"),
                "progress": g.get("progress")}
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return beliefs, goals


def build_character_arcs(run_dir: Path, narrative_dir: Path,
                         v03_dir: Path | None,
                         config_path: Path | None) -> dict:
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    graph = _load(upgrade / "causal_graph.json")
    from narrative.models.story import CausalEdge
    edges = [CausalEdge(**e) for e in graph["edges"]]
    storylines = _load(narrative_dir / "storylines.json")
    meta = _load(run_dir / "run_meta.json")
    characters = meta.get("characters") or meta.get("agents") or []

    central_facts: set[str] = set()
    for line in storylines.get("lines", []):
        central_facts |= set(line.get("propositions", []))
    if v03_dir is not None and (v03_dir / "story_state.json").exists():
        state = _load(v03_dir / "story_state.json")
        for th in (state.get("threads") or {}).values():
            central_facts |= set(th.get("known_facts", []))
    for od in (meta.get("scenario", {}).get("objects") or {}).values():
        if od.get("fact_belief"):
            central_facts.add(od["fact_belief"])

    from narrative.initial_knowledge import seed_initial_knowledge
    initial_knowledge: dict[str, list[str]] = {
        c: list(f) for c, f in
        seed_initial_knowledge(run_dir).items()}
    declared_conflicts: list[dict] = []
    if config_path is not None and config_path.exists():
        cfg = _load(config_path)
        for c, f in (cfg.get("initial_knowledge") or {}).items():
            merged = sorted(set(initial_knowledge.get(c, [])) |
                            set(f))
            initial_knowledge[c] = merged
        declared_conflicts = cfg.get("initial_conflicts") or []

    aux_beliefs, aux_goals = _aux_traces(run_dir)
    by_char_events: dict[str, list[dict]] = {c: [] for c in characters}
    for e in events:
        for c in e.get("participants", []):
            by_char_events.setdefault(c, []).append(e)

    arcs: list[dict] = []
    diagnostics: dict[str, dict] = {}
    for char in characters:
        learned: list[dict] = []
        for e in events:
            for k in e.get("knowledge_changes", []):
                if k["character"] == char and \
                        k["change"] == "learned":
                    learned.append({"fact": k["fact"],
                                    "tick": e["tick"],
                                    "event_id": e["narrative_event_id"],
                                    "via": k["via"]})
        # turning-point gate: central fact + downstream use
        turning: list[dict] = []
        rejected: list[dict] = []
        char_events = by_char_events.get(char, [])
        for item in learned:
            if item["fact"] not in central_facts:
                continue
            learn_tick = item["tick"]
            uses = []
            for e in char_events:
                if e["tick"] <= learn_tick:
                    continue
                if item["event_id"] in trace_causes(
                        edges, e["narrative_event_id"]):
                    uses.append(e["narrative_event_id"])
            # second use signal: the character later relays the
            # fact (their own events teach it to someone else)
            relayed = []
            for e in char_events:
                if e["tick"] <= learn_tick:
                    continue
                for k in e.get("knowledge_changes", []):
                    if k["fact"] == item["fact"] and \
                            k["via"] == "delivery" and \
                            char in e.get("participants", [])[:1]:
                        relayed.append(e["narrative_event_id"])
            downstream = sorted(set(uses) | set(relayed))
            entry = {"fact": item["fact"], "tick": learn_tick,
                     "learned_at": item["event_id"],
                     "downstream_uses": downstream}
            if downstream:
                turning.append(entry)
            else:
                rejected.append(entry)
        # behaviour distribution before/after for the flat test
        types_all = Counter(e["event_type"] for e in char_events)
        total = sum(types_all.values()) or 1
        dist = {t: round(n / total, 3) for t, n in types_all.items()}
        arc_type = "unresolved_arc"
        if not turning and learned:
            arc_type = "flat_arc"  # knowledge moved, no gated
            # turning point: behaviour did not reorganise around
            # any new central fact
        internal = ""
        for c in declared_conflicts:
            if char in (c.get("participants") or []):
                internal = c.get("basis", "")
        arc = CharacterArc(
            character_id=char,
            initial_state={"known_facts":
                           initial_knowledge.get(char, []),
                           "action_distribution": dist},
            current_state={
                "known_facts": sorted({i["fact"] for i in learned} |
                                      set(initial_knowledge.get(
                                          char, []))),
                "goals_aux": aux_goals.get(char, {})},
            initial_beliefs=initial_knowledge.get(char, []),
            belief_changes=learned,
            internal_conflict=internal,
            turning_points=turning,
            character_arc_type=arc_type, arc_status="open")
        arcs.append(arc.model_dump(mode="json"))
        diagnostics[char] = {
            "impact_without_followthrough": rejected,
            "aux_belief_changes": aux_beliefs.get(char, [])}
    return {"arcs": arcs, "diagnostics": diagnostics,
            "notes": ["emotional_changes and value_changes are "
                      "left empty: no deterministic source exists "
                      "in the narrative inputs (no emotion trace "
                      "is recorded by the emergence layer).",
                      "aux traces come from acp.sqlite3 "
                      "(L-AUX corroboration, not objective fact).",
                      "valence arc types are never auto-assigned "
                      "by the rule layer."]}


def write_character_arcs(run_dir: Path, narrative_dir: Path,
                         v03_dir: Path | None,
                         config_path: Path | None,
                         out_dir: Path) -> Path:
    result = build_character_arcs(run_dir, narrative_dir, v03_dir,
                                  config_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "character_arcs.json"
    path.write_text(json.dumps(result, ensure_ascii=False,
                               indent=1), encoding="utf-8")
    return path
