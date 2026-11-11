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
                       plan-layer upgrade: a step is completed by its
                       action's execution RESULT (receive-time,
                       result-driven advancement through the shared
                       predicates in plan_steps.py), not by provider
                       declaration
  Memory            -> AgentMemory (episodic entries + belief merges)

Everything the loop produces for the audit store (prediction errors,
belief changes, plan transitions, consolidations) is buffered as
"chain updates" and drained by the scheduler; the agent itself never
touches the database.
"""
from __future__ import annotations

from pathlib import Path

from .belief_manager import BeliefManager
from .config import CharacterConfig
from .context_builder import action_is_offered, build_context
from .memory import AgentMemory
from .memory_manager import MemoryManager
from .plan_manager import COMMITMENT_GATE, PlanManager
from .plan_steps import (action_signature, owner_location_from_perception,
                         step_consistent, step_fulfilled)
from .prediction import PredictionEngine
from .protocol import AgentDecision
from .providers.base import LLMProvider, ProviderError, QuotaExhaustedError


class CharacterAgent:
    def __init__(self, config: CharacterConfig, provider: LLMProvider,
                 run_char_dir: Path | None = None, resume_dir: bool = False):
        self.config = config
        self.provider = provider
        self.state = config.initial_state()
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
        are rejected and logged (Phase A semantics, PlanManager-enforced)."""
        return self.plan_manager.apply_update(
            self.state, decision.plan_update, self._sig(decision), self._tick)

    # ---------------- Phase C chain-update plumbing ----------------
    def _drain_plan_transitions(self) -> list[dict]:
        trs, self.plan_manager.transitions = self.plan_manager.transitions, []
        return trs

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
        # Plan-layer upgrade: the result is in — it, not the provider,
        # decides whether the step this action was chosen for is done.
        self._advance_step_from_result(action_result, tick, dkey)

    def _advance_step_from_result(self, action_result: dict | None,
                                  tick: int, decision_key: str) -> None:
        """Result-driven plan advancement (plan-layer upgrade).

        decide() stashed the plan step the previous action was chosen
        for. Now its execution result has arrived: if the action
        actually fulfilled that step — judged by the shared predicate
        table in plan_steps.py against the RESULT — the Plan Manager
        advances the plan and the transition joins the audit chain
        under the evaluated decision's key.

        Guards: the plan must still be the same plan, still active,
        with its pointer still on the stashed step. If a provider
        'advance' already moved the pointer at decide() time, or
        interpretation replaced the plan in between, this result has
        nothing left to advance; and PlanManager's signature dedup
        prevents any double count if both paths ever fire for the
        same action signature.
        """
        chk = self._pending_step_check
        self._pending_step_check = None
        if chk is None or action_result is None:
            return
        plan = self.state.current_plan
        if plan is None or plan.status != "active":
            return
        if plan.plan_id != chk["plan_id"] \
                or plan.current_step != chk["step_index"]:
            return
        if not step_fulfilled(chk["step"], chk["action"],
                              bool(action_result.get("ok")),
                              agent_loc=chk["agent_loc"],
                              owner_loc=chk["owner_loc"]):
            # V0.2-A: a genuinely attempted step (the shared predicate
            # judges the action consistent with the step) whose result
            # did not fulfil it counts as a step failure; enough
            # consecutive failures block the plan. An action that was
            # not even aimed at the step is off-plan behaviour, not a
            # step failure, and counts nothing here.
            if step_consistent(chk["step"], chk["action"],
                               agent_loc=chk["agent_loc"],
                               owner_loc=chk["owner_loc"]) is True:
                self.plan_manager.note_step_failure(
                    self.state, tick, chk["step"])
                for tr in self._drain_plan_transitions():
                    self._chain_updates.append(
                        {**tr, "kind": "plan_transition",
                         "transition_kind": tr.get("kind"),
                         "decision_key": decision_key})
            return
        note = self.plan_manager.advance_from_result(
            self.state, action_signature(chk["action"]), tick, chk["step"])
        if note in ("advanced", "advance_dedup"):
            for tr in self._drain_plan_transitions():
                self._chain_updates.append(
                    {**tr, "kind": "plan_transition",
                     "transition_kind": tr.get("kind"),
                     "decision_key": decision_key})
            for gc in self.plan_manager.drain_goal_changes():
                self._chain_updates.append(
                    {**gc, "kind": "goal_change",
                     "change_kind": gc.get("kind"),
                     "decision_key": decision_key})

    # ---------------- perception -> subjective updates ----------------
    def receive(self, perception: dict, action_result: dict | None) -> None:
        st = self.state
        tick = perception["tick"]
        self._tick = tick
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

        if action_result and action_result.get("found"):
            for obj in action_result["found"]:
                if obj == "anonymous_letter" and self.config.character_id != "owner":
                    self.memory.remember(tick, "experienced",
                                         "我在后屋的抽屉里发现并读到一封匿名信，信中称镇长私吞了公共资金。", None)
                    self.memory.update_belief(
                        "anonymous_letter_exists", 1.0, "experienced",
                        "匿名信真实存在（我亲眼读到）",
                        source_type="direct_observation",
                        event_id=f"search_result@{tick}", tick=tick,
                        source="anonymous_letter")
                    self.memory.update_belief(
                        "mayor_embezzled", 0.6, "heard",
                        "信中称镇长私吞公共资金",
                        source_type="testimony",
                        event_id=f"search_result@{tick}", tick=tick,
                        source="anonymous_letter")
                    st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.25)
                    self._set_plan("decide_what_to_do_about_letter",
                                   ["return_to_front", "confront_owner", "observe_response",
                                    "decide_next_action"], critical=True)
                elif obj == "anonymous_letter":
                    self.memory.remember(tick, "experienced", "我检查了抽屉，匿名信还在原处。", None)

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
        for gc in self.plan_manager.drain_goal_changes():
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

    def _perceive_event(self, ev: dict, tick: int) -> None:
        st = self.state
        actor = ev["actors"][0] if ev.get("actors") else None
        etype = ev["type"]
        if actor == self.config.character_id:
            return
        if etype == "move":
            self.memory.remember(tick, "experienced", f"{actor} 来到了/去到了 {ev.get('to')}。", ev["event_id"])
        elif etype in ("speak", "ask", "confront"):
            text = ev.get("text") or f"（{actor} 谈论 {ev.get('topic')}）"
            self.memory.remember(tick, "experienced", f"{actor} 说：{text}", ev["event_id"])
            st.utterances_heard.append({"tick": tick, "from": actor, "topic": ev.get("topic"),
                                        "claim": ev.get("claim"), "kind": etype})
            if etype in ("ask", "confront"):
                st.pending_question = {"from": actor, "topic": ev.get("topic"), "tick": tick}
            self._interpret(ev)
        elif etype == "show" and ev.get("object") == "anonymous_letter":
            self.memory.remember(tick, "experienced", f"{actor} 把一封匿名信拿给我看了。", ev["event_id"])
            self.memory.update_belief(
                "anonymous_letter_exists", 1.0, "experienced", "我亲眼见到了匿名信",
                source_type="direct_interaction", event_id=ev["event_id"],
                tick=tick, source=actor)
            self.memory.update_belief(
                "mayor_embezzled", 0.55, "heard", "信中称镇长私吞公共资金",
                source_type="testimony", event_id=ev["event_id"],
                tick=tick, source="anonymous_letter")
            self.memory.adjust_trust(actor, +0.1)
        elif etype == "search":
            self.memory.remember(tick, "experienced", f"{actor} 在 {ev.get('location')} 翻找着什么。", ev["event_id"])
            st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.1)
        elif etype == "refuse":
            self.memory.remember(tick, "experienced", f"{actor} 拒绝继续谈 {ev.get('topic')}。", ev["event_id"])
            self.memory.adjust_trust(actor, -0.05)
        elif etype == "ambient":
            self.memory.remember(tick, "experienced", f"环境：{ev.get('text')}", ev["event_id"])

    def _interpret(self, ev: dict) -> None:
        st = self.state
        claim = ev.get("claim")
        speaker = ev["actors"][0]
        me = self.config.character_id
        P = self.config.personality
        if claim in ("mayor_hint", "mayor_late_payment", "answer_mayor"):
            base = 0.5 if claim != "mayor_hint" else 0.45
            self.memory.update_belief("mayor_embezzled", base, "heard",
                                      f"{speaker} 的话暗示镇长财务有问题",
                                      event_id=ev["event_id"], tick=self._tick,
                                      source=speaker)
            if me == "merchant":
                self.memory.update_belief("mayor_deal_risky", 0.65, "inferred",
                                          "和镇长做生意可能有回款风险，但消息本身值钱",
                                          event_id=ev["event_id"], tick=self._tick)
                self._set_plan("probe_more_mayor_news", ["probe_more_mayor_news", "protect_wealth"])
                self.memory.remember(st.short_term_memory[-1]["tick"] if st.short_term_memory else 0,
                                     "inferred", "镇长若真出事，货款要小心；这条消息也许能换人情。", None)
            elif me == "soldier":
                st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.2)
                self.memory.update_belief("investigation_widening", 0.6, "inferred",
                                          "事情可能不止酒馆老板，镇长本人也值得查",
                                          event_id=ev["event_id"], tick=self._tick)
                self._set_plan("press_for_evidence", ["press_for_evidence"])
            elif me == "owner":
                st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.3)
                self.memory.update_belief("rumor_spreading", 0.55, "inferred",
                                          "关于镇长资金的风声可能正在扩散",
                                          event_id=ev["event_id"], tick=self._tick)
                self._set_plan("find_letter_source", ["find_letter_source", "stay_calm"])
            self.memory.adjust_trust(speaker, +0.03 * P["sociability"])
        elif claim == "investigation_probe":
            if me == "owner":
                n = sum(1 for u in st.utterances_heard
                        if u["from"] == speaker and u["claim"] == claim)
                scale = 1.0 / max(1, n)
                st.emotions["arousal"] = min(1.0, st.emotions["arousal"] + 0.25 * scale)
                st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.2 * scale)
                self.memory.update_belief("being_investigated", 0.8, "inferred",
                                          "镇长的卫兵在查我，可能和那封信有关",
                                          event_id=ev["event_id"], tick=self._tick)
                self._set_plan("deny_and_observe", ["deny_and_observe", "protect_tavern"],
                               critical=True)
            self.memory.adjust_trust(speaker, -0.02)
        elif claim == "letter_question":
            if me == "owner":
                st.emotions["arousal"] = min(1.0, st.emotions["arousal"] + 0.35)
                self.memory.update_belief("letter_known_by_others", 0.85, "inferred",
                                          "已经有人知道信在我这里（或在诈我）",
                                          event_id=ev["event_id"], tick=self._tick)
                self._set_plan("deny_letter", ["deny_letter", "find_who_talked"],
                               critical=True)
            else:
                self.memory.update_belief("letter_with_owner", 0.5, "inferred", "信可能在老板手里",
                                          event_id=ev["event_id"], tick=self._tick)
        elif claim == "business_proposal":
            if me == "owner":
                n = sum(1 for u in st.utterances_heard
                        if u["from"] == speaker and u["claim"] == claim)
                self.memory.adjust_trust(speaker, +0.05 / max(1, n))
                self.memory.update_belief("business_opportunity", 0.5, "heard", "商人想谈一笔合作",
                                          event_id=ev["event_id"], tick=self._tick,
                                          source=speaker)
            elif me == "soldier":
                st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.05)
        elif claim == "denial":
            if me == "soldier":
                ev_belief = self.memory.belief("mayor_embezzled")
                if ev_belief and ev_belief.confidence >= 0.5:
                    st.emotions["suspicion"] = min(1.0, st.emotions["suspicion"] + 0.2)
                    self.memory.update_belief("owner_hiding_something", 0.65, "inferred",
                                              "老板的否认和已知线索对不上，他在藏事",
                                              event_id=ev["event_id"], tick=self._tick)
                else:
                    self.memory.adjust_trust(speaker, +0.04)
            elif me == "merchant":
                self.memory.update_belief("owner_cautious", 0.7, "inferred", "老板口风很紧，是个谨慎人",
                                          event_id=ev["event_id"], tick=self._tick)
        elif claim == "letter_discussion":
            if me == "merchant":
                self.memory.update_belief("anonymous_letter_exists", 0.7, "heard",
                                          f"{speaker} 当众说他已经看过一封关于镇长的信",
                                          event_id=ev["event_id"], tick=self._tick,
                                          source=speaker)
                self._set_plan("assess_letter_rumor_value", ["assess_letter_rumor_value"],
                               critical=True)
            if me == "owner":
                st.emotions["arousal"] = min(1.0, st.emotions["arousal"] + 0.2)
                self.memory.update_belief("letter_known_by_others", 0.9, "inferred",
                                          "对方已经亲眼看过那封信了",
                                          event_id=ev["event_id"], tick=self._tick)
                self._set_plan("assess_damage", ["assess_damage", "protect_tavern"],
                               critical=True)
        elif claim == "bluff_hint":
            conf = 0.3 + 0.3 * P["curiosity"]
            self.memory.update_belief("speaker_knows_secret", round(conf, 2), "inferred",
                                      f"{speaker} 暗示自己知道镇上的秘密（真假未知）",
                                      event_id=ev["event_id"], tick=self._tick)

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
        record["goal_changes"] = self.plan_manager.drain_goal_changes()
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
        sig = self._sig(decision)
        self.state.action_counts[sig] = self.state.action_counts.get(sig, 0) + 1
        # Phase C: the structured prediction for THIS action, generated
        # now — before execution — and evaluated when its result returns.
        prediction = self.prediction_engine.predict(self, context, decision)
        self.pending_prediction = prediction
        # Plan-layer upgrade: remember which plan step this action was
        # chosen for (the step view the provider actually saw), so
        # receive() can advance the plan from the action's RESULT — a
        # fact about the world — instead of from a provider opinion.
        ps = getattr(context, "plan_step", None)
        if ps and ps.get("step"):
            self._pending_step_check = {
                "plan_id": ps["plan_id"], "step_index": ps["step_index"],
                "step": ps["step"], "tick": perception["tick"],
                "action": decision.action.model_dump(),
                "agent_loc": context.location,
                "owner_loc": owner_location_from_perception(
                    context.perception, context.location),
            }
        else:
            self._pending_step_check = None
        record.update({
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "raw_output": result.raw_output, "parsed_output": decision.model_dump(),
            "reasoning_summary": decision.reasoning_summary, "latency_ms": result.latency_ms,
            "repair_count": result.repair_count,
            "plan_update_result": plan_note,
            "prediction": prediction.model_dump(),
            "plan_transitions": self._drain_plan_transitions(),
            "goal_changes": self.plan_manager.drain_goal_changes(),
        })
        self.decision_records.append(record)
        self.memory.save_state()
        return decision, record
