"""Structure age and distance-to-invalidation.

Swing structure gives something no oscillator does: a *price at which the thesis
is wrong*. In an uptrend making higher lows, a close below the last higher low
breaks it. That level is knowable at entry, so the risk of the trade is knowable
at entry - and when price sits close to it, the same objective buys more reward
per unit of risk with no change to the thesis at all.

Three objects live here:

``structure_fresh``      bars elapsed since the structure last changed.
``near_invalidation``    price within X ATR of the structural invalidation level.
``invalidation_distance_r``  not a filter: the implied reward:risk when the stop
                         sits just beyond invalidation and the target at the
                         prior swing extreme.

Every swing read here is filtered on :attr:`Swing.confirmed_index`. A 3-bar
fractal high at bar *i* is not knowable until bar *i+3*, and a study that reads
it at *i* is measuring a chart it could never have traded.

The per-bar structure state is precomputed into a :class:`StructureTape` keyed by
timestamp, because a ``Condition`` receives a snapshot and nothing else - it
cannot look back along the series, and stateful conditions that try to remember
the previous bar break the moment two strategies interleave.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from futures_agents.data.bars import Bar, BarSeries
from futures_agents.indicators import atr as _atr
from futures_agents.indicators.structure import Swing, find_swings
from futures_agents.schema import Direction
from futures_agents.strategies.base import Condition, ConditionKind, ConditionResult

SWING_LEFT = 3
SWING_RIGHT = 3


# ==========================================================================
# Per-bar structure tape
# ==========================================================================

@dataclass
class BarStructure:
    """Everything this study needs to know at one bar, using confirmed swings only."""

    index: int
    trend: str                       # UPTREND | DOWNTREND | RANGE | UNDEFINED
    trend_age: int                   # bars since the trend LABEL last changed
    swing_age: int                   # bars since the last swing CONFIRMATION
    invalidation: Optional[float]    # last HL in an uptrend, last LH in a downtrend
    target: Optional[float]          # the prior swing extreme in the trend direction
    atr: Optional[float]
    close: float
    intact: bool                     # price still on the right side of invalidation
    dist_atr: Optional[float]        # |close - invalidation| / atr, signed +ve when intact
    target_atr: Optional[float]      # (target - close) in ATR, signed +ve when ahead
    rr: Optional[float]              # implied reward:risk, None when target behind price


class StructureTape:
    """Confirmed-swing structure for every bar of one series.

    Built once per (symbol, timeframe, slice) and looked up by timestamp, which
    is the only key a ``Condition`` can produce from a snapshot.
    """

    def __init__(self, bars: Sequence[Bar], *, left: int = SWING_LEFT,
                 right: int = SWING_RIGHT, pad_frac: float = 0.0):
        self.bars = list(bars)
        n = len(self.bars)
        h = [b.high for b in self.bars]
        lo = [b.low for b in self.bars]
        c = [b.close for b in self.bars]
        a = _atr(h, lo, c, 14)

        swings: List[Swing] = find_swings(self.bars, left, right)
        ordered = sorted(swings, key=lambda s: (s.confirmed_index, s.index))

        rows: List[BarStructure] = []
        k = 0
        last_high = prior_high = last_low = prior_low = None
        last_confirm_bar = None
        trend_prev = "UNDEFINED"
        trend_started = 0

        for i in range(n):
            changed = False
            while k < len(ordered) and ordered[k].confirmed_index <= i:
                s = ordered[k]
                if s.is_high:
                    prior_high, last_high = last_high, s.price
                else:
                    prior_low, last_low = last_low, s.price
                last_confirm_bar = i if s.confirmed_index <= i else s.confirmed_index
                last_confirm_bar = s.confirmed_index
                changed = True
                k += 1

            trend = "UNDEFINED"
            if None not in (last_high, prior_high, last_low, prior_low):
                hh = last_high > prior_high
                hl = last_low > prior_low
                lh = last_high < prior_high
                ll = last_low < prior_low
                if hh and hl:
                    trend = "UPTREND"
                elif lh and ll:
                    trend = "DOWNTREND"
                else:
                    trend = "RANGE"

            if trend != trend_prev:
                trend_started = i
                trend_prev = trend
            trend_age = i - trend_started
            swing_age = (i - last_confirm_bar) if last_confirm_bar is not None else 10 ** 6

            av = a[i] if i < len(a) else None
            close = c[i]
            inval = target = None
            if trend == "UPTREND":
                inval, target = last_low, last_high
            elif trend == "DOWNTREND":
                inval, target = last_high, last_low

            intact = False
            dist_atr = target_atr = rr = None
            if inval is not None and target is not None and av:
                sign = 1.0 if trend == "UPTREND" else -1.0
                risk = (close - inval) * sign
                reward = (target - close) * sign
                intact = risk > 0
                dist_atr = risk / av
                target_atr = reward / av
                if risk > 0 and reward > 0:
                    rr = reward / risk

            rows.append(BarStructure(
                index=i, trend=trend, trend_age=trend_age, swing_age=swing_age,
                invalidation=inval, target=target, atr=av, close=close,
                intact=intact, dist_atr=dist_atr, target_atr=target_atr, rr=rr))

        self.rows = rows
        self.by_ts: Dict[object, BarStructure] = {b.ts: r for b, r in zip(self.bars, rows)}

    def at(self, ts) -> Optional[BarStructure]:
        return self.by_ts.get(ts)


#: Registry the conditions read. ``register(symbol, tf, bars)`` before running.
TAPES: Dict[Tuple[str, int], StructureTape] = {}


def register(symbol: str, tf: int, bars: Sequence[Bar]) -> StructureTape:
    tape = StructureTape(bars)
    TAPES[(symbol.upper(), int(tf))] = tape
    return tape


def register_frame(symbol: str, frame) -> None:
    """Register every timeframe of a built ``SymbolFrame``."""
    for tf, tff in frame.frames.items():
        register(symbol, tf, tff.series.bars)


def _lookup(snap, tf: int) -> Tuple[Optional[BarStructure], Optional[object]]:
    s = snap.tf(tf)
    if s is None:
        return None, None
    tape = TAPES.get((snap.symbol.upper(), int(tf)))
    if tape is None:
        return None, s
    return tape.at(s.bar.ts), s


# ==========================================================================
# Conditions
# ==========================================================================

def structure_direction(tf: Optional[int] = None, name: str = "struct_dir") -> Condition:
    """Baseline SIGNAL: trade the direction of confirmed swing structure.

    This is the control arm's engine. Everything else in this module is a FILTER
    layered on top of it, so that a comparison holds the thesis constant and
    varies only the thing under test.
    """
    def fn(snap, default_tf):
        use = tf or default_tf
        row, s = _lookup(snap, use)
        if row is None or s is None:
            return ConditionResult.no()
        if row.trend == "UPTREND":
            return ConditionResult.yes(Direction.LONG, f"up age={row.trend_age}")
        if row.trend == "DOWNTREND":
            return ConditionResult.yes(Direction.SHORT, f"down age={row.trend_age}")
        return ConditionResult.no()

    return Condition(name=name, group="structure", fn=fn,
                     kind=ConditionKind.SIGNAL, timeframe=tf,
                     description="direction of confirmed swing structure")


def structure_fresh(max_age: int, tf: Optional[int] = None, *,
                    min_age: int = 0, basis: str = "trend") -> Condition:
    """FILTER: the structure changed within ``max_age`` bars.

    ``basis="trend"`` ages the trend LABEL - how long we have been in this
    uptrend. ``basis="swing"`` ages the last swing CONFIRMATION - how long since
    the structural picture was last repainted by a new pivot. They answer
    different questions and a trend can be old while its last swing is new.
    """
    key = "trend_age" if basis == "trend" else "swing_age"
    nm = f"fresh_{basis}_{min_age}_{max_age}" + (f"_tf{tf}" if tf else "")

    def fn(snap, default_tf):
        use = tf or default_tf
        row, _ = _lookup(snap, use)
        if row is None:
            return ConditionResult.no()
        age = getattr(row, key)
        if min_age <= age <= max_age:
            return ConditionResult.yes(Direction.NEUTRAL, f"{key}={age}", value=age)
        return ConditionResult.no()

    return Condition(name=nm, group="structure", fn=fn, kind=ConditionKind.FILTER,
                     timeframe=tf, description=f"{key} in [{min_age},{max_age}]")


def near_invalidation(max_atr: float, tf: Optional[int] = None, *,
                      min_atr: float = 0.0) -> Condition:
    """FILTER: price is within ``max_atr`` ATR of the structural invalidation.

    Uptrend -> the last higher low; downtrend -> the last lower high. Bars where
    price has already closed through the level are rejected: the thesis is not
    "near invalid", it is invalid.
    """
    nm = f"nearinval_{min_atr:g}_{max_atr:g}" + (f"_tf{tf}" if tf else "")

    def fn(snap, default_tf):
        use = tf or default_tf
        row, _ = _lookup(snap, use)
        if row is None or row.dist_atr is None or not row.intact:
            return ConditionResult.no()
        if min_atr <= row.dist_atr <= max_atr:
            return ConditionResult.yes(Direction.NEUTRAL,
                                       f"dist={row.dist_atr:.2f}atr",
                                       value=row.dist_atr)
        return ConditionResult.no()

    return Condition(name=nm, group="structure", fn=fn, kind=ConditionKind.FILTER,
                     timeframe=tf,
                     description=f"distance to invalidation in [{min_atr},{max_atr}] ATR")


def min_invalidation_rr(min_rr: float, tf: Optional[int] = None) -> Condition:
    """FILTER: implied reward:risk (stop past invalidation, target at prior extreme)."""
    nm = f"invrr_{min_rr:g}" + (f"_tf{tf}" if tf else "")

    def fn(snap, default_tf):
        use = tf or default_tf
        row, _ = _lookup(snap, use)
        if row is None or row.rr is None:
            return ConditionResult.no()
        if row.rr >= min_rr:
            return ConditionResult.yes(Direction.NEUTRAL, f"rr={row.rr:.2f}",
                                       value=row.rr)
        return ConditionResult.no()

    return Condition(name=nm, group="structure", fn=fn, kind=ConditionKind.FILTER,
                     timeframe=tf, description=f"implied RR >= {min_rr}")


def htf_fresh_and_near(htf: int, max_age: int, max_atr: float,
                       ltf: Optional[int] = None) -> Condition:
    """FILTER: the HIGHER timeframe structure is fresh AND price is near ITS invalidation.

    The specific conjunction the brief asks about - a fresh 4-hour structure with
    price standing near the 4-hour higher low - expressed as one condition so the
    two halves cannot be satisfied on different bars.
    """
    nm = f"htf{htf}_fresh{max_age}_near{max_atr:g}"

    def fn(snap, default_tf):
        row, _ = _lookup(snap, htf)
        if row is None or row.dist_atr is None or not row.intact:
            return ConditionResult.no()
        if row.trend_age <= max_age and row.dist_atr <= max_atr:
            return ConditionResult.yes(
                Direction.NEUTRAL,
                f"age={row.trend_age} dist={row.dist_atr:.2f}")
        return ConditionResult.no()

    return Condition(name=nm, group="structure", fn=fn, kind=ConditionKind.FILTER,
                     timeframe=None,
                     description=f"{htf}m structure fresh<={max_age} and within {max_atr} ATR")


def htf_direction(htf: int) -> Condition:
    """SIGNAL: direction of the HIGHER timeframe's confirmed structure."""
    def fn(snap, default_tf):
        row, _ = _lookup(snap, htf)
        if row is None:
            return ConditionResult.no()
        if row.trend == "UPTREND":
            return ConditionResult.yes(Direction.LONG, f"htf up age={row.trend_age}")
        if row.trend == "DOWNTREND":
            return ConditionResult.yes(Direction.SHORT, f"htf down age={row.trend_age}")
        return ConditionResult.no()

    return Condition(name=f"htf{htf}_dir", group="structure", fn=fn,
                     kind=ConditionKind.SIGNAL, timeframe=None,
                     description=f"{htf}m structure direction")


