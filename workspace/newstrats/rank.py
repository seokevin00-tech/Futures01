"""The recurring profitability ranking, with its control arm built in.

One entry point - :func:`rank_cell` - takes ``(symbol, timeframe, window_days)``
and returns the cell: the real population plus roughly 10% placebos, all run
through the same engine, clones collapsed on realised trades, ranked by
expectancy in R net of costs.

The five things this file is careful about, each of which is a way a ranking
lies:

**The control is invisible to the sort.** Placebos are ordinary
:class:`Strategy` objects carrying an ordinary ``group``; the only thing that
marks them is a side-channel dict keyed by ``strategy_id``. The ordering is
computed by :func:`_order` from rows with every tag key *deleted*, and
:func:`_assert_tag_blind` re-runs the sort under permuted tags and asserts the
order is byte-identical. A control the ranker can see is not a control.

**Clones are collapsed before counting.** Filter variants that veto nothing
produce an identical trade set; without collapse the top ten is routinely the
top one listed ten times. Collapse is on the realised trades and the survivor is
the member with the lexicographically smallest ``strategy_id`` - a content hash,
which knows nothing about who is real - so a placebo colliding with a real
strategy is not systematically the one deleted.

**Deflation is charged against everything screened**, reals and placebos,
pre-collapse. Selecting ten from four thousand and then deflating against ten
hides the search entirely.

**The floored ranking is reported next to the floor-free census.** A 20-trade
floor selects on exit geometry, not on signal quality: a 0.75-ATR stop yields
several times the trades the same entry yields at 1.5 ATR, so a ranking at a
floor is partly a ranking of tight stops.

**A placebo rank is read against its null.** With ``k`` placebos among ``N``
rows and no edge anywhere, the best placebo's expected rank is ``(N+1)/(k+1)``,
which at a 10% share is about 11. A placebo at rank 11 is what a cell with
nothing in it looks like; a placebo at rank 1 or 2 is the finding.
``null_rank`` is returned in every cell so the comparison is unavoidable.
"""

from __future__ import annotations

import dataclasses
import math
import os
import random
import statistics as st
import sys
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")

import toolkit as T                                            # noqa: E402
import placebo as P                                            # noqa: E402
from futures_agents.backtest.engine import run_portfolio       # noqa: E402
from futures_agents.backtest.metrics import compute_metrics    # noqa: E402
from futures_agents.data.bars import BarSeries                 # noqa: E402
from futures_agents.features import build_symbol_frame         # noqa: E402
from futures_agents.scout import FRAMES                        # noqa: E402
from futures_agents.schema import Direction                    # noqa: E402
from futures_agents.strategies.base import (ConditionKind, Strategy,  # noqa: E402
                                            StrategyFilters)
from futures_agents.strategies.combinator import (TEMPLATES,   # noqa: E402
                                                  generate_strategies)

__all__ = ["rank_cell", "selftest", "FLOOR", "TAG_KEYS"]

FLOOR = 20

#: Keys the sort is forbidden to see. Deleted from the row before ordering and
#: re-attached afterwards.
TAG_KEYS = ("is_placebo", "placebo_kind", "base_id", "base_name",
            "n_placebo_clones", "n_real_clones")

#: Groups that are arithmetically dead on a given timeframe, dropped so they do
#: not consume budget and inflate the deflation penalty for nothing.
#:
#: OPENING_RANGE at 60m/240m: an L-minute opening range is resolvable on a
#: T-minute frame iff ``T | L`` and ``T | (RTH open in minutes from midnight)``
#: (D39). 240m divides no OR length at all; 09:30 = 570 and 08:20 = 500 are not
#: divisible by 60. The template's ``max_minutes_since_open=150`` then cannot be
#: satisfied by a 240-minute bar either (D21). Measured here: 76 rule sets, 10
#: trades between them.
DEAD_GROUPS: Dict[int, Tuple[str, ...]] = {60: ("OPENING_RANGE",),
                                           240: ("OPENING_RANGE",)}


# --------------------------------------------------------------------------
# Population
# --------------------------------------------------------------------------

def _frame(symbol: str, tf: int, window_days: Optional[int],
           slice_bounds: Optional[Tuple[int, int]]):
    """The bars for one cell: a trailing window, or a disjoint slice."""
    if slice_bounds is not None:
        series = T.slice_series(symbol, tf, slice_bounds[0], slice_bounds[1])
    else:
        series = T._series(symbol, tf, window_days)
    if len(series) < 120:
        return None, series
    return build_symbol_frame(series, FRAMES[tf]), series


