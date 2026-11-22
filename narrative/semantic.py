"""Semantic layer (upgrade Phase 2): repetition structure over the
projected NarrativeEvents.

The rule-mode emergence layer repeats canned lines verbatim; the
novel pipeline must know which events say the same thing so the
Writer can narrate a fact once. Two group kinds:
  verbatim_repeat — same speaker, same text;
  fact_relay      — same proposition + stance, different speakers
                    or phrasings.
Groups are organisational facts about the corpus, derived
deterministically; they change no event and assert no causality.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def build_merge_groups(observations: list[dict]) -> dict:
    verbatim: dict[tuple, list[str]] = defaultdict(list)
    relay: dict[tuple, list[str]] = defaultdict(list)
    speech = 0
    for obs in observations:
        if obs["type"] not in ("speak", "ask", "confront"):
            continue
        speech += 1
        text = (obs.get("text") or "").strip()
        speaker = (obs.get("actors") or [""])[0]
        if text:
            verbatim[(speaker, text)].append(obs["obs_id"])
        prop = obs.get("proposition")
        if prop:
            relay[(prop, obs.get("stance") or "")].append(
                obs["obs_id"])
    groups: list[dict] = []
    for (speaker, text), ids in sorted(verbatim.items()):
        if len(ids) >= 2:
            groups.append({
                "group_id": f"MG-V{len(groups) + 1:02d}",
                "kind": "verbatim_repeat", "speaker": speaker,
                "text": text[:80],
                "primary_ne_id": f"NE-{ids[0]}",
                "member_ne_ids": [f"NE-{i}" for i in ids]})
    for (prop, stance), ids in sorted(relay.items()):
        if len(ids) >= 2:
            groups.append({
                "group_id": f"MG-R{len(groups) + 1:02d}",
                "kind": "fact_relay", "proposition": prop,
                "stance": stance,
                "primary_ne_id": f"NE-{ids[0]}",
                "member_ne_ids": [f"NE-{i}" for i in ids]})
    grouped = {m for g in groups for m in g["member_ne_ids"]}
    return {"groups": groups,
            "stats": {"speech_events": speech,
                      "grouped_events": len(grouped),
                      "unique_after_merge": speech - len(grouped)
                      + len(groups)}}


def write_semantic(narrative_dir: Path, out_dir: Path) -> Path:
    observations = json.loads((narrative_dir / "observations.json")
                              .read_text(encoding="utf-8"))
    result = build_merge_groups(observations)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "semantic.json"
    path.write_text(json.dumps(result, ensure_ascii=False,
                               indent=1), encoding="utf-8")
    return path
