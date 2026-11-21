"""Novel Exporter (task book §12): one self-contained material
package for the novel-writing stage — plan, chapters, scene
packages, event graph, setup/payoff registry, continuity report,
character knowledge timelines — as JSON plus a readable Markdown
rendering. Deterministic; no LLM; nothing paraphrased into facts
that are not in the record (event texts are quoted verbatim).
"""
from __future__ import annotations

import json
from pathlib import Path

from v03.graph.builder import GraphBuilder
from v03.graph.packages import (_prepare, build_graph_for_run,
                                build_scene_packages)
from v03.planning.continuity import ContinuityValidator
from v03.planning.planner import build_plan
from v03.story.models import StoryConfig


def export_run(run_dir: str | Path, config: StoryConfig,
               characters_root=None,
               out_dir: str | Path | None = None) -> dict:
    run_dir = Path(run_dir)
    out_dir = Path(out_dir) if out_dir else run_dir
    world_log, object_facts = _prepare(run_dir)
    plan = build_plan(run_dir, config, characters_root)
    packages = build_scene_packages(run_dir, config, characters_root)
    graph = build_graph_for_run(run_dir, config, characters_root)

    facts_builder = GraphBuilder(run_dir.name, object_facts,
                                 initial_knowledge=config
                                 .initial_knowledge)
    facts_builder.build(world_log)
    knowledge_timelines = {
        c: {f: info for f, info in facts.items()}
        for c, facts in facts_builder.learned.items()}

    known_facts = {f for v in object_facts.values()
                   for f in v.get("direct", []) + v.get("hearsay", [])}
    known_facts |= {p for t in config.initial_threads
                    for p in t.propositions}
    basis_free = set()
    meta = json.loads((run_dir / "run_meta.json")
                      .read_text(encoding="utf-8"))
    for cdef in (meta.get("scenario") or {}).get("claims",
                                                 {}).values():
        if cdef.get("basis_required") is False and \
                cdef.get("proposition"):
            basis_free.add(cdef["proposition"])
    tick_of_event = {e["event_id"]: r["tick"]
                     for r in world_log for e in r["events"]}
    thread_first_tick = {}
    from v03.scenes.runtime import replay_runtime
    rt = replay_runtime(run_dir, config, characters_root)
    for tid, t in rt.engine.state.threads.items():
        if t.related_events:
            thread_first_tick[tid] = tick_of_event.get(
                t.related_events[0], 0)
    deps = [(t.thread_id, d) for t in config.initial_threads
            for d in t.dependencies]
    continuity = ContinuityValidator(
        basis_free_props=basis_free, known_facts=known_facts,
        dependencies=deps).validate(world_log, graph,
                                    thread_first_tick)

    export = {"run_id": run_dir.name,
              "story": {"title": config.title,
                        "theme": config.theme,
                        "central_question": config.central_question},
              "plan": plan, "scene_packages": packages,
              "event_graph": graph.model_dump(),
              "knowledge_timelines": knowledge_timelines,
              "continuity": continuity}
    (out_dir / "novel_export.json").write_text(
        json.dumps(export, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (out_dir / "novel_export.md").write_text(
        _render_md(export, world_log), encoding="utf-8")
    return export


def _render_md(export: dict, world_log) -> str:
    text_of = {e["event_id"]: (e.get("text") or "")
               for r in world_log for e in r["events"]}
    plan = export["plan"]
    lines = [f"# {export['story']['title']} — 小说素材包",
             "",
             f"主题：{export['story']['theme']}　核心问题："
             f"{export['story']['central_question']}",
             "",
             "## 故事弧线",
             ""]
    arc = plan["arc"]
    for tr in arc["transitions"]:
        lines.append(f"- tick {tr['tick']}：{tr['from']} → "
                     f"{tr['to']}（{tr['trigger']}）")
    if arc["not_reached"]:
        lines.append(f"- 未抵达阶段：{', '.join(arc['not_reached'])}"
                     f"（如实记录，本场未走到）")
    lines += ["", "## 章节", ""]
    pkg_by_id = {p["scene_id"]: p for p in export["scene_packages"]}
    for ch in plan["chapters"]:
        lines.append(f"### {ch['title']}（tick "
                     f"{ch['tick_range'][0]}–{ch['tick_range'][1]}）")
        for sl in ch["scene_slices"]:
            pkg = pkg_by_id[sl["scene_id"]]
            lines.append(f"- 场景 {sl['scene_id']} · {sl['location']} · "
                         f"参与：{', '.join(pkg['participants'])} · "
                         f"功能：{', '.join(pkg['dramatic_functions'])}")
            for eid in sl["events"][:6]:
                text = text_of.get(eid, "")
                if text:
                    lines.append(f"  - {eid}：{text}")
        lines.append("")
    lines += ["## 伏笔与回收", ""]
    for s in plan["setups"]:
        mark = f"回收于 tick {s['payoff_tick']}" \
            if s["status"] == "paid_off" else "未回收（开放）"
        lines.append(f"- [{s['kind']}] {s['description']} — {mark}")
    lines += ["", "## 连续性校验", "",
              f"错误 {export['continuity']['errors']} · "
              f"警告 {export['continuity']['warnings']}"]
    for issue in export["continuity"]["issues"]:
        lines.append(f"- [{issue['severity']}] tick {issue['tick']} "
                     f"{issue['kind']}：{issue['detail']}")
    return "\n".join(lines) + "\n"
