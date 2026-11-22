"""Phase 6 remaining runs (lighthouse + clinic) on Gemini, with
a writer-sized quota probe first. Exit codes: 0 = all done,
2 = quota still unavailable (caller should retry later),
1 = other failure. Safe to re-run: a run whose novel already
exists (novel_v2.md + trace_v2.json) skips straight to quality
evaluation; a fully complete run is skipped entirely.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from narrative.gemini_client import LLMError, generate  # noqa: E402

REMAINING = ("c5_lighthouse_seed7", "clinic_v02cfix_seed42")
PROBE_TEXT = "酒馆里三个人各怀心事。" * 60  # ~500 chars, writer-class


def probe() -> bool:
    try:
        generate("只输出 JSON", f"把下面这段话压缩成一句：{PROBE_TEXT}",
                 max_tokens=256)
        return True
    except LLMError as e:
        print(f"probe failed: {str(e)[:120]}", flush=True)
        return False


def main() -> int:
    from narrative.quality import write_quality
    from narrative.writer2 import write_novel_files
    todo = []
    for name in REMAINING:
        out = ROOT / "narrative_runs" / name / "upgrade"
        if (out / "narrative_quality_report.json").exists():
            print(f"{name}: already complete, skipped", flush=True)
        else:
            todo.append(name)
    if not todo:
        print("ALL_DONE", flush=True)
        return 0
    if not probe():
        return 2
    for name in todo:
        run_dir = ROOT / "runs_v02" / name
        ndir = ROOT / "narrative_runs" / name
        out = ndir / "upgrade"
        if (out / "novel_v2.md").exists() and \
                (out / "trace_v2.json").exists():
            print(f"{name}: novel already written, "
                  f"resuming at quality evaluation", flush=True)
        else:
            t0 = time.time()
            novel = write_novel_files(run_dir, ndir, out)
            print(f"{name} writer usage: {novel['usage']} "
                  f"stopped={novel['stopped']} "
                  f"elapsed={time.time() - t0:.0f}s", flush=True)
        result = write_quality(run_dir, ndir, out)
        print(f"{name} scores: "
              f"{json.dumps(result['report']['scores'], ensure_ascii=False)}",
              flush=True)
        print(f"{name} comparison: "
              f"{json.dumps(result['comparison'], ensure_ascii=False)}",
              flush=True)
        print(f"{name} review usage: {result['review_usage']}",
              flush=True)
    print("ALL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
