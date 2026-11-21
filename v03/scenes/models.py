"""Scene models (task book §10.1 + §10.4). A runtime scene is a
(location, tick window, participants) registration over the ONE
shared world — it owns no state of its own; positions and events
remain the World Engine's."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Scene(BaseModel):
    scene_id: str
    location_id: str
    start_tick: int
    end_tick: int | None = None
    participants: list[str] = Field(default_factory=list)
    joined_at: dict[str, int] = Field(default_factory=dict)
    left_at: dict[str, int] = Field(default_factory=dict)
    scene_goal: str = ""
    active_conflicts: list[str] = Field(default_factory=list)
    related_threads: list[str] = Field(default_factory=list)
    event_queue: list[str] = Field(default_factory=list)
    status: str = "active"          # active | closed | interrupted
    outcome: dict | None = None


class Propagation(BaseModel):
    """A fact carried across scenes by a moving character (§10.3)."""
    fact: str
    carrier: str
    from_scene: str
    to_scene: str
    learned_at: int
    spoke_at: int
    travel_ticks: int
    via_event: str


class ConsistencyIssue(BaseModel):
    tick: int
    kind: str
    detail: str
