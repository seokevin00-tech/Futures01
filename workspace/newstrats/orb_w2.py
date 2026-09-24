"""Worker-2 Opening Range Breakout primitives and an explicit ORB simulator.

Why a bespoke simulator rather than only ``T.make_strategy``
-----------------------------------------------------------
The question asked is about *reward for risk as a function of the exit*, and
the library ``ExitModel`` cannot express two of the three stops the brief
demands: "the opposite edge of today's opening range" and "the mid of today's
opening range" are per-day, per-configuration price levels, not ATR multiples.
``StopKind.RANGE`` exists but is wired to ``snap.opening_range``, which is
hard-coded to ``or_minutes = 30`` in ``features.py::_build_session_state`` -
so it cannot vary the range length either.

So this module defines the opening range itself and walks the bars. Everything
that could flatter the result is set pessimistically and named:

* Signal is read from a COMPLETED bar's close. Fill is the NEXT bar's open.
  No bar is ever traded on its own close.
* The opening range is built only from bars whose open time lies inside
  [rth_open, rth_open + N). It is not usable until the first bar that opens at
  or after rth_open + N, so an incomplete range can never produce a signal.
* When a bar's range contains both the stop and the target, the STOP is
  assumed to have filled first (``FillModel.stop_before_target_in_same_bar``).
* Gaps through a level fill at the open, not at the level.
* Slippage: 0.5 ticks on a marketable entry, 1.5 ticks on any stop or
  market-on-close exit, 0 ticks on a limit target and on a resting retest
  limit. Commission + exchange fee are the contract's own, both sides.
* One trade per session, first qualifying break only.

The opening range is always measured on 5-minute (or 1-minute, when available)
bars regardless of the signal timeframe, because every symbol's RTH open lies
on the 5-minute grid but MGC's 08:20 open does not lie on the 15-minute grid.
Measuring a 15-minute range with 15-minute bars for MGC would silently shift
the range by 5 minutes. The SIGNAL timeframe is the thing under test.
"""
from __future__ import annotations

import math
import os
import statistics as st
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from futures_agents.config import get_contract
from futures_agents.data.bars import Bar, BarSeries
from futures_agents.data.loader import load_csv
from futures_agents.timeutil import ET, parse_hhmm, to_et

ROOT = "/home/user/Futures01"

# --------------------------------------------------------------------- data

_CACHE: Dict[Tuple[str, str, int], BarSeries] = {}


def series(symbol: str, tf: int, source: str = "raw") -> BarSeries:
    """Bars for ``symbol`` at ``tf`` minutes from one of the two real sources.

    ``raw``  - csv/raw, Yahoo continuous front-month futures. Real contract
               prices, but Yahoo caps intraday retention, so 5m is ~4 weeks and
               15m is ~8 weeks. All five symbols.
    ``deep`` - data/*_1m.csv, Oanda CFD 1-minute bars 2019-01 to 2020-05 for the
               underlyings MGC/MES/MNQ are written on. ~350 sessions. Not the
               futures price and not CME volume - see scripts/fetch_github_data.py.
    """
    key = (symbol, source, tf)
    if key in _CACHE:
        return _CACHE[key]
    if source == "deep":
        base = load_csv(f"{ROOT}/data/{symbol}_1m.csv", symbol, 1)
        out = base if tf == 1 else base.resample(tf, keep_partial=False)
    else:
        suf = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 1440: "1d"}[tf]
        path = f"{ROOT}/csv/raw/{symbol}_{suf}.csv"
        if os.path.exists(path):
            out = load_csv(path, symbol, tf)
        else:                       # NQ has no 15m file; build it from 5m
            out = load_csv(f"{ROOT}/csv/raw/{symbol}_5m.csv", symbol, 5).resample(
                tf, keep_partial=False)
    _CACHE[key] = out
    return out


def _rth_day_index(bars: Sequence[Bar], open_hhmm: str, close_hhmm: str
                   ) -> Dict[date, List[Bar]]:
    """Calendar-date -> the bars of that date's RTH session, in order.

    Keyed on the calendar date of the bar, not ``trading_day``: an opening
    range belongs to the session that opened that morning, and no RTH bar of
    any contract here falls after 18:00 ET, so the two agree inside RTH.
    """
    o, c = parse_hhmm(open_hhmm), parse_hhmm(close_hhmm)
    out: Dict[date, List[Bar]] = {}
    for b in bars:
        t = to_et(b.ts)
        if o <= t.time() < c and t.weekday() < 5:
            out.setdefault(t.date(), []).append(b)
    return out


