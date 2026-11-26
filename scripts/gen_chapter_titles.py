"""Phase 1 close-out: generate literary chapter titles for the
three runs (one Gemini call per run). Titles go into plan.json
as `literary_title`; the ordinal `chapter_title` is preserved.
Based only on each chapter's own outline data (purpose, turning
point summary, threads, unresolved questions) — no invented
plot facts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from narrative.gemini_client import generate  # noqa: E402

SYSTEM = """你是一名小说编辑，为已完成的章节拟文学标题。规则：
1. 每章一个标题，4-10 个汉字，可含少量标点；凝练、有画面感或悬念感，但不得剧透本章之后才揭示的信息。
2. 只能依据给出的本章大纲信息（叙事目的、转折点、未决问题），不得虚构情节、人物或物件。
3. 只输出 JSON：{"titles":[{"chapter_id":"CH01","title":"..."}]}。"""


def main() -> None:
    total_in = total_out = 0
    for run in ("rule_v02c4_seed42", "c5_lighthouse_seed7",
                "clinic_v02cfix_seed42"):
        ndir = ROOT / "narrative_runs" / run
        plan_path = ndir / "upgrade" / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        events = {e["narrative_event_id"]: e for e in json.loads(
            (ndir / "upgrade" / "narrative_events.json")
            .read_text(encoding="utf-8"))}
        brief = []
        for ch in plan["chapters"]:
            tp = events.get(ch.get("turning_point") or "", {})
            brief.append({
                "chapter_id": ch["chapter_id"],
                "narrative_purpose": ch.get("narrative_purpose"),
                "turning_point": tp.get("event_summary", ""),
                "unresolved_questions":
                    ch.get("unresolved_questions", [])})
        text, usage = generate(
            SYSTEM, json.dumps({"story": plan["story"]["title"],
                                "chapters": brief},
                               ensure_ascii=False),
            max_tokens=2048, temperature=0.6)
        data = json.loads(text)
        titles = {t["chapter_id"]: t["title"]
                  for t in data["titles"]}
        for ch in plan["chapters"]:
            if ch["chapter_id"] in titles:
                ch["literary_title"] = titles[ch["chapter_id"]]
        plan_path.write_text(json.dumps(plan, ensure_ascii=False,
                                        indent=1),
                             encoding="utf-8")
        total_in += usage.get("prompt_tokens", 0)
        total_out += usage.get("candidates_tokens", 0)
        print(run, "->", titles, "| usage in/out:",
              usage.get("prompt_tokens"), "/",
              usage.get("candidates_tokens"), flush=True)
    print(f"TOTAL in={total_in} out={total_out}")


if __name__ == "__main__":
    main()
