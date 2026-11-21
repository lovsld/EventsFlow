"""Event graph models (task book §11). Nodes are committed events
only; edges are derived relations, each carrying its derivation
rule and the evidence it rests on. Nothing enters the graph that
is not traceable to the world log."""
from __future__ import annotations

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    event_id: str
    tick: int
    type: str
    location: str = ""
    actors: list[str] = Field(default_factory=list)
    proposition: str | None = None
    stance: str | None = None
    # objective | observed | reported | rumored | prior_knowledge
    # | unverified | false_statement
    epistemic: str = "objective"
    scene_id: str = ""


class GraphEdge(BaseModel):
    source: str            # event_id
    target: str            # event_id
    relation: str          # causes|enables|prevents|reveals|conceals
                           # |escalates|resolves|contradicts|depends_on
    rule: str              # derivation rule id
    evidence: dict = Field(default_factory=dict)


class EventGraph(BaseModel):
    run_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def edge_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.edges:
            counts[e.relation] = counts.get(e.relation, 0) + 1
        return counts