# ------------------------------------------------------------- opening range

class OR:
    __slots__ = ("day", "minutes", "high", "low", "end_ts", "n_bars", "open_px")

    def __init__(self, day, minutes, high, low, end_ts, n_bars, open_px):
        self.day, self.minutes = day, minutes
        self.high, self.low, self.end_ts = high, low, end_ts
        self.n_bars, self.open_px = n_bars, open_px

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def mid(self) -> float:
        return 0.5 * (self.high + self.low)


_OR_CACHE: Dict[tuple, Dict[date, "OR"]] = {}
_ATR_CACHE: Dict[tuple, Dict[datetime, float]] = {}


def opening_ranges(symbol: str, or_minutes: int, source: str = "raw",
                   or_tf: Optional[int] = None) -> Dict[date, OR]:
    """One completed opening range per session, from the finest bars available.

    ``or_minutes`` counts from the CONTRACT's own RTH open (MGC 08:20, MCL
    09:00, MES/MNQ/NQ 09:30), never a global 09:30.
    """
    ck = (symbol, or_minutes, source, or_tf)
    if ck in _OR_CACHE:
        return _OR_CACHE[ck]
    spec = get_contract(symbol)
    if or_tf is None:
        or_tf = 1 if source == "deep" else 5
    bars = series(symbol, or_tf, source).bars
    o = parse_hhmm(spec.rth_open)
    days = _rth_day_index(bars, spec.rth_open, spec.rth_close)
    out: Dict[date, OR] = {}
    for d, day_bars in days.items():
        start = datetime.combine(d, o, tzinfo=ET)
        end = start + timedelta(minutes=or_minutes)
        win = [b for b in day_bars if start <= to_et(b.ts) < end]
        if not win:
            continue
        # The window must actually be covered: the last OR bar has to end at or
        # after the window end, otherwise the session is short of data and the
        # "range" is a fragment.
        last_end = to_et(win[-1].ts) + timedelta(minutes=win[-1].minutes)
        if last_end < end - timedelta(minutes=1):
            continue
        # A bar that runs PAST the window end would put post-range price action
        # into the range. That is how a 15-minute bar "measures" MGC's 08:20
        # open: it cannot, and silently returns a longer range instead.
        if last_end > end + timedelta(minutes=1):
            continue
        hi = max(b.high for b in win)
        lo = min(b.low for b in win)
        if hi <= lo:
            continue
        out[d] = OR(d, or_minutes, hi, lo, end, len(win), win[0].open)
    _OR_CACHE[ck] = out
    return out


# ------------------------------------------------------------------- ATR

