"""Unified causal graph (upgrade Phase 2; design §2).

Edges are built from two existing evidence sources — the
Extractor's E1-E7 edges and the v03 event graph — translated
into the unified vocabulary, then passed through an independent
verifier that re-checks every edge against the observations.
E1 (same-topic chain) and E7 (search sequence) are
organisational only: temporal/topic adjacency is NOT causality,
so they never enter this graph. MOTIVATED_BY is emitted by no
rule here: the narrative inputs carry no deterministic
motivation evidence, and the task book forbids inventing it.
FORESHADOWS / PAYOFF_OF arrive with Phase 4's setup registry.
"""
from __future__ import annotations

import json
from pathlib import Path

from narrative.models.story import CausalEdge

# extractor type -> (relation, confidence); edge direction in the
# extractor is earlier -> later; the unified edge reads
# from=<holder of the relation>, to=<the other end>, e.g.
# from=effect, to=cause, relation=CAUSED_BY.
_EXTRACTOR_MAP = {"E2": ("CAUSED_BY", 0.9), "E3": ("RESPONDED_TO", 0.9),
                  "E4": ("ENABLED_BY", 0.95), "E6": ("ENABLED_BY", 0.6)}
_V03_MAP = {"reveals": "REVEALED_BY", "enables": "ENABLED_BY",
            "escalates": "ESCALATED_BY", "causes": "CAUSED_BY",
            "depends_on": "CAUSED_BY", "contradicts": "CONTRADICTS",
            "prevents": "BLOCKED_BY"}
_TIME_ORDERED = {"CAUSED_BY", "ENABLED_BY", "RESPONDED_TO",
                 "REVEALED_BY", "ESCALATED_BY", "RESOLVED_BY",
                 "BLOCKED_BY"}


def _ne(event_id: str) -> str:
    return f"NE-{event_id}"


def build_causal_edges(narrative_dir: Path,
                       v03_dir: Path | None) -> tuple[list[CausalEdge],
                                                       dict]:
    storylines = json.loads((narrative_dir / "storylines.json")
                            .read_text(encoding="utf-8"))
    edges: dict[tuple[str, str, str], CausalEdge] = {}
    stats = {"extractor_used": 0, "extractor_skipped_org": 0,
             "v03_used": 0, "v03_unmapped": 0}

    def add(frm: str, to: str, relation: str, evidence: list[str],
            confidence: float, source: str) -> None:
        key = (frm, to, relation)
        cur = edges.get(key)
        if cur is None or confidence > cur.confidence:
            edges[key] = CausalEdge(**{"from": frm, "to": to},
                                    relation=relation,
                                    evidence_event_ids=evidence,
                                    confidence=confidence,
                                    source=source)

    for line in storylines.get("lines", []):
        for e in line.get("edges", []):
            types = e.get("types", [])
            mapped = [t for t in types if t in _EXTRACTOR_MAP]
            if not mapped:
                stats["extractor_skipped_org"] += 1
                continue
            relation, conf = _EXTRACTOR_MAP[mapped[0]]
            src, dst = e["from"], e["to"]
            if "E5" in types:
                add(_ne(dst), _ne(src), "CONTRADICTS", [src, dst],
                    0.9, "extractor")
            add(_ne(dst), _ne(src), relation, [src, dst], conf,
                "extractor")
            stats["extractor_used"] += 1
    # E5 edges live on fragments too in some runs; lines carry the
    # strong components, fragments are checked for denials.
    for frag in storylines.get("fragments", []):
        for e in frag.get("edges", []):
            if "E5" in e.get("types", []):
                add(_ne(e["to"]), _ne(e["from"]), "CONTRADICTS",
                    [e["from"], e["to"]], 0.9, "extractor")
                stats["extractor_used"] += 1

    if v03_dir is not None and (v03_dir / "event_graph.json").exists():
        graph = json.loads((v03_dir / "event_graph.json")
                           .read_text(encoding="utf-8"))
        for e in graph.get("edges", []):
            relation = _V03_MAP.get(e["relation"])
            if relation is None:
                stats["v03_unmapped"] += 1
                continue
            src, dst = e["source"], e["target"]
            if relation == "CONTRADICTS":
                add(_ne(dst), _ne(src), relation, [src, dst], 0.9,
                    "v03")
            else:
                add(_ne(dst), _ne(src), relation, [src, dst], 0.9,
                    "v03")
            stats["v03_used"] += 1
    return sorted(edges.values(),
                  key=lambda e: (e.from_id, e.to_id, e.relation)), stats


