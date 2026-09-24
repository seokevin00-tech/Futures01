"""Swing-structure DEPTH: how many consecutive HH/HL (or LH/LL) have printed.

``structure_trend`` in the library is binary - UPTREND or DOWNTREND - and it is
derived from a *count difference* over the whole confirmed swing history
(``bullish >= bearish + 2``). That throws away the thing a trader actually
reads off the chart: how many consecutive higher highs AND higher lows are
sitting behind the current price. A trend two swings old and a trend six swings
old are different trades.

This module rebuilds that reading from the raw material - ``find_swings`` and
``Swing.confirmed_index`` - and exposes it as a family of conditions
parameterised by the required depth, so 1 / 2 / 3 / 4 / 5+ can be *compared*
rather than assumed.

Look-ahead discipline
---------------------
A fractal swing at bar ``i`` with ``right=3`` is not knowable until bar
``i + 3``. :func:`depth_series` walks the swing list in ``confirmed_index``
order and only ever folds a swing into the running depth at the bar it became
confirmed. Nothing in this file reads ``Swing.index`` for gating - only for
measuring how long the structure has been running, and only for swings already
folded in.

Registration rather than reloading
----------------------------------
A condition receives a ``FeatureSnapshot``, which carries no bars, so depth has
to be precomputed and looked up. It is keyed on ``(symbol, timeframe,
bar timestamp)`` and populated from the very ``SymbolFrame`` the backtest will
run on (:func:`register_frame`), not from a re-read of the CSV. Re-deriving it
from disk would risk a silently different bar grid - the 4h series is a
resample of 1h and the daily series is a resample of that - and a lookup miss
would then be reported as "the condition did not fire". Misses are counted in
:data:`MISSES` instead, so a mismatch is visible rather than invisible.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

from futures_agents.indicators.structure import Swing, find_swings
from futures_agents.schema import Direction
from futures_agents.strategies.base import ConditionKind, ConditionResult
from futures_agents.strategies.library import condition

LONG, SHORT = Direction.LONG, Direction.SHORT

#: (symbol, tf, ts) -> BarDepth. Populated by :func:`register_frame`.
_MAP: Dict[Tuple[str, int, object], "BarDepth"] = {}

#: (condition name, symbol, tf) -> count of lookups that found no entry.
MISSES: Dict[Tuple[str, str, int], int] = {}

#: Which timeframe counts as "the higher one" when trading a given timeframe.
#: Matches ``futures_agents.scout.FRAMES`` so the snapshot always carries it.
HTF = {5: 15, 15: 60, 30: 60, 60: 240, 240: 1440}


class BarDepth:
    """Confirmed structural depth as of one bar.

    ``up`` is the number of consecutive higher highs *and* higher lows - the
    minimum of the two trailing runs, because a sequence of higher highs
    against flat lows is not an uptrend a trader would count. ``down`` is the
    mirror. They are mutually exclusive by construction: a new high that is
    higher resets the lower-high run to zero.
    """

    __slots__ = ("up", "down", "age", "up_run_hh", "up_run_hl",
                 "down_run_lh", "down_run_ll")

    def __init__(self, up: int, down: int, age: int,
                 hh: int, hl: int, lh: int, ll: int):
        self.up, self.down, self.age = up, down, age
        self.up_run_hh, self.up_run_hl = hh, hl
        self.down_run_lh, self.down_run_ll = lh, ll

    @property
    def signed(self) -> int:
        return self.up if self.up else -self.down

    def __repr__(self) -> str:
        return f"BarDepth(up={self.up}, down={self.down}, age={self.age})"


_FLAT = BarDepth(0, 0, 0, 0, 0, 0, 0)


def depth_series(bars: Sequence, left: int = 3, right: int = 3) -> List[BarDepth]:
    """Per-bar confirmed structural depth, in one forward pass.

    ``age`` is how many bars ago the *first* swing of the current run printed -
    the duration of the structure, which is the quantity that separates "a
    5-swing count" from "a months-long trend". It is measured to the swing's
    own bar index, not its confirmation bar, because that is where the
    structure began even though it was not knowable until later.
    """
    n = len(bars)
    out: List[BarDepth] = [_FLAT] * n
    if n == 0:
        return out
    swings = sorted(find_swings(bars, left, right), key=lambda s: s.confirmed_index)

    # Trailing runs and the swing indices that produced them. Only the last
    # few matter, so the deques are bounded.
    hi_px: deque = deque(maxlen=12)
    hi_ix: deque = deque(maxlen=12)
    lo_px: deque = deque(maxlen=12)
    lo_ix: deque = deque(maxlen=12)
    hh = hl = lh = ll = 0

    k = 0
    for i in range(n):
        while k < len(swings) and swings[k].confirmed_index <= i:
            s: Swing = swings[k]
            if s.is_high:
                if hi_px:
                    if s.price > hi_px[-1]:
                        hh, lh = hh + 1, 0
                    elif s.price < hi_px[-1]:
                        lh, hh = lh + 1, 0
                    else:
                        hh = lh = 0
                hi_px.append(s.price)
                hi_ix.append(s.index)
            else:
                if lo_px:
                    if s.price > lo_px[-1]:
                        hl, ll = hl + 1, 0
                    elif s.price < lo_px[-1]:
                        ll, hl = ll + 1, 0
                    else:
                        hl = ll = 0
                lo_px.append(s.price)
                lo_ix.append(s.index)
            k += 1

        up, down = min(hh, hl), min(lh, ll)
        d = up or down
        age = 0
        if d:
            # The run of depth d began at the (d+1)-th swing back on each side.
            j = d + 1
            starts = []
            if len(hi_ix) >= j:
                starts.append(hi_ix[-j])
            if len(lo_ix) >= j:
                starts.append(lo_ix[-j])
            if starts:
                age = i - min(starts)
        out[i] = BarDepth(up, down, age, hh, hl, lh, ll)
    return out


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

def register_frame(frame, left: int = 3, right: int = 3) -> Dict[int, int]:
    """Index every timeframe of a ``SymbolFrame`` by bar timestamp.

    Returns ``{timeframe: bars indexed}``. Safe to call repeatedly; later
    registrations overwrite earlier ones for the same key, which is what a
    caller running several disjoint slices of the same symbol wants.
    """
    counts: Dict[int, int] = {}
    sym = frame.symbol.upper()
    for tf, tff in frame.frames.items():
        bars = tff.series.bars
        col = depth_series(bars, left, right)
        for b, d in zip(bars, col):
            _MAP[(sym, int(tf), b.ts)] = d
        counts[int(tf)] = len(bars)
    return counts


def frame_depths(frame, tf: int) -> List[BarDepth]:
    """The depth column for one timeframe of a registered frame."""
    return [lookup(frame.symbol, tf, b.ts) for b in frame.frames[tf].series.bars]


def lookup(symbol: str, tf: int, ts) -> BarDepth:
    return _MAP.get((symbol.upper(), int(tf), ts), _FLAT)


def clear() -> None:
    _MAP.clear()
    MISSES.clear()


def _read(snap, tf: int, name: str) -> Optional[BarDepth]:
    s = snap.tf(tf)
    if s is None:
        return None
    key = (snap.symbol.upper(), int(tf), s.bar.ts)
    d = _MAP.get(key)
    if d is None:
        k = (name, snap.symbol.upper(), int(tf))
        MISSES[k] = MISSES.get(k, 0) + 1
        return None
    return d


# --------------------------------------------------------------------------
# Conditions - the count family
# --------------------------------------------------------------------------

def _make_ge(n: int):
    name = f"sd_count_ge{n}"

    @condition(name, "structure_depth", warmup=30,
               description=f"At least {n} consecutive HH/HL (or LH/LL) confirmed")
    def _fn(snap, tf, _n=n, _name=name):
        d = _read(snap, tf, _name)
        if d is None:
            return ConditionResult.no()
        if d.up >= _n:
            return ConditionResult.yes(LONG, f"{d.up} consecutive HH/HL", d.up,
                                       min(1.0, d.up / 5.0))
        if d.down >= _n:
            return ConditionResult.yes(SHORT, f"{d.down} consecutive LH/LL", d.down,
                                       min(1.0, d.down / 5.0))
        return ConditionResult.no()
    return _fn


def _make_eq(n: int):
    name = f"sd_count_eq{n}"

    @condition(name, "structure_depth", warmup=30,
               description=f"Exactly {n} consecutive HH/HL (or LH/LL) confirmed")
    def _fn(snap, tf, _n=n, _name=name):
        d = _read(snap, tf, _name)
        if d is None:
            return ConditionResult.no()
        if d.up == _n:
            return ConditionResult.yes(LONG, f"exactly {d.up} HH/HL", d.up)
        if d.down == _n:
            return ConditionResult.yes(SHORT, f"exactly {d.down} LH/LL", d.down)
        return ConditionResult.no()
    return _fn


def _make_mtf(n: int):
    name = f"sd_mtf_ge{n}"

    @condition(name, "structure_depth", warmup=30,
               description=f"Higher timeframe shows at least {n} consecutive HH/HL")
    def _fn(snap, tf, _n=n, _name=name):
        htf = HTF.get(int(tf))
        if htf is None:
            return ConditionResult.no()
        d = _read(snap, htf, _name)
        if d is None:
            return ConditionResult.no()
        if d.up >= _n:
            return ConditionResult.yes(LONG, f"{htf}m: {d.up} HH/HL", d.up)
        if d.down >= _n:
            return ConditionResult.yes(SHORT, f"{htf}m: {d.down} LH/LL", d.down)
        return ConditionResult.no()
    return _fn


def _make_age(bars: int):
    name = f"sd_age_ge{bars}"

    @condition(name, "structure_depth", warmup=30,
               description=f"Current structure has been running >= {bars} bars")
    def _fn(snap, tf, _b=bars, _name=name):
        d = _read(snap, tf, _name)
        if d is None or d.age < _b:
            return ConditionResult.no()
        if d.up:
            return ConditionResult.yes(LONG, f"up structure {d.age} bars old", d.age)
        if d.down:
            return ConditionResult.yes(SHORT, f"down structure {d.age} bars old", d.age)
        return ConditionResult.no()
    return _fn


COUNT_GE = {n: _make_ge(n) for n in (1, 2, 3, 4, 5)}
COUNT_EQ = {n: _make_eq(n) for n in (1, 2, 3, 4)}
MTF_GE = {n: _make_mtf(n) for n in (1, 2, 3, 4, 5)}
AGE_GE = {b: _make_age(b) for b in (10, 25, 50, 100)}


@condition("sd_flat", "structure_depth", kind=ConditionKind.FILTER, warmup=30,
           description="No confirmed HH/HL or LH/LL run at all - the null state")
def _sd_flat(snap, tf):
    d = _read(snap, tf, "sd_flat")
    if d is None:
        return ConditionResult.no()
    return (ConditionResult.yes(Direction.NEUTRAL, "no run", 0)
            if d.signed == 0 else ConditionResult.no())


def names() -> List[str]:
    """Every condition name this module registers."""
    return ([f"sd_count_ge{n}" for n in (1, 2, 3, 4, 5)]
            + [f"sd_count_eq{n}" for n in (1, 2, 3, 4)]
            + [f"sd_mtf_ge{n}" for n in (1, 2, 3, 4, 5)]
            + [f"sd_age_ge{b}" for b in (10, 25, 50, 100)]
            + ["sd_flat"])
