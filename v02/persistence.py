"""Persistence layer (SQLAlchemy 2.x). Default engine: SQLite file inside
the run directory; set DATABASE_URL to switch to PostgreSQL later — the
schema is dialect-neutral (task book section 10: agent_decisions /
world_events / event_deliveries, plus dedup_drops for the Phase B
Broker's duplicate-suppression audit, and the Phase C cognitive-chain
tables: predictions / belief_changes / plan_transitions /
memory_consolidations).

Phase C linking: every decision carries a client-side `decision_key`
("<character_id>:<tick>", unique per run — one decision per agent per
tick). The prediction, its error, the belief changes it caused, the
plan transitions of that decision and its memory consolidation all
carry the same key, so Store.chain_for(decision_id) can rebuild the
complete cognitive chain of any single decision from the DB alone.

Agents never touch this module. Only the runner (world side) writes, and
only with data that has already been committed or audit-logged.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Float, Integer, String, Text,
                        create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class DecisionRow(Base):
    __tablename__ = "agent_decisions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    tick = Column(Integer)
    character_id = Column(String, index=True)
    decision_key = Column(String, index=True)   # Phase C chain link
    context_json = Column(Text)                 # Phase C: full context snapshot
    provider = Column(String)
    model = Column(String)
    prompt_version = Column(String)
    context_summary = Column(Text)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    raw_output = Column(Text)
    parsed_output = Column(Text)
    reasoning_summary = Column(Text)
    execution_result = Column(Text)
    error = Column(Text)
    # V0.2-B: the rule generator's candidate scoring (design 3.6) —
    # nullable JSON; None for providers that do not score and for
    # pre-B runs (see the migration guard in Store.__init__).
    candidate_scores = Column(Text)
    # V0.2-C C2 (design 3.6): the candidate census JSON.
    census = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class EventRow(Base):
    __tablename__ = "world_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    event_id = Column(String, index=True)
    tick = Column(Integer)
    type = Column(String)
    location = Column(String)
    actors = Column(Text)
    payload = Column(Text)


class DeliveryRow(Base):
    __tablename__ = "event_deliveries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    tick = Column(Integer)
    agent_id = Column(String)
    delivered_event_ids = Column(Text)
    muffled_event_ids = Column(Text)


class DedupDropRow(Base):
    """Phase B: one row per duplicate delivery the Broker suppressed."""
    __tablename__ = "dedup_drops"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    tick = Column(Integer)
    agent_id = Column(String)
    event_id = Column(String, index=True)
    kind = Column(String)          # "full" | "muffled"
    reason = Column(String)


class CounterfactualSuppressionRow(Base):
    """Phase D: one row per delivery an experiment's counterfactual
    rule suppressed at the Broker layer. Deliberately separate from
    dedup_drops: a dedup drop is the same event offered twice by the
    machinery, a suppression is an experimenter-imposed 'this agent
    never receives this event' — the objective event still happened."""
    __tablename__ = "counterfactual_suppressions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    tick = Column(Integer)
    agent_id = Column(String)
    event_id = Column(String, index=True)
    kind = Column(String)          # "full" | "muffled"
    rule = Column(Text)            # the suppression rule, as JSON


class PredictionRow(Base):
    """Phase C: one row per decision's Prediction; the PredictionError
    is filled in when the action result is evaluated (next tick)."""
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    prediction_id = Column(String, index=True)
    decision_key = Column(String, index=True)
    character_id = Column(String, index=True)
    tick = Column(Integer)
    action_type = Column(String)
    prediction_json = Column(Text)
    error_json = Column(Text)
    surprise = Column(Float)
    evaluated_tick = Column(Integer)


class BeliefChangeRow(Base):
    """Phase C: one row per belief or trust adjustment driven by a
    prediction error. kind is the belief's epistemic kind, or 'trust'
    (then key is 'trust:<other>')."""
    __tablename__ = "belief_changes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    tick = Column(Integer)
    character_id = Column(String, index=True)
    decision_key = Column(String, index=True)
    prediction_id = Column(String, index=True)
    key = Column(String)
    kind = Column(String)
    before = Column(Float)
    after = Column(Float)
    cause = Column(String)


class PlanTransitionRow(Base):
    """Phase C: one row per plan migration (set / replace / advance /
    advance_dedup / commitment_decay / abandon / *_rejected_no_reason /
    continue / none ...), with before/after plan snapshots."""
    __tablename__ = "plan_transitions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    tick = Column(Integer)
    character_id = Column(String, index=True)
    decision_key = Column(String, index=True)
    plan_id = Column(String, index=True)
    kind = Column(String)
    reason = Column(Text)
    before_json = Column(Text)
    after_json = Column(Text)


