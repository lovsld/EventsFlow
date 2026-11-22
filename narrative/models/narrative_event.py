"""NarrativeEvent — the semantic layer over committed world events
(upgrade Phase 1; design: docs/NARRATIVE_UPGRADE_DESIGN.md §1).

Facts and interpretation are kept physically separate: the event
body is a projection of objective fact; every interpretive field
carries its source and confidence. An empty list is a legal state
— not every event has dramatic meaning.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class EvidenceLevel(str, Enum):
    OBJECTIVE = "objective"            # committed world fact
    SUBJECTIVE = "subjective"          # a character's actual knowledge
    INTERPRETATION = "interpretation"  # model/rule reading of meaning
    CREATIVE = "creative"              # novel-writing addition


class DramaticFunction(str, Enum):
    INTRODUCTION = "introduction"
    SETUP = "setup"
    REVELATION = "revelation"
    DISCOVERY = "discovery"
    CONFLICT = "conflict"
    CONFRONTATION = "confrontation"
    ESCALATION = "escalation"
    REVERSAL = "reversal"
    DECISION = "decision"
    SACRIFICE = "sacrifice"
    BETRAYAL = "betrayal"
    RESOLUTION = "resolution"
    AFTERMATH = "aftermath"
    TRANSITION = "transition"
    FORESHADOWING = "foreshadowing"
    PAYOFF = "payoff"


class RelationType(str, Enum):
    """Unified causal vocabulary (upgrade Phase 2)."""
    CAUSED_BY = "CAUSED_BY"
    ENABLED_BY = "ENABLED_BY"
    MOTIVATED_BY = "MOTIVATED_BY"
    RESPONDED_TO = "RESPONDED_TO"
    REVEALED_BY = "REVEALED_BY"
    BLOCKED_BY = "BLOCKED_BY"
    ESCALATED_BY = "ESCALATED_BY"
    RESOLVED_BY = "RESOLVED_BY"
    CONTRADICTS = "CONTRADICTS"
    FORESHADOWS = "FORESHADOWS"
    PAYOFF_OF = "PAYOFF_OF"


class KnowledgeChange(BaseModel):
    character: str
    fact: str
    change: str          # learned | reinforced | contradicted |
                         # false_belief_formed
    via: str             # delivery | find | experienced | initial
    source_event_id: str
    evidence_level: EvidenceLevel = EvidenceLevel.SUBJECTIVE


class CharacterChange(BaseModel):
    character: str
    dimension: str       # belief | emotion | goal | value | behaviour
    description: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.INTERPRETATION
    confidence: float = 1.0


class RelationshipChange(BaseModel):
    pair: tuple[str, str]
    dimension: str       # trust | stance | alliance
    direction: str       # strengthened | weakened | formed | broken
    evidence_event_ids: list[str] = Field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.INTERPRETATION
    confidence: float = 1.0


class ConflictChange(BaseModel):
    conflict_ref: str
    change: str          # raised | escalated | eased | transferred |
                         # resolved
    evidence_event_ids: list[str] = Field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.INTERPRETATION
    confidence: float = 1.0


class CausalLink(BaseModel):
    """Edge in the unified vocabulary, stored on the TARGET event:
    this event stands in `relation` to `target_ne_id` (the other
    end), e.g. relation=CAUSED_BY points at the causing event."""
    relation: RelationType
    target_ne_id: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    source: str = "rule"               # rule | llm | v03 | extractor
    verification_status: str = "verified"  # verified | unverified |
                                           # rejected


class NarrativeEvent(BaseModel):
    narrative_event_id: str
    source_event_ids: list[str]
    tick: int
    event_summary: str
    summary_source: str = "source_text"  # source_text | rule | llm
    participants: list[str] = Field(default_factory=list)
    location: str = ""
    event_type: str = ""
    dramatic_function: list[DramaticFunction] = Field(
        default_factory=list)
    dramatic_function_source: str = "rule"  # rule | llm
    narrative_importance: float = 0.0
    knowledge_changes: list[KnowledgeChange] = Field(
        default_factory=list)
    character_changes: list[CharacterChange] = Field(
        default_factory=list)
    relationship_changes: list[RelationshipChange] = Field(
        default_factory=list)
    conflict_changes: list[ConflictChange] = Field(
        default_factory=list)
    causal_links: list[CausalLink] = Field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.OBJECTIVE
    interpretation_confidence: float | None = None