# ==========================================================================
# invalidation_distance_r - the measurement, not a filter
# ==========================================================================

def invalidation_distance_r(tape: StructureTape, *, pad_atr: float = 0.05
                            ) -> List[dict]:
    """Implied reward:risk at every bar with an intact, defined structure.

    stop  = invalidation level, padded by ``pad_atr`` ATR beyond it
    target = the prior swing extreme in the direction of the structure
    """
    out = []
    for r in tape.rows:
        if r.trend not in ("UPTREND", "DOWNTREND") or not r.intact:
            continue
        if r.atr is None or not r.atr or r.dist_atr is None:
            continue
        risk_atr = r.dist_atr + pad_atr
        if risk_atr <= 0:
            continue
        out.append(dict(index=r.index, trend=r.trend, trend_age=r.trend_age,
                        swing_age=r.swing_age, dist_atr=r.dist_atr,
                        risk_atr=risk_atr, target_atr=r.target_atr,
                        rr=(r.target_atr / risk_atr) if r.target_atr is not None else None))
    return out


# ==========================================================================
# Forward simulation of the structural stop - the event study
# ==========================================================================

def simulate(tape: StructureTape, *, direction_from: str = "trend",
             pad_atr: float = 0.05, horizon: int = 60,
             target_mode: str = "swing", target_atr_mult: float = 1.5,
             cost_r: float = 0.0, entry_lag: int = 1) -> List[dict]:
    """Walk every qualifying bar forward with a stop beyond invalidation.

    Entry is the OPEN of bar ``i + entry_lag`` - the signal bar's close is not a
    fill. Within a bar, the stop is assumed hit before the target whenever both
    are touched, which is the conservative reading and the only one that does not
    manufacture winners out of bar aggregation.
    """
    bars = tape.bars
    n = len(bars)
    out = []
    for r in tape.rows:
        i = r.index
        if r.trend not in ("UPTREND", "DOWNTREND") or not r.intact:
            continue
        if not r.atr or r.dist_atr is None or r.invalidation is None:
            continue
        j = i + entry_lag
        if j >= n:
            continue
        sign = 1.0 if r.trend == "UPTREND" else -1.0
        entry = bars[j].open
        stop = r.invalidation - sign * pad_atr * r.atr
        risk = (entry - stop) * sign
        if risk <= 0:
            continue
        if target_mode == "swing":
            if r.target is None:
                continue
            target = r.target
        else:
            target = entry + sign * target_atr_mult * r.atr
        reward = (target - entry) * sign
        if reward <= 0:
            continue
        rr = reward / risk

        res_r = None
        reason = "TIME"
        bars_held = 0
        mae = mfe = 0.0
        for k in range(j, min(n, j + horizon)):
            b = bars[k]
            bars_held = k - j + 1
            adv = (b.high - entry) * sign if sign > 0 else (entry - b.low) * sign * -1.0
            # signed excursions in R
            up = ((b.high - entry) if sign > 0 else (entry - b.low)) / risk
            dn = ((b.low - entry) if sign > 0 else (entry - b.high)) / risk
            mfe = max(mfe, up)
            mae = min(mae, dn)
            hit_stop = (b.low <= stop) if sign > 0 else (b.high >= stop)
            hit_tgt = (b.high >= target) if sign > 0 else (b.low <= target)
            if hit_stop:
                res_r, reason = -1.0, "STOP"
                break
            if hit_tgt:
                res_r, reason = rr, "TARGET"
                break
        if res_r is None:
            last = bars[min(n - 1, j + horizon - 1)]
            res_r = ((last.close - entry) * sign) / risk
            reason = "TIME"
        out.append(dict(index=i, ts=bars[i].ts, trend=r.trend,
                        trend_age=r.trend_age, swing_age=r.swing_age,
                        dist_atr=r.dist_atr, risk_atr=risk / r.atr, rr_implied=rr,
                        r=res_r - cost_r, reason=reason, bars=bars_held,
                        mae=mae, mfe=mfe))
    return out


