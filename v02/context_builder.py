"""Context Builder — assembles the ONLY input a provider may see.

It reads the agent's own private state and the broker-delivered perception,
nothing else (task book section 6). Available actions come from the
Capability Registry over the run's Scenario Definition (V0.2-B design
3.2): what the environment affords here — never from location or
character names in code, and never from objective object positions.
"""
from __future__ import annotations

from .comm_candidates import applicable_duties, effective_queue
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
    # V0.2-C C1: the Goal Scheduler's decision for this tick (set by
    # CharacterAgent.decide before building the context; absent when
    # a context is built standalone, e.g. in unit tests — providers
    # then fall back to the pre-C priority-first selection).
    sched = getattr(agent, "_schedule_decision", None)
    pursuit_goal = None
    if sched and sched.get("pursuit_goal_id"):
        pursuit_goal = next(
            (g for g in st.goals if g.key == sched["pursuit_goal_id"]),
            None)
    schedule_lines: list[str] = []
    if sched is not None:
        if sched.get("pursuit_goal_id"):
            schedule_lines.append(
                f"本轮调度追求目标：{sched['pursuit_goal_id']}"
                "（selected_goal_id 应填它；计划合成为它服务）")
        paused_keys = [g.key for g in st.goals
                       if g.status == "paused"
                       and g.key != sched.get("pursuit_goal_id")]
        if paused_keys:
            schedule_lines.append(
                "暂停中的目标本轮不可选为 selected_goal_id："
                + "、".join(paused_keys))
    # V0.2-C C2 (design 3.3/3.7): the pending-question queue. The
    # legacy single-slot field carries the queue HEAD so pre-C
    # consumers (the mock provider) keep working; the full queue
    # rides its own extra, and every entry gets a constraint line
    # so LLM providers see the obligation too.
    queue = effective_queue(st)
    head = None
    if queue:
        head = {"from": queue[0].get("asker"),
                "claim": queue[0].get("claim_key"),
                "topic": queue[0].get("claim_key"),
                "tick": queue[0].get("tick_asked"),
                "addressed_to_me": queue[0].get("addressed_to_me", True)}
    comm_lines: list[str] = []
    for entry in queue:
        cdef = agent.scenario.claims.get(entry.get("claim_key") or "")
        topic = (cdef.proposition if cdef is not None
                 else entry.get("claim_key"))
        comm_lines.append(
            f"{entry.get('asker')} 在第 {entry.get('tick_asked')} tick "
            f"向你提问（{topic}），尚待你答复——答复、否认、澄清或拒绝"
            "都是正当回应，沉默则问题将在 3 tick 后过期。")
    try:
        from .conditions import build_view
        _view = build_view(agent, perception["tick"])
    except Exception:
        _view = None
    if _view is not None:
        for item in applicable_duties(
                _view, agent.config, agent.scenario, colocated):
            comm_lines.append(
                f"披露职责：你应当把「{item['duty'].get('proposition')}」"
                f"告知 {item['target']}（紧迫度 "
                f"{item['duty'].get('urgency', 'normal')}）。")
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
        ] + schedule_lines + comm_lines + _inactive_goal_notes(st),
        # V0.2-C C1 extras: the scheduler's full decision (pursuit,
        # eligibility, the utility table — persisted with the
        # decision record's context, design 2.9) and the pursuit
        # goal object itself (it may be PAUSED, hence absent from
        # active_goals, yet still the goal providers must serve).
        schedule=sched,
        scheduled_pursuit_goal_id=(
            sched.get("pursuit_goal_id") if sched else None),
        eligible_goal_ids=(
            sched.get("eligible_goal_ids") if sched else None),
        pursuit_goal=pursuit_goal,
        # runtime extras (DecisionContext allows extras): used by rule/mock
        trust={o: agent.memory.trust_in(o) for o in agent.state.relationships},
        action_counts=dict(st.action_counts),
        pending_question=head if head is not None else st.pending_question,
        pending_questions=queue,
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
