"""Scenario Definition & Capability Registry (V0.2-B, design 3.1/3.2).

A scenario's ENTIRE static semantics — locations, adjacency, scenery,
key objects with capability attributes, utterance claim classes and
speech lines, ambient events — is DATA, loaded once per run into a
ScenarioDefinition and injected into every consumer (World Engine,
Broker, Context Builder, Prediction, Agents, Providers). Decision
code reads only this instance; it contains no location/object/claim
literals of any particular scenario.

The Capability Registry answers "what does the environment allow
here" — the single source of available_actions. Capability is NOT
knowledge: a search is offered where the scenery affords searching
(a drawer exists there — structural, public), never because an
object is objectively present (that would leak the world into the
prompt). What is worth doing is the agents' beliefs' business.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .conditions import validate_subjective


class Reveal(BaseModel):
    """A proposition an object's contents testify to when the object
    is found or shown (design 3.1): the object itself is the evidence
    source, with this declared reliability."""
    model_config = ConfigDict(extra="forbid")
    belief: str
    reliability: float = Field(ge=0.0, le=1.0)


class ScenarioObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: Optional[str] = None      # objective start location
    holder: Optional[str] = None        # registered holder (cast id)
    seen_by: list[str] = []
    searchable: bool = False            # a search can find it
    movable: bool = False               # data bit only in this version
    displayable: bool = False           # a holder can show it
    on_found: Literal["inspect", "acquire"] = "inspect"
    fact_belief: Optional[str] = None   # finding it proves this (direct)
    reveals: list[Reveal] = []


class ClaimDef(BaseModel):
    """One utterance claim class (design 3.5).

    proposition   the belief proposition hearers gain testimony
                  evidence for (assert stance); None = pure social
                  content with no belief effect
    stance        assert | deny | inquire. A deny's proposition is
                  resolved at runtime (what was asked), never declared.
    base_reliability  the claim class's inherent credibility; the
                  hearer-side evidence reliability is this times a
                  trust factor in the speaker (design 3.5 channel 1)
    sensitive     being asked/confronted about this class carries the
                  generic suspicion effect (channel 2)
    speaker_belief / basis_required / speaker_min_confidence /
    speaker_kind
                  SPEAKER-side eligibility for asserting this class:
                  by default the speaker must hold the proposition
                  itself; a claim may instead name the belief that
                  grounds it (a first-person experience whose HEARER
                  conclusion is the proposition — e.g. a late-payment
                  story evidencing embezzlement). basis_required=False
                  declares a basis-free claim (a proposal, a bluff):
                  anyone may voice it, and the SocialCost honesty
                  factor prices unevidenced assertion.
    """
    model_config = ConfigDict(extra="forbid")
    proposition: Optional[str] = None
    stance: Literal["assert", "deny", "inquire"] = "assert"
    base_reliability: float = Field(default=0.5, ge=0.0, le=1.0)
    sensitive: bool = False
    speaker_belief: Optional[str] = None
    basis_required: bool = True
    speaker_min_confidence: float = 0.0
    speaker_kind: Optional[str] = None

    def basis_beliefs(self) -> list[str]:
        if not self.basis_required:
            return []
        if self.speaker_belief:
            return [self.speaker_belief]
        return [self.proposition] if self.proposition else []


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    version: int = 1
    comment: Optional[str] = None       # free annotation; ignored by code
    locations: list[str]
    adjacency: dict[str, list[str]]
    scenery: dict[str, list] = {}
    objects: dict[str, ScenarioObject] = {}
    claims: dict[str, ClaimDef] = {}
    speech_lines: dict[str, object] = {}   # claim -> str | list[str]
    ambient: list = []

    # ---------------- derived structure ----------------
    def adjacency_sets(self) -> dict[str, set]:
        return {loc: set(ns) for loc, ns in self.adjacency.items()}

    def scenery_ids(self, location: str) -> list[str]:
        out = []
        for entry in self.scenery.get(location, []):
            out.append(entry["id"] if isinstance(entry, dict) else entry)
        return out

    def searchable_at(self, location: str) -> bool:
        """Structural searchability: the location's scenery declares
        something searchable. Object presence is deliberately NOT
        consulted — that is objective knowledge, not capability."""
        return any(isinstance(e, dict) and e.get("searchable")
                   for e in self.scenery.get(location, []))

    def searchable_locations(self) -> list[str]:
        return sorted(loc for loc in self.locations if self.searchable_at(loc))

    def claim_for_proposition(self, proposition: str,
                              stance: str) -> str | None:
        """The (first, key-sorted) claim class asserting/inquiring a
        proposition — claim classes are data, so templates resolve
        propositions to classes here, never by name in code."""
        for key in sorted(self.claims):
            c = self.claims[key]
            if c.proposition == proposition and c.stance == stance:
                return key
        return None

    def claims_for_proposition(self, proposition: str,
                               stance: str) -> list[str]:
        """ALL claim classes for a proposition at a stance, key-sorted.
        Different speakers are eligible for different classes (their
        declared bases differ), so generators pick per-speaker from
        this list rather than taking the global first."""
        return [key for key in sorted(self.claims)
                if self.claims[key].proposition == proposition
                and self.claims[key].stance == stance]

    def deny_claim(self) -> str | None:
        for key in sorted(self.claims):
            if self.claims[key].stance == "deny":
                return key
        return None

    def social_claim(self) -> str | None:
        """A proposition-free assert class usable as small talk."""
        for key in sorted(self.claims):
            c = self.claims[key]
            if c.proposition is None and c.stance == "assert":
                return key
        return None

    def object_for_fact_belief(self, proposition: str) -> str | None:
        for oid in sorted(self.objects):
            if self.objects[oid].fact_belief == proposition:
                return oid
        return None

    def to_world_config(self) -> dict:
        """The plain-dict world config the World Engine consumes:
        structure in the historical shape, plus object capability
        attributes the Resolver reads (searchable/displayable/
        on_found per declared object)."""
        return {
            "locations": list(self.locations),
            "scenery": {loc: self.scenery_ids(loc) for loc in self.locations},
            "adjacency": {loc: list(ns) for loc, ns in self.adjacency.items()},
            "objects": {oid: {"holder": o.holder, "location": o.location,
                              "seen_by": list(o.seen_by)}
                        for oid, o in self.objects.items()},
            "object_attrs": {oid: {"searchable": o.searchable,
                                   "displayable": o.displayable,
                                   "on_found": o.on_found}
                             for oid, o in self.objects.items()},
            "ambient": [list(a) for a in self.ambient],
        }


class CapabilityRegistry:
    """available_actions from the scenario instance + the agent's
    position/co-location/holdings (design 3.2). The ONLY source."""

    def __init__(self, scenario: ScenarioDefinition):
        self.scenario = scenario

    def available_actions(self, *, location: str, colocated: list[str],
                          held_objects: list[str]) -> list[dict]:
        sc = self.scenario
        out: list[dict] = []
        for dest in sorted(sc.adjacency.get(location, [])):
            out.append({"type": "move", "target_location": dest})
        for other in colocated:
            for verb in ("speak", "ask", "confront"):
                out.append({"type": verb, "target": other})
        if sc.searchable_at(location):
            out.append({"type": "search", "target_location": location})
        for oid in held_objects:
            obj = sc.objects.get(oid)
            if obj is not None and obj.displayable:
                for other in colocated:
                    out.append({"type": "show", "object": oid,
                                "target": other})
        out += [{"type": "observe"}, {"type": "listen"}, {"type": "work"},
                {"type": "refuse"}, {"type": "wait"}]
        return out


def load_scenario(raw: dict | None) -> ScenarioDefinition:
    """Validate raw scenario data (a world_config-shaped dict, a full
    Scenario Definition dict, or None for the default tavern) into a
    ScenarioDefinition. Reference errors fail HERE, at load, naming
    the culprit — scenario data errors must not explode mid-run."""
    if raw is None:
        from .world_config import DEFAULT_WORLD
        raw = DEFAULT_WORLD
    data = dict(raw)
    data.setdefault("scenario_id", "unnamed_scenario")
    sc = ScenarioDefinition.model_validate(data)
    locs = set(sc.locations)
    for loc, neighbours in sc.adjacency.items():
        if loc not in locs:
            raise ValueError(f"scenario {sc.scenario_id}: adjacency key "
                             f"{loc!r} is not a declared location")
        for n in neighbours:
            if n not in locs:
                raise ValueError(f"scenario {sc.scenario_id}: adjacency "
                                 f"{loc!r} -> {n!r} names an unknown location")
    for loc in sc.scenery:
        if loc not in locs:
            raise ValueError(f"scenario {sc.scenario_id}: scenery for "
                             f"unknown location {loc!r}")
    for oid, obj in sc.objects.items():
        if obj.location is not None and obj.location not in locs:
            raise ValueError(f"scenario {sc.scenario_id}: object {oid!r} "
                             f"location {obj.location!r} is not a location")
    for key, claim in sc.claims.items():
        if claim.stance == "assert" and claim.proposition is None \
                and claim.basis_required and claim.speaker_belief is None:
            raise ValueError(f"scenario {sc.scenario_id}: claim {key!r} "
                             "asserts nothing (no proposition) yet "
                             "requires a speaker basis")
    return sc


def default_scenario() -> ScenarioDefinition:
    return load_scenario(None)


def validate_goal_conditions(goals: list[dict], owner_desc: str) -> None:
    """Character-config contract (design 3.4): goal conditions must
    be subjective closed-vocabulary conditions. Checked at load so a
    bad goals.yaml fails the run at start, naming the goal."""
    for g in goals:
        for field_name in ("activation_condition", "success_condition",
                           "failure_condition"):
            cond = g.get(field_name)
            if cond is not None:
                validate_subjective(
                    cond, f"{owner_desc} goal {g.get('key')!r} {field_name}")
        for i, rule in enumerate(g.get("priority_rules") or []):
            validate_subjective(rule.get("when"),
                                  f"{owner_desc} goal {g.get('key')!r} "
                                  f"priority_rules[{i}]")
