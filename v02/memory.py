"""Agent-side private memory (V0.2 port of the V0.1 MemoryManager semantics).

One instance per CharacterAgent, bound to that agent's CharacterState.
Epistemic kinds stay separated exactly as in V0.1:
  experienced — the agent itself did / saw / heard it directly
  heard       — content another agent claimed (may be a lie)
  inferred    — the agent's own speculation

V0.2-A Phase 3 (task book section 5.3): beliefs are evidence-backed.
Every update appends an Evidence entry (deduplicated by
source_event_id); the belief's confidence is then RECOMPUTED from the
whole evidence set and its legacy `kind` label is DERIVED from the
highest-tier evidence — so a rumour heard first can no longer trap or
drag down later first-hand evidence (the pre-V0.2-A merge blended
0.6*old + 0.4*new across kinds and never upgraded the label).

Persistence to the run folder (state.json / memory.md / beliefs.md) happens
only when a run directory is attached; the authoritative store for V0.2
experiments is the SQLite database (see persistence.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from .protocol import (SOURCE_TIER, SOURCE_TO_KIND, Belief, CharacterState,
                       Evidence)


def _source_group(ev: Evidence) -> str:
    """The independence group of one evidence entry. Corroboration is
    only credited ACROSS groups: repeated testimony from the same
    speaker, or repeated inferences of one's own, are the same channel
    saying it again — not independent confirmation."""
    if ev.source_type == "testimony" and ev.source:
        return f"testimony:{ev.source}"
    if ev.source_type in ("direct_observation", "direct_interaction") \
            and ev.source:
        return f"{ev.source_type}:{ev.source}"
    return f"self:{ev.source_type}"


def recompute_confidence(evidence: list[Evidence]) -> float:
    """Confidence from an evidence set: within each independence
    group only the strongest entry counts; groups combine by noisy-OR
    (1 - prod(1 - r)). Consequences, by construction: a single direct
    evidence (r = 1.0) pins confidence at 1.0 and later weak testimony
    cannot drag it down; independent corroboration raises confidence
    with diminishing returns; the result is always in [0, 1]."""
    best: dict[str, float] = {}
    for ev in evidence:
        g = _source_group(ev)
        best[g] = max(best.get(g, 0.0), ev.reliability)
    miss = 1.0
    for r in best.values():
        miss *= (1.0 - r)
    return round(1.0 - miss, 3)


def derived_kind(evidence: list[Evidence]) -> str:
    """The legacy kind label of the strongest evidence tier present."""
    top = max(evidence, key=lambda e: SOURCE_TIER[e.source_type])
    return SOURCE_TO_KIND[top.source_type]


class AgentMemory:
    def __init__(self, state: CharacterState, run_char_dir: Path | None = None,
                 resume: bool = False):
        self.state = state
        self.dir = Path(run_char_dir) if run_char_dir else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            # On --resume the existing memory.md belongs to the restored
            # state and must not be truncated; resync_files() rewrites
            # it from the checkpointed short-term memory instead.
            if not (resume and (self.dir / "memory.md").exists()):
                (self.dir / "memory.md").write_text(
                    f"# {state.character_id} 的私人记忆（其他 Agent 不可见）\n",
                    encoding="utf-8")

    def resync_files(self) -> None:
        """--resume support: rewrite memory.md from the restored
        short-term memory (same line format as remember()) and refresh
        state.json / beliefs.md, so post-interruption appends continue
        the file instead of duplicating pre-interruption lines."""
        if not self.dir:
            return
        lines = [f"# {self.state.character_id} 的私人记忆（其他 Agent 不可见）"]
        for e in self.state.short_term_memory:
            lines.append(f"- [t{e['tick']}][{e['kind']}] {e['text']}")
        (self.dir / "memory.md").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
        self.save_state()

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

    def update_belief(self, key: str, confidence: float, kind: str, desc: str,
                      *, source_type: str | None = None,
                      reliability: float | None = None,
                      event_id: str | None = None, tick: int = 0,
                      source: str | None = None) -> None:
        """Record one piece of evidence for a belief and recompute.

        The positional arguments are the pre-V0.2-A call shape:
        `confidence` is the strength of THIS evidence (it becomes the
        entry's reliability unless `reliability` is given), `kind` the
        legacy epistemic label (mapped to a source_type unless
        `source_type` is given). `event_id` deduplicates: the same
        event can never add its evidence twice. Callers that have no
        world event id pass a stable synthesized one (e.g. a search
        result or a prediction id)."""
        from .protocol import KIND_TO_SOURCE
        ev = Evidence(
            source_event_id=event_id or f"auto:{tick}:{key}",
            source_type=source_type or KIND_TO_SOURCE[kind],
            reliability=confidence if reliability is None else reliability,
            observed_at=tick, source=source)
        old = self._find(key)
        if old is None:
            self.state.beliefs.append(Belief(
                key=key, confidence=recompute_confidence([ev]),
                kind=derived_kind([ev]), desc=desc, evidence=[ev]))
            return
        if any(e.source_event_id == ev.source_event_id
               for e in old.evidence):
            return                      # same event: never counted twice
        old.evidence.append(ev)
        old.confidence = recompute_confidence(old.evidence)
        old.kind = derived_kind(old.evidence)

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
            prov = ", ".join(f"{e.source_type}@{e.source_event_id}"
                             for e in b.evidence)
            lines.append(f"- {b.key} (confidence {b.confidence:.2f}, {b.kind}): "
                         f"{b.desc}（证据：{prov}）")
        (self.dir / "beliefs.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