class GoalChangeRow(Base):
    """V0.2-A Phase 3: one row per deterministic goal lifecycle
    change driven by a plan event (goal_paused / goal_reactivated /
    goal_progress / goal_completed / goal_abandoned), with before /
    after snapshots of the Goal object."""
    __tablename__ = "goal_changes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    tick = Column(Integer)
    character_id = Column(String, index=True)
    decision_key = Column(String, index=True)
    goal_key = Column(String, index=True)
    kind = Column(String)
    reason = Column(Text)
    before_json = Column(Text)
    after_json = Column(Text)


class ConsolidationRow(Base):
    """Phase C: one row per memory consolidation (i.e. per evaluated
    prediction), with every scored entry and the promoted subset."""
    __tablename__ = "memory_consolidations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    tick = Column(Integer)
    character_id = Column(String, index=True)
    decision_key = Column(String, index=True)
    prediction_id = Column(String, index=True)
    surprise = Column(Float)
    entries_json = Column(Text)
    consolidated_json = Column(Text)


class Store:
    def __init__(self, run_id: str, sqlite_path: str | None = None):
        self.run_id = run_id
        url = os.environ.get("DATABASE_URL")
        if not url:
            url = f"sqlite:///{sqlite_path}"
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)
        # V0.2-B migration guard: create_all does not ALTER existing
        # tables, and experiment tooling opens PRE-B run databases
        # read-mostly through this Store — add the candidate_scores
        # column to an old agent_decisions table if it is missing.
        from sqlalchemy import inspect, text as _text
        cols = [c["name"] for c in
                inspect(self.engine).get_columns("agent_decisions")]
        if "candidate_scores" not in cols:
            with self.engine.begin() as conn:
                conn.execute(_text(
                    "ALTER TABLE agent_decisions "
                    "ADD COLUMN candidate_scores TEXT"))
        if "census" not in cols:
            with self.engine.begin() as conn:
                conn.execute(_text(
                    "ALTER TABLE agent_decisions "
                    "ADD COLUMN census TEXT"))
        self.Session = sessionmaker(bind=self.engine)

    def record_decision(self, rec: dict) -> None:
        with self.Session() as s:
            s.add(DecisionRow(
                run_id=self.run_id, tick=rec.get("tick"), character_id=rec.get("character_id"),
                decision_key=rec.get("decision_key"),
                context_json=json.dumps(rec["context"], ensure_ascii=False)
                if rec.get("context") is not None else None,
                provider=rec.get("provider"), model=rec.get("model"),
                prompt_version=rec.get("prompt_version"),
                context_summary=rec.get("context_summary"),
                input_tokens=rec.get("input_tokens", 0), output_tokens=rec.get("output_tokens", 0),
                raw_output=rec.get("raw_output", ""),
                parsed_output=json.dumps(rec.get("parsed_output"), ensure_ascii=False)
                if rec.get("parsed_output") is not None else None,
                reasoning_summary=rec.get("reasoning_summary"),
                execution_result=json.dumps(rec.get("execution_result"), ensure_ascii=False)
                if rec.get("execution_result") is not None else None,
                candidate_scores=json.dumps(rec.get("candidate_scores"), ensure_ascii=False)
                if rec.get("candidate_scores") is not None else None,
                census=json.dumps(rec.get("census"), ensure_ascii=False)
                if rec.get("census") is not None else None,
                error=rec.get("error")))
            s.commit()

    def record_events(self, events: list[dict]) -> None:
        with self.Session() as s:
            for ev in events:
                s.add(EventRow(run_id=self.run_id, event_id=ev.get("event_id"),
                               tick=ev.get("tick"), type=ev.get("type"),
                               location=ev.get("location"),
                               actors=json.dumps(ev.get("actors", []), ensure_ascii=False),
                               payload=json.dumps(ev, ensure_ascii=False)))
            s.commit()

    def record_deliveries(self, records: list[dict]) -> None:
        with self.Session() as s:
            for r in records:
                s.add(DeliveryRow(run_id=self.run_id, tick=r["tick"], agent_id=r["agent_id"],
                                  delivered_event_ids=json.dumps(r["delivered_event_ids"]),
                                  muffled_event_ids=json.dumps(r["muffled_event_ids"])))
            s.commit()

    def record_dedup_drops(self, drops: list[dict]) -> None:
        with self.Session() as s:
            for d in drops:
                s.add(DedupDropRow(run_id=self.run_id, tick=d["tick"],
                                   agent_id=d["agent_id"], event_id=d["event_id"],
                                   kind=d["kind"], reason=d["reason"]))
            s.commit()

    def record_counterfactual_suppressions(self, records: list[dict]) -> None:
        with self.Session() as s:
            for r in records:
                s.add(CounterfactualSuppressionRow(
                    run_id=self.run_id, tick=r["tick"], agent_id=r["agent_id"],
                    event_id=r["event_id"], kind=r["kind"],
                    rule=json.dumps(r["rule"], ensure_ascii=False)))
            s.commit()

    # ---------------- Phase C: cognitive chain ----------------
    def record_prediction(self, prediction: dict, decision_key: str) -> None:
        with self.Session() as s:
            s.add(PredictionRow(
                run_id=self.run_id, prediction_id=prediction["prediction_id"],
                decision_key=decision_key, character_id=prediction["character_id"],
                tick=prediction["tick"], action_type=prediction["action_type"],
                prediction_json=json.dumps(prediction, ensure_ascii=False)))
            s.commit()

    def update_prediction_error(self, prediction_id: str, error: dict,
                                evaluated_tick: int) -> None:
        with self.Session() as s:
            row = s.query(PredictionRow).filter_by(
                run_id=self.run_id, prediction_id=prediction_id).one()
            row.error_json = json.dumps(error, ensure_ascii=False)
            row.surprise = error.get("surprise")
            row.evaluated_tick = evaluated_tick
            s.commit()

    def record_belief_change(self, rec: dict) -> None:
        with self.Session() as s:
            s.add(BeliefChangeRow(
                run_id=self.run_id, tick=rec.get("tick"),
                character_id=rec.get("character_id"),
                decision_key=rec.get("decision_key"),
                prediction_id=rec.get("prediction_id"),
                key=rec.get("key"), kind=rec.get("change_kind"),
                before=rec.get("before"), after=rec.get("after"),
                cause=rec.get("cause")))
            s.commit()

    def record_plan_transition(self, rec: dict) -> None:
        with self.Session() as s:
            s.add(PlanTransitionRow(
                run_id=self.run_id, tick=rec.get("tick"),
                character_id=rec.get("character_id"),
                decision_key=rec.get("decision_key"),
                plan_id=rec.get("plan_id"),
                kind=rec.get("transition_kind") or rec.get("kind"),
                reason=rec.get("reason"),
                before_json=json.dumps(rec["before"], ensure_ascii=False)
                if rec.get("before") is not None else None,
                after_json=json.dumps(rec["after"], ensure_ascii=False)
                if rec.get("after") is not None else None))
            s.commit()

    def record_goal_change(self, rec: dict) -> None:
        with self.Session() as s:
            s.add(GoalChangeRow(
                run_id=self.run_id, tick=rec.get("tick"),
                character_id=rec.get("character_id"),
                decision_key=rec.get("decision_key"),
                goal_key=rec.get("goal_key"),
                kind=rec.get("kind"), reason=rec.get("reason"),
                before_json=json.dumps(rec["before"], ensure_ascii=False)
                if rec.get("before") is not None else None,
                after_json=json.dumps(rec["after"], ensure_ascii=False)
                if rec.get("after") is not None else None))
            s.commit()

    def record_consolidation(self, rec: dict) -> None:
        with self.Session() as s:
            s.add(ConsolidationRow(
                run_id=self.run_id, tick=rec.get("tick"),
                character_id=rec.get("character_id"),
                decision_key=rec.get("decision_key"),
                prediction_id=rec.get("prediction_id"),
                surprise=rec.get("surprise"),
                entries_json=json.dumps(rec.get("entries", []), ensure_ascii=False),
                consolidated_json=json.dumps(rec.get("consolidated", []),
                                             ensure_ascii=False)))
            s.commit()

    def rollback_after(self, tick: int) -> None:
        """--resume support: delete every row this run wrote AFTER the
        checkpoint taken at world tick `tick`, so the resumed process
        replays exactly the segment the interrupted one may already
        have persisted (including a completed run's final perception
        pass), with no duplicates and no overwrites.

        Boundary rules follow each table's tick semantics:
          - decisions / predictions(created) / belief_changes /
            plan_transitions / consolidations / deliveries /
            dedup_drops / counterfactual_suppressions carry a
            PERCEPTION or delivery tick: rows at tick >= checkpoint are
            post-checkpoint (the final pass writes at tick == final
            world tick, and post-checkpoint ticks start at the
            checkpoint tick);
          - world_events carry the tick of the step that produced them:
            the checkpoint tick's own events are pre-checkpoint and are
            kept, so only tick > checkpoint is deleted;
          - predictions evaluated at tick >= checkpoint are
            un-evaluated (their error is cleared; the resumed final /
            next perception pass re-evaluates them against the same
            restored pending prediction)."""
        with self.Session() as s:
            def wipe(model, cond):
                s.query(model).filter(
                    model.run_id == self.run_id, cond).delete(
                    synchronize_session=False)

            wipe(DecisionRow, DecisionRow.tick >= tick)
            wipe(EventRow, EventRow.tick > tick)
            wipe(DeliveryRow, DeliveryRow.tick >= tick)
            wipe(DedupDropRow, DedupDropRow.tick >= tick)
            wipe(CounterfactualSuppressionRow,
                 CounterfactualSuppressionRow.tick >= tick)
            wipe(PredictionRow, PredictionRow.tick >= tick)
            wipe(BeliefChangeRow, BeliefChangeRow.tick >= tick)
            wipe(PlanTransitionRow, PlanTransitionRow.tick >= tick)
            wipe(GoalChangeRow, GoalChangeRow.tick >= tick)
            wipe(ConsolidationRow, ConsolidationRow.tick >= tick)
            for row in s.query(PredictionRow).filter(
                    PredictionRow.run_id == self.run_id,
                    PredictionRow.evaluated_tick >= tick).all():
                row.error_json = None
                row.surprise = None
                row.evaluated_tick = None
            s.commit()

    def chain_for(self, decision_id: int) -> dict:
        """Rebuild ONE decision's complete cognitive chain from the DB
        alone: context snapshot -> prediction -> decision -> execution
        result -> prediction error -> belief changes -> plan
        transitions -> memory consolidation."""
        def loads(text):
            return json.loads(text) if text else None

        with self.Session() as s:
            row = s.query(DecisionRow).filter_by(
                run_id=self.run_id, id=decision_id).one()
            key = row.decision_key
            pred = s.query(PredictionRow).filter_by(
                run_id=self.run_id, decision_key=key).first()
            cons = s.query(ConsolidationRow).filter_by(
                run_id=self.run_id, decision_key=key).first()
            beliefs = s.query(BeliefChangeRow).filter_by(
                run_id=self.run_id, decision_key=key).order_by(BeliefChangeRow.id).all()
            plans = s.query(PlanTransitionRow).filter_by(
                run_id=self.run_id, decision_key=key).order_by(PlanTransitionRow.id).all()
            return {
                "decision_id": row.id, "decision_key": key,
                "character_id": row.character_id, "tick": row.tick,
                "provider": row.provider, "model": row.model,
                "context": loads(row.context_json),
                "context_summary": row.context_summary,
                "decision": loads(row.parsed_output),
                "execution_result": loads(row.execution_result),
                "decision_error": row.error,
                "prediction": loads(pred.prediction_json) if pred else None,
                "prediction_error": loads(pred.error_json) if pred else None,
                "belief_changes": [
                    {"key": b.key, "kind": b.kind, "before": b.before,
                     "after": b.after, "cause": b.cause, "tick": b.tick}
                    for b in beliefs],
                "plan_transitions": [
                    {"kind": p.kind, "plan_id": p.plan_id, "reason": p.reason,
                     "tick": p.tick, "before": loads(p.before_json),
                     "after": loads(p.after_json)} for p in plans],
                "consolidation": (
                    {"tick": cons.tick, "surprise": cons.surprise,
                     "entries": loads(cons.entries_json),
                     "consolidated": loads(cons.consolidated_json)}
                    if cons else None),
            }

    def counts(self) -> dict:
        with self.Session() as s:
            return {"decisions": s.query(DecisionRow).filter_by(run_id=self.run_id).count(),
                    "events": s.query(EventRow).filter_by(run_id=self.run_id).count(),
                    "deliveries": s.query(DeliveryRow).filter_by(run_id=self.run_id).count(),
                    "dedup_drops": s.query(DedupDropRow).filter_by(run_id=self.run_id).count(),
                    "predictions": s.query(PredictionRow).filter_by(run_id=self.run_id).count(),
                    "belief_changes": s.query(BeliefChangeRow).filter_by(run_id=self.run_id).count(),
                    "plan_transitions": s.query(PlanTransitionRow).filter_by(run_id=self.run_id).count(),
                    "goal_changes": s.query(GoalChangeRow).filter_by(run_id=self.run_id).count(),
                    "consolidations": s.query(ConsolidationRow).filter_by(run_id=self.run_id).count(),
                    "counterfactual_suppressions": s.query(CounterfactualSuppressionRow).filter_by(run_id=self.run_id).count()}
