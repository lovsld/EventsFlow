"""Pydantic v2 protocol layer for ACP V0.2 (task book sections 3 and 6).

Everything that crosses a boundary — perception in, decision out, world
events, character state — is one of these models. LLM output is validated
against AgentDecision; an invalid decision is never executed.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROMPT_VERSION = "acp-v0.2.0"

ActionType = Literal["speak", "ask", "confront", "move", "search", "show",
                     "refuse", "observe", "listen", "work", "wait"]
BeliefKind = Literal["experienced", "heard", "inferred"]

# V0.2-A Phase 3: evidence source types (task book section 5.3). The
# legacy three-value BeliefKind is kept as the derived summary label:
# direct_observation / direct_interaction -> experienced,
# testimony -> heard, inference / assumption -> inferred.
SourceType = Literal["direct_observation", "direct_interaction", "testimony",
                     "inference", "assumption"]
SOURCE_TIER = {"direct_observation": 4, "direct_interaction": 3,
               "testimony": 2, "inference": 1, "assumption": 0}
SOURCE_TO_KIND: dict[str, BeliefKind] = {
    "direct_observation": "experienced", "direct_interaction": "experienced",
    "testimony": "heard", "inference": "inferred", "assumption": "inferred"}
KIND_TO_SOURCE: dict[str, str] = {"experienced": "direct_observation",
                                  "heard": "testimony",
                                  "inferred": "inference"}


class Event(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: str
    tick: int
    type: str
    actors: list[str] = []
    location: Optional[str] = None


class Evidence(BaseModel):
    """One piece of evidence behind a Belief (V0.2-A Phase 3, task book
    section 5.3). A belief's confidence is RECOMPUTED from its evidence
    list (see memory.recompute_confidence); its legacy `kind` label is
    derived from the highest-tier evidence. `source` identifies the
    channel the evidence came through (a speaker's id for testimony,
    an object/document id, or None for the agent's own faculties) —
    evidence from the same channel never stacks as if independent."""
    source_event_id: str
    source_type: SourceType
    reliability: float = Field(ge=0.0, le=1.0)
    observed_at: int = 0                       # tick the evidence arrived
    source: Optional[str] = None


class Belief(BaseModel):
    key: str
    confidence: float = Field(ge=0.0, le=1.0)
    kind: BeliefKind
    desc: str = ""
    evidence: list[Evidence] = []

    @model_validator(mode="after")
    def _seed_legacy_evidence(self) -> "Belief":
        """Compatibility: a belief loaded from a pre-evidence state
        (old state.json / checkpoint, or initial private knowledge)
        carries a single kind label and no evidence list. Migrate it
        to exactly one equivalent evidence entry so the recompute
        machinery applies uniformly; confidence is left as stored."""
        if not self.evidence:
            self.evidence = [Evidence(
                source_event_id=f"seed:{self.key}",
                source_type=KIND_TO_SOURCE[self.kind],
                reliability=self.confidence, observed_at=0)]
        return self


class Goal(BaseModel):
    """A character goal (V0.2-A Phase 3, task book section 5.2).

    `weight` is the configured priority. The remaining fields are the
    runtime lifecycle, driven ONLY by deterministic rules (the Plan
    Manager's events for a plan whose goal label equals this goal's
    key — see plan_manager._sync_goal): providers never edit goals
    directly, and there is no LLM free goal creation/deletion."""
    key: str
    desc: str
    weight: float = 1.0
    status: Literal["active", "paused", "completed", "abandoned"] = "active"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    commitment: float = Field(default=0.8, ge=0.0, le=1.0)
    created_at: int = 0
    deadline: Optional[int] = None


class PlanState(BaseModel):
    plan_id: str
    goal: str
    steps: list[str] = []
    current_step: int = 0
    commitment: float = 0.8
    # V0.2-A Phase 3: "blocked" — the current step was genuinely
    # attempted (a step-consistent action) and failed BLOCK_THRESHOLD
    # times in a row; a blocked plan stops decaying and cannot advance
    # from results — only a deliberate provider advance / replace /
    # abandon moves it again (see plan_manager.note_step_failure).
    status: Literal["active", "blocked", "completed", "abandoned"] = "active"
    step_failures: int = 0
    interruption_conditions: list[str] = ["target_leaves", "new_critical_information",
                                          "immediate_danger"]
    interrupt_log: list[dict] = []
    # Phase C (Plan Manager): per-step action history for the
    # same-action step dedup, and the ticks commitment decay is
    # measured from / last progressed at.
    step_history: list[dict] = []
    last_progress_tick: int = 0
    commitment_updated_tick: int = 0


class CharacterState(BaseModel):
    """Runtime state of ONE agent. Private by construction: each agent owns
    its own instance; nothing here is shared by reference with other agents."""
    character_id: str
    location: str
    personality: dict[str, float]
    principles: list[str]
    goals: list[Goal]
    beliefs: list[Belief] = []
    # Emotion state (V0.2-A Phase 3 alignment, review item 3-5 option
    # b): this system's EmotionState equivalent is exactly
    # {arousal, suspicion} here plus per-character trust in
    # `relationships` — those are the only dimensions with event-driven
    # writers and decision influence. The old "valence" entry was a
    # dead field (never read or written) and is removed; curiosity
    # lives in personality, not emotion. Mapping to the task book's
    # six dimensions is documented in docs/V0.2-A_ARCHITECTURE.md.
    emotions: dict[str, float] = {"arousal": 0.3, "suspicion": 0.3}
    current_plan: Optional[PlanState] = None
    short_term_memory: list[dict] = []
    # Phase C: episodes MemoryManager.consolidate() promoted out of
    # short_term_memory (importance-scored, see memory_manager.py).
    long_term_memory: list[dict] = []
    memory_summary: str = ""
    relationships: dict[str, dict] = {}
    perceived_event_ids: list[str] = []
    utterances_heard: list[dict] = []
    pending_question: Optional[dict] = None
    action_counts: dict[str, int] = {}


class ActionSpec(BaseModel):
    # V0.2-A Phase 2: strict output protocol (task book section 8,
    # validation item 8) — unknown fields are rejected, never silently
    # ignored, on every model a provider's output is validated against.
    model_config = ConfigDict(extra="forbid")
    type: ActionType
    target: Optional[str] = None
    target_location: Optional[str] = None
    object: Optional[str] = None
    content: Optional[str] = None      # utterance text for speak/ask/confront
    claim: Optional[str] = None        # structured claim tag (auditable content)
    topic: Optional[str] = None


class PlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["none", "continue", "advance", "abandon", "replace"] = "none"
    reason: Optional[str] = None
    new_goal: Optional[str] = None
    new_steps: Optional[list[str]] = None


class AgentDecision(BaseModel):
    """The ONLY output a provider may produce (task book section 6)."""
    model_config = ConfigDict(extra="forbid")
    intent: str
    target: Optional[str] = None
    action: ActionSpec
    reasoning_summary: str = ""
    expected_outcome: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    plan_update: PlanUpdate = PlanUpdate()
    # V0.2-A Phase 2 (task book section 8): the goal this decision
    # serves. Optional, but when present the runtime validates that it
    # belongs to THIS agent's goal set — a forged id fails the decision
    # through the same record-and-degrade path as a provider error.
    selected_goal_id: Optional[str] = None


class DecisionContext(BaseModel):
    """The ONLY input a provider may consume (task book section 6)."""
    model_config = ConfigDict(extra="allow")
    character_id: str
    tick: int
    current_time: str
    location: str
    perception: dict[str, Any]         # broker-delivered events/muffled/colocated
    active_goals: list[Goal]
    current_plan: Optional[PlanState] = None
    relevant_memories: list[dict] = []
    beliefs: list[Belief] = []
    emotional_state: dict[str, float] = {}
    available_actions: list[dict] = []
    constraints: list[str] = []


class Prediction(BaseModel):
    """Structured expectation about ONE decision's outcome, generated
    deterministically by the Prediction Engine at decision time (before
    the action is executed — design section 4.3, task-book constraint
    12: never an LLM's free-text reasoning). Every field is checkable
    against the execution result + the next perception."""
    prediction_id: str
    character_id: str
    tick: int                              # the decision's tick
    action_type: str
    target: Optional[str] = None
    target_present: Optional[bool] = None  # speech/show: target in reach
    action_success: bool = True
    refusal_expected: Optional[bool] = None
    new_information_expected: bool = False
    found_expected: Optional[bool] = None  # search only
    basis: dict[str, Any] = {}             # deterministic inputs snapshot


class PredictionError(BaseModel):
    """Structured outcome of comparing a Prediction with reality.
    belief/trust/emotion effects are filled in by
    BeliefManager.apply_prediction_error (the rule-driven adjustment),
    so the stored object is the complete audit record of the surprise
    and what it changed."""
    prediction_id: str
    character_id: str
    decision_tick: int
    evaluated_tick: int
    mismatches: list[str] = []             # field names that missed
    surprise: float = 0.0                  # weighted mismatch magnitude
    details: dict[str, Any] = {}           # field -> {predicted, actual}
    belief_effects: list[dict] = []
    trust_effects: list[dict] = []
    emotion_effects: list[dict] = []


class ProviderResult(BaseModel):
    decision: AgentDecision
    provider: str
    model: Optional[str] = None
    prompt_version: str = PROMPT_VERSION
    input_tokens: int = 0
    output_tokens: int = 0
    raw_output: str = ""
    latency_ms: int = 0
    # V0.2-A Phase 5: how many validation-repair retries this decision
    # cost (LLM providers; 0 for rule/mock). Feeds the experiment
    # metrics' JSON-failure accounting via the runner summary.
    repair_count: int = 0