def _population(symbol: str, tf: int, *, budget: int, seed: int,
                groups: Optional[Sequence[str]], rth_only: bool,
                drop_session_close_exits: bool) -> Tuple[List[Strategy], dict]:
    """The real strategies for one cell, with the known-dead ones removed.

    Two deliberate departures from the library defaults, both applied
    identically to reals and placebos so no comparison is affected:

    ``rth_only=False``
        ``StrategyFilters.rth_only`` defaults True and is the library-wide 4h
        population killer (D24): it reduces the 4h population to one bar per day
        and costs every group a 2-12x factor. Measured here on MGC 60m over 274
        days, 300 rule sets: 102 trades with it on, 377 with it off, and 0 vs 6
        clearing a 20-trade floor. ``toolkit.make_strategy`` already defaults it
        off for exactly this reason; this does the same to the generated
        population and says so.

    ``drop_session_close_exits`` at 240m
        ``exit_at_session_close=True`` closes a 4-hour trade on its entry bar -
        median hold 0.0 minutes - so those rule sets are not slow strategies
        with a bad exit, they are a different instrument. Dropping them at 240m
        stops 60% of the budget being spent measuring the same degeneracy and
        stops it inflating the deflation charge. At 60m they are kept: a
        60-minute bar can sit inside a session, so the exit is a real choice.
    """
    wanted = [t.group for t in TEMPLATES] if groups is None else list(groups)
    dead = set(DEAD_GROUPS.get(tf, ()))
    wanted = [g for g in wanted if g not in dead]
    strats = [s for s in generate_strategies(
        symbol, FRAMES[tf], groups=wanted, max_total=99999,
        max_per_template=budget, seed=seed) if s.primary_tf == tf]
    raw = len(strats)
    if not rth_only:
        strats = [dataclasses.replace(
            s, filters=dataclasses.replace(s.filters, rth_only=False), _id=None)
            for s in strats]
    n_after_rth = len(strats)
    if drop_session_close_exits:
        strats = [s for s in strats if not s.exit.exit_at_session_close]
    diag = {"generated": raw, "after_rth_override": n_after_rth,
            "after_exit_prune": len(strats),
            "dead_groups_dropped": sorted(dead),
            "rth_only": rth_only,
            "dropped_session_close_exits": drop_session_close_exits}
    return strats, diag


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

def _row(s: Strategy, trades, *, full: bool = False) -> dict:
    m = compute_metrics(trades)
    lo, hi = T.wilson(m.wins, m.trades)
    row = dict(
        id=s.strategy_id, name=s.name, group=s.group, symbol=s.symbol,
        tf=s.primary_tf,
        conditions=[c.name for c in s.conditions],
        signals=[c.name for c in s.conditions if c.kind is ConditionKind.SIGNAL],
        filters=[c.name for c in s.conditions if c.kind is ConditionKind.FILTER],
        stop_kind=str(s.exit.stop_kind), stop_mult=s.exit.stop_mult,
        target_kind=str(s.exit.target_kind),
        anchor_mult=list(s.exit.anchor_mult), targets_r=list(s.exit.targets_r),
        session_close_exit=s.exit.exit_at_session_close,
        n=m.trades, win=round(m.win_rate, 4),
        win_lo=round(lo, 4), win_hi=round(hi, 4),
        exp=round(m.expectancy_r, 5), payoff=round(m.payoff_ratio, 4),
        pf=round(m.profit_factor, 4), maxdd=round(m.max_drawdown_r, 3),
        avgdd=round(m.avg_drawdown_r, 3), t=round(m.t_statistic, 3),
        sharpe=round(m.sharpe, 3), sortino=round(m.sortino, 3),
        avg_win=round(m.avg_win_r, 4), avg_loss=round(m.avg_loss_r, 4),
        max_cons_win=m.max_consecutive_wins, max_cons_loss=m.max_consecutive_losses,
        avg_minutes=round(m.avg_minutes_held, 1),
        mae=round(m.avg_mae_r, 4), mfe=round(m.avg_mfe_r, 4),
        longs=m.long_trades, shorts=m.short_trades,
        fp=T.fingerprint(trades), clones=1)
    if full:
        row["metrics"] = m.to_dict()
        row["by_session"] = _slice(trades, lambda t: t.session)
        row["by_regime"] = _slice(trades, lambda t: t.volatility)
        row["by_bucket"] = _slice(trades, lambda t: t.time_bucket)
        row["exit_reasons"] = dict(Counter(t.exit_reason.value for t in trades))
    return row