def atr_map(symbol: str, tf: int, source: str, period: int = 14
            ) -> Dict[datetime, float]:
    """ATR at each bar, computed from bars STRICTLY BEFORE that bar."""
    ck = (symbol, tf, source, period)
    if ck in _ATR_CACHE:
        return _ATR_CACHE[ck]
    bars = series(symbol, tf, source).bars
    trs: List[float] = []
    out: Dict[datetime, float] = {}
    prev_close = None
    for b in bars:
        out[b.ts] = (sum(trs[-period:]) / period) if len(trs) >= period else float("nan")
        tr = b.high - b.low if prev_close is None else max(
            b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
        prev_close = b.close
    _ATR_CACHE[ck] = out
    return out


# ------------------------------------------------------------------ trades

class Trade:
    __slots__ = ("day", "dir", "entry_ts", "entry", "stop", "target", "r_px",
                 "exit_ts", "exit", "reason", "r", "mae_r", "mfe_r", "bars",
                 "or_width", "minutes")

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


SLIP_ENTRY_TICKS = 0.5      # marketable entry into a normal book
SLIP_STOP_TICKS = 1.5       # stop = market order, plus the stop-order penalty
SLIP_EOD_TICKS = 1.5        # market-on-close


def simulate(symbol: str, tf: int, or_minutes: int, entry: str, stop: str,
             target: str, *, source: str = "raw", atr_mult: float = 1.0,
             days: Optional[Sequence[date]] = None) -> Tuple[List[Trade], dict]:
    """Run one ORB configuration. Returns (trades, firing-rate diagnostics).

    entry  : 'break'  - first TF bar CLOSING beyond the edge, filled next open
             'retest' - same trigger, then a resting limit at the edge itself
    stop   : 'opp'    - the opposite edge of the opening range
             'mid'    - the mid of the opening range
             'atr'    - ``atr_mult`` x ATR(14) of the signal timeframe
    target : 'rw1'/'rw1.5'/'rw2' - entry +/- k x the opening range width
             'r1'/'r2'/'r3'      - entry +/- k x the risk distance
    Every configuration also exits at the RTH close if nothing else has hit.
    """
    spec = get_contract(symbol)
    tick, ptval = spec.tick_size, spec.point_value
    comm_px = 2.0 * (spec.commission_per_side + spec.exchange_fee_per_side) / ptval
    ors = opening_ranges(symbol, or_minutes, source)
    bars = series(symbol, tf, source).bars
    day_bars = _rth_day_index(bars, spec.rth_open, spec.rth_close)
    atrs = atr_map(symbol, tf, source) if stop == "atr" else {}

    keys = sorted(set(ors) & set(day_bars))
    if days is not None:
        keys = [d for d in keys if d in set(days)]

    trades: List[Trade] = []
    n_sessions = n_break = n_filled = n_skipped_minstop = 0

    for d in keys:
        orr, dbars = ors[d], day_bars[d]
        elig = [b for b in dbars if to_et(b.ts) >= orr.end_ts]
        if len(elig) < 3:
            continue
        n_sessions += 1

        # ---- trigger: first COMPLETED bar closing beyond an edge -----------
        sig_i = None
        for i, b in enumerate(elig[:-1]):     # need a next bar to fill on
            if b.close > orr.high:
                sig_i, direction, edge = i, 1, orr.high
                break
            if b.close < orr.low:
                sig_i, direction, edge = i, -1, orr.low
                break
        if sig_i is None:
            continue
        n_break += 1

        # ---- fill ---------------------------------------------------------
        if entry == "break":
            fb = elig[sig_i + 1]
            entry_px = fb.open + direction * SLIP_ENTRY_TICKS * tick
            entry_ts, start_i = fb.ts, sig_i + 1
            entry_slip_px = SLIP_ENTRY_TICKS * tick
        else:                                  # retest: resting limit at the edge
            entry_px = entry_ts = None
            for j in range(sig_i + 1, len(elig)):
                b = elig[j]
                touched = (b.low <= edge) if direction > 0 else (b.high >= edge)
                if touched:
                    # a gap straight through the level fills at the open
                    entry_px = (min(b.open, edge) if direction > 0
                                else max(b.open, edge))
                    entry_ts, start_i = b.ts, j
                    break
            if entry_px is None:
                continue
            entry_slip_px = 0.0
        n_filled += 1

        # ---- stop ---------------------------------------------------------
        if stop == "opp":
            stop_px = orr.low if direction > 0 else orr.high
        elif stop == "mid":
            stop_px = orr.mid
        else:
            a = atrs.get(elig[sig_i].ts, float("nan"))
            if not (a == a) or a <= 0:
                continue
            stop_px = entry_px - direction * atr_mult * a
        r_px = (entry_px - stop_px) * direction
        if r_px < spec.min_stop_ticks * tick:
            n_skipped_minstop += 1
            continue

        # ---- target -------------------------------------------------------
        if target.startswith("rw"):
            k = float(target[2:])
            tgt_px = entry_px + direction * k * orr.width
        else:
            k = float(target[1:])
            tgt_px = entry_px + direction * k * r_px
        if (tgt_px - entry_px) * direction <= 0:
            continue

        # ---- walk ---------------------------------------------------------
        exit_px = exit_ts = None
        reason = "eod"
        mae = mfe = 0.0
        nb = 0
        for b in elig[start_i:]:
            nb += 1
            adv = (b.high - entry_px) if direction > 0 else (entry_px - b.low)
            adv_bad = (entry_px - b.low) if direction > 0 else (b.high - entry_px)
            mfe = max(mfe, adv)
            mae = max(mae, adv_bad)
            hit_stop = (b.low <= stop_px) if direction > 0 else (b.high >= stop_px)
            hit_tgt = (b.high >= tgt_px) if direction > 0 else (b.low <= tgt_px)
            if hit_stop:                       # pessimistic: stop first
                gap = (b.open <= stop_px) if direction > 0 else (b.open >= stop_px)
                fill = b.open if gap else stop_px
                exit_px = fill - direction * SLIP_STOP_TICKS * tick
                exit_ts, reason = b.ts, "stop"
                break
            if hit_tgt:
                gap = (b.open >= tgt_px) if direction > 0 else (b.open <= tgt_px)
                exit_px = b.open if gap else tgt_px   # limit: no slippage
                exit_ts, reason = b.ts, "target"
                break
        if exit_px is None:
            lb = elig[-1]
            exit_px = lb.close - direction * SLIP_EOD_TICKS * tick
            exit_ts, reason = lb.ts, "eod"

        gross = (exit_px - entry_px) * direction
        net = gross - comm_px
        t = Trade()
        t.day, t.dir, t.entry_ts, t.entry = d, direction, entry_ts, entry_px
        t.stop, t.target, t.r_px = stop_px, tgt_px, r_px
        t.exit_ts, t.exit, t.reason = exit_ts, exit_px, reason
        t.r = net / r_px
        t.mae_r, t.mfe_r = mae / r_px, mfe / r_px
        t.bars, t.or_width = nb, orr.width
        t.minutes = (to_et(exit_ts) - to_et(entry_ts)).total_seconds() / 60.0
        trades.append(t)

    diag = dict(sessions=n_sessions, breaks=n_break, filled=n_filled,
                skipped_minstop=n_skipped_minstop,
                break_rate=round(n_break / n_sessions, 4) if n_sessions else 0.0,
                fill_rate=round(n_filled / n_break, 4) if n_break else 0.0,
                trade_rate=round(len(trades) / n_sessions, 4) if n_sessions else 0.0)
    return trades, diag


# ------------------------------------------------------------------ metrics

def metrics(trades: Sequence[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return dict(n=0)
    rs = [t.r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    aw = st.mean(wins) if wins else 0.0
    al = st.mean(losses) if losses else 0.0
    gp, gl = sum(wins), -sum(losses)
    eq, peak, maxdd, dds = 0.0, 0.0, 0.0, []
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = peak - eq
        maxdd = max(maxdd, dd)
        if dd > 0:
            dds.append(dd)
    cw = cl = bw = bl = 0
    for r in rs:
        if r > 0:
            cw, cl = cw + 1, 0
        else:
            cl, cw = cl + 1, 0
        bw, bl = max(bw, cw), max(bl, cl)
    sd = st.pstdev(rs) if n > 1 else 0.0
    dn = [min(0.0, r) for r in rs]
    dsd = math.sqrt(sum(x * x for x in dn) / n)
    exp_r = st.mean(rs)
    return dict(
        n=n, win=round(len(wins) / n, 4),
        avg_win=round(aw, 4), avg_loss=round(al, 4),
        payoff=round(aw / abs(al), 4) if al else float("inf"),
        pf=round(gp / gl, 4) if gl else float("inf"),
        exp=round(exp_r, 4), sd=round(sd, 4),
        t=round(exp_r / (sd / math.sqrt(n)), 3) if sd > 0 else 0.0,
        sharpe=round(exp_r / sd, 4) if sd > 0 else 0.0,
        sortino=round(exp_r / dsd, 4) if dsd > 0 else 0.0,
        maxdd=round(maxdd, 3), avgdd=round(st.mean(dds), 3) if dds else 0.0,
        max_cons_w=bw, max_cons_l=bl,
        mae=round(st.median([t.mae_r for t in trades]), 3),
        mfe=round(st.median([t.mfe_r for t in trades]), 3),
        dur_min=round(st.median([t.minutes for t in trades]), 1),
        pct_stop=round(sum(1 for t in trades if t.reason == "stop") / n, 3),
        pct_tgt=round(sum(1 for t in trades if t.reason == "target") / n, 3),
        pct_eod=round(sum(1 for t in trades if t.reason == "eod") / n, 3),
    )


# ------------------------------------------------------------- statistics

def stouffer(zs: Sequence[float]) -> float:
    zs = [z for z in zs if z == z]
    return sum(zs) / math.sqrt(len(zs)) if zs else 0.0


def sign_test_z(deltas: Sequence[float]) -> Tuple[float, int, int]:
    """Normal-approximation sign test on paired differences. Ties dropped."""
    pos = sum(1 for d in deltas if d > 0)
    neg = sum(1 for d in deltas if d < 0)
    m = pos + neg
    if m == 0:
        return 0.0, pos, neg
    z = (pos - m / 2.0) / math.sqrt(m / 4.0)
    return z, pos, neg


def norm_p(z: float) -> float:
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
