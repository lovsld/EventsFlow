"""Phase 1 live experiment (task 1, live half; approved by Hu
2026-11-24): two DeepSeek 20-tick tavern runs, identical except
for the Progress-Judge intervention hook.

- off_seed42: Story Engine attached (as Phase 6 llm_on), no
  intervention. Both arms include the provider parse fix
  (extra-key stripping), so the intervention is the only
  variable between arms.
- on_seed42: + LiveIntervention (judge at tick boundaries;
  on a ZERO_PROGRESS_STREAK diagnosis, one validated
  pressure/opportunity candidate via the World Engine's
  external queue; max 3, cooldown 3 ticks).

After each run: judge it with v03.decision_quality and record
decision-failure stats from acp.sqlite3.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from v03.decision_quality import judge_run          # noqa: E402
from v03.intervention import LiveIntervention       # noqa: E402
from v03.live import run_live_sync                  # noqa: E402
from v03.story.architect import load_config         # noqa: E402
from v03.story.story_engine import StoryEngine      # noqa: E402

OUT = ROOT / "v03_runs" / "phase1_live"
TAVERN = ROOT / "characters_v02"
SCEN = ROOT / "scenarios" / "tavern_letter.json"
CFG_PATH = ROOT / "stories" / "tavern_secret.json"


def _failures(run_dir: Path) -> dict:
    db = run_dir / "acp.sqlite3"
    con = sqlite3.connect(db)
    total = con.execute("SELECT COUNT(*) FROM agent_decisions") \
        .fetchone()[0]
    failed = con.execute(
        "SELECT COUNT(*) FROM agent_decisions "
        "WHERE error != 'None'").fetchone()[0]
    usage = con.execute(
        "SELECT COALESCE(SUM(CAST(input_tokens AS INT)),0), "
        "COALESCE(SUM(CAST(output_tokens AS INT)),0) "
        "FROM agent_decisions").fetchone()
    con.close()
    return {"decisions": total, "failed": failed,
            "input_tokens": usage[0], "output_tokens": usage[1]}


def _finish(run_dir: Path) -> dict:
    judged = judge_run(run_dir, SCEN, CFG_PATH)
    (run_dir / "decision_quality.json").write_text(
        json.dumps(judged, ensure_ascii=False, indent=1),
        encoding="utf-8")
    stats = _failures(run_dir)
    return {"run": run_dir.name, "judge": judged["summary"],
            "decisions": stats}


def _complete(run_dir: Path, ticks: int = 20) -> bool:
    """A run counts as complete only with a full-length
    world_log AND zero failed decisions. Tick count alone is
    not enough: a run whose every decision failed (e.g. the
    2026-11-26 systemd attempt, killed by egress TLS timeouts)
    still writes 20 tick records, and must be re-run in full
    rather than banked as a finished arm."""
    wl_path = run_dir / "world_log.json"
    if not wl_path.exists():
        return False
    try:
        if len(json.loads(wl_path.read_text(
                encoding="utf-8"))) < ticks:
            return False
        return _failures(run_dir)["failed"] == 0
    except (OSError, json.JSONDecodeError, KeyError,
            sqlite3.Error):
        return False


def main() -> None:
    cfg = load_config(CFG_PATH)
    cfg_dict = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    scen = json.loads(SCEN.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    off_dir = OUT / "off_seed42"
    if not _complete(off_dir):
        import shutil
        shutil.rmtree(off_dir, ignore_errors=True)
        run_live_sync(off_dir, cfg, ticks=20, seed=42,
                      characters_root=TAVERN, mode="llm",
                      provider="deepseek")
    results["off"] = _finish(off_dir)
    print("OFF done:", json.dumps(results["off"],
                                  ensure_ascii=False), flush=True)
    on_dir = OUT / "on_seed42"
    if not _complete(on_dir):
        import shutil
        shutil.rmtree(on_dir, ignore_errors=True)
        engine = StoryEngine(cfg, TAVERN)
        intervention = LiveIntervention(on_dir, scen, cfg_dict,
                                        engine.state)
        run_live_sync(on_dir, cfg, ticks=20, seed=42,
                      characters_root=TAVERN, mode="llm",
                      provider="deepseek",
                      extra_hook=intervention.hook)
        intervention.write_log(on_dir / "interventions.json")
    results["on"] = _finish(on_dir)
    print("ON done:", json.dumps(results["on"],
                                 ensure_ascii=False), flush=True)
    (OUT / "experiment_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