def _slice(trades, key) -> dict:
    out = {}
    buckets = defaultdict(list)
    for t in trades:
        buckets[key(t) or "?"].append(t)
    for k, v in sorted(buckets.items()):
        if len(v) < 3:
            continue
        m = compute_metrics(v)
        out[k] = {"n": m.trades, "exp": round(m.expectancy_r, 4),
                  "win": round(m.win_rate, 3)}
    return out


# --------------------------------------------------------------------------
# The sort, which must never see the tag
# --------------------------------------------------------------------------

def _order(rows: Sequence[dict]) -> List[int]:
    """Indices of ``rows`` ordered by expectancy, tag keys deleted first.

    The tiebreak is the realised-trade fingerprint, which is a hash of the
    trades themselves: deterministic, and derived from nothing that knows
    whether the row is a control.
    """
    blind = []
    for i, r in enumerate(rows):
        b = {k: v for k, v in r.items() if k not in TAG_KEYS}
        blind.append((-b["exp"], -b["n"], b["fp"], i))
    blind.sort()
    return [i for *_, i in blind]


def _assert_tag_blind(rows: Sequence[dict]) -> None:
    """Permute every tag and assert the ordering does not move."""
    base = _order(rows)
    rng = random.Random(7)
    shuffled = [dict(r) for r in rows]
    tags = [{k: r.get(k) for k in TAG_KEYS} for r in rows]
    rng.shuffle(tags)
    for r, t in zip(shuffled, tags):
        r.update(t)
    if _order(shuffled) != base:
        raise AssertionError("ranking is not tag-blind - the sort can see the control")


# --------------------------------------------------------------------------
# Clone collapse
# --------------------------------------------------------------------------

def _collapse(rows: Sequence[dict]) -> Tuple[List[dict], dict]:
    """Merge rows whose realised trades are identical.

    The survivor is the smallest ``strategy_id``. That is a content hash of the
    rule set, so which member survives is independent of whether it is a
    control - whereas "first one wins" over a list that happens to put the reals
    first would delete every placebo that collided with one.
    """
    by_fp: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_fp[r["fp"]].append(r)
    out, mixed, cross_exit, cross_exp = [], 0, 0, 0
    for fp, group in by_fp.items():
        group = sorted(group, key=lambda r: r["id"])
        keep = dict(group[0])
        keep["clones"] = len(group)
        keep["n_placebo_clones"] = sum(1 for r in group if r.get("is_placebo"))
        keep["n_real_clones"] = len(group) - keep["n_placebo_clones"]
        if keep["n_placebo_clones"] and keep["n_real_clones"]:
            mixed += 1
        # toolkit.fingerprint keys on (entry timestamp, direction) and ignores
        # the exit, so two rule sets with identical entries and different stop
        # geometry collapse into one. Usually they cannot have identical
        # entries - a different stop changes how long the position is held and
        # therefore which later signals are skipped - but "usually" is not
        # "never", so the exceptions are counted rather than assumed away.
        if len({(r["stop_kind"], r["stop_mult"], r["target_kind"]) for r in group}) > 1:
            cross_exit += 1
        if len({round(r["exp"], 4) for r in group}) > 1:
            cross_exp += 1
        out.append(keep)
    return out, {"pre_collapse": len(rows), "post_collapse": len(out),
                 "duplicate_rows_removed": len(rows) - len(out),
                 "mixed_real_placebo_clones": mixed,
                 "groups_spanning_different_exits": cross_exit,
                 "groups_spanning_different_expectancy": cross_exp}


# --------------------------------------------------------------------------
# The cell
# --------------------------------------------------------------------------

