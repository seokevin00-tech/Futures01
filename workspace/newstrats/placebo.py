"""The placebo cohort: strategies that are real in every respect except the signal.

A top-10 table without a control measures the *search*, not the strategies. The
only way to know how much of a ranking is search luck is to put deliberately
empty rule sets into the same ranking and see where they land. This module
builds them.

The whole design rests on one rule: **a placebo differs from a real strategy in
the entry signal and in nothing else.** It keeps the base strategy's

* exit model (stop kind, stop multiple, targets, scale-out, breakeven, time
  stop, session-close behaviour),
* scope filters (``StrategyFilters``: RTH, session, regime, volatility,
  minutes-since-open, alignment),
* FILTER conditions (``volatility_normal``, ``volume_not_thin``, ...),
* confirmation timeframes and execution timeframe,
* symbol, primary timeframe, allowed directions,

and is run through the same ``run_portfolio`` pass, the same cost model, the
same fill assumptions and the same clone collapse. If it scores well, the score
came from the exit geometry, the filters, the trade frequency or the search -
not from the signal.

Three kinds, and they are deliberately *not* three flavours of the same
randomisation. Each destroys a different channel of information, so the three
together localise where an apparent edge lives:

``placebo_random``
    Entry bars drawn at random, matched exactly on trade count and long/short
    mix. Destroys everything: when, and which way.

``placebo_shift``
    Every real signal displaced by ``SHIFT_BARS`` (5) bars. Count, direction
    mix, clustering, session distribution and regime distribution are all
    preserved exactly; only the alignment between the signal and the price
    action it claims to read is broken. An edge that survives a 5-bar shift was
    never about the bar it fired on.

``placebo_shuffle``
    The real signal's entry timestamps permuted among its own signals, which is
    the same thing as reassigning the direction labels at random across exactly
    the bars the real strategy traded. Timing, clustering, session, regime and
    count are all *real*; only the signal-to-direction pairing is destroyed. An
    edge that survives this was a property of *when* the strategy traded, not of
    which way it went.

Two matching subtleties, both of which are ways this could quietly cheat:

**Where the random draw comes from.** Drawing uniformly over every bar in the
window and then applying the base strategy's filters hands the placebo fewer
realised trades than the real strategy, because a random bar usually fails the
filters. A control with a systematically smaller sample is a handicapped
control, and a handicapped control makes the real strategies look better than
they are. So the default pool is the base strategy's own *eligible* bars -
every bar on which the scope filters pass, the FILTER conditions pass, a stop is
placeable and the reward:risk floor is met - measured per direction, because
stop placement and anchored targets are direction-dependent. ``pool="all"`` is
kept for the sanity check, which measures exactly how much that choice is worth.

**What the count is matched on.** The engine refuses a new signal while a
position is open, so realised trades are far fewer than raw signals. Matching a
placebo on *realised* trades therefore overshoots. Matching is done on the base
strategy's **raw** signal count - every bar it would have fired on, position
state ignored - so that after the engine's own culling the realised counts land
in the same place. ``schedule_vs_realised`` in the returned metadata reports how
well that worked, per placebo, so the assumption is auditable rather than
assumed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import random
from collections import Counter
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from futures_agents.features import FeatureSnapshot, SymbolFrame
from futures_agents.schema import Direction
from futures_agents.strategies.base import (Condition, ConditionKind,
                                            ConditionResult, Strategy)

__all__ = [
    "SHIFT_BARS", "KINDS", "SignalSchedule", "PlaceboMeta",
    "scripted_condition", "scan", "extract_signals", "eligible_bars",
    "build_cohort", "make_placebo", "null_rank_distribution",
]

#: Bars a ``placebo_shift`` signal is displaced by. Five is deliberately small:
#: big enough that the signal no longer reads the bar it claims to, small enough
#: that the market state (session, regime, volatility, neighbourhood) is
#: essentially unchanged. A shift that also moves the trade into a different
#: regime would be testing two things at once.
SHIFT_BARS = 5

KINDS = ("placebo_random", "placebo_shift", "placebo_shuffle")

#: bar index -> direction. Placebos are scheduled on bar INDEX and matched to
#: timestamps at build time, so a schedule cannot silently drift when the same
#: study is re-run over a different window of the same series.
SignalSchedule = Dict[int, Direction]


@dataclasses.dataclass
class PlaceboMeta:
    """Everything the ranker needs to tag a placebo, and an auditor to check it.

    This is the side channel. It is keyed by ``strategy_id`` and handed to the
    ranker separately, so the :class:`Strategy` objects themselves carry no mark
    that could leak into generation, execution or sorting.
    """

    strategy_id: str
    kind: str
    base_id: str
    base_name: str
    group: str
    n_real_signals: int
    n_scheduled: int
    n_long_scheduled: int
    n_short_scheduled: int
    base_realised: int
    pool: str
    #: Filled in by the ranker once the cohort has been run.
    realised: int = -1

    @property
    def schedule_vs_realised(self) -> Optional[float]:
        if self.realised < 0 or self.base_realised <= 0:
            return None
        return round(self.realised / self.base_realised, 3)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["schedule_vs_realised"] = self.schedule_vs_realised
        return d


# --------------------------------------------------------------------------
# The scripted entry condition
# --------------------------------------------------------------------------

def scripted_condition(name: str, schedule: Dict[datetime, Direction], *,
                       group: str = "structure") -> Condition:
    """A SIGNAL condition that fires exactly where it is told to.

    Keyed on the snapshot's timestamp rather than its index so it is immune to
    any re-indexing between construction and execution.

    The name **must** be unique per placebo. ``Condition.evaluate`` memoises on
    ``(name, timeframe)`` across every strategy sharing a bar, so two placebos
    with different schedules and the same condition name would silently return
    each other's answers. The name is derived from a hash of the schedule, so
    two placebos can only collide by being identical.
    """
    sched = dict(schedule)

    def fn(snap: FeatureSnapshot, tf: int) -> ConditionResult:
        d = sched.get(snap.ts)
        if d is None or d is Direction.NEUTRAL:
            return ConditionResult.no()
        return ConditionResult.yes(d, detail="scripted entry")

    return Condition(name=name, group=group, fn=fn, kind=ConditionKind.SIGNAL,
                     description="placebo scripted entry", warmup_bars=0)


def _digest(kind: str, base_id: str, schedule: SignalSchedule) -> str:
    key = f"{kind}|{base_id}|" + ",".join(
        f"{i}:{d.value}" for i, d in sorted(schedule.items()))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def make_placebo(base: Strategy, kind: str, schedule: SignalSchedule,
                 ts_of: Sequence[datetime]) -> Strategy:
    """One placebo built from ``base``: same everything, different signal.

    ``base``'s SIGNAL conditions are dropped and replaced by a single scripted
    condition. Its FILTER conditions, exit model, scope filters, confirmation
    timeframes and execution timeframe are carried across untouched, because the
    question is what the *signal* contributed and nothing else may vary.
    """
    digest = _digest(kind, base.strategy_id, schedule)
    cond = scripted_condition(f"plc_{kind}_{digest}",
                              {ts_of[i]: d for i, d in schedule.items()
                               if 0 <= i < len(ts_of)},
                              group=base.signal_conditions[0].group)
    filters = tuple(c for c in base.conditions if c.kind is ConditionKind.FILTER)
    return dataclasses.replace(
        base,
        name=f"{kind}__{base.name}"[:120],
        conditions=(cond,) + filters,
        trigger_conditions=(),
        _id=None,
    )


# --------------------------------------------------------------------------
# Reading the real signal out of a real strategy
# --------------------------------------------------------------------------

def extract_signals(frame: SymbolFrame, strategies: Sequence[Strategy], *,
                    start: int = 0, end: Optional[int] = None
                    ) -> Dict[str, SignalSchedule]:
    """Every bar each strategy would fire on, **position state ignored**.

    ``BacktestResult.signals_generated`` counts only signals raised while flat,
    so it is a realised-trade count in disguise and is the wrong thing to match
    a placebo against. This is the raw firing schedule.

    Runs the full ``Strategy.evaluate`` path, so a bar only appears here if the
    scope filters passed, every FILTER condition passed, the signals agreed on a
    direction, a stop was placeable outside the contract's noise floor and the
    reward:risk floor was met - i.e. exactly the bars the engine would have
    considered.
    """
    return scan(frame, strategies, want_pool=False, start=start, end=end)[0]


_ALWAYS = {Direction.LONG: "probe_long", Direction.SHORT: "probe_short"}


def _probes(strategies: Sequence[Strategy]) -> List[Tuple[str, Direction, Strategy]]:
    out: List[Tuple[str, Direction, Strategy]] = []
    for s in strategies:
        for d, tag in _ALWAYS.items():
            cond = Condition(
                name=f"{tag}_{s.strategy_id}", group=s.signal_conditions[0].group,
                fn=(lambda dd: (lambda snap, tf: ConditionResult.yes(dd)))(d),
                kind=ConditionKind.SIGNAL, warmup_bars=0)
            filt = tuple(c for c in s.conditions if c.kind is ConditionKind.FILTER)
            out.append((s.strategy_id, d, dataclasses.replace(
                s, name=f"probe_{d.value}", conditions=(cond,) + filt,
                trigger_conditions=(), allowed_directions=(d,), _id=None)))
    return out


def scan(frame: SymbolFrame, strategies: Sequence[Strategy], *,
         want_pool: bool = True, start: int = 0, end: Optional[int] = None):
    """One pass over the bars producing both the real schedules and the pools.

    ``SymbolFrame.snapshot`` rebuilds the cross-timeframe snapshot from scratch
    on every call - it is the single most expensive thing in this module - so
    the raw signal scan and the eligibility scan share one loop rather than
    paying for the bars twice.

    Returns ``(real_schedules, pools)`` where ``pools`` maps
    ``strategy_id -> {direction: [bar indices]}``: every bar on which the
    strategy *could* have entered in that direction. "Could" means exactly what
    the engine means - scope filters pass, FILTER conditions pass, a stop is
    placeable outside the noise floor, and the reward:risk floor is met.
    Measured per direction because stop placement (``STRUCTURE``,
    ``VWAP_BAND``) and anchored targets (``ANCHOR_STRUCTURE``) are not
    symmetric: a bar can be a legal long entry and an illegal short one.
    """
    bars = frame.base.bars
    stop_at = len(bars) if end is None else min(end, len(bars))
    sched: Dict[str, SignalSchedule] = {s.strategy_id: {} for s in strategies}
    pools: Dict[str, Dict[Direction, List[int]]] = {
        s.strategy_id: {Direction.LONG: [], Direction.SHORT: []} for s in strategies}
    probes = _probes(strategies) if want_pool else []
    for i in range(max(0, start), stop_at):
        snap = frame.snapshot(i)
        if snap is None:
            continue
        cache: Dict[Tuple[str, int], ConditionResult] = {}
        for s in strategies:
            sig = s.evaluate(snap, cache)
            if sig is not None:
                sched[s.strategy_id][i] = sig.direction
        for sid, d, probe in probes:
            if probe.evaluate(snap, cache) is not None:
                pools[sid][d].append(i)
    return sched, pools


def eligible_bars(frame: SymbolFrame, strategies: Sequence[Strategy], *,
                  start: int = 0, end: Optional[int] = None
                  ) -> Dict[str, Dict[Direction, List[int]]]:
    """Bars on which each strategy could have entered, per direction."""
    return scan(frame, strategies, want_pool=True, start=start, end=end)[1]


# --------------------------------------------------------------------------
# The three schedules
# --------------------------------------------------------------------------

def _schedule_random(real: SignalSchedule, pool: Dict[Direction, List[int]],
                     n_bars: int, rng: random.Random, pool_mode: str
                     ) -> SignalSchedule:
    """Random bars, matched exactly on count and long/short mix."""
    want = Counter(real.values())
    out: SignalSchedule = {}
    taken: set = set()
    for d in (Direction.LONG, Direction.SHORT):
        k = want.get(d, 0)
        if k <= 0:
            continue
        cand = (list(range(n_bars)) if pool_mode == "all"
                else [i for i in pool.get(d, ()) if i not in taken])
        cand = [i for i in cand if i not in taken]
        if not cand:
            continue
        # Fewer eligible bars than the real strategy had signals is possible
        # only if the real strategy re-fired on bars the probe rejected, which
        # cannot happen; the guard is here so a degenerate cell cannot raise.
        picks = rng.sample(cand, min(k, len(cand)))
        for i in picks:
            out[i] = d
            taken.add(i)
    return out


def _schedule_shift(real: SignalSchedule, n_bars: int) -> SignalSchedule:
    """Every real signal displaced forward by :data:`SHIFT_BARS` bars.

    Forward, never backward: a backward shift would place the entry *before* the
    information that produced it, which is look-ahead wearing a control's
    clothes. Forward is the honest direction - it can only ever know less.
    """
    out: SignalSchedule = {}
    for i, d in real.items():
        j = i + SHIFT_BARS
        if 0 <= j < n_bars:
            out[j] = d
    return out


def _schedule_shuffle(real: SignalSchedule, rng: random.Random) -> SignalSchedule:
    """The real signal's timestamps permuted among its own signals.

    With k signals at k timestamps, permuting the timestamps is exactly
    reassigning the direction labels across the same k bars. Everything about
    *when* the strategy traded is preserved bar for bar; only *which way* is
    destroyed.

    Note the consequence for a lopsided signal: a 90/10 long/short mix leaves
    ~82% of bars with their original direction, so this is a deliberately weak
    placebo for a directional rule set. ``direction_changed`` is reported so a
    reader can see how much information was actually destroyed rather than
    assuming it was all of it.
    """
    idx = sorted(real)
    dirs = [real[i] for i in idx]
    rng.shuffle(dirs)
    return {i: d for i, d in zip(idx, dirs)}


# --------------------------------------------------------------------------
# Cohort
# --------------------------------------------------------------------------

def build_cohort(frame: SymbolFrame, bases: Sequence[Strategy],
                 realised: Dict[str, int], *, seed: int = 0,
                 pool: str = "eligible", kinds: Sequence[str] = KINDS,
                 start: int = 0, end: Optional[int] = None,
                 ) -> Tuple[List[Strategy], Dict[str, PlaceboMeta], dict]:
    """Build one placebo per (base strategy, kind).

    Parameters
    ----------
    bases
        Real strategies to derive placebos from. These should be drawn from the
        strategies that actually entered the ranking, not from the whole
        generated population: a placebo derived from a rule set that never
        cleared the trade floor cannot clear it either, and a control that is
        always discarded is not a control.
    realised
        ``strategy_id -> realised trade count`` from the real run, carried into
        the metadata so ``schedule_vs_realised`` can be checked afterwards.
    pool
        ``"eligible"`` (default) draws random entries from the base strategy's
        own legal bars; ``"all"`` draws from every bar in the window. The
        difference is measured by :func:`rank.selftest`.

    Returns ``(strategies, meta_by_id, diagnostics)``.
    """
    rng = random.Random(f"placebo:{seed}:{frame.symbol}:{frame.base.minutes}")
    bars = frame.base.bars
    n_bars = len(bars) if end is None else min(end, len(bars))
    ts_of = [b.ts for b in bars]

    real_sched, pools = scan(frame, bases, want_pool=(pool == "eligible"),
                             start=start, end=end)
    if pool != "eligible":
        pools = {s.strategy_id: {Direction.LONG: list(range(n_bars)),
                                 Direction.SHORT: list(range(n_bars))}
                 for s in bases}

    out: List[Strategy] = []
    meta: Dict[str, PlaceboMeta] = {}
    diag = {"pool": pool, "shift_bars": SHIFT_BARS, "n_bases": len(bases),
            "eligible_rate": [], "direction_changed": [], "signal_rate": [],
            "dropped_empty": 0, "id_collisions": 0}

    for s in bases:
        real = real_sched.get(s.strategy_id, {})
        if len(real) < 2:
            diag["dropped_empty"] += 1
            continue
        pl = pools.get(s.strategy_id, {})
        diag["signal_rate"].append(round(len(real) / max(1, n_bars), 4))
        diag["eligible_rate"].append(round(
            (len(pl.get(Direction.LONG, ())) + len(pl.get(Direction.SHORT, ())))
            / max(1, 2 * n_bars), 4))
        for kind in kinds:
            if kind == "placebo_random":
                sched = _schedule_random(real, pl, n_bars, rng, pool)
            elif kind == "placebo_shift":
                sched = _schedule_shift(real, n_bars)
            elif kind == "placebo_shuffle":
                sched = _schedule_shuffle(real, rng)
                diag["direction_changed"].append(round(
                    sum(1 for i in sched if real.get(i) is not sched[i])
                    / max(1, len(sched)), 3))
            else:
                raise ValueError(f"unknown placebo kind {kind!r}")
            if len(sched) < 2:
                diag["dropped_empty"] += 1
                continue
            p = make_placebo(s, kind, sched, ts_of)
            if p.strategy_id in meta:
                diag["id_collisions"] += 1
                continue
            cnt = Counter(sched.values())
            meta[p.strategy_id] = PlaceboMeta(
                strategy_id=p.strategy_id, kind=kind, base_id=s.strategy_id,
                base_name=s.name, group=s.group, n_real_signals=len(real),
                n_scheduled=len(sched),
                n_long_scheduled=cnt.get(Direction.LONG, 0),
                n_short_scheduled=cnt.get(Direction.SHORT, 0),
                base_realised=int(realised.get(s.strategy_id, 0)), pool=pool)
            out.append(p)

    for k in ("eligible_rate", "direction_changed", "signal_rate"):
        v = diag.pop(k)
        diag[k + "_mean"] = round(sum(v) / len(v), 4) if v else None
    diag["n_placebos"] = len(out)
    return out, meta, diag


# --------------------------------------------------------------------------
# How to read a placebo rank
# --------------------------------------------------------------------------

def null_rank_distribution(n_total: int, n_placebo: int) -> dict:
    """Where the best placebo lands **if nothing in the cell has any edge**.

    This is the number every placebo rank has to be read against, and it is not
    intuitive. With ``k`` placebos among ``N`` rows and no real edge anywhere,
    the rank ``R`` of the best placebo satisfies

        P(R > r) = C(N-k, r) / C(N, r)

    (all of the top ``r`` rows are real), giving ``E[R] = (N+1)/(k+1)``. At the
    brief's 10% placebo share that expectation is about **11**, so a placebo
    landing 11th is not a scandal - it is exactly what a cell with no edge in it
    looks like. What would be informative is a placebo consistently *inside* the
    top few, or - in the other direction - placebos consistently far below 11,
    which would mean the matching is handicapping them.
    """
    if n_total <= 0 or n_placebo <= 0 or n_placebo > n_total:
        return {}
    surv = 1.0
    p_top1 = p_top5 = p_top10 = 0.0
    for r in range(1, n_total + 1):
        # P(R > r) = prod_{j=0..r-1} (N-k-j)/(N-j)
        if n_total - n_placebo - (r - 1) <= 0:
            surv = 0.0
        else:
            surv *= (n_total - n_placebo - (r - 1)) / (n_total - (r - 1))
        if r == 1:
            p_top1 = 1.0 - surv
        if r == 5:
            p_top5 = 1.0 - surv
        if r == 10:
            p_top10 = 1.0 - surv
        if surv <= 0.0:
            break
    return {"n_total": n_total, "n_placebo": n_placebo,
            "expected_best_rank": round((n_total + 1) / (n_placebo + 1), 2),
            "p_best_placebo_rank_1": round(p_top1, 4),
            "p_best_placebo_in_top5": round(p_top5, 4),
            "p_best_placebo_in_top10": round(p_top10, 4)}
