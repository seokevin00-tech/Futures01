"""Opening Range Breakout - parameterised, and with the resolvability bug fixed.

Why this module exists
======================
``library.opening_range_breakout`` fires on **4 of 4,256 NQ 60m bars**. The
cause is not the breakout test; it is that ``FeatureSnapshot.opening_range`` is
usually ``None``. ``features._build_session_state`` accumulates the opening
range over base bars satisfying ``is_rth(b.ts)`` and ``0 <= minutes_since_open
< 30``. A bar only satisfies that if a bar *starts* exactly on the session open.

That makes the opening range a function of clock arithmetic:

    an L-minute opening range is resolvable on a T-minute frame
    iff  T divides L  AND  T divides (minutes-from-midnight of the session open)

Measured, per trading day, on this repo's data:

    symbol  open    tf     OR5   OR15  OR30  OR60
    NQ/MES  09:30   60m     0%     0%    0%    1%     <- 570 % 60 != 0
    NQ/MES  09:30   30m     0%     0%  100%  100%
    NQ/MES  09:30   15m     0%   100%  100%  100%
    NQ/MES  09:30    5m   100%   100%  100%  100%
    MGC     08:20   60m     0%     0%    0%    0%     <- 500 % 60,30,15 != 0
    MGC     08:20   30m     0%     0%    0%    0%
    MGC     08:20   15m     0%     0%    0%    0%
    MGC     08:20    5m    95%    95%   95%   95%
    MCL     09:00   60m     0%     0%    0%   93%     <- 540 % 60 == 0
    MCL     09:00   15m     0%   100%  100%  100%

Two distinct failure modes follow, and the library has both:

1. **The null case.** 09:30 is not on an hourly grid, so no 60m bar ever has
   ``0 <= mso < 30``; ``opening_range`` is ``None`` on 99.8% of NQ and MES 60m
   bars and on 100% of MGC 60m bars. The condition cannot fire. This is what
   4/4,256 is. At 240m and 1440m it is unconditional - 240 divides no opening
   range length, which is D21's mechanism and D20's conclusion restated as
   arithmetic rather than as a market fact.
2. **The silent-widening case.** MCL opens at 09:00, which *is* on the hourly
   grid, so a 60m bar does enter the window - and the resulting object is
   labelled ``minutes=30`` while actually spanning **60** minutes. The library
   ORB fires on 17.3% of MCL 60m bars off a range that is twice the advertised
   width. A firing rate is not evidence that the right thing fired.

This module never silently widens. A range is built only from bars that fit
*entirely* inside ``[0, L)``, and is ``complete`` only when those bars tile the
whole window. Where that is impossible the conditions decline and the decline
is counted in :data:`INERT`, so an unmeasurable cell is visible as an
unmeasurable cell rather than as a market result.

Registration
------------
A condition receives only ``(FeatureSnapshot, tf)`` and cannot reach the bar
series, so the caller registers the frame the backtest runs on::

    import orb as O
    frame = build_symbol_frame(series, FRAMES[tf])
    O.register_frame(frame)

State is keyed by bar timestamp, never by index, so a slice, a window and the
full series cannot read one another's state.

Look-ahead
----------
Every range is built from bars whose ``mso + minutes <= L``; every signal
requires ``mso >= L``. The two sets are disjoint, so no bar is ever tested
against a range it helped form. The retest and fade variants read only breaks
recorded on *strictly earlier* bars of the same session.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from futures_agents.config import get_contract
from futures_agents.schema import Direction
from futures_agents.strategies.base import Condition, ConditionKind, ConditionResult
from futures_agents.timeutil import is_rth, minutes_since_open, trading_day

LONG, SHORT, FLAT = Direction.LONG, Direction.SHORT, Direction.NEUTRAL

#: Opening range lengths this module builds conditions for.
LENGTHS = (5, 15, 30, 60)

# -------------------------------------------------------------- parameters
# Module globals so a sensitivity sweep can reassign them without editing
# conditions. Defaults are the canonical ORB reading.

#: Only the FIRST breakout of each session counts. This is the textbook rule
#: (Crabel places one resting stop either side of the range); with it False the
#: condition also fires on every subsequent bar that holds beyond the boundary,
#: which is a trend-continuation statement, not an ORB.
FIRST_ONLY = True

#: Signals only inside the contract's own RTH session. Without this an "opening
#: range breakout" fires at 02:00 against a range set 17 hours earlier.
SESSION_ONLY = True

#: Retest window: bars after the break within which a return to the boundary
#: still counts as the retest of that break.
RETEST_BARS = 12

#: Fade needs the rejection to be a real rejection, not a tick: the excursion
#: beyond the boundary must be at least this fraction of the range height.
FADE_MIN_POKE = 0.05


# --------------------------------------------------------------- registry
#: Our own registry. We deliberately do NOT write into ``library.CONDITIONS``:
#: five agents sharing that dict is how name collisions happen.
CONDITIONS: Dict[str, Condition] = {}

#: name -> bars on which the condition declined because the opening range was
#: not resolvable on this frame at all. Distinguishes "no signal" from
#: "unmeasurable here", which is the distinction the library lost.
INERT: Dict[str, int] = defaultdict(int)


def condition(name: str, group: str = "orb", *,
              kind: ConditionKind = ConditionKind.SIGNAL,
              description: str = "", warmup: int = 20):
    def deco(fn):
        CONDITIONS[name] = Condition(name=name, group=group, fn=fn, kind=kind,
                                     description=description or (fn.__doc__ or "").strip(),
                                     warmup_bars=warmup)
        return fn
    return deco


def get(name: str) -> Condition:
    return CONDITIONS[name]


def names() -> List[str]:
    return sorted(CONDITIONS)


# ------------------------------------------------------------------ state
@dataclass(frozen=True)
class ORState:
    """The opening range as it is knowable at the close of one bar."""
    day: date
    minutes: int          # requested length, and the ACTUAL length when complete
    high: float
    low: float
    complete: bool
    in_session: bool
    mso: float
    #: Direction of the first break of this session, if one has already
    #: happened on a STRICTLY EARLIER bar, and the bar index it happened on.
    prior_break: Optional[str] = None
    prior_break_i: Optional[int] = None
    i: int = 0

    @property
    def height(self) -> float:
        return max(self.high - self.low, 0.0)


#: (symbol, base_tf) -> {L -> {bar_ts -> ORState}}
_STATE: Dict[Tuple[str, int], Dict[int, Dict[object, ORState]]] = {}
#: (symbol, base_tf) -> {L -> resolvable?}
_RESOLVABLE: Dict[Tuple[str, int], Dict[int, bool]] = {}
_ACTIVE: Optional[Tuple[str, int]] = None


def _open_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def resolvable(symbol: str, tf: int, length: int) -> bool:
    """Can a ``length``-minute opening range be built from ``tf``-minute bars?

    Pure clock arithmetic: the bars must tile ``[0, length)`` exactly, so the
    bar size must divide the range length, and a bar boundary must fall on the
    session open.
    """
    if tf > length or length % tf:
        return False
    return _open_minutes(get_contract(symbol).rth_open) % tf == 0


def register_frame(frame) -> Dict[int, bool]:
    """Precompute per-bar opening-range state for the frame's base series.

    Returns ``{length: resolvable}`` so a caller can report which range lengths
    this cell can actually carry before reporting any performance number.
    """
    global _ACTIVE
    bars = list(frame.base.bars)
    tf = int(frame.base.minutes)
    spec = frame.spec
    key = (frame.symbol.upper(), tf)
    _ACTIVE = key
    _RESOLVABLE[key] = {L: resolvable(frame.symbol, tf, L) for L in LENGTHS}
    _STATE[key] = {}

    for L in LENGTHS:
        per_ts: Dict[object, ORState] = {}
        _STATE[key][L] = per_ts
        if not _RESOLVABLE[key][L]:
            continue                      # leave empty: every lookup is inert
        cur: Optional[date] = None
        hi = lo = None
        covered = 0.0
        brk: Optional[str] = None
        brk_i: Optional[int] = None
        for i, b in enumerate(bars):
            day = trading_day(b.ts)
            if day != cur:
                cur, hi, lo, covered, brk, brk_i = day, None, None, 0.0, None, None
            mso = minutes_since_open(b.ts, spec.rth_open)
            in_rth = is_rth(b.ts, spec.rth_open, spec.rth_close)
            # ---- accumulate, but only bars that fit ENTIRELY inside [0, L)
            if in_rth and mso >= 0 and mso + b.minutes <= L:
                hi = b.high if hi is None else max(hi, b.high)
                lo = b.low if lo is None else min(lo, b.low)
                covered = max(covered, mso + b.minutes)
            complete = hi is not None and covered >= L and mso >= L
            if complete:
                # state as of the OPEN of this bar: the break recorded here is
                # only one from a strictly earlier bar.
                per_ts[b.ts] = ORState(
                    day=day, minutes=L, high=hi, low=lo, complete=True,
                    in_session=in_rth, mso=mso, prior_break=brk,
                    prior_break_i=brk_i, i=i)
                if brk is None:
                    if b.close > hi:
                        brk, brk_i = "UP", i
                    elif b.close < lo:
                        brk, brk_i = "DOWN", i
    return dict(_RESOLVABLE[key])


def _st(snap, length: int) -> Tuple[Optional[ORState], bool]:
    """``(state, inert)``. ``inert`` means unmeasurable here, not "no signal"."""
    key = (snap.symbol.upper(), _ACTIVE[1]) if _ACTIVE else None
    tbl = _STATE.get(key or (snap.symbol.upper(), 0), {})
    if not _RESOLVABLE.get(key, {}).get(length, False):
        return None, True
    return tbl.get(length, {}).get(snap.ts), False


def _gate(st: Optional[ORState], inert: bool, name: str) -> bool:
    if inert:
        INERT[name] += 1
        return False
    if st is None or not st.complete or st.height <= 0:
        return False
    return st.in_session if SESSION_ONLY else True


# ------------------------------------------------------------- conditions
def _make_break(L: int):
    name = f"orb_break_{L}m"

    @condition(name, description=(
        f"Close beyond a completed {L}-minute opening range, first break of "
        f"the session (FIRST_ONLY)"))
    def _fn(snap, tf, _L=L, _name=name):
        st, inert = _st(snap, _L)
        if not _gate(st, inert, _name):
            return ConditionResult.no()
        if FIRST_ONLY and st.prior_break is not None:
            return ConditionResult.no()
        c = snap.tf(tf).close if snap.tf(tf) else snap.price
        if c > st.high:
            return ConditionResult.yes(LONG, f"close above {_L}m OR high {st.high:g}", st.high)
        if c < st.low:
            return ConditionResult.yes(SHORT, f"close below {_L}m OR low {st.low:g}", st.low)
        return ConditionResult.no()
    return _fn


def _make_touch(L: int):
    name = f"orb_touch_{L}m"

    @condition(name, description=(
        f"Trade through a completed {L}-minute opening range boundary - stop "
        f"order fill, not a close. Crabel's resting-stop entry."))
    def _fn(snap, tf, _L=L, _name=name):
        st, inert = _st(snap, _L)
        if not _gate(st, inert, _name):
            return ConditionResult.no()
        if FIRST_ONLY and st.prior_break is not None:
            return ConditionResult.no()
        s = snap.tf(tf)
        if s is None:
            return ConditionResult.no()
        b = s.bar
        if b.high > st.high:
            return ConditionResult.yes(LONG, f"touched {_L}m OR high {st.high:g}", st.high)
        if b.low < st.low:
            return ConditionResult.yes(SHORT, f"touched {_L}m OR low {st.low:g}", st.low)
        return ConditionResult.no()
    return _fn


def _make_retest(L: int):
    name = f"orb_retest_{L}m"

    @condition(name, description=(
        f"Return to a broken {L}-minute opening range boundary that holds: the "
        f"break happened on an earlier bar, this bar trades back to the level "
        f"and closes on the breakout side."))
    def _fn(snap, tf, _L=L, _name=name):
        st, inert = _st(snap, _L)
        if not _gate(st, inert, _name):
            return ConditionResult.no()
        if st.prior_break is None or st.prior_break_i is None:
            return ConditionResult.no()
        if st.i - st.prior_break_i > RETEST_BARS:
            return ConditionResult.no()
        s = snap.tf(tf)
        if s is None:
            return ConditionResult.no()
        b = s.bar
        if st.prior_break == "UP" and b.low <= st.high < b.close:
            return ConditionResult.yes(LONG, f"retest held {_L}m OR high {st.high:g}",
                                       st.high, 0.9)
        if st.prior_break == "DOWN" and b.high >= st.low > b.close:
            return ConditionResult.yes(SHORT, f"retest held {_L}m OR low {st.low:g}",
                                       st.low, 0.9)
        return ConditionResult.no()
    return _fn


def _make_fade(L: int):
    name = f"orb_fade_{L}m"

    @condition(name, description=(
        f"Opening range breakout FAILURE: bar pokes beyond a completed "
        f"{L}-minute OR boundary by >= FADE_MIN_POKE of the range height and "
        f"closes back inside. Trade the reversal."))
    def _fn(snap, tf, _L=L, _name=name):
        st, inert = _st(snap, _L)
        if not _gate(st, inert, _name):
            return ConditionResult.no()
        s = snap.tf(tf)
        if s is None:
            return ConditionResult.no()
        b = s.bar
        poke = FADE_MIN_POKE * st.height
        if b.high >= st.high + poke and st.low <= b.close <= st.high:
            return ConditionResult.yes(SHORT, f"failed {_L}m OR high {st.high:g}",
                                       st.high, 0.85)
        if b.low <= st.low - poke and st.low <= b.close <= st.high:
            return ConditionResult.yes(LONG, f"failed {_L}m OR low {st.low:g}",
                                       st.low, 0.85)
        return ConditionResult.no()
    return _fn


for _L in LENGTHS:
    _make_break(_L)
    _make_touch(_L)
    _make_retest(_L)
    _make_fade(_L)
del _L


# --------------------------------------------------------------- reporting
def firing_rates(frame, tfs=None) -> List[dict]:
    """Bar-by-bar firing rate of every condition on ``frame``.

    Reports ``inert`` separately from ``fired``: a 0% rate because the range is
    unresolvable and a 0% rate because the market never broke out are different
    facts, and conflating them is how 4/4,256 was read as a market result.
    """
    register_frame(frame)
    tf = int(frame.base.minutes)
    tfs = tfs or [tf]
    rows = []
    snaps = [frame.snapshot(i) for i in range(len(frame))]
    snaps = [s for s in snaps if s is not None]
    n = len(snaps)
    for nm in names():
        INERT[nm] = 0
        c = CONDITIONS[nm]
        fired = longs = shorts = 0
        for s in snaps:
            r = c.fn(s, tf)
            if r.triggered:
                fired += 1
                longs += r.direction is LONG
                shorts += r.direction is SHORT
        L = int(nm.rsplit("_", 1)[-1][:-1])
        rows.append(dict(symbol=frame.symbol, tf=tf, condition=nm, n_bars=n,
                         fired=fired, rate=round(fired / n, 5) if n else 0.0,
                         longs=longs, shorts=shorts,
                         resolvable=_RESOLVABLE[(frame.symbol.upper(), tf)][L],
                         inert_bars=INERT[nm]))
    return rows


# ------------------------------------------------------------ data access
#: The ORB sample-size problem, and the one way out of it.
#:
#: ORB is a once-per-session signal, so the effective sample size is TRADING
#: DAYS, not bars. Measured spans of ``csv/raw``:
#:
#:     file      bars   trading days   max ORB trades
#:     *_1h      5000        ~220           ~220
#:     *_30m     1877         ~41            ~41
#:     *_15m     3753         ~41            ~41
#:     *_5m      5000         ~19            ~19   <- below the 20-trade floor
#:
#: and the opening range is unresolvable at 60m for every symbol whose open is
#: not on the hour (NQ, MES, MNQ at 09:30; MGC at 08:20). So the only timeframe
#: with an adequate sample is the one timeframe that cannot carry the signal,
#: and every timeframe that can carry it has 19-41 days. A 60/40 out-of-sample
#: split of 41 days is 25 trades against 16.
#:
#: ``data/{MES,MGC,MNQ}_1m.csv`` is the way out: 404k-470k genuine 1-minute
#: bars, 2019-01-01 to 2020-05-14, ~350 trading days. Resampled it makes every
#: opening range length resolvable on every grid (1 divides everything), for
#: MGC's 08:20 open as well. Caveat: the span contains the February-March 2020
#: crash, a volatility regime unlike the rest of it, so a disjoint-slice design
#: will land one slice on it and must say so.
LONG_1M = {"MES": "data/MES_1m.csv", "MGC": "data/MGC_1m.csv",
           "MNQ": "data/MNQ_1m.csv"}


def long_series(symbol: str, tf: int):
    """A long ``tf``-minute series resampled from the 1-minute archive.

    Use this rather than ``toolkit._series`` for any ORB measurement: it is the
    difference between 19 trading days and ~350.
    """
    from futures_agents.data.loader import load_csv
    path = LONG_1M.get(symbol.upper())
    if path is None:
        raise KeyError(f"no 1-minute archive for {symbol}; have {sorted(LONG_1M)}")
    base = load_csv(path, symbol, 1)
    return base if tf == 1 else base.resample(tf, keep_partial=False)
