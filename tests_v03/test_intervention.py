"""Phase 1 live intervention: feeding the Phase 6 LLM run's
committed history through the hook must fire a validated
intervention (that run is diagnosed zero-progress), while the
rule run's history must not fire any."""
from __future__ import annotations

import json
from pathlib import Path

from v03.intervention import LiveIntervention
from v03.story.architect import load_config
from v03.story.story_engine import StoryEngine

ROOT = Path(__file__).resolve().parent.parent
SCEN = json.loads((ROOT / "scenarios" / "tavern_letter.json")
                  .read_text(encoding="utf-8"))
CFG = json.loads((ROOT / "stories" / "tavern_secret.json")
                 .read_text(encoding="utf-8"))


class FakeWorld:
    def __init__(self):
        self.queued = []

    def queue_external_event(self, event):
        self.queued.append(event)


def _feed(run_name: str) -> tuple[LiveIntervention, FakeWorld]:
    run_dir = ROOT / "v03_runs" / "phase6" / run_name
    world_log = json.loads((run_dir / "world_log.json")
                           .read_text(encoding="utf-8"))
    cfg_model = load_config(ROOT / "stories" / "tavern_secret.json")
    engine = StoryEngine(cfg_model, ROOT / "characters_v02")
    iv = LiveIntervention(run_dir, SCEN, CFG, engine.state)
    world = FakeWorld()
    records, deliveries = [], {}
    for rec in world_log:
        records.append({"tick": rec["tick"],
                        "events": rec["events"],
                        "positions": rec.get("positions", {})})
        deliveries[rec["tick"]] = rec.get("deliveries", [])
        iv.hook(records, deliveries, world)
    return iv, world


def test_llm_history_fires_validated_intervention():
    iv, world = _feed("llm_on_seed42")
    fired = [e for e in iv.log if e.get("fired")]
    assert fired, iv.log
    assert len(world.queued) == len(fired)
    for ev in world.queued:
        assert ev["type"] == "ambient"
        assert ev["source"] == "story_engine"
        assert ev["location"] in SCEN["locations"]
        assert ev["text"]  # config template text, not fabricated
    assert len(fired) <= 3  # restraint budget


def test_rule_history_fires_nothing():
    iv, world = _feed("rule_on_seed42")
    assert [e for e in iv.log if e.get("fired")] == []
    assert world.queued == []
