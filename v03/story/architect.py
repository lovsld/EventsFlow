"""Story Architect (task book §4): builds the initial StoryState
from a story configuration. It establishes initial conditions ONLY —
threads, declared conflicts, stakes, ending constraints as ranges.
It never writes a plot outline and never touches character state.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import yaml

from v03.story.models import (CharacterGoalView, StoryConfig, StoryState,
                              StoryThread)


def load_config(path: str | Path) -> StoryConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return StoryConfig(**data)


def _beliefs_of(condition: dict | None) -> list[str]:
    """Belief keys referenced by a V0.2 goal condition tree."""
    out: list[str] = []
    if not isinstance(condition, dict):
        return out
    if condition.get("type") == "belief_threshold" and \
            condition.get("belief"):
        out.append(condition["belief"])
    for key in ("all", "any", "conditions"):
        sub = condition.get(key)
        if isinstance(sub, list):
            for item in sub:
                out += _beliefs_of(item)
    if isinstance(condition.get("condition"), dict):
        out += _beliefs_of(condition["condition"])
    return out


def load_goal_views(characters_root: str | Path,
                    config: StoryConfig) -> list[CharacterGoalView]:
    """CharacterGoalViews from each character's own goals.yaml."""
    views: list[CharacterGoalView] = []
    for path in sorted(glob.glob(str(Path(characters_root) / "*" /
                                     "goals.yaml"))):
        data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        profile = yaml.safe_load(open(Path(path).parent / "profile.yaml",
                                      encoding="utf-8")) or {}
        cid = profile.get("character_id") or Path(path).parent.name
        for goal in data.get("goals", []):
            meta = config.goal_metadata.get(goal["key"], {})
            views.append(CharacterGoalView(
                character_id=cid,
                goal_id=goal["key"],
                description=goal.get("desc", ""),
                priority=float(goal.get("weight", 0.5)),
                urgency=float(meta.get("urgency", 0.0)),
                kind=goal.get("kind", "achievement"),
                success_beliefs=_beliefs_of(goal.get("success_condition")),
                failure_beliefs=_beliefs_of(goal.get("failure_condition")),
                required_resources=list(meta.get("required_resources", [])),
                deadline=meta.get("deadline")))
    return views


class StoryArchitect:
    def build(self, config: StoryConfig,
              characters_root: str | Path | None = None) -> StoryState:
        state = StoryState(
            story_id=config.story_id, title=config.title,
            genre=config.genre, theme=config.theme,
            central_question=config.central_question,
            stakes=list(config.stakes),
            ending_constraints=list(config.ending_constraints))
        for thread in config.initial_threads:
            t = thread.model_copy(deep=True)
            t.created_at = 0
            t.last_developed_at = 0
            # seed per-character knowledge from declared initial facts
            for char, facts in config.initial_knowledge.items():
                if char in t.involved_characters:
                    known = [f for f in facts
                             if f in t.propositions or
                             f in t.sensitive_facts or not t.propositions]
                    if known:
                        t.known_by[char] = sorted(set(known))
                        for f in known:
                            if f not in t.known_facts:
                                t.known_facts.append(f)
            state.threads[t.thread_id] = t
        # dependencies imply a recorded relation both ways (§5.4)
        for t in state.threads.values():
            for dep in t.dependencies:
                if dep in state.threads:
                    if dep not in t.related_threads:
                        t.related_threads.append(dep)
                    other = state.threads[dep]
                    if t.thread_id not in other.related_threads:
                        other.related_threads.append(t.thread_id)
        if characters_root is not None:
            state.goals = load_goal_views(characters_root, config)
        state.change_log.append(
            {"tick": 0, "kind": "story_initialized",
             "detail": f"{len(state.threads)} threads, "
                       f"{len(state.goals)} goals"})
        return state
