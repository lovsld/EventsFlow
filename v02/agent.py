"""Character Agent Runtime (task book sections 3 and 4).

One instance per character, holding that character's private CharacterState
and AgentMemory. The perception -> interpretation -> belief/emotion update
path is the V0.1 brain's receive()/_interpret(), ported: the SAME utterance
is understood differently by different personalities, and nothing here ever
touches another agent's state or the objective world.

Cognitive loop (Phase C completed the design section 4.3 cycle):
  Perception        -> receive(perception, action_result)
  Prediction error  -> receive() first evaluates the PREVIOUS decision's
                       Prediction against its action result
                       (PredictionEngine.compare), lets BeliefManager
                       apply the error to beliefs/trust, and lets
                       MemoryManager consolidate episodes by importance
  Subjective model  -> state.beliefs / emotions / relationships
  Decision          -> decide(): PlanManager.reconcile (commitment
                       decay), ContextBuilder (injects the plan's
                       current step), provider.generate_decision,
                       PlanManager.apply_update (reason-gated), and a
                       fresh structured Prediction for the new action
  Plan handling     -> PlanManager (plan_manager.py); plus the
                       result-driven Plan Engine: a step is completed
                       by its declared success_condition holding after
                       its action's execution RESULT (receive-time
                       StepEvaluator, v02/plan_evaluator.py), not by
                       provider declaration
  Memory            -> AgentMemory (episodic entries + belief merges)

Everything the loop produces for the audit store (prediction errors,
belief changes, plan transitions, consolidations) is buffered as
"chain updates" and drained by the scheduler; the agent itself never
touches the database.
"""
from __future__ import annotations

from pathlib import Path

from . import conditions, plan_evaluator
from .belief_manager import BeliefManager
from .config import CharacterConfig
from .context_builder import action_is_offered, build_context
from .memory import AgentMemory
from .memory_manager import MemoryManager
from .plan_manager import COMMITMENT_GATE, PlanManager
from .prediction import PredictionEngine
from .protocol import AgentDecision
from .providers.base import LLMProvider, ProviderError, QuotaExhaustedError
from .scenario import CapabilityRegistry, default_scenario


