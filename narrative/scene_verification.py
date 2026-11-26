"""Post-scene verification (Phase 2, task book 4.7): compare
what the scene plan expected with what the generated text
actually did. Deterministic checks only:

- future_event_reference: a paragraph cites (via event_ids)
  an event that belongs to a LATER scene -- the text used
  material its characters could not have witnessed yet.
- expected_change_missing: a fact-level knowledge change the
  plan expected in this scene whose triggering event is not
  narrated by any paragraph of the scene.
- psychology_stated_not_shown: heuristic regex for narration
  that states a character's psychology instead of showing it
  (labelled heuristic, not a proof).

Diffs are RECORDED, never auto-fixed, and expectations never
override the text: what the text did is the record; the plan
is only the expectation (task book 4.7, final rule).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_PSYCH_PATTERNS = [
    re.compile(r"(感到|感觉到|心中|内心深处|心里).{0,10}"
               r"(恐惧|愤怒|悲伤|喜悦|不安|犹豫|愤慨|绝望)"),
    re.compile(r"(恐惧|愤怒|犹豫|不安).{0,6}(因此|于是).{0,8}决定"),
]

_QUOTES = re.compile(r"「([^」]{2,})」|“([^”]{2,})”"
                     r"|\"([^\"]{2,})\"")


def verify_novel(narrative_dir: Path, novel: dict) -> dict:
    plan = json.loads((narrative_dir / "upgrade" / "plan.json")
                      .read_text(encoding="utf-8"))
    scenes = {s["scene_id"]: s for s in plan["scenes"]}
    order = [s["scene_id"] for s in plan["scenes"]]
    event_scene = {}
    for s in plan["scenes"]:
        for eid in s["event_ids"]:
            event_scene[eid] = s["scene_id"]
    paras_by_scene: dict[str, list[dict]] = {}
    for ch in novel["chapters"]:
        for p in ch["paragraphs"]:
            paras_by_scene.setdefault(p.get("scene_id"),
                                      []).append(p)
    records = []
    total_diffs = 0
    quote_first_seen: dict[str, tuple] = {}
    for sid in order:
        scene = scenes[sid]
        paras = paras_by_scene.get(sid, [])
        diffs = []
        cited = {e for p in paras for e in p.get("event_ids", [])}
        for eid in sorted(cited):
            owner = event_scene.get(eid)
            if owner and order.index(owner) > order.index(sid):
                diffs.append({
                    "kind": "future_event_reference",
                    "event_id": eid, "belongs_to_scene": owner,
                    "severity": "high"})
        cs = scene.get("character_state") or {}
        for exp in cs.get("expected_state_changes", []):
            if exp["field"] == "knowledge" and exp["tier"] == "fact":
                ev = exp["event_id"]
                ev = ev[3:] if ev.startswith("NE-") else ev
                if not any(ev in (p.get("event_ids") or [])
                           for p in paras):
                    diffs.append({
                        "kind": "expected_change_missing",
                        "character_id": exp["character_id"],
                        "event_id": exp["event_id"],
                        "severity": "medium"})
        scene_has_dialogue = any(
            "「" in p.get("text", "") or "“" in p.get("text", "")
            for p in paras)
        for i, p in enumerate(paras):
            text = p.get("text", "")
            if any(rx.search(text) for rx in _PSYCH_PATTERNS):
                diffs.append({
                    "kind": "psychology_stated_not_shown",
                    "paragraph_index": i, "severity": "low",
                    "note": "heuristic match; human judgement "
                            "decides"})
                if scene_has_dialogue and \
                        p.get("level") in ("F2", "F3"):
                    diffs.append({
                        "kind": "emotion_restatement",
                        "paragraph_index": i, "severity": "low",
                        "note": "narration restates an emotion "
                                "in a scene that also carries "
                                "dialogue (heuristic)"})
        for i, p in enumerate(paras):
            for q in _QUOTES.findall(p.get("text", "")):
                q = (q[0] or q[1] or q[2]).strip()
                if len(q) < 6:
                    continue
                if q in quote_first_seen and \
                        quote_first_seen[q] != (sid, i):
                    diffs.append({
                        "kind": "verbatim_dialogue_repeat",
                        "paragraph_index": i, "severity": "low",
                        "quote_prefix": q[:20],
                        "first_seen": quote_first_seen[q][0],
                        "note": "the same quoted line appears "
                                "again verbatim"})
                else:
                    quote_first_seen.setdefault(q, (sid, i))
        total_diffs += len(diffs)
        records.append({
            "scene_id": sid, "paragraphs": len(paras),
            "expected_changes":
                len(cs.get("expected_state_changes", [])),
            "diffs": diffs,
            "verdict": "ok" if not diffs else "diffs_recorded"})
    return {"scenes": records,
            "summary": {"scenes": len(records),
                        "total_diffs": total_diffs,
                        "by_kind": {
                            k: sum(1 for r in records
                                   for d in r["diffs"]
                                   if d["kind"] == k)
                            for k in ("future_event_reference",
                                      "expected_change_missing",
                                      "psychology_stated_not_shown",
                                      "emotion_restatement",
                                      "verbatim_dialogue_repeat")}},
            "note": "Deterministic plan-vs-text verification. "
                    "Diffs are recorded, never auto-fixed; the "
                    "text is the record, the plan the expectation."}


def write_verification(narrative_dir: Path, novel: dict,
                       out_dir: Path | None = None) -> Path:
    data = verify_novel(narrative_dir, novel)
    out = out_dir or (narrative_dir / "phase2")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "scene_verification.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path
