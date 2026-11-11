"""Agent-side private memory (V0.2 port of the V0.1 MemoryManager semantics).

One instance per CharacterAgent, bound to that agent's CharacterState.
Epistemic kinds stay separated exactly as in V0.1:
  experienced — the agent itself did / saw / heard it directly
  heard       — content another agent claimed (may be a lie)
  inferred    — the agent's own speculation

Persistence to the run folder (state.json / memory.md / beliefs.md) happens
only when a run directory is attached; the authoritative store for V0.2
experiments is the SQLite database (see persistence.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from .protocol import Belief, CharacterState


class AgentMemory:
    def __init__(self, state: CharacterState, run_char_dir: Path | None = None):
        self.state = state
        self.dir = Path(run_char_dir) if run_char_dir else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "memory.md").write_text(
                f"# {state.character_id} 的私人记忆（其他 Agent 不可见）\n", encoding="utf-8")

    # ---------- episodic ----------
    def remember(self, tick: int, kind: str, text: str, event_id: str | None = None) -> None:
        assert kind in ("experienced", "heard", "inferred"), kind
        self.state.short_term_memory.append(
            {"tick": tick, "kind": kind, "text": text, "event_id": event_id})
        if event_id and event_id not in self.state.perceived_event_ids:
            self.state.perceived_event_ids.append(event_id)
        if self.dir:
            with (self.dir / "memory.md").open("a", encoding="utf-8") as f:
                f.write(f"- [t{tick}][{kind}] {text}\n")

    # ---------- beliefs ----------
    def _find(self, key: str) -> Belief | None:
        for b in self.state.beliefs:
            if b.key == key:
                return b
        return None

    def update_belief(self, key: str, confidence: float, kind: str, desc: str) -> None:
        old = self._find(key)
        if old:
            # Same V0.1 merge rule: never blindly overwrite a stronger source.
            merged = max(old.confidence, confidence) if kind == old.kind else \
                round(0.6 * old.confidence + 0.4 * confidence, 3)
            old.confidence = merged
        else:
            self.state.beliefs.append(Belief(key=key, confidence=confidence,
                                             kind=kind, desc=desc))

    def belief(self, key: str) -> Belief | None:
        return self._find(key)

    # ---------- relationships ----------
    def adjust_trust(self, other: str, delta: float) -> None:
        rel = self.state.relationships.setdefault(other, {"trust": 0.3, "notes": []})
        rel["trust"] = round(min(1.0, max(0.0, rel["trust"] + delta)), 3)

    def trust_in(self, other: str) -> float:
        return self.state.relationships.get(other, {}).get("trust", 0.3)

    # ---------- persistence (run artefacts) ----------
    def save_state(self) -> None:
        if not self.dir:
            return
        (self.dir / "state.json").write_text(
            json.dumps(self.state.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"# {self.state.character_id} 当前相信的事情（自动生成，可能与事实不符）", ""]
        for b in sorted(self.state.beliefs, key=lambda x: x.key):
            lines.append(f"- {b.key} (confidence {b.confidence:.2f}, {b.kind}): {b.desc}")
        (self.dir / "beliefs.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
