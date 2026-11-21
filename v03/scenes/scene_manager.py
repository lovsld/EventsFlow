"""Scene Manager + cross-scene propagation + consistency checks
(task book §10.2–§10.5).

Everything is driven by COMMITTED tick records (positions are the
authority for where everyone is). The manager registers scenes,
never moves anyone: movement already happened in the world; what
this layer adds is the scene bookkeeping, the propagation ledger,
and independent consistency verification of the stream:

- a character's events must occur at the location the world says
  they occupy that tick (single-location invariant, §10.4);
- a fact may cross scenes only with a carrier who learned it
  (delivery / find / initial knowledge), moved, and only then
  spoke it — travel takes >= 1 tick (§10.4/§11.3);
- an assertion of a fact the speaker never learned anywhere is
  flagged as unexplained knowledge (it may still be legitimate —
  e.g. a bluff — but it is recorded, not silently accepted).
"""
from __future__ import annotations

from v03.scenes.models import ConsistencyIssue, Propagation, Scene

PLOT_TYPES = {"speak", "ask", "confront", "search", "show", "refuse"}


class SceneManager:
    def __init__(self, threads: dict | None = None):
        self.scenes: dict[str, Scene] = {}
        self.active: dict[str, Scene] = {}   # location -> scene
        self._counter = 0
        self.threads = threads or {}
        self._session_open: dict[str, bool] = {}

    def _open(self, location: str, tick: int) -> Scene:
        self._counter += 1
        scene = Scene(scene_id=f"S{self._counter:02d}",
                      location_id=location, start_tick=tick)
        self.scenes[scene.scene_id] = scene
        self.active[location] = scene
        return scene

    def process_tick(self, tick_record: dict) -> dict:
        tick = tick_record["tick"]
        positions: dict[str, str] = tick_record.get("positions", {})
        events = tick_record.get("events", [])
        opened, closed = [], []
        present_by_loc: dict[str, set[str]] = {}
        for char, loc in positions.items():
            present_by_loc.setdefault(loc, set()).add(char)
        event_locs = {e.get("location") for e in events
                      if e.get("location")}
        # open scenes where someone is present and something happens
        # (or a session starts); a lone passer-by with no event does
        # not constitute a scene
        for loc, chars in present_by_loc.items():
            if loc in self.active:
                continue
            has_event = loc in event_locs
            if has_event:
                scene = self._open(loc, tick)
                opened.append(scene.scene_id)
        # membership + events
        for loc, scene in list(self.active.items()):
            present = present_by_loc.get(loc, set())
            for char in present:
                if char not in scene.participants:
                    scene.participants.append(char)
                    scene.joined_at[char] = tick
                elif char in scene.left_at:
                    del scene.left_at[char]
            for char in list(scene.participants):
                if char not in present and char not in scene.left_at:
                    scene.left_at[char] = tick
            for e in events:
                if e.get("location") == loc:
                    scene.event_queue.append(e["event_id"])
                    if e.get("type") == "session_start":
                        self._session_open[loc] = True
                    if e.get("type") == "session_end":
                        self._session_open[loc] = False
                    self._link_threads(scene, e)
            # close when the location empties
            if not present:
                scene.end_tick = tick - 1
                scene.status = "interrupted" if \
                    self._session_open.get(loc) else "closed"
                scene.outcome = {
                    "events": len(scene.event_queue),
                    "participants": list(scene.participants),
                    "threads": list(scene.related_threads)}
                closed.append(scene.scene_id)
                del self.active[loc]
        return {"opened": opened, "closed": closed}

    def _link_threads(self, scene: Scene, event: dict) -> None:
        prop = event.get("proposition")
        for tid, t in self.threads.items():
            hit = (prop and prop in t.propositions) or \
                any(o in t.objects for o in event.get("found") or [])
            if hit and tid not in scene.related_threads:
                scene.related_threads.append(tid)

    def finalize(self, last_tick: int) -> None:
        for loc, scene in list(self.active.items()):
            scene.end_tick = last_tick
            scene.status = "interrupted" if \
                self._session_open.get(loc) else "closed"
            scene.outcome = {"events": len(scene.event_queue),
                             "participants": list(scene.participants),
                             "threads": list(scene.related_threads)}
        self.active.clear()

    def parallel_at(self, tick: int) -> list[str]:
        return [s.scene_id for s in self.scenes.values()
                if s.start_tick <= tick and
                (s.end_tick is None or s.end_tick >= tick)]


