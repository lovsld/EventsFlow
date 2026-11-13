"""Goal Scheduler (V0.2-C C1, design section 2).

A PURE decision component: given one agent's goals, its current
plan, the tick and its own scheduler memory, it answers "which goal
is pursued this tick, which goals are pursuit-eligible, and what is
every eligible goal's utility". It writes NO goal state — the
GoalEvaluator remains the only writer of goal state; the scheduler's
conclusions take effect through two existing channels:

  1. the decision context (extras: scheduled_pursuit_goal_id,
     eligible_goal_ids, the full utility table) -> providers select
     and synthesize for the pursuit goal;
  2. the evaluator's pause POLICY (may_pause below): pausing is no
     longer an unconditional reaction to a plan switch — a goal the
     policy protects is not paused, and the contention is resolved
     at the plan layer instead (the incumbent plan suspends).

Utility (design 2.4):

  U(g) = priority_current(g)
       + DEP * sum(priority_current(h) for h in dependents(g)
                   if h is active-unfinished)
       + WAIT * min(1, (tick - last_pursued_tick(g)) / WAIT_SCALE)
              [never pursued counts as the full 1]
       + URG * 0        (no deadline semantics this round, by design)
       - SWITCH         (only for challengers while the incumbent's
                         plan commitment is at/above the gate)

Anti-thrash (design 2.5), all three layers: a hysteresis margin
(SWITCH_MARGIN) a challenger must beat; a minimum dwell
(MIN_DWELL ticks) before a fresh pursuit can be displaced; and the
starvation guarantee — a goal force-reactivated by the evaluator
after max_pause_ticks carries a protection window during which the
scheduler locks it as pursuit.

Coefficients are module configuration, calibrated by the
pre-registered grid experiment of design 2.8 (see
docs/V0.2-C_IMPLEMENTATION_REPORT.md); the runner snapshots them
into run_meta so every run records the values it ran under.
Determinism: enumeration is sorted, ties break on goal key.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class SchedulerConfig:
    dep: float = 0.4            # dependency traction (design 2.4)
    wait: float = 0.15          # aging weight for unpursued goals
    wait_scale: int = 10        # ticks over which aging saturates
    switch: float = 0.2         # switching cost vs a committed plan
    switch_margin: float = 0.10  # hysteresis a challenger must beat
    min_dwell: int = 2          # ticks a fresh pursuit cannot be displaced
    max_pause_ticks: int = 8    # global default pause cap


# Production values = the design 2.8 pre-registered grid calibration
# winner (18 combos screened on clinic 2 seeds + tavern 2 seeds; the
# winner rule — lowest tavern switching, then smallest DEP / WAIT,
# then margin nearest 0.10 — selected dep=0.4, wait=0.15,
# margin=0.10; the full table is in the C1 section of
# docs/V0.2-C_IMPLEMENTATION_REPORT.md). switch / wait_scale /
# min_dwell / max_pause_ticks were not grid dimensions and keep the
# design defaults.
CONFIG = SchedulerConfig()


def set_config(cfg: SchedulerConfig) -> None:
    """Replace the module configuration (calibration experiments and
    tests only; production runs use the calibrated CONFIG)."""
    global CONFIG
    CONFIG = cfg


def config_with(**overrides) -> SchedulerConfig:
    return replace(CONFIG, **overrides)


# ---------------- dependency structure ----------------

def dependents_map(goals) -> dict[str, list[str]]:
    """goal key -> keys of goals that declare it in depends_on."""
    out: dict[str, list[str]] = {g.key: [] for g in goals}
    for g in goals:
        for dep in g.depends_on or []:
            if dep in out:
                out[dep].append(g.key)
    return out


def deps_completed(goal, by_key: dict) -> bool:
    """Pursuit gating (design 2.2): every declared dependency must
    be completed. Unknown keys were rejected at load; a missing key
    here (hand-built state) gates closed rather than open."""
    for dep in goal.depends_on or []:
        other = by_key.get(dep)
        if other is None or other.status != "completed":
            return False
    return True


def eligible_goals(goals, by_key: dict) -> list:
    """The pursuit-eligible set: achievement goals that are active
    or paused (paused goals may be RE-selected — that is how a
    paused goal resumes) with all dependencies completed."""
    return [g for g in goals
            if g.kind == "achievement"
            and g.status in ("active", "paused")
            and deps_completed(g, by_key)]


# ---------------- pause policy (evaluator-side) ----------------

def may_pause(goal, tick: int, cfg: SchedulerConfig | None = None) -> bool:
    """May the GoalEvaluator pause this goal at this tick? Policy
    (design 2.1/2.3): maintenance goals never pause (pre-C rule,
    kept); pausable=False refuses; a live starvation-protection
    window refuses. The max_pause cap is enforced separately by
    forced reactivation in the evaluator, not here."""
    if goal.kind != "achievement":
        return False
    if not goal.pausable:
        return False
    if goal.protection_until is not None and tick <= goal.protection_until:
        return False
    return True


def max_pause_for(goal, cfg: SchedulerConfig | None = None) -> int | None:
    """The continuous-pause cap for a goal: its own declaration,
    else the global default. starvation_protection=False opts out
    of the cap entirely (unbounded waiting is the author's intent)."""
    if cfg is None:
        cfg = CONFIG
    if not goal.starvation_protection:
        return None
    if goal.max_pause_ticks is not None:
        return goal.max_pause_ticks
    return cfg.max_pause_ticks


# ---------------- utility ----------------

def utility(goal, goals, by_key: dict, dependents: dict[str, list[str]],
            tick: int, incumbent_key: str | None,
            plan_commitment: float | None,
            cfg: SchedulerConfig | None = None) -> float:
    if cfg is None:
        cfg = CONFIG
    u = goal.priority_current
    traction = 0.0
    for h_key in dependents.get(goal.key, []):
        h = by_key.get(h_key)
        if h is not None and h.status in ("active", "paused"):
            traction += h.priority_current
    u += cfg.dep * traction
    if goal.last_pursued_tick is None:
        aging = 1.0
    else:
        aging = min(1.0, max(0, tick - goal.last_pursued_tick)
                    / cfg.wait_scale)
    u += cfg.wait * aging
    # URG term: identically zero this round (design 2.4) — no
    # deadline semantics exist to ground it.
    if incumbent_key is not None and goal.key != incumbent_key \
            and plan_commitment is not None \
            and plan_commitment >= 0.7:
        u -= cfg.switch
    return round(u, 4)


# ---------------- the schedule decision ----------------

def schedule(goals, current_plan, tick: int, memory: dict,
             cfg: SchedulerConfig | None = None,
             newly_activated: frozenset | set = frozenset()) -> dict:
    """One scheduling pass (design 2.1: runs once per decide(),
    after PlanManager.reconcile, before build_context).

    Returns a plain dict:
      pursuit_goal_id   the goal pursued this tick (or None)
      eligible_goal_ids sorted pursuit-eligible goal keys
      utilities         {goal_key: utility} for the eligible set
      memory            the scheduler memory to store back
                        ({pursuit, since}) — the caller writes it;
                        this function mutates nothing it was given
      switched          True when the pursuit changed hands
    """
    if cfg is None:
        cfg = CONFIG
    by_key = {g.key: g for g in goals}
    dependents = dependents_map(goals)
    eligible = eligible_goals(goals, by_key)
    eligible_ids = sorted(g.key for g in eligible)
    plan_goal_id = current_plan.goal_id if current_plan is not None \
        else None
    plan_commitment = current_plan.commitment \
        if current_plan is not None else None
    incumbent = memory.get("pursuit")
    utilities = {
        g.key: utility(g, goals, by_key, dependents, tick,
                       incumbent_key=incumbent,
                       plan_commitment=plan_commitment, cfg=cfg)
        for g in eligible}

    def best_key(keys) -> str | None:
        if not keys:
            return None
        return sorted(keys, key=lambda k: (-utilities[k], k))[0]

    pursuit = None
    # Starvation guarantee (design 2.5 layer 3): a goal inside its
    # protection window is locked as pursuit, whatever the table
    # says, unless it completed/failed out of the eligible set.
    protected = [g.key for g in eligible
                 if g.protection_until is not None
                 and tick <= g.protection_until]
    if protected:
        pursuit = best_key(protected)
    elif not eligible:
        pursuit = None
    elif incumbent is None or incumbent not in utilities:
        # No incumbent (or it lost eligibility — completed, failed,
        # dependency-gated): the margin never blocks a necessary
        # handover (design 2.5 layer 1, waiver arm).
        pursuit = best_key(eligible_ids)
    else:
        challenger = best_key(eligible_ids)
        if challenger == incumbent:
            pursuit = incumbent
        else:
            since = int(memory.get("since", 0))
            dwell_ok = (tick - since) >= cfg.min_dwell
            margin_ok = utilities[challenger] > \
                utilities[incumbent] + cfg.switch_margin
            # Waivers: the incumbent's plan exists but is spent or
            # displaced (blocked / suspended / serving another
            # goal — nothing live to protect), or the challenger
            # was activated by a fresh event (activation preempts,
            # design 2.5 layer 2 — the preemption still counts as
            # a switch). Having NO plan at all is not a waiver: the
            # anti-thrash layers exist precisely for planless
            # contention.
            plan_spent = current_plan is not None and (
                plan_goal_id != incumbent
                or current_plan.status in ("blocked", "suspended",
                                           "completed", "abandoned"))
            fresh = challenger in newly_activated
            if plan_spent or fresh or (dwell_ok and margin_ok):
                pursuit = challenger
            else:
                pursuit = incumbent
    switched = pursuit != incumbent
    new_memory = {"pursuit": pursuit,
                  "since": tick if switched else int(memory.get("since", 0))}
    return {"pursuit_goal_id": pursuit,
            "previous_pursuit": incumbent,
            "eligible_goal_ids": eligible_ids,
            "utilities": utilities,
            "memory": new_memory,
            "switched": switched}
