"""Phase 2 acceptance (section 2.4): build the human sampling
pack from REAL generated novels. Deterministic selection:
5 paragraphs per run, spread across chapters, dialogue and
non-dialogue mixed, F levels varied. Scores are left EMPTY --
they are Hu's to give; nothing here fabricates human judgement.
Outputs docs/PHASE1_HUMAN_SAMPLING_PACK.md (for reading/scoring)
and narrative_runs/human_sampling_pack.json (machine twin)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SOURCES = [
    ("酒馆 tavern", "rule_v02c4_seed42",
     "narrative_runs/rule_v02c4_seed42/phase1/trace_v2.json"),
    ("灯塔 lighthouse", "c5_lighthouse_seed7",
     "narrative_runs/c5_lighthouse_seed7/upgrade/trace_v2.json"),
    ("诊所 clinic", "clinic_v02cfix_seed42",
     "narrative_runs/clinic_v02cfix_seed42/phase1/trace_v2.json"),
]

DIMS = ["自然度", "人物一致性", "情节推进", "信息重复", "事实一致性"]
TAGS = ["机械复述", "对话不自然", "日志/标签泄漏", "无依据事实",
        "心理说明书式旁白", "其他"]


def pick(trace: dict, n: int = 5) -> list[dict]:
    flat = []
    for ch in trace["chapters"]:
        for i, p in enumerate(ch["paragraphs"]):
            if len(p.get("text", "")) >= 30:
                flat.append({"chapter_id": ch["chapter_id"],
                             "chapter_title": ch.get("title", ""),
                             "scene_id": p.get("scene_id"),
                             "para_index": i,
                             "level": p.get("level"),
                             "event_ids": p.get("event_ids", []),
                             "text": p["text"]})
    if len(flat) <= n:
        return flat
    step = len(flat) / n
    chosen = [flat[int(i * step)] for i in range(n)]
    # ensure at least one dialogue paragraph when available
    if not any("“" in c["text"] for c in chosen):
        for c in flat:
            if "“" in c["text"]:
                chosen[-1] = c
                break
    return chosen


def main() -> None:
    pack = {"dimensions": DIMS, "problem_tags": TAGS, "samples": []}
    lines = ["# Phase 1 人工抽样验收包", "",
             "说明：以下段落全部抽自真实生成的小说（非单元测试"
             "文本）。请按 1–5 分评五个维度，并勾选问题类型；"
             "评分由 Hu 本人给出，自动指标另行记录、不混入。",
             "", "维度：" + " / ".join(DIMS),
             "问题类型：" + " / ".join(TAGS), ""]
    sid = 0
    for label, run_id, rel in SOURCES:
        trace = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        lines.append(f"## {label}（{run_id}）")
        lines.append("")
        for s in pick(trace):
            sid += 1
            s = {"sample_id": f"S{sid:02d}", "run": run_id, **s,
                 "scores": None, "problem_tags": None, "note": None}
            pack["samples"].append(s)
            lines.append(f"### {s['sample_id']} · {s['chapter_id']} "
                         f"{s['chapter_title']} · {s['scene_id']} · "
                         f"段 {s['para_index']} · {s['level']}")
            lines.append("")
            lines.append(s["text"])
            lines.append("")
            lines.append("评分（1–5）：" +
                         "　".join(f"{d} __" for d in DIMS))
            lines.append("问题类型：" +
                         "　".join(f"□{t}" for t in TAGS))
            lines.append("备注：____")
            lines.append("")
    out_md = ROOT / "docs" / "PHASE1_HUMAN_SAMPLING_PACK.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    out_json = ROOT / "narrative_runs" / "human_sampling_pack.json"
    out_json.write_text(json.dumps(pack, ensure_ascii=False,
                                   indent=1), encoding="utf-8")
    print(f"samples: {len(pack['samples'])} -> {out_md.name}, "
          f"{out_json.name}")


if __name__ == "__main__":
    main()
