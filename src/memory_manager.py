"""Memory Manager — agent-side only.

Each agent process owns exactly one MemoryManager bound to its own
characters/<id>/ directory inside the run folder. It never opens another
agent's directory. Memories are tagged by epistemic kind:

  experienced - the agent itself did / saw / heard it directly
  heard       - another agent said it (may be a lie)
  inferred    - the agent's own speculation

Objective world history lives in the World Engine's event log, which is a
different file in a different directory (runs/<run>/world_log.jsonl) and is
never read by agents.
"""
from __future__ import annotations

import json
from pathlib import Path


class MemoryManager:
    def __init__(self, char_dir: Path):
        self.dir = Path(char_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.memory_path = self.dir / "memory.md"
        self.beliefs_path = self.dir / "beliefs.md"
        self.state = self._load_state()

    # ---------- state (beliefs / emotions / plan / relationships) ----------
    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        profile = json.loads((self.dir / "profile.json").read_text(encoding="utf-8"))
        beliefs = {
            k["key"]: {"confidence": k["confidence"], "kind": k["kind"], "desc": k["desc"]}
            for k in profile.get("private_knowledge", [])
        }
        others = [x for x in ("A", "B", "C") if x != profile["id"]]
        state = {
            "agent_id": profile["id"],
            "beliefs": beliefs,
            "emotions": {"arousal": 0.3, "valence": 0.0, "suspicion": 0.3},
            "relationships": {o: {"trust": 0.3, "notes": []} for o in others},
            "plan": ["observe_surroundings"],
            "perceived_event_ids": [],
            "utterances_heard": [],
        }
        self.save_state(state)
        return state

    def save_state(self, state: dict | None = None) -> None:
        if state is not None:
            self.state = state
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._render_beliefs_md()

    def _render_beliefs_md(self) -> None:
        lines = [f"# {self.state['agent_id']} 当前相信的事情（自动生成，可能与事实不符）", ""]
        for key, b in sorted(self.state["beliefs"].items()):
            lines.append(f"- {key} (confidence {b['confidence']:.2f}, {b['kind']}): {b['desc']}")
        self.beliefs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- episodic memory ----------
    def remember(self, tick: int, kind: str, text: str, event_id: str | None = None) -> None:
        assert kind in ("experienced", "heard", "inferred"), kind
        with self.memory_path.open("a", encoding="utf-8") as f:
            f.write(f"- [t{tick}][{kind}] {text}\n")
        if event_id and event_id not in self.state["perceived_event_ids"]:
            self.state["perceived_event_ids"].append(event_id)

    # ---------- beliefs ----------
    def update_belief(self, key: str, confidence: float, kind: str, desc: str) -> None:
        old = self.state["beliefs"].get(key)
        if old:
            # Hearing the same claim again nudges confidence toward the new
            # evidence; it never blindly overwrites a stronger source.
            merged = max(old["confidence"], confidence) if kind == old["kind"] else \
                round(0.6 * old["confidence"] + 0.4 * confidence, 3)
            self.state["beliefs"][key] = {"confidence": merged, "kind": old["kind"], "desc": old["desc"]}
        else:
            self.state["beliefs"][key] = {"confidence": confidence, "kind": kind, "desc": desc}

    def belief(self, key: str) -> dict | None:
        return self.state["beliefs"].get(key)

    def adjust_trust(self, other: str, delta: float) -> None:
        rel = self.state["relationships"].setdefault(other, {"trust": 0.3, "notes": []})
        rel["trust"] = round(min(1.0, max(0.0, rel["trust"] + delta)), 3)

    def trust_in(self, other: str) -> float:
        return self.state["relationships"].get(other, {}).get("trust", 0.3)