def verify_causal_edges(edges: list[CausalEdge],
                        observations: list[dict],
                        narrative_events: list[dict]
                        ) -> tuple[list[CausalEdge], dict]:
    """Independent re-check against observations. An edge is
    rejected when its claimed evidence does not exist or its
    temporal order is impossible; CAUSED_BY additionally requires
    a delivery or find among the evidence events — sharing a
    proposition is never enough."""
    obs_by_id = {o["obs_id"]: o for o in observations}
    tick_of = {e["narrative_event_id"]: e["tick"]
               for e in narrative_events}
    report = {"verified": 0, "unverified": 0, "rejected": 0,
              "reasons": []}
    out: list[CausalEdge] = []
    for edge in edges:
        frm = edge.from_id.removeprefix("NE-")
        to = edge.to_id.removeprefix("NE-")
        reason = None
        if frm not in obs_by_id or to not in obs_by_id:
            reason = "endpoint not an observed event"
        elif any(ev not in obs_by_id
                 for ev in edge.evidence_event_ids):
            reason = "evidence event missing"
        elif edge.relation in _TIME_ORDERED and \
                tick_of[edge.to_id] > tick_of[edge.from_id]:
            reason = "cause later than effect"
        elif edge.relation == "CONTRADICTS":
            d = obs_by_id.get(frm, {})
            a = obs_by_id.get(to, {})
            if not (d.get("stance") == "deny" and
                    a.get("proposition") and
                    a.get("proposition") == d.get("proposition")):
                reason = "not a deny-vs-assert pair on one " \
                         "proposition"
        elif edge.relation == "CAUSED_BY":
            ev_obs = [obs_by_id[ev] for ev in
                      edge.evidence_event_ids if ev in obs_by_id]
            supported = any(
                (o["type"] == "search" and o.get("found")) or
                (o["type"] in ("speak", "ask", "confront") and
                 o.get("delivered_to")) for o in ev_obs)
            if not supported:
                reason = "no delivery or find evidence " \
                         "(proposition-only link refused)"
        if reason:
            edge = edge.model_copy(
                update={"verification_status": "rejected"})
            report["rejected"] += 1
            report["reasons"].append(
                {"edge": [edge.from_id, edge.to_id, edge.relation],
                 "reason": reason})
        else:
            report["verified"] += 1
        out.append(edge)
    return out, report


def trace_causes(edges: list[CausalEdge], ne_id: str,
                 max_depth: int = 12) -> list[str]:
    """All causal ancestors of an event (CAUSED_BY / ENABLED_BY /
    REVEALED_BY), nearest first, cycle-safe."""
    causes: dict[str, list[str]] = {}
    for e in edges:
        if e.relation in ("CAUSED_BY", "ENABLED_BY",
                          "REVEALED_BY") and \
                e.verification_status == "verified":
            causes.setdefault(e.from_id, []).append(e.to_id)
    seen: list[str] = []
    frontier = list(causes.get(ne_id, []))
    depth = 0
    while frontier and depth < max_depth:
        nxt: list[str] = []
        for node in frontier:
            if node not in seen:
                seen.append(node)
                nxt.extend(causes.get(node, []))
        frontier = nxt
        depth += 1
    return seen


def write_causal_graph(run_dir: Path, narrative_dir: Path,
                       v03_dir: Path | None, out_dir: Path) -> dict:
    from narrative.models.build import build_narrative_events
    events = build_narrative_events(run_dir, narrative_dir)
    event_dicts = [e.model_dump(mode="json") for e in events]
    observations = json.loads((narrative_dir / "observations.json")
                              .read_text(encoding="utf-8"))
    edges, stats = build_causal_edges(narrative_dir, v03_dir)
    edges, report = verify_causal_edges(edges, observations,
                                        event_dicts)
    links: dict[str, list[dict]] = {}
    for e in edges:
        if e.verification_status != "verified":
            continue
        links.setdefault(e.from_id, []).append({
            "relation": e.relation, "target_ne_id": e.to_id,
            "evidence_event_ids": e.evidence_event_ids,
            "confidence": e.confidence, "source": e.source,
            "verification_status": e.verification_status})
    for d in event_dicts:
        d["causal_links"] = links.get(d["narrative_event_id"], [])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "narrative_events.json").write_text(json.dumps(
        event_dicts, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (out_dir / "causal_graph.json").write_text(json.dumps(
        {"edges": [e.model_dump(mode="json", by_alias=True)
                   for e in edges],
         "build_stats": stats, "verification": report},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return {"edges": len(edges), **stats,
            "verified": report["verified"],
            "rejected": report["rejected"]}