def rank_cell(symbol: str, timeframe: int, window_days: Optional[int] = None, *,
              budget: int = 2400, seed: int = 1, floor: int = FLOOR,
              placebo_frac: float = 0.10, min_placebos: int = 12,
              max_placebos: int = 240, groups: Optional[Sequence[str]] = None,
              slice_bounds: Optional[Tuple[int, int]] = None,
              rth_only: bool = False,
              drop_session_close_exits: Optional[bool] = None,
              pool: str = "eligible",
              watch_ids: Optional[Sequence[str]] = None,
              verbose: bool = True) -> dict:
    """Rank one (symbol, timeframe, window) cell, control arm included.

    ``watch_ids`` names strategies whose result is wanted **whatever their
    trade count**, reported in ``watch`` outside the ranking. That is what the
    replication column needs: a 9-month top-10 strategy that takes 11 trades in
    one disjoint third has not failed to replicate, it has failed to reach the
    floor, and those are different findings. ``strategy_id`` is a content hash
    of the rule set and is stable across windows, so the same row can be
    followed from one period to another.

    ``slice_bounds`` takes a ``(start_days_ago, end_days_ago)`` pair from
    ``toolkit.disjoint_slices`` and replaces the trailing window, which is how
    the replication column is produced. The trailing windows are nested by
    construction - 30d is a subset of 90d is a subset of 180d - so a strategy in
    all four top tens is one observation seen four times. The disjoint slices
    are the only genuinely independent periods here.
    """
    t0 = time.time()
    if drop_session_close_exits is None:
        drop_session_close_exits = timeframe >= 240
    frame, series = _frame(symbol, timeframe, window_days, slice_bounds)
    if frame is None:
        return {"symbol": symbol, "tf": timeframe, "window": window_days,
                "slice": slice_bounds, "skipped": f"only {len(series)} bars"}

    reals, popdiag = _population(
        symbol, timeframe, budget=budget, seed=seed, groups=groups,
        rth_only=rth_only, drop_session_close_exits=drop_session_close_exits)
    res_real = run_portfolio(frame, reals)
    if verbose:
        print(f"  [{symbol} {timeframe}m w={window_days} s={slice_bounds}] "
              f"{len(reals)} reals run in {time.time()-t0:.0f}s", flush=True)

    realised = {s.strategy_id: len(res_real[s.strategy_id].trades) for s in reals}
    cleared = _ranked_reals(reals, res_real, floor)

    # ---- placebos, derived from the strategies that actually got ranked ----
    target = min(max_placebos,
                 max(min_placebos, int(round(placebo_frac * max(1, len(cleared))))))
    n_bases = max(1, int(math.ceil(target / len(P.KINDS))))
    bases = _stratified(cleared, n_bases, seed, realised)
    placebos, meta, plcdiag = ([], {}, {"n_placebos": 0, "note": "no base cleared the floor"})
    res_plc: dict = {}
    if bases:
        placebos, meta, plcdiag = P.build_cohort(
            frame, bases, realised, seed=seed, pool=pool)
        # Canaries: a handful of reals re-run inside the placebo pass. If the
        # second pass does not reproduce the first exactly, the two arms were
        # not run under identical conditions and nothing below is comparable.
        canaries = bases[:5]
        res_plc = run_portfolio(frame, list(placebos) + list(canaries))
        bad = [c.strategy_id for c in canaries
               if T.fingerprint(res_plc[c.strategy_id].trades)
               != T.fingerprint(res_real[c.strategy_id].trades)]
        plcdiag["canaries_checked"] = len(canaries)
        plcdiag["canaries_failed"] = bad
        if bad:
            raise AssertionError(f"placebo pass did not reproduce reals: {bad}")

    # ---- rows ----------------------------------------------------------
    rows: List[dict] = []
    for s in reals:
        tr = res_real[s.strategy_id].trades
        if len(tr) >= floor:
            r = _row(s, tr)
            r.update(is_placebo=False, placebo_kind=None, base_id=None,
                     base_name=None)
            rows.append(r)
    for s in placebos:
        tr = res_plc[s.strategy_id].trades
        m = meta[s.strategy_id]
        m.realised = len(tr)
        if len(tr) >= floor:
            r = _row(s, tr)
            r.update(is_placebo=True, placebo_kind=m.kind, base_id=m.base_id,
                     base_name=m.base_name)
            rows.append(r)

    collapsed, clonediag = _collapse(rows)
    _assert_tag_blind(collapsed)
    order = _order(collapsed)
    ranked = []
    for pos, i in enumerate(order, 1):
        r = dict(collapsed[i])
        r["rank"] = pos
        ranked.append(r)

    n_screened = len(reals) + len(placebos)
    ft = T.free_t(n_screened)
    for r in ranked:
        r["free_t"] = round(ft, 3)
        r["clears_free_t"] = bool(r["t"] > ft)

    watch: Dict[str, dict] = {}
    if watch_ids:
        want = set(watch_ids)
        for s in reals:
            if s.strategy_id in want:
                tr = res_real[s.strategy_id].trades
                w = (_row(s, tr) if tr else
                     {"id": s.strategy_id, "name": s.name, "group": s.group,
                      "n": 0, "exp": None, "t": None})
                w["below_floor"] = len(tr) < floor
                w["in_ranking"] = False
                watch[s.strategy_id] = w
        for k, v in watch.items():
            v["in_ranking"] = any(r["id"] == k for r in rows)
        watch["_missing"] = sorted(want - set(watch))   # not generated in this cell

    plc_ranks = [r["rank"] for r in ranked if r["is_placebo"]]
    best = min(plc_ranks) if plc_ranks else None
    by_kind: Dict[str, Optional[int]] = {}
    for k in P.KINDS:
        rr = [r["rank"] for r in ranked if r["placebo_kind"] == k]
        by_kind[k] = min(rr) if rr else None

    # ---- floor-free census --------------------------------------------
    census = _census(reals, res_real, placebos, res_plc, meta, floor)

    out = {
        "symbol": symbol, "tf": timeframe, "window": window_days,
        "slice": list(slice_bounds) if slice_bounds else None,
        "bars": len(series),
        "span": [str(series.bars[0].ts), str(series.bars[-1].ts)],
        "floor": floor, "seed": seed, "budget": budget,
        "n_screened": n_screened, "n_reals_run": len(reals),
        "n_placebos_run": len(placebos),
        "placebo_share": round(len(placebos) / max(1, n_screened), 4),
        "placebo_row_share": round(len(plc_ranks) / max(1, len(ranked)), 4),
        "free_t": round(ft, 3),
        "n_rows": len(ranked),
        "n_rows_placebo": len(plc_ranks),
        "n_clear_free_t": sum(1 for r in ranked if r["clears_free_t"]),
        "n_positive": sum(1 for r in ranked if r["exp"] > 0),
        "best_placebo_rank": best,
        "best_placebo_rank_by_kind": by_kind,
        "placebo_ranks": sorted(plc_ranks),
        "null_rank": P.null_rank_distribution(len(ranked), len(plc_ranks)),
        "population": popdiag, "placebo_build": plcdiag, "clones": clonediag,
        "census": census,
        "match_audit": _match_audit(ranked, meta),
        "watch": watch,
        "rows": ranked,
        "seconds": round(time.time() - t0, 1),
    }
    # Full metrics only for the head of the table and for every control, which
    # is where the reporting detail is actually read.
    keep_full = {r["id"] for r in ranked[:25]} | {r["id"] for r in ranked if r["is_placebo"]}
    lookup = {s.strategy_id: (s, res_real[s.strategy_id].trades) for s in reals}
    lookup.update({s.strategy_id: (s, res_plc[s.strategy_id].trades) for s in placebos})
    for r in ranked:
        if r["id"] in keep_full and r["id"] in lookup:
            s, tr = lookup[r["id"]]
            r.update({k: v for k, v in _row(s, tr, full=True).items()
                      if k in ("metrics", "by_session", "by_regime", "by_bucket",
                               "exit_reasons")})
    if verbose:
        print(f"  -> {len(ranked)} rows, best placebo rank {best} "
              f"(null E={out['null_rank'].get('expected_best_rank')}), "
              f"free_t {ft:.2f}, {out['n_clear_free_t']} clear it, "
              f"{out['seconds']}s", flush=True)
    return out


