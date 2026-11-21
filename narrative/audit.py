"""Deterministic fidelity audit for Writer drafts (design §6.3).

Checks, all mechanical:
1. every non-connective paragraph maps to >= 1 existing event id;
2. every quoted span is a verbatim substring of the text of one of the
   paragraph's referenced events (quote fidelity target: 100%);
3. coverage: form A must cover every plot/structure event; form B
   must cover every event of every storyline (fragments exempt), and
   any uncovered plot event must be listed in the omissions with a
   reason; omitted events must not belong to a storyline;
4. temporal monotonicity: paragraph reference points (earliest event
   order index per paragraph) never decrease (no time reordering);
5. unsupported-fact paragraphs (non-connective, no mapping) == 0.

Semantic fabrication inside a correctly-mapped paragraph cannot be
caught mechanically; that residue is for the human spot-check and is
stated as such in every audit report.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

QUOTE_RE = re.compile(r"“([^”]{2,})”|「([^」]{2,})」")


def audit_draft(trace: dict, observations: list[dict],
                storylines: dict) -> dict:
    by_id = {o["obs_id"]: o for o in observations}
    order = {o["obs_id"]: i for i, o in enumerate(observations)}
    line_members: set[str] = set()
    for line in storylines["lines"]:
        line_members |= set(line["event_ids"])
    violations: list[str] = []
    covered: set[str] = set()
    quote_total = 0
    quote_ok = 0
    ref_points: list[int] = []
    unsupported = 0

    for para in trace["paragraphs"]:
        pid = para["id"]
        ids = para.get("event_ids") or []
        mode = para.get("mode")
        if mode not in ("quote", "paraphrase", "connective"):
            violations.append(f"{pid}: invalid mode {mode!r}")
        for eid in ids:
            if eid not in by_id:
                violations.append(f"{pid}: unknown event id {eid}")
        covered |= set(ids)
        if mode != "connective" and not ids:
            unsupported += 1
            violations.append(f"{pid}: factual paragraph without mapping")
        if ids and all(e in order for e in ids):
            ref_points.append(min(order[e] for e in ids))
        # quotes must be verbatim in a referenced event's text
        texts = [by_id[e].get("text") or "" for e in ids if e in by_id]
        for m in QUOTE_RE.finditer(para["text"]):
            span = m.group(1) or m.group(2)
            quote_total += 1
            if any(span in t for t in texts):
                quote_ok += 1
            else:
                violations.append(
                    f"{pid}: quote not verbatim in referenced events: "
                    f"{span[:30]!r}")

    if ref_points != sorted(ref_points):
        violations.append("temporal order violated: paragraph reference "
                          "points decrease (time reordering)")

    omissions = {o["event_id"]: o.get("reason", "")
                 for o in trace.get("omitted", [])}
    for eid in omissions:
        if eid not in by_id:
            violations.append(f"omission lists unknown event {eid}")
        if eid in line_members:
            violations.append(f"omitted event {eid} belongs to a storyline")

    form = trace["meta"]["form"]
    plot_events = {o["obs_id"] for o in observations
                   if o["narrative_role"] in ("plot", "structure")}
    if form == "A":
        missing = plot_events - covered
        if missing:
            violations.append(f"form A coverage gap: {sorted(missing)}")
    else:
        missing_main = line_members - covered
        if missing_main:
            violations.append(
                f"form B main-line coverage gap: {sorted(missing_main)}")
        unaccounted = (plot_events - covered) - set(omissions)
        if unaccounted:
            violations.append(
                f"plot events neither covered nor omitted with reason: "
                f"{sorted(unaccounted)}")

    result = {
        "form": form,
        "paragraphs": len(trace["paragraphs"]),
        "unsupported_fact_paragraphs": unsupported,
        "quotes_checked": quote_total,
        "quotes_verbatim": quote_ok,
        "quote_fidelity": (quote_ok / quote_total) if quote_total else 1.0,
        "covered_events": len(covered),
        "line_events_total": len(line_members),
        "line_events_covered": len(line_members & covered),
        "violations": violations,
        "pass": not violations and unsupported == 0,
        "limitation": "Semantic fabrication inside a correctly mapped "
                      "paragraph is not mechanically detectable; human "
                      "spot-check remains necessary.",
    }
    return result


def audit_run(narrative_dir: Path, form: str) -> dict:
    trace = json.loads((narrative_dir / f"trace_{form}.json")
                       .read_text(encoding="utf-8"))
    observations = json.loads((narrative_dir / "observations.json")
                              .read_text(encoding="utf-8"))
    storylines = json.loads((narrative_dir / "storylines.json")
                            .read_text(encoding="utf-8"))
    return audit_draft(trace, observations, storylines)
