"""N3 Script Writer (design §6).

Form A (faithful chronicle) and form B (scene-based short story) are
produced by DeepSeek from the Observer/Extractor products ONLY
(observations + storylines + scenes). The model must answer with
structured paragraphs — each paragraph declares its event ids and
mode (quote / paraphrase / connective) — which makes the fidelity
audit mechanical. Latitude is L1 (design §6.2): quotes verbatim,
paraphrase preserves proposition and stance, connective text carries
no new facts; no omniscient interiority, no invented motives, no
invented scenery; broken lines stay unresolved; no time reordering.

One repair pass is allowed: if the deterministic audit fails, its
violations are fed back once. All token usage is recorded in the
trace meta. Products: story_<form>.md, trace_<form>.json,
audit_<form>.json in narrative_runs/<run>/.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

from narrative import OBSERVER_VERSION
from narrative.audit import audit_draft
from narrative.llm_client import chat

WRITER_VERSION = "n3-writer-1.0"

SYSTEM = """你是一名严格忠于素材的叙事写作者。你会收到一份事件清单（每条带唯一编号、时刻、人物、地点、原话文本）和故事线清单。你的任务是把这些已经发生的事件写成中文叙事文。

不可违反的规则（尺度 L1）：
1. 只能写事件清单里发生过的事。不得虚构事件、不得补写没发生的结果、不得给任何人物添加清单之外的言行。
2. 直接引语必须逐字摘自对应事件的 text 字段（包括“人名：”前缀在内一字不改），并放进中文引号“”内。
3. 转述不得改变原话的命题与立场：肯定不能写成否定，试探不能写成断言，否认不能写成承认。
4. 连接性文字（时间、地点、氛围过渡）不得携带任何新事实。
5. 禁止全知心理描写：不得写任何人物的内心想法、动机、情绪成因——只能写其言行本身。事件清单里没有的动机一律不写。
6. 禁止虚构布景：不得描写清单之外的天气、陈设、服饰、声音等细节。
7. 状态为 broken 的故事线必须停在断点上，不得补写成完成或成功；状态为 open 的线不得写出结局。结构判定为 closed 只表示链条完整，不等于矛盾已解决——只写事件支持到的程度。
8. 严格按事件发生的先后顺序叙述，不得倒叙、不得重排时间。
9. 说话失败（speak_failed）的事件：只能写“试图开口/没能说出口”这一事实，不得写出其未说出的内容。

输出格式：只输出一个 JSON 对象：
{"title": "标题（简短，中性）",
 "paragraphs": [{"text": "段落正文", "event_ids": ["E0001"], "mode": "quote|paraphrase|connective"}],
 "omitted": [{"event_id": "E0000", "reason": "省略理由"}]}
