"""Thread Manager (task book §5): owns StoryThread lifecycles.

All updates are driven by COMMITTED world events fed in by the
Story Engine's observe step. The manager never invents developments:
an event develops a thread only if it matches the thread's declared
hooks (propositions / objects), and knowledge accrues only to
characters the delivery records say actually received it (plus the
actor). Status machine:

  active --(no development for dormant_after ticks)--> dormant
  dormant --(new development)--> active
  active --(escalating fact asserted / confrontation)--> escalating
  escalating --(quiet for dormant_after ticks)--> active
  any --(all questions answered)--> resolved
  any --(a dependency resolved without answering)--> blocked
  blocked --(new development)--> active        (reopen)
  resolved --(new development)--> active       (reopen, resolved_at kept in log)
"""
from __future__ import annotations

from v03.story.models import StoryState, StoryThread

PLOT_TYPES = {"speak", "ask", "confront", "search", "show", "refuse"}


class ThreadManager:
    def __init__(self, state: StoryState, dormant_after: int = 8):
        self.state = state
        self.dormant_after = dormant_after

    # ---------- CRUD ----------
    def create(self, thread: StoryThread, tick: int) -> StoryThread:
        thread.created_at = tick
        thread.last_developed_at = tick
        self.state.threads[thread.thread_id] = thread
        self._log(tick, "thread_created", thread.thread_id)
        return thread

    def get(self, thread_id: str) -> StoryThread:
        return self.state.threads[thread_id]

    def set_status(self, thread_id: str, status: str, tick: int,
                   reason: str = "") -> None:
        t = self.state.threads[thread_id]
        if t.status != status:
            self._log(tick, "thread_status",
                      f"{thread_id}: {t.status} -> {status} {reason}")
            t.status = status
            if status == "resolved":
                t.resolved_at = tick

    def link_threads(self, a: str, b: str, kind: str = "related") -> None:
        ta, tb = self.state.threads[a], self.state.threads[b]
        if kind == "dependency":
            if b not in ta.dependencies:
                ta.dependencies.append(b)
        for t, other in ((ta, b), (tb, a)):
            if other not in t.related_threads:
                t.related_threads.append(other)

    def mark_shared_event(self, event_id: str,
                          thread_ids: list[str]) -> None:
        for tid in thread_ids:
            t = self.state.threads[tid]
            if event_id not in t.shared_events:
                t.shared_events.append(event_id)

    # ---------- event-driven updates ----------
    def observe_event(self, event: dict, delivered_to: list[str],
                      tick: int,
                      object_facts: dict[str, dict]) -> list[str]:
        """Feed one committed event. Returns ids of threads developed.

        Facts carry an evidence grade: "direct" (the finder's own
        find of the object's fact_belief; an assertion by a speaker
        with experienced knowledge of it) or "hearsay" (everything
        else). Only direct evidence ANSWERS a thread question; any
        assertion at least ADDRESSES it. A hinted accusation must
        never read as a settled question.
        """
        developed: list[str] = []
        etype = event.get("type")
        prop = event.get("proposition")
        for t in self.state.threads.values():
            if t.status in ("abandoned",):
                continue
            graded_facts: list[tuple[str, str]] = []
            matched = False
            if etype in ("speak", "ask", "confront") and \
                    prop and prop in t.propositions:
                matched = True
                if event.get("stance") == "assert":
                    grade = "direct" if event.get("_direct") else "hearsay"
                    graded_facts.append((prop, grade))
            if etype == "search":
                for obj in event.get("found") or []:
                    if obj in t.objects:
                        matched = True
                        facts = object_facts.get(obj, {})
                        graded_facts += [(f, "direct")
                                         for f in facts.get("direct", [])]
                        graded_facts += [(f, "hearsay")
                                         for f in facts.get("hearsay", [])]
            if not matched:
                continue
            developed.append(t.thread_id)
            if event["event_id"] not in t.related_events:
                t.related_events.append(event["event_id"])
            t.last_developed_at = tick
            # knowledge: actor + actual recipients only
            learners = set(delivered_to)
            if event.get("actors"):
                learners.add(event["actors"][0])
            for fact, grade in graded_facts:
                if fact not in t.known_facts:
                    t.known_facts.append(fact)
                for char in learners:
                    known = t.known_by.setdefault(char, [])
                    if fact not in known:
                        known.append(fact)
                self._answer_questions(t, fact, grade, tick)
            self._refresh_status(t, event, tick)
        return developed

    def _answer_questions(self, t: StoryThread, fact: str, grade: str,
                          tick: int) -> None:
        for q in t.questions:
            if q.answered_by != fact:
                continue
            if q.addressed_at is None:
                q.addressed_at = tick
            if grade == "direct" and q.answered_at is None:
                q.answered_at = tick
                self._log(tick, "question_answered",
                          f"{t.thread_id}/{q.id} by direct evidence "
                          f"({fact})")

    def _refresh_status(self, t: StoryThread, event: dict,
                        tick: int) -> None:
        if t.questions:
            answered = sum(1 for q in t.questions
                           if q.answered_at is not None)
            t.progress = answered / len(t.questions)
            if answered == len(t.questions):
                self.set_status(t.thread_id, "resolved", tick,
                                "all questions answered")
                return
        if t.status == "resolved":
            self.set_status(t.thread_id, "active", tick, "reopened")
        elif t.status in ("dormant", "blocked"):
            self.set_status(t.thread_id, "active", tick, "developed")
        escalates = event.get("type") == "confront" or (
            event.get("stance") == "assert"
            and event.get("proposition") in t.sensitive_facts)
        if escalates and t.status == "active":
            self.set_status(t.thread_id, "escalating", tick,
                            "confrontation or sensitive assertion")

    def end_of_tick(self, tick: int) -> None:
        for t in self.state.threads.values():
            if t.status in ("resolved", "abandoned"):
                continue
            # blocked: a dependency resolved while this thread still
            # has unanswered questions that needed it open
            for dep_id in t.dependencies:
                dep = self.state.threads.get(dep_id)
                if dep and dep.status == "resolved" and \
                        t.questions and \
                        any(q.answered_at is None for q in t.questions):
                    if t.status != "blocked":
                        self.set_status(t.thread_id, "blocked", tick,
                                        f"dependency {dep_id} resolved")
            quiet = tick - t.last_developed_at
            if t.status == "active" and quiet >= self.dormant_after:
                self.set_status(t.thread_id, "dormant", tick,
                                f"no development for {quiet} ticks")
            elif t.status == "escalating" and \
                    quiet >= self.dormant_after:
                self.set_status(t.thread_id, "active", tick,
                                "escalation cooled")

    # ---------- queries ----------
    def stagnant_threads(self, tick: int,
                         min_quiet: int) -> list[StoryThread]:
        return [t for t in self.state.threads.values()
                if t.status in ("active", "escalating", "dormant")
                and tick - t.last_developed_at >= min_quiet]

    def knowledge_gaps(self, thread_id: str) -> dict[str, list[str]]:
        """Facts of this thread each involved character does NOT know."""
        t = self.state.threads[thread_id]
        gaps: dict[str, list[str]] = {}
        for char in t.involved_characters:
            known = set(t.known_by.get(char, []))
            missing = [f for f in t.sensitive_facts if f not in known]
            if missing:
                gaps[char] = missing
        return gaps

    def _log(self, tick: int, kind: str, detail: str) -> None:
        self.state.change_log.append(
            {"tick": tick, "kind": kind, "detail": detail})
