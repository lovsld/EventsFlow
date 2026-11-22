"""Companion schemas for the novel upgrade (design §1.3): typed
now, implemented phase by phase. Fields follow the task book so
later phases build against stable shapes."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CausalEdge(BaseModel):
    """Unified edge (task book §6). `from`/`to` are NarrativeEvent
    ids; direction reads as the relation states, e.g.
    from=NE002, to=NE001, relation=CAUSED_BY means NE002 was
    caused by NE001."""
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    relation: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    source: str = "rule"
    verification_status: str = "verified"

    model_config = {"populate_by_name": True}


class CharacterArc(BaseModel):
    character_id: str
    initial_state: dict = Field(default_factory=dict)
    current_state: dict = Field(default_factory=dict)
    final_state: dict | None = None
    initial_beliefs: list[str] = Field(default_factory=list)
    belief_changes: list[dict] = Field(default_factory=list)
    value_changes: list[dict] = Field(default_factory=list)
    emotional_changes: list[dict] = Field(default_factory=list)
    relationship_changes: list[dict] = Field(default_factory=list)
    internal_conflict: str = ""
    turning_points: list[dict] = Field(default_factory=list)
    character_arc_type: str = "unresolved_arc"
    arc_status: str = "open"           # open | closed


class ConflictView(BaseModel):
    conflict_id: str
    participants: list[str] = Field(default_factory=list)
    conflict_type: str = ""            # goal|interest|information|
                                       # relationship|moral|resource
                                       # |internal|survival
    core_issue: str = ""
    opposing_goals: dict = Field(default_factory=dict)
    stakes: str = ""
    severity: float = 0.0
    tension: float = 0.0               # separate from severity
    escalation_history: list[dict] = Field(default_factory=list)
    triggering_events: list[str] = Field(default_factory=list)
    turning_points: list[str] = Field(default_factory=list)
    resolution_conditions: list[str] = Field(default_factory=list)
    status: str = "active"             # escalating|eased|stalled|
                                       # transferred|resolved|active


class ThreadView(BaseModel):
    thread_id: str
    title: str = ""
    central_question: str = ""
    involved_characters: list[str] = Field(default_factory=list)
    related_conflicts: list[str] = Field(default_factory=list)
    related_events: list[str] = Field(default_factory=list)
    opening_event: str = ""
    turning_points: list[str] = Field(default_factory=list)
    climax_event: str | None = None
    resolution_event: str | None = None
    stakes: str = ""
    progress: float = 0.0
    tension: float = 0.0
    status: str = "open"
    unresolved_questions: list[str] = Field(default_factory=list)
    relations: list[dict] = Field(default_factory=list)
    # {other_thread, relation: dependent|intersects|parallel|
    #  conflicts_with|enables}


class SetupPayoffRecord(BaseModel):
    setup_id: str
    kind: str                # question | object | concealed_fact |
                             # bluff
    description: str = ""
    setup_event_ids: list[str] = Field(default_factory=list)
    related_thread_id: str = ""
    expected_payoff: str = ""
    actual_payoff_event_ids: list[str] = Field(default_factory=list)
    status: str = "setup"    # setup | reinforcement | misdirection
                             # | payoff | unresolved
    evidence: list[str] = Field(default_factory=list)


class StoryOutline(BaseModel):
    title: str = ""
    theme: str = ""
    central_question: str = ""
    main_thread: str = ""
    sub_threads: list[str] = Field(default_factory=list)
    main_characters: list[str] = Field(default_factory=list)
    core_conflicts: list[str] = Field(default_factory=list)
    key_turning_points: list[str] = Field(default_factory=list)
    climax_candidates: list[str] = Field(default_factory=list)
    ending_state: str = ""             # as-is; never auto-completed


class ChapterOutline(BaseModel):
    chapter_id: str
    chapter_title: str = ""
    narrative_purpose: str = ""
    opening_state: dict = Field(default_factory=dict)
    ending_state: dict = Field(default_factory=dict)
    included_threads: list[str] = Field(default_factory=list)
    included_events: list[str] = Field(default_factory=list)
    turning_point: str | None = None
    unresolved_questions: list[str] = Field(default_factory=list)
    emotional_curve: list[float] = Field(default_factory=list)


class SceneOutline(BaseModel):
    scene_id: str
    location: str = ""
    time_range: tuple[int, int] = (0, 0)
    participants: list[str] = Field(default_factory=list)
    viewpoint_character: str = ""
    scene_goal: str = ""
    opposition: str = ""
    stakes: str = ""
    conflict: str = ""
    turning_point: str | None = None
    outcome: str = ""
    next_scene_hook: str = ""
    event_ids: list[str] = Field(default_factory=list)


class QualityReport(BaseModel):
    run_id: str
    scores: dict[str, float] = Field(default_factory=dict)
    issues: list[dict] = Field(default_factory=list)
    # {dimension, severity, detail, chapter_id, event_ids}
    evaluator: str = ""                # who/what scored; never the
                                       # writer grading its own draft