# ==========================================================================
# Sequential, non-overlapping event study
# ==========================================================================

def _metrics(trades: List[dict]) -> dict:
    """Full metric block for a list of trades carrying an ``r`` key."""
    import statistics as _st
    n = len(trades)
    if n == 0:
        return dict(n=0)
    rs = [t["r"] for t in trades]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    aw = _st.mean(wins) if wins else 0.0
    al = _st.mean(losses) if losses else 0.0
    gp = sum(wins)
    gl = -sum(losses)
    eq, peak, maxdd, dds = 0.0, 0.0, 0.0, []
    for x in rs:
        eq += x
        peak = max(peak, eq)
        dd = peak - eq
        maxdd = max(maxdd, dd)
        dds.append(dd)
    cw = cl = bw = bl = 0
    for x in rs:
        if x > 0:
            cw, cl = cw + 1, 0
        else:
            cl, cw = cl + 1, 0
        bw, bl = max(bw, cw), max(bl, cl)
    sd = _st.pstdev(rs) if n > 1 else 0.0
    downside = [min(0.0, x) for x in rs]
    dsd = (sum(x * x for x in downside) / n) ** 0.5
    mu = _st.mean(rs)
    return dict(
        n=n, win=round(len(wins) / n, 4), exp=round(mu, 4),
        avg_win=round(aw, 4), avg_loss=round(al, 4),
        payoff=round(aw / abs(al), 4) if al else None,
        pf=round(gp / gl, 4) if gl else None,
        t=round(mu / (sd / math.sqrt(n)), 3) if sd else 0.0,
        sharpe=round(mu / sd, 4) if sd else 0.0,
        sortino=round(mu / dsd, 4) if dsd else 0.0,
        maxdd=round(maxdd, 3), avgdd=round(sum(dds) / n, 3),
        max_cons_win=bw, max_cons_loss=bl,
        avg_bars=round(sum(t.get("bars", 0) for t in trades) / n, 2),
        avg_mae=round(sum(t.get("mae", 0.0) for t in trades) / n, 4),
        avg_mfe=round(sum(t.get("mfe", 0.0) for t in trades) / n, 4),
        rr_implied=round(_st.median([t["rr_implied"] for t in trades]), 3),
        pct_target=round(sum(1 for t in trades if t["reason"] == "TARGET") / n, 3),
        pct_stop=round(sum(1 for t in trades if t["reason"] == "STOP") / n, 3),
    )


