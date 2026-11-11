"""Pydantic v2 protocol layer for ACP V0.2 (task book sections 3 and 6).

Everything that crosses a boundary — perception in, decision out, world
events, character state — is one of these models. LLM output is validated
against AgentDecision; an invalid decision is never executed.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "acp-v0.2.0"

ActionType = Literal["speak", "ask", "confront", "move", "search", "show",
                     "refuse", "observe", "listen", "work", "wait"]
BeliefKind = Literal["experienced", "heard", "inferred"]


class Event(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: str
    tick: int
    type: str
    actors: list[str] = []
    location: Optional[str] = None


class Belief(BaseModel):
    key: str
    confidence: float = Field(ge=0.0, le=1.0)
    kind: BeliefKind
    desc: str = ""


class Goal(BaseModel):
    key: str
    desc: str
    weight: float = 1.0


class PlanState(BaseModel):
    plan_id: str
    goal: str
    steps: list[str] = []
    current_step: int = 0
    commitment: float = 0.8
    status: Literal["active", "completed", "abandoned"] = "active"
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
    emotions: dict[str, float] = {"arousal": 0.3, "valence": 0.0, "suspicion": 0.3}
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
    type: ActionType
    target: Optional[str] = None
    target_location: Optional[str] = None
    object: Optional[str] = None
    content: Optional[str] = None      # utterance text for speak/ask/confront
    claim: Optional[str] = None        # structured claim tag (auditable content)
    topic: Optional[str] = None


class PlanUpdate(BaseModel):
    status: Literal["none", "continue", "advance", "abandon", "replace"] = "none"
    reason: Optional[str] = None
    new_goal: Optional[str] = None
    new_steps: Optional[list[str]] = None


class AgentDecision(BaseModel):
    """The ONLY output a provider may produce (task book section 6)."""
    intent: str
    target: Optional[str] = None
    action: ActionSpec
    reasoning_summary: str = ""
    expected_outcome: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    plan_update: PlanUpdate = PlanUpdate()


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
