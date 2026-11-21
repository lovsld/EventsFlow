"""Continuity Validator (task book §12): independent consistency
checks over a finished run. Errors are contradictions with the
committed record; warnings are claims the record cannot verify.
Basis-free propositions (proposals, bluffs — the scenario declares
them basis_required=false) are exempt from knowledge checks: they
are originations, not knowledge claims.
"""
from __future__ import annotations

from v03.graph.models import EventGraph


class ContinuityValidator:
    def __init__(self, basis_free_props: set[str] | None = None,
                 known_facts: set[str] | None = None,
                 dependencies: list[tuple[str, str]] | None = None):
        self.basis_free = basis_free_props or set()
        self.known_facts = known_facts or set()
        self.dependencies = dependencies or []  # (dependent, source)
        self.issues: list[dict] = []

    def _add(self, severity: str, kind: str, detail: str,
             tick: int = 0) -> None:
        self.issues.append({"severity": severity, "kind": kind,
                            "tick": tick, "detail": detail})

    def validate(self, tick_records: list[dict], graph: EventGraph,
                 thread_first_tick: dict[str, int]) -> dict:
        self._timeline(tick_records)
        self._knowledge(graph)
        self._objects(tick_records)
        self._dependencies(thread_first_tick)
        errors = sum(1 for i in self.issues
                     if i["severity"] == "error")
        return {"issues": self.issues, "errors": errors,
                "warnings": len(self.issues) - errors}

    def _timeline(self, tick_records) -> None:
        last_num, last_tick = -1, -1
        for record in tick_records:
            if record["tick"] < last_tick:
                self._add("error", "tick_regression",
                          f"tick {record['tick']} after {last_tick}",
                          record["tick"])
            last_tick = record["tick"]
            for e in record["events"]:
                try:
                    num = int(e["event_id"][1:])
                except (ValueError, IndexError):
                    continue
                if num <= last_num:
                    self._add("error", "event_id_order",
                              f"{e['event_id']} not increasing",
                              record["tick"])
                last_num = num

    def _knowledge(self, graph: EventGraph) -> None:
        for n in graph.nodes:
            if n.epistemic != "unverified" or not n.proposition:
                continue
            if n.proposition in self.basis_free:
                continue
            if n.proposition in self.known_facts:
                self._add("error", "knowledge_use_before_learning",
                          f"{n.actors} asserted {n.proposition} "
                          f"without a learning source", n.tick)
            else:
                self._add("warning", "unverifiable_assertion",
                          f"{n.actors} asserted {n.proposition} "
                          f"(not a tracked fact)", n.tick)

    def _objects(self, tick_records) -> None:
        holder: dict[str, str] = {}
        for record in tick_records:
            for e in record["events"]:
                if e.get("type") != "search":
                    continue
                finder = (e.get("actors") or [""])[0]
                for obj in e.get("found") or []:
                    prev = holder.get(obj)
                    if prev and prev != finder:
                        self._add("error", "duplicate_find",
                                  f"{obj} found by {finder} while "
                                  f"held by {prev}", record["tick"])
                    holder[obj] = finder

    def _dependencies(self, thread_first_tick: dict[str, int]) -> None:
        for dependent, source in self.dependencies:
            d = thread_first_tick.get(dependent)
            s = thread_first_tick.get(source)
            if d is not None and s is not None and d < s:
                self._add("error", "dependency_inversion",
                          f"{dependent} developed at {d} before its "
                          f"dependency {source} at {s}", d)
