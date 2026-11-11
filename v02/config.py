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

    def initial_state(self) -> CharacterState:
        others = [x for x in ALL_IDS if x != self.character_id]
        return CharacterState(
            character_id=self.character_id,
            location=self.start_location,
            personality=dict(self.personality),
            principles=list(self.principles),
            goals=[Goal(**g) for g in self.goals],
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
    return {cid: load_config(cid, root) for cid in ALL_IDS}
