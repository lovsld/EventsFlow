"""Context Builder — assembles the ONLY input a provider may see.

It reads the agent's own private state and the broker-delivered perception,
nothing else (task book section 6). Available actions come from the
Capability Registry over the run's Scenario Definition (V0.2-B design
3.2): what the environment affords here — never from location or
character names in code, and never from objective object positions.
"""
from __future__ import annotations

from .protocol import DecisionContext


def action_is_offered(action: dict, available: list[dict]) -> bool:
    """Membership test used by the runtime's intent validation
    (V0.2-A Phase 2): is this action among the ones the context
    offered? Same compatibility semantics as the step-1 audit's M4c
    check — an offered entry matches when the verb is equal and every
    field the entry specifies (target / target_location / object)
    equals the action's."""
    return any(
        o.get("type") == action.get("type")
        and ("target" not in o or o.get("target") == action.get("target"))
        and ("target_location" not in o
             or o.get("target_location") == action.get("target_location"))
        and ("object" not in o or o.get("object") == action.get("object"))
        for o in available)


def build_context(agent, perception: dict, registry=None) -> DecisionContext:
    st = agent.state
    loc = perception["location"]
    colocated = [c["id"] for c in perception.get("colocated", [])]
    registry = registry if registry is not None else agent.registry
    available = registry.available_actions(
        location=loc, colocated=colocated, held_objects=list(st.holdings))

    perc = dict(perception)
    return DecisionContext(
        character_id=st.character_id,
        tick=perception["tick"],
        current_time=perception["time"],
        location=loc,
        perception=perc,
        # Goals are presented priority-first (V0.2-B: priority_current,
        # ties broken by key for determinism). Inactive goals are NOT
        # presented as active (B2 Goal Engine); they are summarised in
        # constraints so a provider knows they exist without pursuing
        # them.
        active_goals=sorted(
            (g for g in st.goals if g.status == "active"),
            key=lambda g: (-g.priority_current, g.key)),
        current_plan=st.current_plan,
        relevant_memories=st.short_term_memory[-12:],
        beliefs=list(st.beliefs),
        emotional_state=dict(st.emotions),
        available_actions=available,
        constraints=list(st.principles) + [
            "只能从 available_actions 中选择行动",
            "不得提及你没有感知到的事件或他人的内心想法",
        ] + _inactive_goal_notes(st),
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
        # V0.2-B extras: the agent's OWN holdings / findings / history
        # (subjective state the generic generator and the predicate
        # evaluator read), and the scenario's PUBLIC structure —
        # place names and the location graph only. Object positions,
        # holders and claim propositions are NOT public structure and
        # deliberately stay out of the serialized context: the context
        # is the LLM prompt, and prompt-level isolation is a pinned
        # invariant (see the V0.2-B report for this threading choice).
        holdings=list(st.holdings),
        found_objects=list(st.found_objects),
        searched_empty_places=list(st.searched_empty_places),
        object_sightings=dict(st.object_sightings),
        perceived_events=list(st.perceived_events[-40:]),
        own_actions=list(st.own_actions[-40:]),
        scenario={"locations": list(agent.scenario.locations),
                  "adjacency": {k: sorted(v) for k, v
                                in agent.scenario.adjacency.items()}},
    )


def _inactive_goal_notes(st) -> list[str]:
    notes = []
    for g in st.goals:
        if g.status == "inactive":
            notes.append(f"未激活目标（尚不追求）：{g.key}（{g.desc}）")
        elif g.status in ("completed", "failed", "abandoned"):
            notes.append(f"目标 {g.key} 已{g.status}，不再追求。")
    return notes