def _ranked_reals(reals: Sequence[Strategy], res, floor: int) -> List[Strategy]:
    """The real strategies that will actually appear as rows, clones removed.

    Bases have to be drawn from here, not from every strategy that cleared the
    floor. Clone collapse is not neutral in the trade count: rule sets that
    differ only by an inert filter produce identical trades and collapse to one
    row, and those duplicates are not spread evenly over the trade-count range.
    Drawing bases before the collapse gave a control arm with a median of 34
    trades against 56 for the rows it was ranked against - the second half of
    the same matching defect the first version of :func:`_stratified` had.

    Dedupe order matches :func:`_collapse` exactly (smallest ``strategy_id``
    survives), so the base pool is the ranked population and not an
    approximation of it.
    """
    seen, out = set(), []
    for s in sorted(reals, key=lambda x: x.strategy_id):
        tr = res[s.strategy_id].trades
        if len(tr) < floor:
            continue
        fp = T.fingerprint(tr)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(s)
    return out


def _stratified(cleared: Sequence[Strategy], n: int, seed: int,
                realised: Optional[Dict[str, int]] = None) -> List[Strategy]:
    """Draw base strategies matched to the ranked population's trade counts.

    A placebo inherits its base's trade frequency almost exactly - measured
    ratio of realised placebo trades to base trades, 1.00 - so the control
    arm's trade-count distribution *is* the base set's. If that does not match
    the population being controlled for, the comparison is rigged before a
    single trade is simulated, and it is rigged in the direction that flatters
    the real rows: a control with half the sample has a wider expectancy
    distribution and a worse floor survival rate than the thing it controls
    for.

    **Two versions of this function were wrong and the uniformity check caught
    both**, which is the entire argument for running that check.

    *Version 1* stratified by group and by trade-count quantile, interleaving
    each group's lowest and highest and round-robining across groups. With
    roughly as many bases as groups - exactly the regime at a 10% placebo
    share - every group contributed only its lowest member. Measured across 23
    cells: median placebo trade count 23-30 against 43-68 for the rows it was
    ranked against, mean normalised placebo rank 0.57 rather than 0.50.

    *Version 2* drew a stratified random sample by group, which is unbiased in
    expectation but has nothing holding the trade count in place at eight or
    ten bases; on MGC 60m it produced a base median of 96 trades against a
    population median of 56, an error the same size in the other direction.

    So the quantity that has to match is stratified on directly. Bases are
    allocated evenly across the ranked population's trade-count quartiles, and
    within a quartile the draw is random but prefers groups not yet
    represented, so families are covered without that coverage being allowed to
    distort the trade counts. Group coverage is the secondary objective because
    it is the one whose failure is merely unrepresentative; trade-count
    mismatch is the one whose failure is a bias.
    """
    if not cleared:
        return []
    n = min(n, len(cleared))
    if n <= 0:
        return []
    rng = random.Random(f"bases:{seed}")
    key = (lambda s: (realised.get(s.strategy_id, 0), s.strategy_id)) if realised \
        else (lambda s: s.strategy_id)
    pool = sorted(cleared, key=key)

    n_bins = min(4, n)
    bins: List[List[Strategy]] = []
    for i in range(n_bins):
        lo = len(pool) * i // n_bins
        hi = len(pool) * (i + 1) // n_bins
        b = pool[lo:hi]
        rng.shuffle(b)
        bins.append(b)
    quota = [n // n_bins] * n_bins
    for i in range(n % n_bins):
        quota[i] += 1

    out: List[Strategy] = []
    used: set = set()
    for b, q in zip(bins, quota):
        picked = 0
        # first pass: prefer a group not already in the control arm
        for s in list(b):
            if picked >= q:
                break
            if s.group not in used:
                out.append(s)
                used.add(s.group)
                b.remove(s)
                picked += 1
        # second pass: fill the quartile's remaining seats at random
        while picked < q and b:
            out.append(b.pop())
            picked += 1
    # any seats a short quartile could not fill
    leftover = [s for bn in bins for s in bn]
    rng.shuffle(leftover)
    while len(out) < n and leftover:
        out.append(leftover.pop())
    return out[:n]


def _census(reals, res_real, placebos, res_plc, meta, floor) -> dict:
    """The floor-free picture, so the floored ranking can be read honestly.

    The 20-trade floor selects on exit geometry: a tight stop produces several
    times the trades the same entry produces at a wide one, so the floored
    ranking is partly a ranking of tight stops. This reports what the floor
    removed, by trade count and by stop multiple.
    """
    def summarise(items, res, tag):
        ns = [len(res[s.strategy_id].trades) for s in items]
        if not ns:
            return {}
        exps, tight, wide = [], [], []
        for s in items:
            tr = res[s.strategy_id].trades
            if not tr:
                continue
            e = compute_metrics(tr).expectancy_r
            exps.append(e)
            (tight if s.exit.stop_mult <= 1.0 else wide).append(len(tr))
        return {
            "arm": tag, "n_strategies": len(items),
            "n_zero_trade": sum(1 for x in ns if x == 0),
            "n_ge_floor": sum(1 for x in ns if x >= floor),
            "median_trades": st.median(ns),
            "median_trades_nonzero": st.median([x for x in ns if x]) if any(ns) else 0,
            "median_exp_all_nonzero": round(st.median(exps), 4) if exps else None,
            "pct_positive_nonzero": (round(sum(1 for e in exps if e > 0) / len(exps), 3)
                                     if exps else None),
            "median_trades_stop_le_1atr": st.median(tight) if tight else None,
            "median_trades_stop_gt_1atr": st.median(wide) if wide else None,
        }
    return {"real": summarise(reals, res_real, "real"),
            "placebo": summarise(placebos, res_plc, "placebo") if placebos else {}}


def _match_audit(ranked: Sequence[dict], meta: Dict[str, P.PlaceboMeta]) -> dict:
    """Is the control arm actually matched, or is it quietly handicapped?

    A control with systematically fewer trades than the strategies it is meant
    to control for is not a control - the floor removes it more often and the
    survivors are a luckier subset. These are the numbers that decide whether
    the whole exercise means anything.
    """
    reals = [r for r in ranked if not r["is_placebo"]]
    plcs = [r for r in ranked if r["is_placebo"]]
    ratios = [m.schedule_vs_realised for m in meta.values()
              if m.schedule_vs_realised is not None]
    out = {
        "n_real_rows": len(reals), "n_placebo_rows": len(plcs),
        "median_n_real": st.median([r["n"] for r in reals]) if reals else None,
        "median_n_base": (st.median([m.base_realised for m in meta.values()])
                          if meta else None),
        "median_n_placebo": st.median([r["n"] for r in plcs]) if plcs else None,
        "median_realised_over_base": round(st.median(ratios), 3) if ratios else None,
        "floor_survival_placebo": (round(len(plcs) / len(meta), 3) if meta else None),
        "long_share_real": _long_share(reals),
        "long_share_placebo": _long_share(plcs),
    }
    if reals and plcs:
        out["n_rank_sum"] = T.mann_whitney_u([r["n"] for r in plcs],
                                             [r["n"] for r in reals])
        out["exp_rank_sum"] = T.mann_whitney_u([r["exp"] for r in plcs],
                                               [r["exp"] for r in reals])
    return out


def _long_share(rows) -> Optional[float]:
    tot = sum(r["longs"] + r["shorts"] for r in rows)
    return round(sum(r["longs"] for r in rows) / tot, 3) if tot else None


# --------------------------------------------------------------------------
# Sanity check
# --------------------------------------------------------------------------

def selftest(symbol: str, timeframe: int, window_days: int = 274, *,
             budget: int = 900, seed: int = 1, floor: int = FLOOR,
             reps: int = 400, verbose: bool = True) -> dict:
    """Three checks that have to pass before any cell above means anything.

    A note on what a self-test *cannot* be. The obvious version - build an
    all-placebo cohort, declare a random 10% of the rows to be "the control",
    check their ranks are uniform - is a tautology: a random subset of a fixed
    ordering has uniform ranks whatever the ordering is, so it passes even if
    the machinery is badly broken. It is written here in a form that can fail.

    **1. Tag-blindness.** :func:`_assert_tag_blind` re-sorts under permuted tags
    and requires a byte-identical order, so the control cannot influence its own
    position. Cheap, and it is the only one of the three that is a proof rather
    than a measurement.

    **2. Kind against kind, on an all-placebo cohort.** Every row is null, so
    the three kinds are ranked against each other with no real edge anywhere.
    ``placebo_random`` - the kind whose entries are relocated and therefore the
    one exposed to the count-matching machinery - is declared the control and
    its normalised ranks are tested against U(0,1). If count matching handicaps
    a relocated entry, ``placebo_random`` piles up at the bottom against
    ``placebo_shift`` and ``placebo_shuffle``, which keep the real bar locations
    and pay no matching cost. This one can fail.

    **3. Matching audit against the real arm.** The dangerous failure is the
    placebo machinery handicapping placebos *relative to real strategies* -
    fewer trades, worse floor survival, a skewed long/short mix - because that
    is a bias whose sign is always favourable to the real arm, and D35 is the
    standing reminder that resampling cannot catch one of those. Only a direct
    audit can. Run for both random-entry pools (``eligible`` and ``all``) so the
    cost of that choice is measured rather than argued.
    """
    frame, series = _frame(symbol, timeframe, window_days, None)
    if frame is None:
        return {"skipped": f"only {len(series)} bars"}
    reals, _ = _population(symbol, timeframe, budget=budget, seed=seed,
                           groups=None, rth_only=False,
                           drop_session_close_exits=timeframe >= 240)
    res_real = run_portfolio(frame, reals)
    realised = {s.strategy_id: len(res_real[s.strategy_id].trades) for s in reals}
    cleared = _ranked_reals(reals, res_real, floor)
    if len(cleared) < 6:
        return {"skipped": f"only {len(cleared)} reals cleared the floor"}
    bases = _stratified(cleared, min(40, len(cleared)), seed, realised)

    out: dict = {"symbol": symbol, "tf": timeframe, "window": window_days,
                 "n_reals": len(reals), "n_cleared": len(cleared),
                 "n_bases": len(bases), "reps": reps}

    for pool in ("eligible", "all"):
        placebos, meta, diag = P.build_cohort(frame, bases, realised,
                                              seed=seed, pool=pool)
        res = run_portfolio(frame, placebos)
        rows = []
        for s in placebos:
            tr = res[s.strategy_id].trades
            meta[s.strategy_id].realised = len(tr)
            if len(tr) >= floor:
                r = _row(s, tr)
                r.update(is_placebo=True, placebo_kind=meta[s.strategy_id].kind,
                         base_id=meta[s.strategy_id].base_id, base_name=None)
                rows.append(r)
        coll, _ = _collapse(rows)
        N = len(coll)
        arm = {"pool": pool, "n_placebos_run": len(placebos), "n_rows": N,
               "build": diag,
               "floor_survival": round(len(rows) / max(1, len(placebos)), 3),
               "median_n": st.median([r["n"] for r in coll]) if coll else None,
               "median_realised_over_base": _med(
                   [m.schedule_vs_realised for m in meta.values()
                    if m.schedule_vs_realised is not None])}
        if N >= 10:
            _assert_tag_blind(coll)
            order = _order(coll)
            ranked = [dict(coll[i], rank=pos) for pos, i in enumerate(order, 1)]
            per_kind = {}
            for k in P.KINDS:
                rr = [r["rank"] for r in ranked if r["placebo_kind"] == k]
                if len(rr) < 5:
                    per_kind[k] = {"n": len(rr)}
                    continue
                norm = [(x - 0.5) / N for x in rr]
                per_kind[k] = {
                    "n": len(rr), "best_rank": min(rr),
                    "mean_normalised_rank": round(sum(norm) / len(norm), 4),
                    "median_n_trades": st.median(
                        [r["n"] for r in ranked if r["placebo_kind"] == k]),
                    "ks_vs_uniform": _ks_uniform(norm),
                    "null": P.null_rank_distribution(N, len(rr))}
            arm["by_kind"] = per_kind
            ctl = per_kind.get("placebo_random", {})
            arm["uniformity"] = ctl.get("ks_vs_uniform", {"p": 1.0, "d": 0.0, "n": 0})
            arm["mean_normalised_rank"] = ctl.get("mean_normalised_rank")
            mnr = arm["mean_normalised_rank"]
            arm["verdict"] = (
                "inconclusive - too few relocated-entry rows"
                if mnr is None else
                "uniform - count matching does not push a relocated entry down"
                if arm["uniformity"]["p"] > 0.01 and abs(mnr - 0.5) < 0.12 else
                "NOT uniform - the matching is leaking, placebo ranks are not "
                "interpretable until it is fixed")
        # matching against the real arm
        real_rows = [_row(s, res_real[s.strategy_id].trades) for s in reals
                     if realised[s.strategy_id] >= floor]
        if coll and real_rows:
            arm["vs_real_n"] = T.mann_whitney_u([r["n"] for r in coll],
                                                [r["n"] for r in real_rows])
            arm["median_n_real"] = st.median([r["n"] for r in real_rows])
        out[pool] = arm
        if verbose:
            print(f"  selftest pool={pool}: rows={arm['n_rows']} "
                  f"floor_survival={arm['floor_survival']} "
                  f"median_n={arm.get('median_n')} vs real "
                  f"{arm.get('median_n_real')} :: {arm.get('verdict')}", flush=True)
    return out


def _med(v):
    return round(st.median(v), 3) if v else None


def _ks_uniform(xs: Sequence[float]) -> dict:
    """One-sample Kolmogorov-Smirnov against U(0,1)."""
    xs = sorted(xs)
    n = len(xs)
    if n < 5:
        return {"d": 0.0, "p": 1.0, "n": n}
    d = max(max((i + 1) / n - x, x - i / n) for i, x in enumerate(xs))
    lam = (math.sqrt(n) + 0.12 + 0.11 / math.sqrt(n)) * d
    p = 2 * sum((-1) ** (j - 1) * math.exp(-2 * j * j * lam * lam)
                for j in range(1, 101))
    return {"d": round(d, 4), "p": round(min(1.0, max(0.0, p)), 4), "n": n}