def simulate_seq(tape: StructureTape, gate=None, *, pad_atr: float = 0.05,
                 horizon: int = 60, target_mode: str = "swing",
                 target_atr_mult: float = 1.5, cost_fn=None, entry_lag: int = 1,
                 min_rr: float = 0.0, stop_mode: str = "structure",
                 stop_atr_mult: float = 1.0) -> Tuple[List[dict], int, int]:
    """Non-overlapping walk-forward: one position at a time.

    Returns ``(trades, eligible_bars, gated_bars)``. Overlapping observations -
    one per bar - are not independent, and a z-statistic computed over them is
    inflated by roughly the holding period. This takes the trade and then stands
    aside until it is closed, which is also what a live account would do.
    """
    bars = tape.bars
    n = len(bars)
    trades: List[dict] = []
    eligible = gated = 0
    i = 0
    while i < n:
        r = tape.rows[i]
        ok_base = (r.trend in ("UPTREND", "DOWNTREND") and r.intact
                   and r.atr and r.dist_atr is not None and r.invalidation is not None)
        if ok_base:
            eligible += 1
        if not ok_base or (gate is not None and not gate(r)):
            i += 1
            continue
        gated += 1
        j = i + entry_lag
        if j >= n:
            break
        sign = 1.0 if r.trend == "UPTREND" else -1.0
        entry = bars[j].open
        if stop_mode == "structure":
            stop = r.invalidation - sign * pad_atr * r.atr
        else:
            stop = entry - sign * stop_atr_mult * r.atr
        risk = (entry - stop) * sign
        if risk <= 0:
            i += 1
            continue
        if target_mode == "swing":
            if r.target is None:
                i += 1
                continue
            target = r.target
        else:
            target = entry + sign * target_atr_mult * r.atr
        reward = (target - entry) * sign
        if reward <= 0:
            i += 1
            continue
        rr = reward / risk
        if rr < min_rr:
            i += 1
            continue

        cost_r = cost_fn(risk) if cost_fn else 0.0
        res_r, reason, held = None, "TIME", 0
        mae = mfe = 0.0
        end = min(n, j + horizon)
        k = j
        while k < end:
            b = bars[k]
            held = k - j + 1
            up = ((b.high - entry) if sign > 0 else (entry - b.low)) / risk
            dn = ((b.low - entry) if sign > 0 else (entry - b.high)) / risk
            mfe, mae = max(mfe, up), min(mae, dn)
            hit_stop = (b.low <= stop) if sign > 0 else (b.high >= stop)
            hit_tgt = (b.high >= target) if sign > 0 else (b.low <= target)
            if hit_stop:
                res_r, reason = -1.0, "STOP"
                break
            if hit_tgt:
                res_r, reason = rr, "TARGET"
                break
            k += 1
        if res_r is None:
            k = end - 1
            res_r = ((bars[k].close - entry) * sign) / risk
            reason = "TIME"
        trades.append(dict(index=i, ts=bars[i].ts, trend=r.trend,
                           trend_age=r.trend_age, swing_age=r.swing_age,
                           dist_atr=r.dist_atr, rr_implied=rr,
                           r=res_r - cost_r, gross_r=res_r, cost_r=cost_r,
                           reason=reason, bars=held, mae=mae, mfe=mfe))
        i = k + 1
    return trades, eligible, gated