class CharacterAgent:
    def __init__(self, config: CharacterConfig, provider: LLMProvider,
                 run_char_dir: Path | None = None, resume_dir: bool = False,
                 scenario=None):
        self.config = config
        self.provider = provider
        self.state = config.initial_state()
        # V0.2-B threading: the run's Scenario Definition instance and
        # its Capability Registry live on the agent; perception
        # interpretation, context building and prediction all read
        # THIS instance (design section 3.7).
        self.scenario = scenario if scenario is not None else default_scenario()
        self.registry = CapabilityRegistry(self.scenario)
        # Holdings (design 3.2, one documented deviation): the
        # design's init rule also counted "objects whose scenario
        # holder is this agent" — but a scenario's registered holder
        # is the custodian OF RECORD, and a scenario may precisely be
        # about that object being missing (the warehouse key is
        # registered to the keeper and objectively gone; counting it
        # as held would satisfy his possession goal before he ever
        # searches). Subjective holdings therefore start from the
        # profile's physical possessions ∩ scenario objects, and are
        # maintained afterwards from own action results (acquire)
        # and perceptions. The tavern is unaffected: the owner's
        # profile lists the letter among his possessions.
        held = [o for o in config.possessions if o in self.scenario.objects]
        self.state.holdings = held
        # The agent's last executed action (set at decide()), consumed
        # by receive() into the subjective own_actions history.
        self._last_action: dict | None = None
        self.memory = AgentMemory(self.state, run_char_dir, resume=resume_dir)
        self.last_context = None
        self.decision_records: list[dict] = []
        # Phase C cognitive components
        self.plan_manager = PlanManager()
        self.belief_manager = BeliefManager()
        self.prediction_engine = PredictionEngine()
        self.memory_consolidator = MemoryManager()
        self.pending_prediction = None     # Prediction awaiting its result
        self.last_error = None             # most recent PredictionError
        # V0.2-B B2: the Goal Engine — the only writer of goal state.
        from .goal_engine import GoalEvaluator
        self.goal_evaluator = GoalEvaluator()
        self._goal_changes: list[dict] = []
        # Plan-layer upgrade: the plan step the pending decision was
        # chosen for, awaiting its execution result (see decide()).
        self._pending_step_check: dict | None = None
        self._chain_updates: list[dict] = []
        self._cons_from = 0                # consolidation cursor into STM

    # ---------------- plan helpers (PlanManager-backed) ----------------
    def _set_plan(self, goal: str, steps: list[str],
                  critical: bool = False) -> None:
        """Interpretation-driven plan setting, with the V0.2-A
        commitment gate: a plan at or above COMMITMENT_GATE commitment
        is NOT displaced by ordinary (non-critical) information — the
        attempted replacement is recorded and the plan stands. Only
        critical information (first-hand discovery, or an interlocutor
        directly revealing something about the agent's own situation)
        or a same-goal refresh replaces a high-commitment plan."""
        plan = self.state.current_plan
        if plan is not None and plan.status in ("active", "blocked") \
                and plan.goal != goal \
                and plan.commitment >= COMMITMENT_GATE and not critical:
            self.plan_manager.note_blocked_replacement(
                self.state, getattr(self, "_tick", 0), goal)
            return
        self.plan_manager.set_plan(self.state, goal, steps,
                                   tick=getattr(self, "_tick", 0),
                                   reason="interpretation")

    def _apply_plan_update(self, decision: AgentDecision) -> str:
        """Reason-gated plan changes: abandon/replace without a reason
        are rejected and logged (Phase A semantics, PlanManager-enforced).

        V0.2-B B3 (design 5.5): a BLOCKED plan is handled here first,
        before the generic update path — (a) an explicit
        plan_update.recovery from the provider preempts everything;
        (b) a replace carrying fresh steps IS the replan material;
        (c) otherwise the Plan Manager's scheduled policy default
        executes (with this decision's steps as replan material when
        the default is replan). The blocked branch consumes the
        whole plan_update for this decision."""
        plan = self.state.current_plan
        pu = decision.plan_update
        if plan is not None and plan.status == "blocked":
            pm = self.plan_manager
            if pu.recovery is not None:
                return pm.execute_recovery(
                    self.state, self._tick, pu.recovery.strategy,
                    pu.recovery.reason, source="provider",
                    new_steps=list(pu.new_steps or []) or None,
                    new_goal_label=pu.new_goal, goal_id=pu.goal_id,
                    reactivate_when=pu.recovery.reactivate_when)
            if pu.status == "replace" and pu.new_steps:
                return pm.execute_recovery(
                    self.state, self._tick, "replan", pu.reason,
                    source="provider", new_steps=list(pu.new_steps or []),
                    new_goal_label=pu.new_goal, goal_id=pu.goal_id)
            if plan.pending_recovery:
                spec = plan.pending_recovery
                return pm.execute_recovery(
                    self.state, self._tick, spec.get("strategy", "retry"),
                    "policy default recovery", source="policy_default",
                    new_steps=list(pu.new_steps or []) or None,
                    new_goal_label=pu.new_goal, goal_id=pu.goal_id)
            return "blocked_no_recovery_available"
        return self.plan_manager.apply_update(
            self.state, decision.plan_update, self._sig(decision), self._tick)

    # ---------------- Phase C chain-update plumbing ----------------
    def _drain_plan_transitions(self) -> list[dict]:
        trs, self.plan_manager.transitions = self.plan_manager.transitions, []
        return trs

    # ---------------- V0.2-B B2: goal evaluation ----------------
    def evaluate_goals(self, tick: int, cause: str) -> list[dict]:
        """Run the GoalEvaluator over my goals and buffer the change
        records for the audit chain. Called by the runner at init, by
        receive() at its end (cause='perception'), and by decide()
        after a plan update (cause='plan_event') — the pinned
        evaluation points of design section 4.2."""
        changes = self.goal_evaluator.evaluate(self, tick, cause)
        self._goal_changes.extend(changes)
        return changes

    def _drain_goal_changes(self) -> list[dict]:
        out, self._goal_changes = self._goal_changes, []
        return out

    def drain_chain_updates(self) -> list[dict]:
        ups, self._chain_updates = self._chain_updates, []
        return ups

    def _evaluate_pending(self, perception: dict,
                          action_result: dict | None) -> None:
        """on_action_result (design 4.3): compare the previous decision's
        prediction with reality, apply the error, consolidate memory.
        Runs BEFORE the new perception is interpreted, and the records
        it emits are keyed to the evaluated decision, not the new one."""
        pred = self.pending_prediction
        if pred is None or action_result is None:
            return
        self.pending_prediction = None
        error = self.prediction_engine.compare(pred, action_result, perception)
        self.belief_manager.apply_prediction_error(self, pred, error)
        self.last_error = error
        tick = perception["tick"]
        dkey = f"{self.config.character_id}:{pred.tick}"
        self._chain_updates.append(
            {"kind": "prediction_error", "prediction_id": pred.prediction_id,
             "decision_key": dkey, "tick": tick, "error": error.model_dump()})
        for eff in error.belief_effects:
            self._chain_updates.append(
                {"kind": "belief_change", "tick": tick,
                 "character_id": self.config.character_id, "decision_key": dkey,
                 "prediction_id": pred.prediction_id, "key": eff["key"],
                 "change_kind": eff["kind"], "before": eff["before"],
                 "after": eff["after"], "cause": eff["cause"]})
        for eff in error.trust_effects:
            self._chain_updates.append(
                {"kind": "belief_change", "tick": tick,
                 "character_id": self.config.character_id, "decision_key": dkey,
                 "prediction_id": pred.prediction_id,
                 "key": f"trust:{eff['other']}", "change_kind": "trust",
                 "before": eff["before"], "after": eff["after"],
                 "cause": eff["cause"]})
        cons = self.memory_consolidator.consolidate(self, pred, error, tick)
        self._chain_updates.append({"kind": "consolidation", **cons})
        # V0.2-B B3: step evaluation is NOT here anymore — design
        # 5.2 pins it after the action-result facts are ingested
        # (receive step 3), because the step's conditions judge the
        # post-result state (holdings, found objects), which the
        # prediction step precedes.

    def _evaluate_step_from_result(self, perception: dict,
                                   action_result: dict | None,
                                   tick: int) -> None:
        """Design 5.2 step 3: the pending step meets its result.

        The StepEvaluator judges the stashed step's success/failure
        conditions over the agent's CURRENT subjective view (the
        result facts — found objects, holdings — are already
        ingested). Before judging, the environment-reset rule
        (5.4): a newly perceived event intersecting the step
        condition's reference set zeroes the failure counter."""
        chk = self._pending_step_check
        self._pending_step_check = None
        if chk is None or action_result is None:
            return
        plan = self.state.current_plan
        if plan is None or plan.plan_id != chk["plan_id"]:
            return
        dkey = f"{self.config.character_id}:{chk['tick']}"
        if plan.status == "active" \
                and plan.current_step == chk["step_index"] \
                and plan.current_step < len(plan.steps):
            step = plan.steps[plan.current_step]
            if step.success_condition is not None:
                refs = conditions.references(step.success_condition)
                hit = self._events_intersect(perception, refs)
                if hit:
                    self.plan_manager.\
                        reset_attempts_on_environment_change(
                            self.state, tick, hit)
            view = conditions.build_view(self, tick)
            evaluation = plan_evaluator.evaluate_step(
                step, action=chk["action"],
                action_result=action_result, view=view)
            self.plan_manager.apply_step_evaluation(
                self.state, tick, step.step_id, evaluation,
                plan_evaluator.action_signature(chk["action"]))
        for tr in self._drain_plan_transitions():
            self._chain_updates.append(
                {**tr, "kind": "plan_transition",
                 "transition_kind": tr.get("kind"),
                 "decision_key": dkey})

    def _events_intersect(self, perception: dict,
                          refs: set) -> str | None:
        """Does any event in this perception touch the reference set
        of a step condition (conditions.references: a set of
        (kind, name) pairs)? Returns a short description or None."""
        objects = {n for k, n in refs if k == "object"}
        locations = {n for k, n in refs if k == "location"}
        agents = {n for k, n in refs if k == "agent"}
        beliefs = {n for k, n in refs if k == "belief"}
        claims = {n for k, n in refs if k == "claim"}
        for ev in perception.get("events", []):
            obj = ev.get("object")
            if obj and obj in objects:
                return f"object {obj}"
            loc = ev.get("location") or ev.get("to")
            if loc and loc in locations:
                return f"location {loc}"
            for actor in ev.get("actors") or []:
                if actor in agents:
                    return f"character {actor}"
            claim = ev.get("claim")
            if claim:
                if claim in claims:
                    return f"claim {claim}"
                cdef = self.scenario.claims.get(claim)
                if cdef is not None and cdef.proposition in beliefs:
                    return f"proposition {cdef.proposition}"
            for found in ev.get("found") or []:
                if found in objects:
                    return f"object {found}"
        return None

    # ---------------- perception -> subjective updates ----------------
    def receive(self, perception: dict, action_result: dict | None) -> None:
        st = self.state
        tick = perception["tick"]
        self._tick = tick
        # V0.2-B (design 5.2 step 2 begins here): record my own last
        # action and its search outcome into the subjective history
        # the predicate evaluator reads — before anything else, so
        # later steps this tick see a complete view.
        self._ingest_own_action(action_result)
        # Phase C: the previous decision's prediction meets its result
        # before anything new is interpreted.
        self._evaluate_pending(perception, action_result)
        st.location = perception["location"]
        # V0.2-A (belief-history completion): snapshot beliefs/trust
        # AFTER the prediction-error path has run, so the diff below
        # captures exactly the interpretation path's changes — the
        # belief_changes table then covers every belief change, not
        # only prediction-error ones (review item P-2).
        beliefs_before = {b.key: (b.confidence, b.kind) for b in st.beliefs}
        trust_before = {o: rel.get("trust", 0.3)
                        for o, rel in st.relationships.items()}

        # V0.2-B: action-result facts, ingested generically (design
        # 3.5 / 5.2 step 2). Finding ANY object applies the effects
        # the SCENARIO declares for it — its fact_belief as direct
        # observation, its reveals as testimony sourced to the object
        # itself, acquisition for on_found="acquire" objects. The
        # interpretation layer creates NO plans here (or anywhere).
        self._ingest_found(action_result, tick)

        # V0.2-B B3 (design 5.2 steps 3-4): the pending plan step is
        # evaluated against its conditions NOW — after the result
        # facts exist, before new utterances are interpreted — and
        # the GoalEvaluator runs immediately after, so a step
        # fulfilment and the goal state it implies land in the same
        # receive, in that order.
        self._evaluate_step_from_result(perception, action_result, tick)
        self.evaluate_goals(tick, cause="perception")

        for ev in perception["events"]:
            self._perceive_event(ev, tick)
        for mf in perception.get("muffled", []):
            st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.05)
            self.memory.remember(tick, "inferred",
                                 f"隔墙隐约听到 {','.join(mf['actors'])} 在{mf['location']}说话，听不清内容。",
                                 mf["event_id"])
        # plans created/replaced by interpretation are audit transitions
        for tr in self._drain_plan_transitions():
            # the envelope kind must stay "plan_transition" for the
            # scheduler's chain-update dispatch; the transition's own
            # kind ("set"/"replace"/...) travels as transition_kind —
            # spreading tr after "kind" let tr's kind overwrite the
            # envelope and the records were silently dropped (step-1
            # audit finding 1)
            self._chain_updates.append(
                {**tr, "kind": "plan_transition",
                 "transition_kind": tr.get("kind"), "decision_key": None})
        # V0.2-B B2 (design 4.2): the GoalEvaluator runs at the end
        # of receive() — perception has been ingested and interpreted,
        # so conditions judge the agent's freshest subjective state.
        self.evaluate_goals(tick, cause="perception")
        for gc in self._drain_goal_changes():
            self._chain_updates.append(
                {**gc, "kind": "goal_change",
                 "change_kind": gc.get("kind"), "decision_key": None})
        # interpretation-path belief/trust changes join the audit table
        for b in st.beliefs:
            old = beliefs_before.get(b.key)
            if old is None or old != (b.confidence, b.kind):
                self._chain_updates.append(
                    {"kind": "belief_change", "tick": tick,
                     "character_id": self.config.character_id,
                     "decision_key": None, "prediction_id": None,
                     "key": b.key, "change_kind": b.kind,
                     "before": old[0] if old else None,
                     "after": b.confidence, "cause": "interpretation"})
        for other, rel in st.relationships.items():
            old_t = trust_before.get(other, 0.3)
            if rel.get("trust", 0.3) != old_t:
                self._chain_updates.append(
                    {"kind": "belief_change", "tick": tick,
                     "character_id": self.config.character_id,
                     "decision_key": None, "prediction_id": None,
                     "key": f"trust:{other}", "change_kind": "trust",
                     "before": old_t, "after": rel.get("trust", 0.3),
                     "cause": "interpretation"})
        self.memory.save_state()

    def _ingest_own_action(self, action_result: dict | None) -> None:
        """The subjective record of my own last action: appended to
        own_actions (event_occurred predicates with actor=self read
        it), and a search that came up empty marks the place in
        searched_empty_places (the prediction engine's found
        expectation and belief-tier logic read it)."""
        st = self.state
        act = self._last_action
        self._last_action = None
        if act is None or action_result is None:
            return
        rec = {"tick": act["tick"], "type": act["type"],
               "target": act.get("target"),
               "target_location": act.get("target_location"),
               "object": act.get("object"), "claim": act.get("claim"),
               "topic": act.get("topic"),
               "ok": bool(action_result.get("ok"))}
        st.own_actions.append(rec)
        if act["type"] == "search" and action_result.get("ok"):
            place = act.get("target_location") or st.location
            if action_result.get("found"):
                if place in st.searched_empty_places:
                    st.searched_empty_places.remove(place)
            elif place not in st.searched_empty_places:
                st.searched_empty_places.append(place)

    def _ingest_found(self, action_result: dict | None, tick: int) -> None:
        st = self.state
        for obj in (action_result or {}).get("found") or []:
            decl = self.scenario.objects.get(obj)
            st.object_sightings[obj] = st.location
            if obj not in st.found_objects:
                st.found_objects.append(obj)
            self.memory.remember(tick, "experienced",
                                 f"我在{st.location}找到了 {obj}。", None)
            if decl is None:
                continue
            evid = f"search_result:{obj}@{tick}"
            if decl.fact_belief:
                self.memory.update_belief(
                    decl.fact_belief, 1.0, "experienced",
                    f"我亲手找到了 {obj}，它确实存在",
                    source_type="direct_observation", event_id=evid,
                    tick=tick, source=obj)
            for rev in decl.reveals:
                self.memory.update_belief(
                    rev.belief, rev.reliability, "heard",
                    f"{obj} 的内容表明：{rev.belief}",
                    source_type="testimony", event_id=evid,
                    tick=tick, source=obj)
            if decl.reveals:
                # content that accuses someone is inherently
                # suspicion-raising (design 3.5, replaces the old
                # letter-specific +0.25 with the generic +0.15)
                st.emotions["suspicion"] = min(
                    1.0, st.emotions["suspicion"] + 0.15)
            if decl.on_found == "acquire" and obj not in st.holdings:
                st.holdings.append(obj)

    def _perceive_event(self, ev: dict, tick: int) -> None:
        st = self.state
        actor = ev["actors"][0] if ev.get("actors") else None
        etype = ev["type"]
        if actor == self.config.character_id:
            return
        # V0.2-B: the structured perception history the predicate
        # evaluator reads (event_occurred / agent_at_location /
        # object sightings) — recorded for every perceived event,
        # before interpretation.
        st.perceived_events.append({
            "tick": tick, "type": etype, "actor": actor,
            "target": (ev["actors"][1] if len(ev.get("actors") or []) > 1
                       else ev.get("target")),
            "claim": ev.get("claim"), "topic": ev.get("topic"),
            "object": ev.get("object"), "location": ev.get("location"),
            "to": ev.get("to"), "found": list(ev.get("found") or [])})
        if etype == "move":
            self.memory.remember(tick, "experienced", f"{actor} 来到了/去到了 {ev.get('to')}。", ev["event_id"])
        elif etype in ("speak", "ask", "confront"):
            text = ev.get("text") or f"（{actor} 谈论 {ev.get('topic')}）"
            self.memory.remember(tick, "experienced", f"{actor} 说：{text}", ev["event_id"])
            st.utterances_heard.append({"tick": tick, "from": actor, "topic": ev.get("topic"),
                                        "claim": ev.get("claim"), "kind": etype})
            if etype in ("ask", "confront"):
                st.pending_question = {
                    "from": actor, "topic": ev.get("topic"),
                    "claim": ev.get("claim"), "tick": tick,
                    # V0.2-B: was the question addressed to ME? (the
                    # event's target) — bystanders overhear a question
                    # without acquiring the obligation to answer it
                    "addressed_to_me": (
                        (ev["actors"][1] if len(ev.get("actors") or []) > 1
                         else ev.get("target")) == self.config.character_id)}
            self._interpret_speech(ev)
        elif etype == "show":
            obj = ev.get("object")
            decl = self.scenario.objects.get(obj) if obj else None
            self.memory.remember(tick, "experienced", f"{actor} 把 {obj} 拿给我看了。", ev["event_id"])
            if obj:
                st.object_sightings[obj] = st.location
                self.memory.update_belief(
                    f"holds:{actor}:{obj}", 1.0, "experienced",
                    f"我亲眼看见 {actor} 持有 {obj}",
                    source_type="direct_interaction", event_id=ev["event_id"],
                    tick=tick, source=actor)
            if decl is not None and decl.fact_belief:
                self.memory.update_belief(
                    decl.fact_belief, 1.0, "experienced", f"我亲眼见到了 {obj}",
                    source_type="direct_interaction", event_id=ev["event_id"],
                    tick=tick, source=actor)
                for rev in decl.reveals:
                    self.memory.update_belief(
                        rev.belief, rev.reliability, "heard",
                        f"{obj} 的内容表明：{rev.belief}",
                        source_type="testimony", event_id=ev["event_id"],
                        tick=tick, source=obj)
            self.memory.adjust_trust(actor, +0.1)
        elif etype == "search":
            self.memory.remember(tick, "experienced", f"{actor} 在 {ev.get('location')} 翻找着什么。", ev["event_id"])
            st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.1)
            for obj in ev.get("found") or []:
                st.object_sightings[obj] = ev.get("location")
                self.memory.update_belief(
                    f"holds:{actor}:{obj}", 1.0, "experienced",
                    f"我看见 {actor} 在 {ev.get('location')} 找到了 {obj}",
                    source_type="direct_observation", event_id=ev["event_id"],
                    tick=tick, source=actor)
        elif etype == "refuse":
            self.memory.remember(tick, "experienced", f"{actor} 拒绝继续谈 {ev.get('topic')}。", ev["event_id"])
            # design 3.5 channel 2: having MY OWN utterance refused
            # costs the refuser trust; overhearing someone else's
            # refusal does not. The Resolver always sets the refuse
            # event's target in real runs; a target-less event (older
            # fixtures) is treated as directed at the perceiver.
            if ev.get("target") in (None, self.config.character_id):
                self.memory.adjust_trust(actor, -0.05)
        elif etype == "ambient":
            self.memory.remember(tick, "experienced", f"环境：{ev.get('text')}", ev["event_id"])

    # ---------------- utterance interpretation (V0.2-B) ----------------
    # The old _interpret() — a hardcoded claim x character table that
    # also CREATED PLANS — is replaced by three GENERIC channels
    # (design section 3.5). Nothing here branches on a character id
    # or a claim literal; everything scenario-specific is the claim
    # DECLARATION in the Scenario Definition. The interpretation
    # layer never creates plans: plans come from providers and the
    # plan synthesizer only.
    def _interpret_speech(self, ev: dict) -> None:
        st = self.state
        claim = ev.get("claim")
        speaker = ev["actors"][0]
        tick = self._tick
        P = self.config.personality
        cdef = self.scenario.claims.get(claim) if claim else None
        if cdef is None:
            return  # undeclared claim: episodic memory only

        # Channel 1 — proposition evidence: an assertion gives the
        # hearer TESTIMONY evidence for the claim's declared
        # proposition, discounted by how little the hearer trusts
        # the speaker.
        if cdef.stance == "assert" and cdef.proposition:
            trust = self.memory.trust_in(speaker)
            reliability = round(
                cdef.base_reliability * (0.4 + 0.6 * trust), 4)
            self.memory.update_belief(
                cdef.proposition, reliability, "heard",
                f"{speaker} 声称：{cdef.proposition}",
                event_id=ev["event_id"], tick=tick, source=speaker)

        # Channel 2 — speech-act effects: being asked or confronted
        # is arousing in proportion to one's own caution, and a
        # question about a SENSITIVE claim class is suspicious; both
        # decay with repetition from the same speaker on the same
        # claim (the utterance was appended to utterances_heard just
        # before this call, so the first hearing counts n=1).
        if ev["type"] in ("ask", "confront"):
            n = sum(1 for u in st.utterances_heard
                    if u["from"] == speaker and u["claim"] == claim)
            decay = 1.0 / max(1, n)
            st.emotions["arousal"] = min(
                1.0, st.emotions["arousal"]
                + 0.25 * P.get("caution", 0.5) * decay)
            if cdef.sensitive:
                st.emotions["suspicion"] = min(
                    1.0, st.emotions["suspicion"] + 0.2 * decay)

        # Channel 3 — contradiction inference: a denial of a
        # proposition the hearer already holds with confidence >= 0.5
        # is evidence the denier conceals something (conceals:<who>),
        # with reliability growing in the contradicted belief's
        # strength. The denied proposition is recovered from the
        # hearer's own perception history: what was this speaker last
        # ASKED about.
        if cdef.stance == "deny":
            prop = self._denied_proposition(speaker)
            if prop:
                held = self.memory.belief(prop)
                if held is not None and held.confidence >= 0.5:
                    reliability = max(0.5, min(
                        0.8, 0.5 + 0.3 * (held.confidence - 0.5) * 2))
                    self.memory.update_belief(
                        f"conceals:{speaker}", round(reliability, 4),
                        "inferred",
                        f"{speaker} 否认了 {prop}，与其我已知的线索矛盾",
                        event_id=ev["event_id"], tick=tick)
                    st.emotions["suspicion"] = min(
                        1.0, st.emotions["suspicion"] + 0.15)

    def _denied_proposition(self, speaker: str) -> str | None:
        """The proposition of the most recent perceived utterance
        ADDRESSED TO `speaker` whose claim class declares one — i.e.
        what the denial is about, from my own perception history."""
        for entry in reversed(self.state.perceived_events):
            if entry.get("target") != speaker:
                continue
            cdef = self.scenario.claims.get(entry.get("claim") or "")
            if cdef is not None and cdef.proposition:
                return cdef.proposition
        return None

    # ---------------- decision ----------------
    def _fail_decision(self, record: dict, error: str,
                       quota: bool = False) -> tuple[None, dict]:
        """The single failure exit of decide(): the error is recorded
        on the decision's audit record, no prediction is made, and the
        scheduler substitutes a 'wait' intent — a failed or invalid
        decision never reaches the world."""
        record["error"] = error
        if quota:
            record["quota_exhausted"] = True
        self._pending_step_check = None
        record["plan_transitions"] = self._drain_plan_transitions()
        record["goal_changes"] = self._drain_goal_changes()
        self.decision_records.append(record)
        return None, record

    @staticmethod
    def _sig(decision: AgentDecision) -> str:
        a = decision.action
        return (f"{a.type}:{a.target or a.target_location or ''}"
                f":{a.topic or a.object or ''}")

    async def decide(self, perception: dict) -> tuple[AgentDecision | None, dict]:
        """Returns (decision_or_None, audit_record). On provider failure the
        decision is None and the runner substitutes a 'wait' intent; the
        world is never touched by a failed call."""
        self._tick = perception["tick"]
        # Phase C: per-tick plan housekeeping (commitment decay) runs
        # before the context is built, so the context shows the decayed
        # commitment and the current step.
        self.plan_manager.reconcile(self.state, self._tick)
        context = build_context(self, perception)
        self.last_context = context
        record = {
            "character_id": self.config.character_id, "tick": perception["tick"],
            "decision_key": f"{self.config.character_id}:{perception['tick']}",
            "context": context.model_dump(mode="json"),
            "provider": self.provider.name, "model": self.provider.model,
            "prompt_version": "acp-v0.2.0",
            "context_summary": (f"loc={context.location} "
                                f"colocated={[c['id'] for c in perception.get('colocated', [])]} "
                                f"events={len(perception.get('events', []))} "
                                f"beliefs={len(context.beliefs)} plan="
                                f"{context.current_plan.goal if context.current_plan else None}"),
            "input_tokens": 0, "output_tokens": 0, "raw_output": "",
            "parsed_output": None, "reasoning_summary": "", "execution_result": None,
            "latency_ms": 0, "error": None,
        }
        try:
            result = await self.provider.generate_decision(context)
        except ProviderError as e:
            # Structured quota flag (from the exception TYPE, never the
            # message text): the scheduler's --loop mode retries this
            # same tick after a wait when this is set, instead of letting
            # the degraded wait stand as the tick's decision.
            return self._fail_decision(
                record, str(e), quota=isinstance(e, QuotaExhaustedError))
        decision = result.decision
        # ---- V0.2-A Phase 2: runtime intent validation ----
        # selected_goal_id ownership (task book section 8 item 5): a
        # decision naming a goal the agent does not have is invalid,
        # whoever produced it. Checked whenever the field is present.
        if decision.selected_goal_id is not None \
                and decision.selected_goal_id not in \
                {g.key for g in self.state.goals}:
            return self._fail_decision(
                record, f"validation: selected_goal_id "
                        f"{decision.selected_goal_id!r} is not one of "
                        f"this agent's goals")
        # available_actions membership for LLM decisions (the step-1
        # audit's M4c check, moved from after-the-fact to runtime):
        # an LLM may only choose among the actions its context
        # offered. Rule/mock providers are exempt — their candidates
        # are generated from the world rules directly, and their
        # behaviour must stay bit-identical.
        if getattr(self.provider, "validates_membership", False) \
                and not action_is_offered(decision.action.model_dump(),
                                          context.available_actions):
            return self._fail_decision(
                record, f"validation: action {self._sig(decision)} is "
                        f"not in this tick's available_actions")
        # consume pending question exactly as V0.1 did (it is answered or dropped now)
        self.state.pending_question = None
        plan_note = self._apply_plan_update(decision)
        # V0.2-B B2 (design 4.2): after a plan update, goals are
        # re-evaluated (a plan switch can pause the displaced goal;
        # a fresh link can reactivate one)
        self.evaluate_goals(self._tick, cause="plan_event")
        sig = self._sig(decision)
        self.state.action_counts[sig] = self.state.action_counts.get(sig, 0) + 1
        # V0.2-B: stash the executed action for receive()'s subjective
        # own_actions history (design 3.3's event_occurred actor=self).
        self._last_action = {**decision.action.model_dump(),
                             "tick": perception["tick"]}
        # Phase C: the structured prediction for THIS action, generated
        # now — before execution — and evaluated when its result returns.
        prediction = self.prediction_engine.predict(self, context, decision)
        self.pending_prediction = prediction
        # V0.2-B B3: remember which plan step this action was chosen
        # for (the step view the provider actually saw), so receive()
        # can judge the step by its declared CONDITIONS against the
        # action's RESULT — a fact about the agent's world — instead
        # of by a provider opinion or a label table.
        ps = getattr(context, "plan_step", None)
        if ps and ps.get("step"):
            self._pending_step_check = {
                "plan_id": ps["plan_id"], "step_index": ps["step_index"],
                "step_id": ps.get("step_id"),
                "tick": perception["tick"],
                "action": decision.action.model_dump(),
            }
        else:
            self._pending_step_check = None
        record.update({
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "raw_output": result.raw_output, "parsed_output": decision.model_dump(),
            "reasoning_summary": decision.reasoning_summary, "latency_ms": result.latency_ms,
            "repair_count": result.repair_count,
            # V0.2-B: the rule generator's auditable candidate scoring
            # (None for providers that do not score candidates).
            "candidate_scores": getattr(result, "candidate_scores", None),
            "plan_update_result": plan_note,
            "prediction": prediction.model_dump(),
            "plan_transitions": self._drain_plan_transitions(),
            "goal_changes": self._drain_goal_changes(),
        })
        self.decision_records.append(record)
        self.memory.save_state()
        return decision, record
