"""Initial knowledge derived from the run's own character state
files: beliefs whose evidence cites a `seed:` source were
planted at character creation, before tick 1. Relational
pseudo-beliefs (keys containing ':') are not facts and are
skipped. This is L-CTX data — part of the run's frozen record.
"""
from __future__ import annotations

import json
from pathlib import Path


def seed_initial_knowledge(run_dir: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    chars_dir = run_dir / "characters"
    if not chars_dir.exists():
        return out
    for d in sorted(chars_dir.iterdir()):
        state_file = d / "state.json"
        if not state_file.exists():
            continue
        st = json.loads(state_file.read_text(encoding="utf-8"))
        facts = []
        for b in st.get("beliefs", []):
            key = b.get("key", "")
            if ":" in key:
                continue
            if any(str(e.get("source_event_id", "")).startswith(
                    "seed:") for e in b.get("evidence", [])):
                facts.append(key)
        if facts:
            out[st.get("character_id", d.name)] = sorted(facts)
    return out
