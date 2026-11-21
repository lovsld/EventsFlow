"""Arc Manager (task book §12): story-stage transitions driven by
EVENT STRUCTURE, never by tick counts alone. Every transition
records its trigger and the evidence (event/thread ids) so the
arc can be audited. Stages move forward only; a stage whose
trigger never fires is reported as not reached — an unresolved
20-tick run honestly has no resolution stage.

Triggers (evaluated per tick over replayed signals):
- setup -> development: a thread develops past its opening event
  (related_events >= 2) or the first cross-scene propagation
  occurs — the world is in motion.
- development -> escalation: any thread enters `escalating`, or
  any conflict severity reaches 0.6.
- escalation -> climax: the main thread's confrontation ladder
  (graph escalates edges whose target lies on the main thread)
  reaches 3 — sustained, structured confrontation, not a spike.
- climax -> aftermath: 4 consecutive ticks with no thread
  development after the climax.
- any -> resolution: a main thread reaches `resolved`
  (checked from development onward).
"""
from __future__ import annotations

STAGES = ["setup", "development", "escalation", "climax",
          "aftermath", "resolution"]


class ArcManager:
    def __init__(self, main_thread_id: str = ""):
        self.main_thread_id = main_thread_id
        self.stage = "setup"
        self.transitions: list[dict] = []
        self.stage_at: dict[int, str] = {}
        self._quiet_after_climax = 0
        self._main_ladder = 0

    def _go(self, tick: int, to: str, trigger: str,
            evidence: list[str]) -> None:
        self.transitions.append({"tick": tick, "from": self.stage,
                                 "to": to, "trigger": trigger,
                                 "evidence": evidence})
        self.stage = to

    def observe(self, tick: int, signals: dict) -> str:
        threads = signals.get("threads", {})
        if self.stage == "setup":
            developed = [tid for tid, t in threads.items()
                         if t.get("events", 0) >= 2]
            if developed or signals.get("propagations", 0) > 0:
                self._go(tick, "development",
                         "thread developed past opening / first "
                         "propagation", developed[:2])
        elif self.stage == "development":
            esc = [tid for tid, t in threads.items()
                   if t.get("status") == "escalating"]
            hot = [c for c in signals.get("conflicts", [])
                   if c.get("severity", 0) >= 0.6]
            if esc or hot:
                self._go(tick, "escalation",
                         "thread escalating or conflict severity "
                         ">= 0.6", esc + hot[:1])
        elif self.stage == "escalation":
            self._main_ladder = max(self._main_ladder,
                                    signals.get("main_ladder", 0))
            if self._main_ladder >= 3:
                self._go(tick, "climax",
                         "main-thread confrontation ladder >= 3",
                         signals.get("main_ladder_events", []))
        elif self.stage == "climax":
            if signals.get("developed_any"):
                self._quiet_after_climax = 0
            else:
                self._quiet_after_climax += 1
                if self._quiet_after_climax >= 4:
                    self._go(tick, "aftermath",
                             "4 quiet ticks after climax", [])
        if self.stage not in ("resolution",) and \
                self.main_thread_id:
            main = threads.get(self.main_thread_id, {})
            if main.get("status") == "resolved" and \
                    self.stage in ("development", "escalation",
                                   "climax", "aftermath"):
                self._go(tick, "resolution",
                         "main thread resolved", [self.main_thread_id])
        self.stage_at[tick] = self.stage
        return self.stage

    def report(self, last_tick: int) -> dict:
        reached = {t["to"] for t in self.transitions} | {"setup"}
        return {"final_stage": self.stage,
                "transitions": self.transitions,
                "stage_at": {str(k): v for k, v in
                             self.stage_at.items()},
                "not_reached": [s for s in STAGES
                                if s not in reached]}
