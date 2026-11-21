"""V0.3 story-domain models (task book §4/§5.2/§6.1/§6.3).

Pydantic schemas. These are the Story Engine's own bookkeeping —
subjective story state, NOT objective world facts. Objective facts
live only in world_log / acp.sqlite3; everything here is derived
from committed events plus the story configuration.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

THREAD_STATUSES = ("active", "dormant", "escalating", "blocked",
                   "resolved", "abandoned")
CONFLICT_TYPES = ("goal", "resource", "information", "interest",
                  "value", "relationship", "authority", "moral")
CONFLICT_STATUSES = ("latent", "active", "escalating", "resolved")


class CharacterGoalView(BaseModel):
    """A character goal as the Story Engine sees it (task book §6.1).

    Built from the character's own goals.yaml (V0.2 format) plus the
    story config's metadata overlay. The V0.2 Goal Engine remains the
    only evaluator of goal success/failure; this view never feeds
    back into character decisions.
    """
    character_id: str
    goal_id: str
    description: str = ""
    priority: float = 0.5          # from goals.yaml weight
    urgency: float = 0.0
    kind: str = "achievement"
    status: str = "unknown"        # mirrored from goal_changes, if fed
    success_beliefs: list[str] = Field(default_factory=list)
    failure_beliefs: list[str] = Field(default_factory=list)
    required_resources: list[str] = Field(default_factory=list)
    deadline: int | None = None


class ThreadQuestion(BaseModel):
    id: str
    text: str = ""
    answered_by: str | None = None   # proposition that can answer it
    answered_at: int | None = None   # answered by DIRECT evidence only
    addressed_at: int | None = None  # voiced/asserted in-world (weaker)


class StoryThread(BaseModel):
    """Task book §5.2, plus the event hooks the manager matches on."""
    thread_id: str
    title: str
    thread_type: str = "main"
    central_question: str = ""
    status: str = "active"
    involved_characters: list[str] = Field(default_factory=list)
    questions: list[ThreadQuestion] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    stakes: list[str] = Field(default_factory=list)
    progress: float = 0.0
    dependencies: list[str] = Field(default_factory=list)
    related_threads: list[str] = Field(default_factory=list)
    shared_events: list[str] = Field(default_factory=list)
    related_events: list[str] = Field(default_factory=list)
    possible_resolutions: list[str] = Field(default_factory=list)
    created_at: int = 0
    last_developed_at: int = 0
    resolved_at: int | None = None
    # --- matching hooks (config, not story facts) ---
    propositions: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    sensitive_facts: list[str] = Field(default_factory=list)
    # optional declared pressure event for the Phase 2 generator:
    # {kind, figure, location, description} — a happening, never a
    # fact implant; characters respond (or not) on their own
    pressure_event: dict | None = None
    # optional declared opportunity event for dormant-thread revival:
    # {location, description}
    opportunity_event: dict | None = None
    # --- per-character knowledge: char -> fact ids known about this thread
    known_by: dict[str, list[str]] = Field(default_factory=dict)


class CharacterConflict(BaseModel):
    """Task book §6.3."""
    conflict_id: str
    conflict_type: str
    participants: list[str]
    goals_involved: list[str] = Field(default_factory=list)
    conflicting_interests: list[str] = Field(default_factory=list)
    severity: float = 0.3
    escalation: float = 0.0
    related_threads: list[str] = Field(default_factory=list)
    related_events: list[str] = Field(default_factory=list)
    status: str = "latent"
    basis: str = ""                # how this conflict was detected
    last_touched_at: int = 0


class InitialConflict(BaseModel):
    conflict_type: str
    participants: list[str]
    goals_involved: list[str] = Field(default_factory=list)
    conflicting_interests: list[str] = Field(default_factory=list)
    related_threads: list[str] = Field(default_factory=list)
    severity: float = 0.3
    basis: str = "declared in story config"


class StoryConfig(BaseModel):
    """Story Architect input (task book §4). Initial conditions only —
    no plot outline, no prescribed ending."""
    story_id: str
    title: str
    genre: str = ""
    theme: str = ""
    central_question: str = ""
    scenario_id: str = ""
    characters: list[str] = Field(default_factory=list)
    stakes: list[str] = Field(default_factory=list)
    ending_constraints: list[str] = Field(default_factory=list)
    initial_threads: list[StoryThread] = Field(default_factory=list)
    initial_conflicts: list[InitialConflict] = Field(default_factory=list)
    goal_metadata: dict[str, dict] = Field(default_factory=dict)
    initial_knowledge: dict[str, list[str]] = Field(default_factory=dict)
    dormant_after_ticks: int = 8
    stagnation_threshold: int = 6
    proposal_cooldown: int = 5


class EventCandidate(BaseModel):
    """Task book §7.2. A PROPOSAL only: it enters the world solely
    by passing causal + world-rule validation and then being
    committed by the World Engine as an ordinary event."""
    candidate_id: str
    source_type: str               # character_driven|world_driven|story_driven
    kind: str = ""                 # external_arrival|opportunity_notice|trace_event
    description: str = ""
    proposed_location: str = ""
    involved_characters: list[str] = Field(default_factory=list)
    triggering_conditions: list[str] = Field(default_factory=list)
    causal_basis: list[str] = Field(default_factory=list)  # event ids
    related_threads: list[str] = Field(default_factory=list)
    related_conflicts: list[str] = Field(default_factory=list)
    expected_world_changes: dict = Field(default_factory=dict)
    dramatic_functions: list[str] = Field(default_factory=list)
    uncertainty: float = 0.5
    validity_requirements: list[str] = Field(default_factory=list)
    fingerprint: str = ""
    created_at: int = 0
    evaluation: dict = Field(default_factory=dict)
    validation: dict = Field(default_factory=dict)


class StoryState(BaseModel):
    story_id: str
    title: str
    genre: str = ""
    theme: str = ""
    central_question: str = ""
    stakes: list[str] = Field(default_factory=list)
    ending_constraints: list[str] = Field(default_factory=list)
    tick: int = 0
    threads: dict[str, StoryThread] = Field(default_factory=dict)
    conflicts: dict[str, CharacterConflict] = Field(default_factory=dict)
    goals: list[CharacterGoalView] = Field(default_factory=list)
    # stagnation signals (deterministic; consumed by Phase 2)
    ticks_since_plot_event: int = 0
    ticks_since_thread_progress: int = 0
    ticks_since_belief_change: int = 0
    change_log: list[dict] = Field(default_factory=list)
