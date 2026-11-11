"""Context Builder — assembles the ONLY input a provider may see.

It reads the agent's own private state and the broker-delivered perception,
nothing else (task book section 6). Available actions are computed from the
agent's location and co-location, so a provider cannot invent impossible
actions; the World Engine still re-validates everything.
"""
from __future__ import annotations

from .protocol import DecisionContext

ADJACENCY = {
    "bar": {"tables"}, "tables": {"bar", "door", "back_room"},
    "door": {"tables", "outside"}, "back_room": {"tables"}, "outside": {"door"},
}


def build_context(agent, perception: dict) -> DecisionContext:
    st = agent.state
    loc = perception["location"]
    colocated = [c["id"] for c in perception.get("colocated", [])]
    available: list[dict] = []
    for dest in sorted(ADJACENCY.get(loc, set())):
        available.append({"type": "move", "target_location": dest})
    for other in colocated:
        for verb in ("speak", "ask", "confront"):
            available.append({"type": verb, "target": other})
    if loc == "back_room":
        available.append({"type": "search", "target_location": "back_room"})
    letter = agent.memory.belief("anonymous_letter_exists")
    if agent.config.character_id == "owner" and letter and letter.kind == "experienced":
        for other in colocated:
            available.append({"type": "show", "object": "anonymous_letter", "target": other})
    available += [{"type": "observe"}, {"type": "listen"}, {"type": "work"},
                  {"type": "refuse"}, {"type": "wait"}]

    perc = dict(perception)
    return DecisionContext(
        character_id=st.character_id,
        tick=perception["tick"],
        current_time=perception["time"],
        location=loc,
        perception=perc,
        active_goals=st.goals,
        current_plan=st.current_plan,
        relevant_memories=st.short_term_memory[-12:],
        beliefs=list(st.beliefs),
        emotional_state=dict(st.emotions),
        available_actions=available,
        constraints=list(st.principles) + [
            "只能从 available_actions 中选择行动",
            "不得提及你没有感知到的事件或他人的内心想法",
        ],
        # runtime extras (DecisionContext allows extras): used by rule/mock
        trust={o: agent.memory.trust_in(o) for o in agent.state.relationships},
        action_counts=dict(st.action_counts),
        pending_question=st.pending_question,
        utterances_heard=list(st.utterances_heard[-20:]),
        # Phase C extras: the plan's current step (design 4.4) and the
        # most recent prediction error, so the next decision is made in
        # light of what the last prediction got wrong (design 4.3).
        plan_step=(agent.plan_manager.current_step_view(st)
                   if getattr(agent, "plan_manager", None) else None),
        last_prediction_error=(
            {"mismatches": agent.last_error.mismatches,
             "surprise": agent.last_error.surprise}
            if getattr(agent, "last_error", None) else None),
    )
