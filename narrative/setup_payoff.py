"""Setup / payoff registry (upgrade Phase 4; design §4).

The discipline that matters here is the three-level evidence
ladder, kept explicit on every record:
  discovery     — someone found / first learned the thing;
  corroboration — an independent second source confirms it;
  resolution    — the question itself is answered by direct
                  evidence (find or experienced assertion).
Finding an object is level 1. It is never, by itself, a payoff.
A record reaches `payoff` only with a resolution event.
"""
from __future__ import annotations

import json
from pathlib import Path

from narrative.models.story import CausalEdge, SetupPayoffRecord


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_setup_payoff(run_dir: Path, narrative_dir: Path,
                       v03_dir: Path | None,
                       config_path: Path | None) -> dict:
    upgrade = narrative_dir / "upgrade"
    events = _load(upgrade / "narrative_events.json")
    observations = _load(narrative_dir / "observations.json")
    storylines = _load(narrative_dir / "storylines.json")
    meta = _load(run_dir / "run_meta.json")
    last_tick = max((e["tick"] for e in events), default=0)

    state = None
    if v03_dir is not None and (v03_dir / "story_state.json").exists():
        state = _load(v03_dir / "story_state.json")
    threads = (state or {}).get("threads") or {}

    # fact -> learning events by route
    learns: dict[str, list[dict]] = {}
    for e in events:
        for k in e.get("knowledge_changes", []):
            if k["change"] == "learned":
                learns.setdefault(k["fact"], []).append(
                    {"ne": e["narrative_event_id"], "tick": e["tick"],
                     "via": k["via"], "character": k["character"]})
    mentions: dict[str, list[str]] = {}
    for o in observations:
        if o.get("proposition"):
            mentions.setdefault(o["proposition"], []).append(
                o["obs_id"])

    def ladder(fact: str, answered_at: int | None) -> dict:
        ls = learns.get(fact, [])
        discovery = [x for x in ls if x["via"] in ("find",
                                                   "experienced")]
        corroboration = []
        seen_chars = set()
        for x in sorted(ls, key=lambda y: y["tick"]):
            if x["character"] in seen_chars:
                continue
            if seen_chars:
                corroboration.append(x)
            seen_chars.add(x["character"])
        resolution = []
        if answered_at is not None:
            resolution = [x for x in discovery
                          if x["tick"] <= answered_at][:1]
        return {"discovery": discovery,
                "corroboration": corroboration,
                "resolution": resolution}

    records: list[dict] = []
    edges: list[dict] = []
    diagnostics: dict[str, dict] = {}
    n = 0

    def add(record: SetupPayoffRecord, lad: dict,
            note: str = "") -> None:
        records.append(record.model_dump(mode="json"))
        diagnostics[record.setup_id] = {
            "discovery_events": [x["ne"] for x in lad["discovery"]],
            "corroboration_events": [x["ne"] for x in
                                     lad["corroboration"]],
            "resolution_events": [x["ne"] for x in lad["resolution"]],
            "note": note}
        if record.status == "payoff" and record.setup_event_ids \
                and record.actual_payoff_event_ids:
            setup_ne = record.setup_event_ids[0]
            payoff_ne = record.actual_payoff_event_ids[0]
            if setup_ne == payoff_ne:
                diagnostics[record.setup_id]["note"] = (
                    "setup and payoff coincide in one event; "
                    "no foreshadow edge is emitted")
                return
            edges.append(CausalEdge(
                **{"from": setup_ne, "to": payoff_ne},
                relation="FORESHADOWS",
                evidence_event_ids=[setup_ne.removeprefix("NE-"),
                                    payoff_ne.removeprefix("NE-")],
                confidence=0.9, source="setup_payoff").model_dump(
                    mode="json", by_alias=True))
            edges.append(CausalEdge(
                **{"from": payoff_ne, "to": setup_ne},
                relation="PAYOFF_OF",
                evidence_event_ids=[setup_ne.removeprefix("NE-"),
                                    payoff_ne.removeprefix("NE-")],
                confidence=0.9, source="setup_payoff").model_dump(
                    mode="json", by_alias=True))

    # ---- kind: question (from v03 threads) ----
    for tid, th in threads.items():
        for q in th.get("questions", []):
            n += 1
            fact = q.get("answered_by")
            lad = ladder(fact, q.get("answered_at"))
            setup_events = [f"NE-{i}" for i in
                            mentions.get(fact, [])[:1]]
            if not setup_events:
                # fallback: first utterance of a claim carrying
                # this fact (e.g. the question was asked before
                # anyone asserted the fact)
                claim_ids = {cid for cid, cd in
                             (meta.get("scenario", {}).get(
                                 "claims") or {}).items()
                             if cd.get("proposition") == fact}
                for o in observations:
                    if o.get("claim") in claim_ids:
                        setup_events = [f"NE-{o['obs_id']}"]
                        break
            rementions = len(mentions.get(fact, []))
            if lad["resolution"]:
                status = "payoff"
            elif lad["corroboration"] or rementions >= 3:
                status = "reinforcement"
            elif setup_events and events and \
                    mentions.get(fact) and \
                    obs_tick(observations, mentions[fact][0]) \
                    >= last_tick:
                status = "setup"
            else:
                status = "unresolved"
            add(SetupPayoffRecord(
                setup_id=f"SP-Q{n:02d}", kind="question",
                description=q.get("text", ""),
                setup_event_ids=setup_events,
                related_thread_id=tid,
                expected_payoff=f"问题被直接证据回答（{fact}）",
                actual_payoff_event_ids=[x["ne"] for x in
                                         lad["resolution"]],
                status=status,
                evidence=[x["ne"] for x in lad["discovery"]] +
                         [x["ne"] for x in lad["corroboration"]]),
                lad)

    # ---- kind: object (discovery is level 1, never the payoff)
    obj_threads: dict[str, str] = {}
    if config_path is not None and config_path.exists():
        cfg = _load(config_path)
        for th in cfg.get("initial_threads", []):
            for obj in th.get("objects", []):
                obj_threads[obj] = th.get("thread_id", "")
    for obj, od in (meta.get("scenario", {}).get("objects")
                    or {}).items():
        fact = od.get("fact_belief")
        finds = [o for o in observations if o.get("found") and
                 obj in o["found"] and
                 (o.get("features") or {}).get(
                     "first_find_of_object")]
        if not finds:
            continue
        n += 1
        find_ne = f"NE-{finds[0]['obs_id']}"
        # payoff requires the object's fact to resolve its thread
        # question by direct evidence
        answered_at = None
        for th in threads.values():
            for q in th.get("questions", []):
                if q.get("answered_by") == fact:
                    answered_at = q.get("answered_at")
        lad = ladder(fact, answered_at) if fact else {
            "discovery": [], "corroboration": [], "resolution": []}
        status = "payoff" if lad["resolution"] else (
            "reinforcement" if lad["corroboration"] else
            "unresolved")
        add(SetupPayoffRecord(
            setup_id=f"SP-O{n:02d}", kind="object",
            description=f"物件 {obj} 被发现",
            setup_event_ids=[find_ne],
            related_thread_id=obj_threads.get(obj, ""),
            expected_payoff=f"物件揭示的事实（{fact}）被证实并"
                            f"解决关联问题",
            actual_payoff_event_ids=[x["ne"] for x in
                                     lad["resolution"]],
            status=status, evidence=[find_ne]),
            lad,
            note="discovery is level 1 evidence only; status "
                 "payoff requires the linked question to be "
                 "answered by direct evidence")

    # ---- kind: bluff (basis-free claims named as bluffs) ----
    for o in observations:
        if o.get("claim") and "bluff" in o["claim"] and \
                o.get("type") in ("speak", "confront"):
            n += 1
            add(SetupPayoffRecord(
                setup_id=f"SP-B{n:02d}", kind="bluff",
                description=f"虚张声势：{o.get('proposition')}",
                setup_event_ids=[f"NE-{o['obs_id']}"],
                expected_payoff="听者识破或事实澄清",
                status="misdirection",
                evidence=[f"NE-{o['obs_id']}"]),
                {"discovery": [], "corroboration": [],
                 "resolution": []},
                note="a bluff is a misdirection by construction; "
                     "it is never 'paid off' by an object find")
            break  # one record per run for the bluff line

    return {"records": records, "graph_edges": edges,
            "diagnostics": diagnostics,
            "line_states": {l["line_id"]: l["state"]
                            for l in storylines.get("lines", [])}}


def obs_tick(observations: list[dict], obs_id: str) -> int:
    for o in observations:
        if o["obs_id"] == obs_id:
            return o["tick"]
    return 0


def write_setup_payoff(run_dir: Path, narrative_dir: Path,
                       v03_dir: Path | None,
                       config_path: Path | None,
                       out_dir: Path) -> dict:
    result = build_setup_payoff(run_dir, narrative_dir, v03_dir,
                                config_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "setup_payoff.json").write_text(json.dumps(
        {k: v for k, v in result.items() if k != "graph_edges"},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "foreshadow_edges.json").write_text(json.dumps(
        {"edges": result["graph_edges"]}, ensure_ascii=False,
        indent=1), encoding="utf-8")
    return result
