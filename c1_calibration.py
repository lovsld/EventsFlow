"""V0.2-C C1 calibration experiment (design 2.8, pre-registered).

Grid: DEP in {0.4, 0.6, 0.8} x WAIT in {0.15, 0.3} x SWITCH_MARGIN
in {0.05, 0.10, 0.20} = 18 combos. Screening set per combo: clinic
seeds {42, 7} (24 ticks) + tavern seeds {42, 7} (20 ticks).

Pre-registered winner criteria (design 2.8):
  1. clinic BOTH seeds: brief_nurse becomes pursuit before
     ensure_readiness AND completes (goal_changes evidence);
  2. tavern invariants: soldier's anonymous_letter_exists belief
     ends at 1.0 / experienced, mayor_embezzled inside the B band,
     and Goal Switching Rate <= 0.25 pursuit changes per decision;
  3. warehouse B1-class trajectories not degraded (checked for the
     winner on the confirmation set, not the screen).
Winner among screen-passing combos: lowest tavern switching rate,
then smallest DEP, then smallest WAIT, then margin closest to 0.10.

Usage: .venv/bin/python c1_calibration.py [screen|confirm DEP WAIT MARGIN]
Results are appended to runs_v02/calib_c1/results.jsonl and printed.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3
import sys
from pathlib import Path

from v02 import goal_scheduler
from v02.goal_scheduler import SchedulerConfig
from v02.runner import run_simulation

ROOT = Path(__file__).resolve().parent
OUT = Path("/tmp/calib_c1")
CLINIC_WORLD = json.loads(
    (ROOT / "scenarios" / "scenario_c_clinic.json").read_text(encoding="utf-8"))
WAREHOUSE_WORLD = json.loads(
    (ROOT / "scenarios" / "warehouse_key_world.json").read_text(encoding="utf-8"))


def _goal_changes(run_dir: Path, character: str) -> list[dict]:
    db = sqlite3.connect(f"file:{run_dir}/acp.sqlite3?mode=ro", uri=True)
    rows = db.execute(
        "select tick, goal_key, kind, reason from goal_changes "
        "where character_id=? order by id", (character,)).fetchall()
    db.close()
    return [{"tick": t, "goal": g, "kind": k, "reason": r}
            for t, g, k, r in rows]


def _counts(run_dir: Path) -> dict:
    db = sqlite3.connect(f"file:{run_dir}/acp.sqlite3?mode=ro", uri=True)
    decisions = db.execute(
        "select count(*) from agent_decisions where error is null"
    ).fetchone()[0]
    switches = db.execute(
        "select count(*) from goal_changes where kind='pursuit_changed'"
    ).fetchone()[0]
    starvations = db.execute(
        "select count(*) from goal_changes "
        "where kind='starvation_protection'").fetchone()[0]
    db.close()
    return {"decisions": decisions, "switches": switches,
            "starvations": starvations}


def _final_beliefs(run_dir: Path, character: str) -> dict:
    state = json.loads((run_dir / "characters" / character
                        / "state.json").read_text(encoding="utf-8"))
    return {b["key"]: b for b in state["beliefs"]}


def _final_goals(run_dir: Path, character: str) -> dict:
    state = json.loads((run_dir / "characters" / character
                        / "state.json").read_text(encoding="utf-8"))
    return {g["key"]: g for g in state["goals"]}


def clinic_facts(run_dir: Path) -> dict:
    changes = _goal_changes(run_dir, "doctor_shen")
    pursuits = [c for c in changes if c["kind"] == "pursuit_changed"]
    first_brief = next((c["tick"] for c in pursuits
                        if c["goal"] == "brief_nurse"), None)
    first_ensure = next((c["tick"] for c in pursuits
                         if c["goal"] == "ensure_readiness"), None)
    goals = _final_goals(run_dir, "doctor_shen")
    counts = _counts(run_dir)
    return {
        "first_brief_pursuit": first_brief,
        "first_ensure_pursuit": first_ensure,
        "brief_before_ensure": first_brief is not None and (
            first_ensure is None or first_brief < first_ensure),
        "brief_completed": goals["brief_nurse"]["status"] == "completed",
        "ensure_completed": goals["ensure_readiness"]["status"] == "completed",
        "doctor_starvations": sum(
            1 for c in changes if c["kind"] == "starvation_protection"),
        **counts,
    }


def tavern_facts(run_dir: Path) -> dict:
    beliefs = _final_beliefs(run_dir, "soldier")
    letter = beliefs.get("anonymous_letter_exists", {})
    mayor = beliefs.get("mayor_embezzled", {})
    counts = _counts(run_dir)
    return {
        "letter_conf": letter.get("confidence"),
        "letter_kind": letter.get("kind"),
        "mayor_conf": mayor.get("confidence"),
        "switching_rate": (counts["switches"] / counts["decisions"]
                           if counts["decisions"] else None),
        **counts,
    }


def warehouse_facts(run_dir: Path) -> dict:
    log = json.loads((run_dir / "world_log.json").read_text(encoding="utf-8"))
    found_tick = None
    for snap in log:
        for ev in snap["events"]:
            if ev["type"] == "search" and ev.get("found") \
                    and "warehouse_key" in ev["found"] \
                    and ev["actors"][:1] == ["keeper"]:
                found_tick = snap["tick"]
    goals = _final_goals(run_dir, "keeper")
    return {"key_found_tick": found_tick,
            "find_missing_key_status": goals["find_missing_key"]["status"]}


def run_one(kind: str, seed: int, tag: str) -> Path:
    name = f"{kind}_seed{seed}_{tag}"
    if kind == "clinic":
        return asyncio.run(run_simulation(
            mode="rule", ticks=24, seed=seed, run_name=name, out_root=OUT,
            characters_root=ROOT / "characters_scenario_c",
            world_config=CLINIC_WORLD))
    if kind == "warehouse":
        return asyncio.run(run_simulation(
            mode="rule", ticks=20, seed=seed, run_name=name, out_root=OUT,
            characters_root=ROOT / "characters_variant",
            world_config=WAREHOUSE_WORLD))
    return asyncio.run(run_simulation(
        mode="rule", ticks=20, seed=seed, run_name=name, out_root=OUT))


def screen() -> None:
    results = []
    for dep, wait, margin in itertools.product(
            (0.4, 0.6, 0.8), (0.15, 0.3), (0.05, 0.10, 0.20)):
        tag = f"d{dep}_w{wait}_m{margin}".replace(".", "p")
        goal_scheduler.set_config(SchedulerConfig(
            dep=dep, wait=wait, switch_margin=margin))
        row = {"dep": dep, "wait": wait, "margin": margin, "clinic": {},
               "tavern": {}}
        for seed in (42, 7):
            row["clinic"][seed] = clinic_facts(run_one("clinic", seed, tag))
            row["tavern"][seed] = tavern_facts(run_one("tavern", seed, tag))
        c_ok = all(v["brief_before_ensure"] and v["brief_completed"]
                   and v["doctor_starvations"] == 0
                   for v in row["clinic"].values())
        t_ok = all(v["letter_conf"] == 1.0
                   and v["letter_kind"] == "experienced"
                   and v["mayor_conf"] is not None
                   and 0.70 <= v["mayor_conf"] <= 0.72
                   and v["switching_rate"] <= 0.25
                   for v in row["tavern"].values())
        row["screen_pass"] = bool(c_ok and t_ok)
        row["tavern_switching_mean"] = sum(
            v["switching_rate"] for v in row["tavern"].values()) / 2
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "screen_results.jsonl", "w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    passing = [r for r in results if r["screen_pass"]]
    passing.sort(key=lambda r: (r["tavern_switching_mean"], r["dep"],
                                r["wait"], abs(r["margin"] - 0.10)))
    print("SCREEN PASSING:", [(r["dep"], r["wait"], r["margin"])
                              for r in passing])


def confirm(dep: float, wait: float, margin: float) -> None:
    tag = f"confirm_d{dep}_w{wait}_m{margin}".replace(".", "p")
    goal_scheduler.set_config(SchedulerConfig(
        dep=dep, wait=wait, switch_margin=margin))
    out = {"dep": dep, "wait": wait, "margin": margin,
           "clinic": {}, "tavern": {}, "warehouse": {}}
    for seed in (42, 7):
        out["clinic"][seed] = clinic_facts(run_one("clinic", seed, tag))
    for seed in (42, 7, 101, 2024, 555):
        out["tavern"][seed] = tavern_facts(run_one("tavern", seed, tag))
    for seed in (42, 7):
        out["warehouse"][seed] = warehouse_facts(
            run_one("warehouse", seed, tag))
    print(json.dumps(out, ensure_ascii=False, indent=1))


def one(dep: float, wait: float, margin: float) -> None:
    tag = f"d{dep}_w{wait}_m{margin}".replace(".", "p")
    goal_scheduler.set_config(SchedulerConfig(
        dep=dep, wait=wait, switch_margin=margin))
    row = {"dep": dep, "wait": wait, "margin": margin, "clinic": {},
           "tavern": {}}
    for seed in (42, 7):
        row["clinic"][seed] = clinic_facts(run_one("clinic", seed, tag))
        row["tavern"][seed] = tavern_facts(run_one("tavern", seed, tag))
    c_ok = all(v["brief_before_ensure"] and v["brief_completed"]
               and v["doctor_starvations"] == 0
               for v in row["clinic"].values())
    t_ok = all(v["letter_conf"] == 1.0
               and v["letter_kind"] == "experienced"
               and v["mayor_conf"] is not None
               and 0.70 <= v["mayor_conf"] <= 0.72
               and v["switching_rate"] <= 0.25
               for v in row["tavern"].values())
    row["screen_pass"] = bool(c_ok and t_ok)
    row["tavern_switching_mean"] = sum(
        v["switching_rate"] for v in row["tavern"].values()) / 2
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "screen_results.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "confirm":
        confirm(float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]))
    elif len(sys.argv) > 1 and sys.argv[1] == "one":
        one(float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]))
    else:
        screen()
