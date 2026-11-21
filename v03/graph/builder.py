"""Deterministic event-graph derivation (task book §11).

Every edge names its derivation rule and carries the evidence it
rests on (delivery records, find events, thread bookkeeping,
candidate causal_basis). A relation with no evidence is not
derived — e.g. in the tavern canonical run `contradicts`,
`conceals` and `prevents` legitimately yield zero edges because
the denial claim carries no proposition and no refuse events
occur; the rules exist and are covered by synthetic tests.
"""
from __future__ import annotations

from v03.graph.models import EventGraph, GraphEdge, GraphNode

SPEECH = ("speak", "ask", "confront")


class GraphBuilder:
    def __init__(self, run_id: str, object_facts: dict[str, dict],
                 initial_knowledge: dict[str, list[str]] | None = None,
                 scene_by_event: dict[str, str] | None = None):
        self.graph = EventGraph(run_id=run_id)
        self.object_facts = object_facts
        self.direct_facts = {f for v in object_facts.values()
                             for f in v.get("direct", [])}
        self.hearsay_facts = {f for v in object_facts.values()
                              for f in v.get("hearsay", [])}
        # char -> fact -> {tick, source, kind}
        self.learned: dict[str, dict[str, dict]] = {
            c: {f: {"tick": 0, "source": "", "kind": "initial"}
                for f in facts}
            for c, facts in (initial_knowledge or {}).items()}
        self.fact_source: dict[str, str] = {}
        self.scene_by_event = scene_by_event or {}
        self._asserted_after_learn: set[tuple[str, str]] = set()
        self._last_on_prop: dict[str, dict] = {}
        self._asks: list[dict] = []

    # ---------- helpers ----------
    def _learn(self, char: str, fact: str, tick: int, source: str,
               kind: str) -> None:
        known = self.learned.setdefault(char, {})
        if fact not in known:
            known[fact] = {"tick": tick, "source": source, "kind": kind}
        if fact not in self.fact_source and source:
            self.fact_source[fact] = source

    def _edge(self, source: str, target: str, relation: str,
              rule: str, **evidence) -> None:
        if source and target and source != target:
            self.graph.edges.append(GraphEdge(
                source=source, target=target, relation=relation,
                rule=rule, evidence=evidence))

    def _epistemic(self, event: dict) -> str:
        prop = event.get("proposition")
        actors = event.get("actors") or []
        if event.get("type") not in SPEECH or not prop or not actors:
            return "objective"
        if event.get("stance") == "deny":
            return "false_statement"
        speaker = actors[0]
        if prop in self.hearsay_facts:
            return "rumored"
        known = self.learned.get(speaker, {}).get(prop)
        if known is None:
            return "unverified"
        return {"find": "observed", "experienced": "observed",
                "initial": "prior_knowledge",
                "delivery": "reported"}.get(known["kind"], "reported")

    # ---------- main ----------
    def build(self, tick_records: list[dict],
              story_state=None) -> EventGraph:
        for record in tick_records:
            self._process(record)
        if story_state is not None:
            self._resolves(story_state)
        node_ids = {n.event_id for n in self.graph.nodes}
        self.graph.edges = [e for e in self.graph.edges
                            if e.source in node_ids
                            and e.target in node_ids]
        return self.graph

    def _process(self, record: dict) -> None:
        tick = record["tick"]
        delivered: dict[str, list[str]] = {}
        for d in record.get("deliveries", []):
            for eid in d.get("delivered_event_ids", []):
                delivered.setdefault(eid, []).append(d["agent_id"])
        for e in record.get("events", []):
            self.graph.nodes.append(GraphNode(
                event_id=e["event_id"], tick=tick,
                type=e.get("type", ""), location=e.get("location") or "",
                actors=list(e.get("actors") or []),
                proposition=e.get("proposition"),
                stance=e.get("stance"),
                epistemic=self._epistemic(e),
                scene_id=self.scene_by_event.get(e["event_id"], "")))
            self._relations(e, tick, delivered)

    def _relations(self, e: dict, tick: int,
                   delivered: dict[str, list[str]]) -> None:
        eid = e["event_id"]
        actors = e.get("actors") or []
        if e.get("source") == "story_engine":
            for basis in e.get("causal_basis") or []:
                self._edge(eid, basis, "depends_on", "G-DEPENDS",
                           via="candidate_causal_basis")
        if e.get("type") == "search" and actors:
            finder = actors[0]
            for obj in e.get("found") or []:
                facts = self.object_facts.get(obj, {})
                for fact in (facts.get("direct", []) +
                             facts.get("hearsay", [])):
                    self._learn(finder, fact, tick, eid, "find")
            return
        if e.get("type") == "refuse" and actors:
            self._refuse(e, tick)
            return
        if e.get("type") not in SPEECH or not actors:
            return
        speaker = actors[0]
        prop = e.get("proposition")
        if e.get("type") == "ask":
            self._asks.append({"event": eid, "tick": tick,
                               "asker": speaker,
                               "addressee": actors[1] if len(actors) > 1
                               else ""})
        if not prop:
            return
        if e.get("_direct") and e.get("stance") == "assert":
            self._learn(speaker, prop, tick, eid, "experienced")
        if e.get("stance") == "assert":
            self._assert_edges(e, tick, delivered)
        elif e.get("stance") == "deny":
            self._deny_edges(e, tick)

    def _assert_edges(self, e: dict, tick: int,
                      delivered: dict[str, list[str]]) -> None:
        eid = e["event_id"]
        speaker = (e.get("actors") or [""])[0]
        prop = e["proposition"]
        known = self.learned.get(speaker, {}).get(prop)
        key = (speaker, prop)
        if known and key not in self._asserted_after_learn:
            self._asserted_after_learn.add(key)
            if known["kind"] == "find":
                self._edge(known["source"], eid, "enables", "G-ENABLE",
                           via_fact=prop, learner=speaker)
            elif known["kind"] == "delivery":
                self._edge(known["source"], eid, "causes", "G-CAUSE",
                           via_fact=prop, learner=speaker)
        recipients = [c for c in delivered.get(eid, [])
                      if c != speaker]
        new_audience = [c for c in recipients
                        if prop not in self.learned.get(c, {})]
        source_event = self.fact_source.get(prop)
        if source_event and source_event != eid and new_audience:
            self._edge(source_event, eid, "reveals", "G-REVEAL",
                       via_fact=prop, new_audience=new_audience)
        prior = self._last_on_prop.get(prop)
        if prior and prior["speaker"] != speaker and (
                e.get("type") == "confront"
                or prior["type"] == "confront"):
            self._edge(prior["event"], eid, "escalates", "G-ESCALATE",
                       via_fact=prop)
        self._last_on_prop[prop] = {"event": eid, "speaker": speaker,
                                    "type": e.get("type")}
        for char in recipients:
            if char != speaker:  # nobody learns their own claim
                self._learn(char, prop, tick, eid, "delivery")

    def _deny_edges(self, e: dict, tick: int) -> None:
        eid = e["event_id"]
        speaker = (e.get("actors") or [""])[0]
        prop = e["proposition"]
        prior = self._last_on_prop.get(prop)
        if prior and prior["speaker"] != speaker:
            self._edge(eid, prior["event"], "contradicts", "G-CONTRA",
                       via_fact=prop)
        known = self.learned.get(speaker, {}).get(prop)
        if known:
            self._edge(known["source"], eid, "conceals", "G-CONCEAL",
                       via_fact=prop, denier=speaker)
        self._last_on_prop[prop] = {"event": eid, "speaker": speaker,
                                    "type": e.get("type")}

    def _refuse(self, e: dict, tick: int) -> None:
        eid = e["event_id"]
        refuser = (e.get("actors") or [""])[0]
        for ask in reversed(self._asks):
            if ask["addressee"] == refuser and \
                    tick - ask["tick"] <= 2:
                self._edge(eid, ask["event"], "prevents", "G-PREVENT",
                           via="refusal_blocks_disclosure")
                break
        prop = e.get("proposition")
        if prop:
            known = self.learned.get(refuser, {}).get(prop)
            if known:
                self._edge(known["source"], eid, "conceals",
                           "G-CONCEAL", via_fact=prop, denier=refuser)

    def _resolves(self, story_state) -> None:
        # ThreadQuestion.answered_by records the answering FACT (a
        # belief name), answered_at its tick; the answering event
        # is the thread's related event at that tick.
        tick_of = {n.event_id: n.tick for n in self.graph.nodes}
        for thread in story_state.threads.values():
            if not thread.related_events:
                continue
            first = thread.related_events[0]
            for q in thread.questions:
                if q.answered_at is None:
                    continue
                answering = next(
                    (eid for eid in thread.related_events
                     if tick_of.get(eid) == q.answered_at), None)
                if answering:
                    self._edge(answering, first, "resolves",
                               "G-RESOLVE", thread=thread.thread_id,
                               question=q.id, fact=q.answered_by)
