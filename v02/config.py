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
    # V0.2-B: the character's routine work — data for the Generic
    # Candidate Generator's baseline candidates ({"verb": "work",
    # "topic": ...}); replaces the role-keyed work topics the old
    # rule provider hardcoded.
    routine: dict = field(default_factory=dict)
    # V0.2-C C2 (design 3.5): disclosure duties — standing
    # obligations to inform, declared in the character's profile
    # (NOT as goals: a duty must not be pausable by goal
    # contention). Shape: [{when: <subjective Condition>,
    # proposition: str, targets: [agent_id, ...] | "colocated",
    # urgency: low|normal|high}]. Validated at load (when must be
    # a subjective condition; the proposition/cast cross-checks
    # need the scenario and run in the agent's init).
    disclosure_duties: list = field(default_factory=list)

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
            # V0.2-B: priority_current starts at the static weight.
            g.setdefault("priority_current", g.get("weight", 1.0))
            # V0.2-B B2: a goal WITH an activation condition starts
            # inactive — the GoalEvaluator activates it when the
            # condition first holds (design 4.2). Goals without one
            # are active from the start, as before.
            if g.get("activation_condition") is not None:
                g.setdefault("status", "inactive")
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


def validate_goal_dependencies(goals: list[dict], owner_desc: str) -> None:
    """V0.2-C C1 (design 2.2): depends_on names keys of the SAME
    character's goals only. Unknown keys (which is what a
    cross-character key necessarily is, from one character's file),
    self-dependencies and cycles all fail at load, naming the goal —
    a dependency typo must never silently gate a goal forever."""
    import warnings
    keys = {g.get("key") for g in goals}
    graph: dict[str, list[str]] = {}
    for g in goals:
        key = g.get("key")
        deps = list(g.get("depends_on") or [])
        for dep in deps:
            if dep == key:
                raise ValueError(
                    f"{owner_desc} goal {key!r}: depends_on names itself")
            if dep not in keys:
                raise ValueError(
                    f"{owner_desc} goal {key!r}: depends_on names "
                    f"{dep!r}, which is not one of this character's "
                    "goals (depends_on is same-character only; "
                    "cross-character information flow is expressed "
                    "through activation conditions over own beliefs)")
        graph[key] = deps
        # scheduling.pausable is meaningless on maintenance goals
        # (they never pause); declaring it is ignored, with a warning
        sched = g.get("scheduling") or {}
        if g.get("kind") == "maintenance" and (
                "pausable" in sched or "pausable" in g):
            warnings.warn(
                f"{owner_desc} goal {key!r}: 'pausable' declared on "
                "a maintenance goal — maintenance goals never pause; "
                "the declaration is ignored", stacklevel=2)
    # cycle check (iterative DFS over the dependency graph)
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in graph}
    for start in sorted(graph):
        if color[start] != WHITE:
            continue
        stack = [(start, iter(sorted(graph[start])))]
        color[start] = GREY
        path = [start]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color[nxt] == GREY:
                    cycle = path[path.index(nxt):] + [nxt]
                    raise ValueError(
                        f"{owner_desc}: depends_on cycle: "
                        + " -> ".join(cycle))
                if color[nxt] == WHITE:
                    color[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, iter(sorted(graph[nxt]))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
                path.pop()


def load_config(character_id: str, root: Path = CHARACTERS_ROOT) -> CharacterConfig:
    d = Path(root) / character_id
    profile = yaml.safe_load((d / "profile.yaml").read_text(encoding="utf-8"))
    personality = yaml.safe_load((d / "personality.yaml").read_text(encoding="utf-8"))["personality"]
    principles = yaml.safe_load((d / "principles.yaml").read_text(encoding="utf-8"))["principles"]
    goals = yaml.safe_load((d / "goals.yaml").read_text(encoding="utf-8"))["goals"]
    # V0.2-B: goal conditions are decision-path data — they must be
    # subjective closed-vocabulary conditions (design 3.4). Fail at
    # load, naming the goal, not mid-run.
    from .conditions import validate_subjective
    from .scenario import validate_goal_conditions
    validate_goal_conditions(goals, f"character {character_id}")
    validate_goal_dependencies(goals, f"character {character_id}")
    duties = profile.get("disclosure_duties", []) or []
    for i, duty in enumerate(duties):
        if duty.get("when") is not None:
            validate_subjective(
                duty["when"],
                f"character {character_id} disclosure_duties[{i}].when")
        if not duty.get("proposition"):
            raise ValueError(
                f"character {character_id} disclosure_duties[{i}]: "
                "a duty must name its proposition")
        if duty.get("urgency", "normal") not in ("low", "normal", "high"):
            raise ValueError(
                f"character {character_id} disclosure_duties[{i}]: "
                f"urgency {duty.get('urgency')!r} is not "
                "low|normal|high")
        targets = duty.get("targets")
        if targets != "colocated" and not (
                isinstance(targets, list) and targets):
            raise ValueError(
                f"character {character_id} disclosure_duties[{i}]: "
                "targets must be a non-empty agent-id list or "
                "\"colocated\"")
    return CharacterConfig(
        character_id=profile["character_id"], legacy_id=profile.get("legacy_id", ""),
        name=profile["name"], role=profile["role"], identity=profile["identity"],
        personality=personality, principles=principles, goals=goals,
        start_location=profile["start_location"],
        possessions=profile.get("possessions", []),
        seed_offset=profile.get("seed_offset", 0),
        disclosure_threshold=profile.get("disclosure_threshold") or 0.75,
        private_knowledge=profile.get("private_knowledge", []),
        routine=profile.get("routine", {}) or {},
        disclosure_duties=profile.get("disclosure_duties", []) or [],
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
