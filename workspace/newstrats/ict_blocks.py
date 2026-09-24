"""ICT order blocks, fair value gaps, breakers and inversion FVGs.

Two concepts here are precise enough to code without interpretation, and that
is the whole reason this module exists: most ICT material is stated in a way
that cannot be falsified, but an order block and a fair value gap are both
*geometry* and geometry can be tested.

Definitions used (standard, stated so they can be argued with)
--------------------------------------------------------------
**Bullish order block** - the last down-close candle before an up-move that
*displaces*: within ``disp_bars`` bars the market must (a) trade above the
candle's high, (b) travel at least ``disp_atr`` ATR from the candle's low, (c)
*close* above the candle's high, and optionally (d) leave a bullish three-bar
imbalance inside that departure leg. The zone is the candle's full range
(``body_only=True`` narrows it to open..close). Bearish is the exact mirror.

"Last" is enforced: a down candle immediately followed by another down candle
is not the last one and is rejected. Without that rule a five-bar decline
paints five order blocks and the firing rate is a measure of nothing.

**Fair value gap** - three bars where bar i-1's high is below bar i+1's low
(bullish). The gap is [high(i-1), low(i+1)] and is only *knowable* at bar i+1,
which is the visibility index used everywhere below.

**Breaker block** - an order block that failed. A bullish OB whose zone price
*closed* below is dead as support; the claim is that it then acts as
resistance, i.e. flips polarity. Implemented literally.

**Inversion FVG** - a fair value gap that was filled through its far edge and
then acts from the other side. Same flip, applied to a gap.

Look-ahead discipline
---------------------
Every object carries ``confirmed_index``: the first bar at which every clause
of its definition was satisfiable from closed bars only. A block whose
displacement completes at bar i+3 does not exist at bar i+1, and nothing in
this file lets a condition see it there. Touch counts, invalidation and fills
are all folded forward in a single causal pass (:func:`bar_states`), so a
snapshot for bar t is a function of bars 0..t and nothing else.

Conditions are registered into the shared library registry under an ``ict_``
prefix, but ``library.py`` itself is untouched - five agents are editing this
tree at once.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from futures_agents.data.bars import Bar
from futures_agents.indicators.core import atr as atr_series
from futures_agents.schema import Direction
from futures_agents.strategies.base import ConditionKind, ConditionResult
from futures_agents.strategies.library import CONDITIONS, condition

LONG, SHORT, FLAT = Direction.LONG, Direction.SHORT, Direction.NEUTRAL

# ==========================================================================
# GEOMETRY
# ==========================================================================


@dataclass
class Zone:
    """A price band with a polarity, a birth bar and a life story."""

    kind: str                 # "OB" | "FVG"
    direction: str            # "BULL" | "BEAR" - the side it is claimed to act for
    top: float
    bottom: float
    origin_index: int         # the bar the geometry sits on
    confirmed_index: int      # first bar at which it was knowable
    # filled in causally by bar_states(); never read before its own bar
    touch_indices: List[int] = field(default_factory=list)
    post_break_touches: List[int] = field(default_factory=list)
    broken_index: Optional[int] = None      # closed through the far edge
    disp: float = 0.0                       # displacement in ATR, for sizing
    width_atr: float = 0.0
    dist_atr: float = 0.0                   # distance from close@confirm to proximal

    @property
    def proximal(self) -> float:
        """The edge price meets first on the way back."""
        return self.top if self.direction == "BULL" else self.bottom

    @property
    def distal(self) -> float:
        return self.bottom if self.direction == "BULL" else self.top

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    def touched_by(self, bar: Bar) -> bool:
        return bar.low <= self.top and bar.high >= self.bottom


def _atr(bars: Sequence[Bar], period: int = 14) -> List[Optional[float]]:
    return atr_series([b.high for b in bars], [b.low for b in bars],
                      [b.close for b in bars], period)


def detect_order_blocks(bars: Sequence[Bar], atr: Sequence[Optional[float]], *,
                        disp_bars: int = 3, disp_atr: float = 1.0,
                        require_fvg: bool = True, body_only: bool = False,
                        require_last: bool = True) -> List[Zone]:
    """Order blocks with an explicit displacement requirement.

    Returns them in ``confirmed_index`` order, which is the order a forward
    walk becomes aware of them.
    """
    n = len(bars)
    out: List[Zone] = []
    for i in range(1, n - 1):
        a = atr[i]
        if not a or a <= 0:
            continue
        b = bars[i]
        if b.close == b.open:
            continue
        bull = b.close < b.open          # a DOWN candle is a BULLISH order block
        if require_last:
            nxt = bars[i + 1]
            # the last down candle before the up move: the next bar must not
            # also be a down candle, or this one is not the last.
            if bull and nxt.close < nxt.open:
                continue
            if not bull and nxt.close > nxt.open:
                continue
        hi = max(b.high, b.open, b.close)
        lo = min(b.low, b.open, b.close)
        run_hi, run_lo = -1e18, 1e18
        seen_fvg = not require_fvg
        confirmed = None
        for j in range(i + 1, min(i + 1 + disp_bars, n)):
            run_hi = max(run_hi, bars[j].high)
            run_lo = min(run_lo, bars[j].low)
            # imbalance inside the departure leg, knowable at bar j
            if require_fvg and not seen_fvg and j - 1 >= i + 1:
                m = j - 1                       # middle bar of the 3-bar gap
                if m - 1 >= i:
                    if bull and bars[j].low > bars[m - 1].high:
                        seen_fvg = True
                    if (not bull) and bars[j].high < bars[m - 1].low:
                        seen_fvg = True
            if not seen_fvg:
                continue
            if bull:
                ok = (run_hi > hi and bars[j].close > hi
                      and run_hi - lo >= disp_atr * a)
            else:
                ok = (run_lo < lo and bars[j].close < lo
                      and hi - run_lo >= disp_atr * a)
            if ok:
                confirmed = j
                break
        if confirmed is None:
            continue
        if body_only:
            top, bottom = max(b.open, b.close), min(b.open, b.close)
        else:
            top, bottom = b.high, b.low
        if top <= bottom:
            continue
        direction = "BULL" if bull else "BEAR"
        z = Zone("OB", direction, top, bottom, i, confirmed)
        z.disp = (run_hi - lo) / a if bull else (hi - run_lo) / a
        z.width_atr = (top - bottom) / a
        cc = bars[confirmed].close
        ca = atr[confirmed] or a
        z.dist_atr = abs(cc - z.proximal) / ca
        out.append(z)
    out.sort(key=lambda z: z.confirmed_index)
    return out


def detect_fvgs(bars: Sequence[Bar], atr: Sequence[Optional[float]], *,
                min_width_atr: float = 0.0) -> List[Zone]:
    """Three-bar fair value gaps, visible at the third bar."""
    n = len(bars)
    out: List[Zone] = []
    for i in range(1, n - 1):
        a = atr[i]
        if not a or a <= 0:
            continue
        prev, nxt = bars[i - 1], bars[i + 1]
        if nxt.low > prev.high:
            top, bottom, d = nxt.low, prev.high, "BULL"
        elif nxt.high < prev.low:
            top, bottom, d = prev.low, nxt.high, "BEAR"
        else:
            continue
        if (top - bottom) / a < min_width_atr:
            continue
        z = Zone("FVG", d, top, bottom, i, i + 1)
        z.width_atr = (top - bottom) / a
        ca = atr[i + 1] or a
        z.dist_atr = abs(bars[i + 1].close - z.proximal) / ca
        out.append(z)
    out.sort(key=lambda z: z.confirmed_index)
    return out


# ==========================================================================
# CAUSAL FORWARD PASS
# ==========================================================================

@dataclass
class BarState:
    """What an ICT reader could truthfully say at one bar. Nothing more."""

    ob: Optional[Tuple[str, bool, float]] = None       # (dir, fresh, disp)
    breaker: Optional[str] = None                      # flipped direction
    breaker_fresh: Optional[str] = None                # first retest after failure
    fvg: Optional[Tuple[str, bool, float]] = None      # (dir, fresh, width_atr)
    ifvg: Optional[str] = None                         # inverted direction
    ifvg_fresh: Optional[str] = None                   # first retest after inversion
    ob_close_in: Optional[str] = None                  # close inside OB zone
    fvg_close_in: Optional[str] = None                 # close inside FVG zone
    ob_newest: Optional[Tuple[str, bool, float]] = None   # only the newest live OB
    fvg_newest: Optional[Tuple[str, bool, float]] = None  # only the newest live gap
    #: A faithful re-implementation of the library's ``fvg_nearby`` semantics
    #: inside this machinery: a gap dies at 50% penetration (the library calls
    #: that "filled") and the test is CLOSE-inside, not range-touch. Exists
    #: purely so the Jaccard against ``fvg_nearby`` can separate "different
    #: idea" from "different bookkeeping".
    fvg_libclone: Optional[str] = None
    n_ob_active: int = 0
    n_fvg_active: int = 0


#: How long a zone stays on the chart before it stops being a level. Matches
#: the library's FVG_SCAN_WINDOW/ZONE_SCAN_WINDOW spirit: a gap four hundred
#: bars old that price never revisited is not something anyone trades.
OB_LIFE = 120
FVG_LIFE = 60


def _ci(slot) -> float:
    """Confirmed index stashed in the third slot of a ``*_newest`` tuple."""
    return slot[2]


def bar_states(bars: Sequence[Bar], obs: Sequence[Zone], fvgs: Sequence[Zone], *,
               ob_life: int = OB_LIFE, fvg_life: int = FVG_LIFE
               ) -> List[BarState]:
    """One causal walk. Everything a condition reads is produced here.

    Zones enter the active set at ``confirmed_index``, leave it when price
    closes through the far edge (they become breakers / inversions) or when
    they age out. Touch counts are incremented as the walk passes, so
    ``fresh`` at bar t means "untouched as of t", never "untouched ever".
    """
    n = len(bars)
    states = [BarState() for _ in range(n)]
    obs = list(obs)
    fvgs = list(fvgs)
    oi = fi = 0
    live_ob: List[Zone] = []
    live_fvg: List[Zone] = []
    dead_ob: List[Zone] = []      # breakers
    dead_fvg: List[Zone] = []     # inversions

    # library-clone bookkeeping: gaps that die at 50% penetration
    lib_live: List[Zone] = []
    li = 0

    for t in range(n):
        while oi < len(obs) and obs[oi].confirmed_index <= t:
            live_ob.append(obs[oi]); oi += 1
        while fi < len(fvgs) and fvgs[fi].confirmed_index <= t:
            live_fvg.append(fvgs[fi]); fi += 1
        while li < len(fvgs) and fvgs[li].confirmed_index <= t:
            lib_live.append(fvgs[li]); li += 1
        bar = bars[t]
        st = states[t]

        # ---- library clone: fvg_nearby semantics, 50% fill kills the gap
        lk: List[Zone] = []
        for z in lib_live:
            if t - z.confirmed_index > fvg_life:
                continue
            if t > z.confirmed_index and bar.low <= z.mid <= bar.high:
                continue                      # "filled" in the library's sense
            if t >= z.confirmed_index and st.fvg_libclone is None \
                    and z.bottom <= bar.close <= z.top:
                st.fvg_libclone = z.direction
            lk.append(z)
        lib_live = lk

        # ---- live order blocks
        keep: List[Zone] = []
        for z in live_ob:
            if t - z.confirmed_index > ob_life:
                continue
            if t > z.confirmed_index and z.touched_by(bar):
                fresh = not z.touch_indices
                if st.ob is None:
                    st.ob = (z.direction, fresh, z.disp)
                if st.ob_close_in is None and z.bottom <= bar.close <= z.top:
                    st.ob_close_in = z.direction
                z.touch_indices.append(t)
            broke = ((z.direction == "BULL" and bar.close < z.bottom)
                     or (z.direction == "BEAR" and bar.close > z.top))
            if broke and t > z.confirmed_index:
                z.broken_index = t
                dead_ob.append(z)
                continue
            keep.append(z)
        st.n_ob_active = len(keep)
        # Only the most recent unmitigated block. A live ICT reader is looking
        # at one block, not at the thirteen that happen to be on the chart, and
        # the difference between those two readings is the whole firing rate.
        elig = [z for z in keep if z.confirmed_index < t]
        if elig:
            nz = max(elig, key=lambda z: z.confirmed_index)
            if nz.touched_by(bar):
                st.ob_newest = (nz.direction, len(nz.touch_indices) <= 1,
                                float(nz.confirmed_index))
        live_ob = keep

        # ---- breakers: a failed OB, claimed to act with opposite polarity
        bk: List[Zone] = []
        for z in dead_ob:
            if t - (z.broken_index or 0) > ob_life:
                continue
            if t > (z.broken_index or 0) and z.touched_by(bar):
                flip = "BEAR" if z.direction == "BULL" else "BULL"
                if st.breaker is None:
                    st.breaker = flip
                if not z.post_break_touches and st.breaker_fresh is None:
                    st.breaker_fresh = flip
                z.post_break_touches.append(t)
            bk.append(z)
        dead_ob = bk

        # ---- live fair value gaps
        keepf: List[Zone] = []
        for z in live_fvg:
            if t - z.confirmed_index > fvg_life:
                continue
            if t > z.confirmed_index and z.touched_by(bar):
                fresh = not z.touch_indices
                if st.fvg is None:
                    st.fvg = (z.direction, fresh, z.width_atr)
                if st.fvg_close_in is None and z.bottom <= bar.close <= z.top:
                    st.fvg_close_in = z.direction
                z.touch_indices.append(t)
            broke = ((z.direction == "BULL" and bar.close < z.bottom)
                     or (z.direction == "BEAR" and bar.close > z.top))
            if broke and t > z.confirmed_index:
                z.broken_index = t
                dead_fvg.append(z)
                continue
            keepf.append(z)
        st.n_fvg_active = len(keepf)
        eligf = [z for z in keepf if z.confirmed_index < t]
        if eligf:
            nz = max(eligf, key=lambda z: z.confirmed_index)
            if nz.touched_by(bar):
                st.fvg_newest = (nz.direction, len(nz.touch_indices) <= 1,
                                 float(nz.confirmed_index))
        live_fvg = keepf

        # ---- inversion FVGs
        kf: List[Zone] = []
        for z in dead_fvg:
            if t - (z.broken_index or 0) > fvg_life:
                continue
            if t > (z.broken_index or 0) and z.touched_by(bar):
                flip = "BEAR" if z.direction == "BULL" else "BULL"
                if st.ifvg is None:
                    st.ifvg = flip
                if not z.post_break_touches and st.ifvg_fresh is None:
                    st.ifvg_fresh = flip
                z.post_break_touches.append(t)
            kf.append(z)
        dead_fvg = kf
    return states


# ==========================================================================
# REGISTRATION - conditions read a snapshot, which carries no bars
# ==========================================================================

#: (symbol, tf, ts) -> BarState
_MAP: Dict[Tuple[str, int, object], BarState] = {}
#: (symbol, tf) -> (bars, atr, obs, fvgs, states), for the bar-level studies
CACHE: Dict[Tuple[str, int], tuple] = {}
#: (condition, symbol, tf) -> lookups that found nothing. A miss is a mismatch
#: between the bar grid the conditions see and the one they were built on, and
#: it must be visible rather than reported as "did not fire".
MISSES: Dict[Tuple[str, str, int], int] = {}

PARAMS = dict(disp_bars=3, disp_atr=1.0, require_fvg=True, body_only=False,
              min_width_atr=0.0, ob_life=OB_LIFE, fvg_life=FVG_LIFE)


def build(bars: Sequence[Bar], **over):
    p = dict(PARAMS); p.update(over)
    a = _atr(bars)
    obs = detect_order_blocks(bars, a, disp_bars=p["disp_bars"],
                              disp_atr=p["disp_atr"],
                              require_fvg=p["require_fvg"],
                              body_only=p["body_only"])
    fvgs = detect_fvgs(bars, a, min_width_atr=p["min_width_atr"])
    states = bar_states(bars, obs, fvgs, ob_life=p["ob_life"],
                        fvg_life=p["fvg_life"])
    return a, obs, fvgs, states


def register_frame(frame, symbol: str, tfs: Sequence[int] = (), **over) -> None:
    """Populate the lookup from the very frame the backtest will run on.

    Re-reading the CSV would risk a different bar grid - 240m is a resample of
    60m - and a grid mismatch would surface as "the condition never fired".
    """
    for tf, tff in frame.frames.items():
        if tfs and tf not in tfs:
            continue
        bars = list(tff.series.bars)
        if len(bars) < 60:
            continue
        a, obs, fvgs, states = build(bars, **over)
        CACHE[(symbol.upper(), tf)] = (bars, a, obs, fvgs, states)
        for i, b in enumerate(bars):
            _MAP[(symbol.upper(), tf, b.ts)] = states[i]


def _state(snap, tf) -> Optional[BarState]:
    s = snap.tf(tf)
    if s is None:
        return None
    return _MAP.get((snap.symbol.upper(), tf, s.bar.ts))


def _miss(name, snap, tf):
    k = (name, snap.symbol.upper(), tf)
    MISSES[k] = MISSES.get(k, 0) + 1


def _dir(d: str) -> Direction:
    return LONG if d == "BULL" else SHORT


def _reg(name, group, kind=ConditionKind.SIGNAL, description=""):
    """Register, tolerating a re-import in the same process."""
    def deco(fn):
        if name in CONDITIONS:
            return fn
        return condition(name, group, kind=kind, description=description)(fn)
    return deco


@_reg("ict_ob_return", "ict", description="Price back inside a displaced order block")
def _ob_return(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_ob_return", snap, tf); return ConditionResult.no()
    if st.ob is None:
        return ConditionResult.no()
    d, fresh, disp = st.ob
    return ConditionResult.yes(_dir(d), f"{d.lower()} OB retest"
                               f"{' (fresh)' if fresh else ''}", round(disp, 2))


@_reg("ict_ob_fresh", "ict", description="First return to an untested order block")
def _ob_fresh(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_ob_fresh", snap, tf); return ConditionResult.no()
    if st.ob is None or not st.ob[1]:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.ob[0]), "fresh OB", round(st.ob[2], 2))


@_reg("ict_ob_retested", "ict", description="Return to an order block already tested once")
def _ob_retested(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_ob_retested", snap, tf); return ConditionResult.no()
    if st.ob is None or st.ob[1]:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.ob[0]), "retested OB", round(st.ob[2], 2))


@_reg("ict_ob_close_in", "ict", description="Bar CLOSED inside an order block")
def _ob_close(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_ob_close_in", snap, tf); return ConditionResult.no()
    if st.ob_close_in is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.ob_close_in), "close inside OB")


@_reg("ict_fvg_return", "ict", description="Price back inside an unfilled fair value gap")
def _fvg_return(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_fvg_return", snap, tf); return ConditionResult.no()
    if st.fvg is None:
        return ConditionResult.no()
    d, fresh, w = st.fvg
    return ConditionResult.yes(_dir(d), f"{d.lower()} FVG retest"
                               f"{' (fresh)' if fresh else ''}", round(w, 2))


@_reg("ict_fvg_fresh", "ict", description="First return to an untouched fair value gap")
def _fvg_fresh(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_fvg_fresh", snap, tf); return ConditionResult.no()
    if st.fvg is None or not st.fvg[1]:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.fvg[0]), "fresh FVG", round(st.fvg[2], 2))


@_reg("ict_fvg_retested", "ict", description="Return to a gap already traded into once")
def _fvg_retested(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_fvg_retested", snap, tf); return ConditionResult.no()
    if st.fvg is None or st.fvg[1]:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.fvg[0]), "retested FVG", round(st.fvg[2], 2))


@_reg("ict_fvg_close_in", "ict", description="Bar CLOSED inside an unfilled gap")
def _fvg_close(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_fvg_close_in", snap, tf); return ConditionResult.no()
    if st.fvg_close_in is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.fvg_close_in), "close inside FVG")


@_reg("ict_breaker", "ict", description="Retest of a failed order block, opposite polarity")
def _breaker(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_breaker", snap, tf); return ConditionResult.no()
    if st.breaker is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.breaker), f"{st.breaker.lower()} breaker")


@_reg("ict_inversion_fvg", "ict", description="Retest of a filled gap from the other side")
def _ifvg(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_inversion_fvg", snap, tf); return ConditionResult.no()
    if st.ifvg is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.ifvg), f"{st.ifvg.lower()} inversion FVG")


# --------------------------------------------------------------------------
# PLACEBO - the control that makes every number above mean something.
#
# Same geometry, same distance in ATR, same side, same width, same holding
# horizon: everything except the claim that the location is special. Without
# it "price returns to order blocks 70% of the time" is a statement about
# price wandering, not about order blocks.
# --------------------------------------------------------------------------

#: (which, symbol, tf, ts) -> direction. Keyed on the TIMESTAMP, not the bar
#: index, because the in-sample and out-of-sample frames are slices with
#: different indices and an index key would silently shift the whole arm.
_PLACEBO: Dict[Tuple[str, str, int, object], str] = {}

#: Which BarState attribute each placebo is matched to.
PLACEBO_SRC = {"ob": "ob_fresh_dir", "fvg": "fvg_fresh_dir"}


def _fresh_dir(st: BarState, which: str) -> Optional[str]:
    slot = st.ob if which == "ob" else st.fvg
    return slot[0] if slot and slot[1] else None


def register_placebo(symbol: str, tf: int, seed: int = 7) -> None:
    """Sham arms matched to the real firing pattern.

    Same number of firings, same long/short mix, drawn from bars with a valid
    ATR - only the *locations* are random. This is the control that separates
    "the order block did something" from "any condition that fires this often
    and this directionally would have".
    """
    key = (symbol.upper(), tf)
    bars, a, obs, fvgs, states = CACHE[key]
    n = len(bars)
    valid = [i for i in range(30, n - 5) if a[i]]
    for off, which in enumerate(("ob", "fvg")):
        rng = random.Random(seed + off)
        dirs = [_fresh_dir(st, which) for st in states]
        fired = [d for d in dirs if d]
        picks = rng.sample(valid, min(len(fired), len(valid)))
        for d, j in zip(fired, picks):
            _PLACEBO[(which, symbol.upper(), tf, bars[j].ts)] = d


def _placebo_fn(which: str):
    def fn(snap, tf):
        s = snap.tf(tf)
        if s is None:
            return ConditionResult.no()
        d = _PLACEBO.get((which, snap.symbol.upper(), tf, s.bar.ts))
        if d is None:
            return ConditionResult.no()
        return ConditionResult.yes(_dir(d), f"{which} placebo")
    return fn


_reg("ict_placebo_ob", "ict",
     description="Sham locations matched to ict_ob_fresh - the control arm")(
    _placebo_fn("ob"))
_reg("ict_placebo_fvg", "ict",
     description="Sham locations matched to ict_fvg_fresh - the control arm")(
    _placebo_fn("fvg"))


@_reg("ict_breaker_fresh", "ict", description="FIRST retest of a failed order block")
def _breaker_fresh(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_breaker_fresh", snap, tf); return ConditionResult.no()
    if st.breaker_fresh is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.breaker_fresh), "first breaker retest")


@_reg("ict_ifvg_fresh", "ict", description="FIRST retest of an inverted fair value gap")
def _ifvg_fresh(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_ifvg_fresh", snap, tf); return ConditionResult.no()
    if st.ifvg_fresh is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.ifvg_fresh), "first inversion retest")


@_reg("ict_ob_newest", "ict", description="Return to the NEWEST live order block only")
def _ob_newest(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_ob_newest", snap, tf); return ConditionResult.no()
    if st.ob_newest is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.ob_newest[0]), "newest OB")


@_reg("ict_fvg_newest", "ict", description="Return to the NEWEST live fair value gap only")
def _fvg_newest(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_fvg_newest", snap, tf); return ConditionResult.no()
    if st.fvg_newest is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.fvg_newest[0]), "newest FVG")


@_reg("ict_fvg_libclone", "ict",
      description="fvg_nearby's exact semantics, rebuilt here as a Jaccard control")
def _fvg_libclone(snap, tf):
    st = _state(snap, tf)
    if st is None:
        _miss("ict_fvg_libclone", snap, tf); return ConditionResult.no()
    if st.fvg_libclone is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dir(st.fvg_libclone), "lib-clone FVG")


ALL = ["ict_ob_return", "ict_ob_fresh", "ict_ob_retested", "ict_ob_close_in",
       "ict_ob_newest", "ict_fvg_return", "ict_fvg_fresh", "ict_fvg_retested",
       "ict_fvg_close_in", "ict_fvg_newest", "ict_fvg_libclone",
       "ict_breaker", "ict_breaker_fresh", "ict_inversion_fvg",
       "ict_ifvg_fresh"]