class PropagationTracker:
    def __init__(self, initial_knowledge: dict[str, list[str]],
                 basis_free_claims: set[str] | None = None):
        # char -> fact -> (tick, scene_id)
        self.learned: dict[str, dict[str, tuple[int, str]]] = {
            c: {f: (0, "") for f in facts}
            for c, facts in initial_knowledge.items()}
        self.propagations: list[Propagation] = []
        self.issues: list[ConsistencyIssue] = []
        # claims the scenario itself declares basis-free (proposals,
        # bluffs, greetings): asserting one is ORIGINATION, not a
        # knowledge claim, so it needs no learning source
        self.basis_free_claims = basis_free_claims or set()

    def process_tick(self, tick_record: dict,
                     scene_by_location: dict[str, str],
                     delivered: dict[str, list[str]],
                     object_facts: dict[str, dict]) -> None:
        tick = tick_record["tick"]
        positions = tick_record.get("positions", {})
        for e in tick_record.get("events", []):
            loc = e.get("location") or ""
            scene_id = scene_by_location.get(loc, "")
            actors = e.get("actors") or []
            if e.get("type") == "search" and actors:
                finder = actors[0]
                for obj in e.get("found") or []:
                    facts = object_facts.get(obj, {})
                    for fact in (facts.get("direct", []) +
                                 facts.get("hearsay", [])):
                        self._learn(finder, fact, tick, scene_id)
            if e.get("type") in ("speak", "ask", "confront"):
                prop = e.get("proposition")
                if not prop or not actors:
                    continue
                speaker = actors[0]
                if e.get("stance") == "assert":
                    known = self.learned.setdefault(speaker, {})
                    if prop in known:
                        learned_tick, learned_scene = known[prop]
                        if learned_scene and scene_id and \
                                learned_scene != scene_id:
                            self.propagations.append(Propagation(
                                fact=prop, carrier=speaker,
                                from_scene=learned_scene,
                                to_scene=scene_id,
                                learned_at=learned_tick, spoke_at=tick,
                                travel_ticks=tick - learned_tick,
                                via_event=e["event_id"]))
                    elif e.get("claim") not in self.basis_free_claims:
                        self.issues.append(ConsistencyIssue(
                            tick=tick, kind="unexplained_assertion",
                            detail=f"{speaker} asserted {prop} at "
                                   f"{scene_id or loc} without a "
                                   f"recorded learning source"))
                # recipients learn asserted propositions via delivery
                if e.get("stance") == "assert":
                    for char in delivered.get(e["event_id"], []):
                        self._learn(char, prop, tick, scene_id)

    def _learn(self, char: str, fact: str, tick: int,
               scene_id: str) -> None:
        known = self.learned.setdefault(char, {})
        if fact not in known:
            known[fact] = (tick, scene_id)


class ConsistencyValidator:
    """Independent stream checks (§10.4)."""

    def check_tick(self, tick_record: dict) -> list[ConsistencyIssue]:
        issues: list[ConsistencyIssue] = []
        tick = tick_record["tick"]
        positions = tick_record.get("positions", {})
        seen_ids: set[str] = set()
        for e in tick_record.get("events", []):
            if e["event_id"] in seen_ids:
                issues.append(ConsistencyIssue(
                    tick=tick, kind="duplicate_event",
                    detail=f"event {e['event_id']} committed twice"))
            seen_ids.add(e["event_id"])
            loc = e.get("location")
            for actor in e.get("actors") or []:
                actual = positions.get(actor)
                if loc and actual and actual != loc:
                    issues.append(ConsistencyIssue(
                        tick=tick, kind="actor_location_mismatch",
                        detail=f"{actor} acts at {loc} but world "
                               f"places them at {actual}"))
        return issues
