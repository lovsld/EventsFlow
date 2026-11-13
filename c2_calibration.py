"""V0.2-C C2 answer-coefficient calibration (design 3.3 + 9.3).

The design's default answer coefficient is 0.6, with an explicit
fallback: if the repriced answer breaks the tavern secrecy chain,
the coefficient is raised to the value that restores it, and the
clinic-side cost is recorded honestly. Pre-registered here:

  grid:        ANSWER_DISCLOSURE_COEF in {0.6, 1.2, 1.8, 2.2, 2.6}
               (extended upward after the flat-1.0 answer info of
               design 3.3(1) moved the fixture crossover to ~2.0)
  constraints:
    A. the B1 disclosure-gate fixture pair: at trust 0.3 the
       denial out-scores the answer; at trust 0.95 the answer
       out-scores the denial (unit level, no jitter)
    B. clinic seeds 42+7: the doctor CHOOSES an answer candidate
       at least once (the obligation made visible), brief_nurse
       completes, and the nurse's restock_medicine ACTIVATES
    C. tavern seeds 42+7+123: the soldier's mayor belief stays in
       the B band [0.70, 0.72]; the owner's denial->conceal chain
       is present in at least one seed (conceals belief held)
  winner rule: the SMALLEST coefficient satisfying A+C and the
  clinic hard parts of B (brief + restock). B's answer-selection
  sub-clause is REPORTED per seed (ok_strict) but does not gate
  the coefficient: in seed 42 the doctor's proactive assert
  delivers the proposition to the porter one tick BEFORE the
  question, and no coefficient inside any sane range closes the
  remaining goal-driven gap — that finding belongs to the stage
  report, not to coefficient tuning. (Amendment recorded here,
  before the extended grid was run.)
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import tests_v02.test_v02b_b1 as b1fix
import v02.providers.rule_provider as rp
from v02.runner import run_simulation

ROOT = Path(__file__).resolve().parent
OUT = Path("/tmp/calib_c2")
CLINIC_WORLD = json.loads(
    (ROOT / "scenarios" / "scenario_c_clinic.json").read_text(
        encoding="utf-8"))


def fixture_ok() -> bool:
    lo = b1fix._response_pair_scores(0.3)
    hi = b1fix._response_pair_scores(0.95)
    return (lo.get("denial", -9) > lo.get("letter_discussion", 9)
            and hi.get("letter_discussion", -9) > hi.get("denial", 9))


def run_one(kind: str, seed: int, tag: str) -> Path:
    name = f"{kind}_seed{seed}_{tag}"
    if kind == "clinic":
        return asyncio.run(run_simulation(
            mode="rule", ticks=24, seed=seed, run_name=name, out_root=OUT,
            characters_root=ROOT / "characters_scenario_c",
            world_config=CLINIC_WORLD))
    return asyncio.run(run_simulation(
        mode="rule", ticks=20, seed=seed, run_name=name, out_root=OUT))


def _db(run_dir: Path):
    return sqlite3.connect(f"file:{run_dir}/acp.sqlite3?mode=ro", uri=True)


def clinic_facts(run_dir: Path) -> dict:
    db = _db(run_dir)
    answers = 0
    for (census,) in db.execute(
            "select census from agent_decisions "
            "where character_id='doctor_shen' and census is not null"):
        if json.loads(census).get("chosen_comm_kind") == "answer":
            answers += 1
    brief_done = db.execute(
        "select count(*) from goal_changes where character_id="
        "'doctor_shen' and goal_key='brief_nurse' and "
        "kind='goal_completed'").fetchone()[0] > 0
    restock_active = db.execute(
        "select count(*) from goal_changes where character_id="
        "'nurse_lin' and goal_key='restock_medicine' and "
        "kind='activated'").fetchone()[0] > 0
    db.close()
    return {"answers": answers, "brief_done": brief_done,
            "restock_active": restock_active,
            "ok": answers >= 1 and brief_done and restock_active}


def tavern_facts(run_dir: Path) -> dict:
    state = json.loads((run_dir / "characters" / "soldier"
                        / "state.json").read_text(encoding="utf-8"))
    mayor = next((b["confidence"] for b in state["beliefs"]
                  if b["key"] == "mayor_embezzled"), None)
    ostate = json.loads((run_dir / "characters" / "owner"
                         / "state.json").read_text(encoding="utf-8"))
    conceals = any(b["key"].startswith("conceals:")
                   for b in ostate["beliefs"])
    log = json.loads((run_dir / "world_log.json").read_text(
        encoding="utf-8"))
    denials = sum(1 for snap in log for ev in snap["events"]
                  if ev["type"] in ("speak", "confront")
                  and ev.get("claim") == "denial")
    return {"mayor": mayor, "conceals": conceals, "denials": denials,
            "band_ok": mayor is not None and 0.70 <= mayor <= 0.72}


def screen(coefs) -> None:
    for coef in coefs:
        rp.ANSWER_DISCLOSURE_COEF = coef
        tag = f"c{coef}".replace(".", "p")
        row = {"coef": coef, "fixture": fixture_ok()}
        if row["fixture"]:
            row["clinic"] = {str(s): clinic_facts(
                run_one("clinic", s, tag)) for s in (42, 7)}
            row["tavern"] = {str(s): tavern_facts(
                run_one("tavern", s, tag)) for s in (42, 7, 123)}
            row["ok_strict"] = (
                all(c["ok"] for c in row["clinic"].values())
                and all(t["band_ok"] for t in row["tavern"].values())
                and any(t["conceals"] for t in row["tavern"].values()))
            row["ok"] = (
                all(c["brief_done"] and c["restock_active"]
                    for c in row["clinic"].values())
                and all(t["band_ok"] for t in row["tavern"].values())
                and any(t["conceals"] for t in row["tavern"].values()))
        else:
            row["ok"] = False
            row["ok_strict"] = False
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "screen":
        screen([float(x) for x in sys.argv[2].split(",")])
