"""N1 Observer acceptance tests (design §4.6 / §7 / §8 N1 gate).

Run: python -m pytest tests_narrative/ -q
All expectations are recomputed independently from the raw run products,
not from the Observer's own intermediate state.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from narrative.observer import observe  # noqa: E402

RUNS = {
    "rule_v02c4_seed42": 73,
    "c5_lighthouse_seed7": 104,
}


def load(run_id: str, name: str):
    return json.loads((PROJECT / "narrative_runs" / run_id / name)
                      .read_text(encoding="utf-8"))


def raw_world_log(run_id: str):
    return json.loads((PROJECT / "runs_v02" / run_id / "world_log.json")
                      .read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
def produce_outputs(tmp_path_factory):
    for run_id in RUNS:
        observe(PROJECT / "runs_v02" / run_id,
                PROJECT / "narrative_runs" / run_id)


@pytest.mark.parametrize("run_id,total", RUNS.items())
def test_coverage_one_to_one(run_id, total):
    wl = raw_world_log(run_id)
    raw_ids = [ev["event_id"] for rec in wl for ev in rec["events"]]
    obs = load(run_id, "observations.json")
    assert len(obs) == total == len(raw_ids)
    assert [o["obs_id"] for o in obs] == raw_ids  # same order, same ids


@pytest.mark.parametrize("run_id", RUNS)
def test_deliveries_reverse_index_independent(run_id):
    wl = raw_world_log(run_id)
    exp_delivered, exp_muffled = {}, {}
    for rec in wl:
        for d in rec["deliveries"]:
            for eid in d["delivered_event_ids"]:
                exp_delivered.setdefault(eid, set()).add(d["agent_id"])
            for eid in d["muffled_event_ids"]:
                exp_muffled.setdefault(eid, set()).add(d["agent_id"])
    for o in load(run_id, "observations.json"):
        assert set(o["delivered_to"]) == exp_delivered.get(o["obs_id"], set())
        assert set(o["muffled_for"]) == exp_muffled.get(o["obs_id"], set())


@pytest.mark.parametrize("run_id", RUNS)
def test_scenes_partition_all_events(run_id):
    scenes = load(run_id, "scenes.json")
    placed = [eid for sc in scenes
              for eid in sc["event_ids"] + sc["context_event_ids"]]
    raw_ids = [ev["event_id"] for rec in raw_world_log(run_id)
               for ev in rec["events"]]
    assert sorted(placed) == sorted(raw_ids)
    assert len(placed) == len(set(placed))  # exactly once


@pytest.mark.parametrize("run_id", RUNS)
def test_frontier_monotone_and_grounded(run_id):
    wl = raw_world_log(run_id)
    frontier = load(run_id, "frontier.json")
    delivered_pairs = {(d["agent_id"], eid) for rec in wl
                       for d in rec["deliveries"]
                       for eid in d["delivered_event_ids"]}
    actor_pairs = {(a, ev["event_id"]) for rec in wl for ev in rec["events"]
                   for a in ev["actors"]}
    for cid, rows in frontier.items():
        counts = [r["cumulative_count"] for r in rows]
        assert counts == sorted(counts)  # monotone non-decreasing
        seen = set()
        for r in rows:
            for eid in r["new_event_ids"]:
                assert (cid, eid) in delivered_pairs | actor_pairs
                assert eid not in seen
                seen.add(eid)
            assert r["cumulative_count"] == len(seen)


@pytest.mark.parametrize("run_id", RUNS)
def test_no_error_issues(run_id):
    issues = load(run_id, "issues.json")
    assert [i for i in issues if i["severity"] == "error"] == []


def test_tavern_spot_checks():
    obs = {o["obs_id"]: o for o in load("rule_v02c4_seed42",
                                        "observations.json")}
    e8 = obs["E0008"]
    assert e8["type"] == "search" and e8["found"] == ["anonymous_letter"]
    assert e8["narrative_role"] == "plot"
    assert e8["features"]["first_find_of_object"] is True
    e14 = obs["E0014"]
    assert e14["claim"] == "business_proposal"
    assert e14["proposition"] == "business_opportunity"
    assert e14["stance"] == "assert"
    assert e14["text"].startswith("米拉：老板，我手里有批货")
    prov = load("rule_v02c4_seed42", "provenance.json")
    assert prov["names"]["merchant"]["name"] == "米拉"
    assert prov["names"]["owner"]["name"] == "奥伦"
    assert prov["input_hash_check_after"] == "pass"


def test_lighthouse_spot_checks():
    obs = {o["obs_id"]: o for o in load("c5_lighthouse_seed7",
                                        "observations.json")}
    e26 = obs["E0026"]
    assert e26["claim"] == "ship_notice"
    assert e26["proposition"] == "ship_passing_tonight"
    assert "lin_tao" in e26["delivered_to"]
    failed = [o for o in obs.values() if o["type"] == "speak_failed"]
    assert len(failed) == 4
    for o in failed:
        assert o["text"] is None
        assert o["features"]["is_failed_attempt"] is True
        assert o["narrative_role"] == "plot"
    prov = load("c5_lighthouse_seed7", "provenance.json")
    assert prov["names"]["keeper_zhou"]["name"] == "周守义"


def test_determinism_double_run(tmp_path):
    outs = []
    for i in (1, 2):
        out = tmp_path / f"out{i}"
        observe(PROJECT / "runs_v02" / "rule_v02c4_seed42", out)
        outs.append({p.name: p.read_bytes() for p in sorted(out.iterdir())})
    assert outs[0] == outs[1]


def test_input_hashes_unchanged(tmp_path):
    run_dir = PROJECT / "runs_v02" / "c5_lighthouse_seed7"
    files = ["world_log.json", "acp.sqlite3", "summary.json",
             "run_meta.json", "narrative.md"]
    before = {f: hashlib.sha256((run_dir / f).read_bytes()).hexdigest()
              for f in files}
    observe(run_dir, tmp_path / "out")
    after = {f: hashlib.sha256((run_dir / f).read_bytes()).hexdigest()
             for f in files}
    assert before == after


def test_import_isolation():
    pkg = PROJECT / "narrative"
    for py in pkg.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+v02\b", src, re.M), py
    # importing the package must not pull v02 into the interpreter
    code = ("import sys; sys.path.insert(0, %r); import narrative.observer; "
            "assert not any(m == 'v02' or m.startswith('v02.') "
            "for m in sys.modules), [m for m in sys.modules "
            "if m.startswith('v02')]; print('clean')") % str(PROJECT)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True)
    assert r.returncode == 0 and "clean" in r.stdout, r.stderr


def test_private_character_files_never_read(tmp_path):
    """Observer must not open run characters/* files (design §3.1 rule 3).

    Verified by observing a copy of a run whose characters/ dir was
    removed entirely: output must be identical to the full run's.
    """
    import shutil
    src = PROJECT / "runs_v02" / "rule_v02c4_seed42"
    bare = tmp_path / "bare_run"
    shutil.copytree(src, bare, ignore=shutil.ignore_patterns("characters"))
    out_bare = tmp_path / "out_bare"
    observe(bare, out_bare)
    ref = PROJECT / "narrative_runs" / "rule_v02c4_seed42"
    for name in ("observations.json", "frontier.json", "scenes.json"):
        assert (out_bare / name).read_bytes() == (ref / name).read_bytes()
