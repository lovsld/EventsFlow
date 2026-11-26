"""Three-level planner (upgrade Phase 5): story outline ->
chapter outlines -> scene outlines, plus continuity validation.
All deterministic; titles/theme come from the story config,
never invented."""
from __future__ import annotations

import json
from pathlib import Path

from narrative.planner.chapter_planner import (build_chapters,
                                               build_story_outline)
from narrative.planner.continuity_validator import validate_plan
from narrative.planner.scene_planner import build_scene_outlines


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_plan(run_dir: Path, narrative_dir: Path,
               config_path: Path | None) -> dict:
    upgrade = narrative_dir / "upgrade"
    views = _load(upgrade / "story_views.json")
    arcs = _load(upgrade / "character_arcs.json")
    setup = _load(upgrade / "setup_payoff.json")
    scenes = build_scene_outlines(run_dir, narrative_dir, views)
    # Phase 2: scenes read the unified character-state view.
    from narrative.character_state import build_character_states
    from narrative.planner.scene_planner import (
        enrich_scenes_with_states)
    from narrative.psychology import build_state_updates
    _states = build_character_states(run_dir, narrative_dir)
    _updates = build_state_updates(narrative_dir)["updates"]
    scenes = enrich_scenes_with_states(scenes, _states, _updates,
                                       narrative_dir)
    story = build_story_outline(run_dir, narrative_dir, views,
                                arcs, config_path)
    chapters = build_chapters(narrative_dir, scenes, views, setup)
    plan = {"story": story, "chapters": chapters, "scenes": scenes}
    plan["validation"] = validate_plan(run_dir, narrative_dir,
                                       plan, config_path)
    return plan


def write_plan(run_dir: Path, narrative_dir: Path,
               config_path: Path | None, out_dir: Path) -> Path:
    plan = build_plan(run_dir, narrative_dir, config_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path
