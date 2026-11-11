"""Character configuration loading (characters_v02/<id>/*.yaml).

Configuration (profile/personality/principles/goals) is stable and shared
with nobody's runtime state; CharacterState is built from it per run.
Content is migrated verbatim from the V0.1 profiles (A=owner, B=merchant,
C=soldier).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .protocol import CharacterState, Goal

CHARACTERS_ROOT = Path(__file__).resolve().parent.parent / "characters_v02"
ALL_IDS = ["owner", "merchant", "soldier"]


@dataclass
class CharacterConfig:
    character_id: str
    legacy_id: str
    name: str
    role: str
    identity: str
    personality: dict
    principles: list
    goals: list
    start_location: str
    possessions: list = field(default_factory=list)
    seed_offset: int = 0
    disclosure_threshold: float = 0.75
    private_knowledge: list = field(default_factory=list)

    # V0.2-A: ids of the whole cast, set by load_all() after a
    # directory scan; initial_state() falls back to the canonical
    # ALL_IDS when a config is loaded standalone.
    all_ids: list = field(default_factory=list)

    def initial_state(self) -> CharacterState:
        others = [x for x in (self.all_ids or ALL_IDS)
                  if x != self.character_id]
        goals = []
        for g in self.goals:
            g = dict(g)
            # V0.2-A Goal lifecycle: an unspecified commitment defaults
            # to the goal's configured priority (weight); status /
            # progress / created_at / deadline default in the model.
            g.setdefault("commitment", g.get("weight", 1.0))
            goals.append(Goal(**g))
        return CharacterState(
            character_id=self.character_id,
            location=self.start_location,
            personality=dict(self.personality),
            principles=list(self.principles),
            goals=goals,
            beliefs=[{"key": k["key"], "confidence": k["confidence"],
                      "kind": k["kind"], "desc": k["desc"]}
                     for k in self.private_knowledge],
            relationships={o: {"trust": 0.3, "notes": []} for o in others},
        )


def load_config(character_id: str, root: Path = CHARACTERS_ROOT) -> CharacterConfig:
    d = Path(root) / character_id
    profile = yaml.safe_load((d / "profile.yaml").read_text(encoding="utf-8"))
    personality = yaml.safe_load((d / "personality.yaml").read_text(encoding="utf-8"))["personality"]
    principles = yaml.safe_load((d / "principles.yaml").read_text(encoding="utf-8"))["principles"]
    goals = yaml.safe_load((d / "goals.yaml").read_text(encoding="utf-8"))["goals"]
    return CharacterConfig(
        character_id=profile["character_id"], legacy_id=profile.get("legacy_id", ""),
        name=profile["name"], role=profile["role"], identity=profile["identity"],
        personality=personality, principles=principles, goals=goals,
        start_location=profile["start_location"],
        possessions=profile.get("possessions", []),
        seed_offset=profile.get("seed_offset", 0),
        disclosure_threshold=profile.get("disclosure_threshold") or 0.75,
        private_knowledge=profile.get("private_knowledge", []),
    )


def load_all(root: Path = CHARACTERS_ROOT) -> dict[str, CharacterConfig]:
    """Load every character under `root`. V0.2-A (experiment 3): the
    cast is discovered by scanning the directory for profile.yaml
    files (sorted, so ordering is deterministic) instead of the
    hardcoded ALL_IDS list — a variant scenario is a new directory of
    character data, no code change. The canonical directory contains
    exactly the ALL_IDS cast, so behaviour there is unchanged."""
    root = Path(root) if root else CHARACTERS_ROOT
    ids = sorted(d.name for d in root.iterdir()
                 if d.is_dir() and (d / "profile.yaml").exists())
    configs = {cid: load_config(cid, root) for cid in ids}
    for cfg in configs.values():
        cfg.all_ids = ids
    return configs