- 每段必须给出 mode；事实段（quote/paraphrase）的 event_ids 不得为空；纯过渡段才可用 connective 且 event_ids 为空。
- 一段可以合并相邻的几个事件，但不得跨越很远的时间合并。"""

FORM_A_EXTRA = """本篇是形态 A（忠实编年体）：按时间顺序完整叙述全部 plot 与 structure 事件，不做取舍，不加文学修饰，语言平实。每段覆盖 1-3 个相邻事件。omitted 必须为空数组。"""

FORM_B_EXTRA = """本篇是形态 B（场景化短篇）：按场景组织行文，节奏可以有疏密，但故事线清单中每条线的全部成员事件必须都被某些段落引用（不得省略线内事件）；线外的 plot 事件若省略，必须逐个写进 omitted 并说明理由（只许省略重复性的）；texture 事件可自由取舍。语言可以有文学性，但仍受全部规则约束。篇幅以把主要故事线讲完整为准。"""


def _character_names(project_root: Path) -> dict:
    names: dict[str, str] = {}
    for pattern in ("characters*/*/profile.yaml",):
        for p in sorted(glob.glob(str(project_root / pattern))):
            text = Path(p).read_text(encoding="utf-8")
            mid = re.search(r"(?m)^id:\s*(\S+)", text)
            mname = re.search(r"(?m)^name:\s*(.+)$", text)
            if mid and mname:
                names.setdefault(mid.group(1), mname.group(1).strip())
    return names


def _event_script(observations: list[dict], names: dict) -> str:
    lines = []
    for o in observations:
        who = "、".join(names.get(a, a) for a in o["actors"]) or "—"
        parts = [f"{o['obs_id']} t{o['tick']} {o['time']} [{o['type']}] "
                 f"{who} @{o['location']}"]
        if o["claim"]:
            parts.append(f"claim={o['claim']}")
        if o["proposition"]:
            parts.append(f"prop={o['proposition']} stance={o['stance']}")
        if o["found"]:
            parts.append(f"found={o['found']}")
        if o.get("object"):
            parts.append(f"object={o['object']}")
        if o["narrative_role"] != "plot":
            parts.append(f"class={o['narrative_role']}")
        if o["text"]:
            parts.append(f"text={o['text']}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _lines_script(storylines: dict) -> str:
    out = []
    for line in storylines["lines"]:
        out.append(f"{line['line_id']} 状态={line['state']} "
                   f"事件={','.join(line['event_ids'])} "
                   f"判定依据={'; '.join(line['state_reasons'])}")
        if line["break_event"]:
            out.append(f"  断点事件={line['break_event']}")
    for frag in storylines["fragments"]:
        out.append(f"{frag['line_id']} 片段 "
                   f"事件={','.join(frag['event_ids'])}")
    return "\n".join(out)


def build_messages(run_dir: Path, narrative_dir: Path,
                   form: str) -> list[dict]:
    observations = json.loads((narrative_dir / "observations.json")
                              .read_text(encoding="utf-8"))
    storylines = json.loads((narrative_dir / "storylines.json")
                            .read_text(encoding="utf-8"))
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    names = _character_names(run_dir.parent.parent)
    scenario = (meta.get("scenario") or {}).get("name") or run_dir.name
    user = (f"场景：{scenario}（run {run_dir.name}）\n\n"
            f"【事件清单】\n{_event_script(observations, names)}\n\n"
            f"【故事线清单】\n{_lines_script(storylines)}\n\n"
            + (FORM_A_EXTRA if form == "A" else FORM_B_EXTRA))
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": user}]


def _parse(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model output contains no JSON object")
    return json.loads(content[start:end + 1])


def write_form(run_dir: Path, narrative_dir: Path, form: str,
               chat_fn=chat) -> dict:
    observations = json.loads((narrative_dir / "observations.json")
                              .read_text(encoding="utf-8"))
    storylines = json.loads((narrative_dir / "storylines.json")
                            .read_text(encoding="utf-8"))
    messages = build_messages(run_dir, narrative_dir, form)
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    attempts = []
    draft = None
    audit = None
    for attempt in (1, 2):
        content, usage = chat_fn(messages, max_tokens=16384)
        for k in usage_total:
            usage_total[k] += usage.get(k, 0)
        parsed = _parse(content)
        paragraphs = []
        for i, p in enumerate(parsed["paragraphs"], 1):
            paragraphs.append({"id": f"P{i:02d}", "text": p["text"],
                               "event_ids": p.get("event_ids") or [],
                               "mode": p.get("mode", "paraphrase")})
        draft = {"title": parsed.get("title", ""),
                 "paragraphs": paragraphs,
                 "omitted": parsed.get("omitted") or [],
                 "meta": {"run_id": run_dir.name, "form": form,
                          "writer_version": WRITER_VERSION,
                          "observer_version": OBSERVER_VERSION,
                          "model": "deepseek-flash",
                          "usage": dict(usage_total),
                          "attempt": attempt}}
        audit = audit_draft(draft, observations, storylines)
        attempts.append({"attempt": attempt, "pass": audit["pass"],
                         "violations": len(audit["violations"])})
        if audit["pass"]:
            break
        if attempt == 1:
            feedback = ("上一稿未通过保真审计，问题如下，请逐条修正后"
                        "重新输出完整 JSON（不要解释，只输出 JSON）：\n"
                        + "\n".join(f"- {v}"
                                    for v in audit["violations"]))
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": feedback}]
    assert draft is not None and audit is not None
    md = [f"# {draft['title']}", ""]
    for p in draft["paragraphs"]:
        md.append(p["text"])
        md.append("")
    (narrative_dir / f"story_{form}.md").write_text(
        "\n".join(md), encoding="utf-8")
    (narrative_dir / f"trace_{form}.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    audit_out = dict(audit)
    audit_out["attempts"] = attempts
    audit_out["usage"] = usage_total
    (narrative_dir / f"audit_{form}.json").write_text(
        json.dumps(audit_out, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return audit_out


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[3] not in ("A", "B"):
        print("usage: python -m narrative.writer RUN_DIR NARRATIVE_DIR "
              "A|B", file=sys.stderr)
        return 2
    result = write_form(Path(argv[1]), Path(argv[2]), argv[3])
    print(json.dumps({"form": argv[3], "pass": result["pass"],
                      "violations": len(result["violations"]),
                      "usage": result["usage"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
