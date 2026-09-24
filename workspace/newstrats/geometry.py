"""Swing GEOMETRY conditions - the shape of the swings, not their direction.

``structure_trend`` says "highs and lows are rising". It says nothing about
*how*. Two uptrends carrying identical HH/HL labels are different trades when
one has impulse legs getting longer and pullbacks getting shallower and the
other has legs contracting and pullbacks deepening.

Everything here is built from :func:`find_swings` and every swing is filtered
on ``confirmed_index`` - a fractal swing at bar *i* is not knowable until bar
*i + right*, and a geometry built from swings at their formation index is a
look-ahead bug that prints beautiful equity curves.

Normalisation
-------------
Leg lengths are carried in points but every *test* is scale-free:

* leg expansion is a monotone comparison of consecutive impulse legs, so the
  price unit cancels;
* retracement depth is a fraction of the leg it retraces, so the price unit
  cancels;
* swing symmetry is a fraction of time, so the price unit cancels.

The only place a magnitude enters is the ``min_atr`` gate, and that is measured
in ATR of the evaluating timeframe. MGC at 4,400 and MES at 6,800 therefore
produce comparable numbers, and a symbol effect cannot masquerade as a geometry
effect.

Registration
------------
A condition function receives only ``(FeatureSnapshot, tf)`` and cannot reach
the bar series, so the caller registers the exact bars the frame is running on
before the backtest::

    import geometry as G
    frame = build_symbol_frame(series, FRAMES[tf])
    G.register_frame(frame)

Geometry is keyed by bar timestamp rather than bar index so a slice, a window
and the full series can never silently read one another's indices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from futures_agents.indicators.structure import Swing, find_swings
from futures_agents.schema import Direction
from futures_agents.strategies.base import Condition, ConditionKind, ConditionResult

LONG, SHORT, FLAT = Direction.LONG, Direction.SHORT, Direction.NEUTRAL

#: Our own registry. We deliberately do NOT write into
#: ``library.CONDITIONS`` - five agents sharing that dict is how name
#: collisions and cross-study contamination happen.
CONDITIONS: Dict[str, Condition] = {}


def condition(name: str, group: str = "geometry", *,
              kind: ConditionKind = ConditionKind.SIGNAL,
              description: str = "", warmup: int = 60):
    def deco(fn):
        CONDITIONS[name] = Condition(name=name, group=group, fn=fn, kind=kind,
                                     description=description or (fn.__doc__ or "").strip(),
                                     warmup_bars=warmup)
        return fn
    return deco


def get(name: str) -> Condition:
    return CONDITIONS[name]


# ==========================================================================
# Geometry state
# ==========================================================================

@dataclass(frozen=True)
class Leg:
    """One zigzag leg: from one confirmed pivot to the next."""
    i0: int
    i1: int
    p0: float
    p1: float

    @property
    def up(self) -> bool:
        return self.p1 > self.p0

    @property
    def size(self) -> float:
        return abs(self.p1 - self.p0)

    @property
    def bars(self) -> int:
        return max(1, self.i1 - self.i0)


@dataclass(frozen=True)
class Geo:
    """Swing geometry visible at one bar. ``legs`` is oldest-first."""
    legs: Tuple[Leg, ...]

    def impulses(self, up: bool) -> List[Leg]:
        return [l for l in self.legs if l.up == up]

    def retraces(self, up: bool) -> List[Leg]:
        return [l for l in self.legs if l.up != up]

    def retrace_ratios(self, up: bool) -> List[float]:
        """Fraction of the *preceding* impulse that each retracement took back.

        Measured against the leg it actually retraces, not against the average
        leg, so a shallow pullback after a huge leg is still shallow.
        """
        out = []
        for k in range(1, len(self.legs)):
            prev, cur = self.legs[k - 1], self.legs[k]
            if cur.up == up or prev.up != up or prev.size <= 0:
                continue
            out.append(cur.size / prev.size)
        return out

    def symmetry(self, up: bool, n_legs: int = 4) -> Optional[float]:
        """Share of recent time spent in impulse rather than in retracement."""
        legs = self.legs[-n_legs:]
        if len(legs) < n_legs:
            return None
        imp = sum(l.bars for l in legs if l.up == up)
        ret = sum(l.bars for l in legs if l.up != up)
        if imp + ret == 0:
            return None
        return imp / (imp + ret)


def _zigzag_states(bars, left: int = 3, right: int = 3,
                   keep: int = 9) -> Dict[object, Geo]:
    """Per-bar swing geometry, built forward in confirmation order.

    The pivot list is rebuilt incrementally as each swing *confirms*, so the
    state stored against bar *i* depends on nothing after bar *i*. Consecutive
    same-kind swings collapse to the more extreme one, which is what turns a
    raw fractal list into an alternating zigzag; the collapse can only use
    swings already confirmed, so it cannot reach forward either.
    """
    swings: List[Swing] = sorted(find_swings(bars, left, right),
                                 key=lambda s: (s.confirmed_index, s.index))
    out: Dict[object, Geo] = {}
    pivots: List[Swing] = []
    k = 0
    cur: Geo = Geo(())
    dirty = True
    for i in range(len(bars)):
        while k < len(swings) and swings[k].confirmed_index <= i:
            s = swings[k]
            k += 1
            if pivots and pivots[-1].kind == s.kind:
                if ((s.is_high and s.price >= pivots[-1].price)
                        or (not s.is_high and s.price <= pivots[-1].price)):
                    pivots[-1] = s
                    dirty = True
                continue
            pivots.append(s)
            if len(pivots) > keep + 1:
                del pivots[0]
            dirty = True
        if dirty:
            cur = Geo(tuple(Leg(pivots[j].index, pivots[j + 1].index,
                                pivots[j].price, pivots[j + 1].price)
                            for j in range(len(pivots) - 1)))
            dirty = False
        out[bars[i].ts] = cur
    return out


# ---- registry -------------------------------------------------------------
_CACHE: Dict[Tuple[str, int], Dict[object, Geo]] = {}
#: Instrumentation: how many bars each condition saw and on how many it fired.
STATS: Dict[str, List[int]] = {}


def register(symbol: str, tf: int, bars, left: int = 3, right: int = 3) -> None:
    _CACHE[(symbol.upper(), int(tf))] = _zigzag_states(list(bars), left, right)


def register_frame(frame) -> None:
    """Register every timeframe a :class:`SymbolFrame` actually built."""
    for tf, tff in frame.frames.items():
        register(frame.symbol, tf, tff.series.bars)


def clear() -> None:
    _CACHE.clear()


def reset_stats() -> None:
    STATS.clear()


def firing_rates() -> Dict[str, dict]:
    return {k: {"evaluated": v[0], "fired": v[1],
                "rate": round(v[1] / v[0], 5) if v[0] else None}
            for k, v in sorted(STATS.items())}


def _geo(snap, tf) -> Optional[Geo]:
    tbl = _CACHE.get((snap.symbol.upper(), int(tf)))
    if tbl is None:
        return None
    s = snap.tf(tf)
    return tbl.get(s.bar.ts) if s is not None else None


def _tally(name: str, fired: bool) -> None:
    v = STATS.setdefault(name, [0, 0])
    v[0] += 1
    v[1] += int(fired)


def _ctx(snap, tf, name):
    """Common preamble: trend label, ATR and visible geometry, or ``None``."""
    s = snap.tf(tf)
    if s is None:
        _tally(name, False)
        return None
    if s.structure_trend not in ("UPTREND", "DOWNTREND"):
        _tally(name, False)
        return None
    atr = s.get("atr")
    if not atr or atr <= 0:
        _tally(name, False)
        return None
    g = _geo(snap, tf)
    if g is None:
        _tally(name, False)
        return None
    return s, (s.structure_trend == "UPTREND"), float(atr), g


def _fire(name, side, detail, value, strength=1.0):
    _tally(name, True)
    return ConditionResult.yes(side, detail, value, strength)


def _no(name):
    _tally(name, False)
    return ConditionResult.no()


# ==========================================================================
# 1. LEG LENGTH
# ==========================================================================

MIN_ATR = 0.5          # an impulse smaller than half an ATR is noise
N_IMPULSE = 3          # three impulses => two comparisons


def _leg_trend(g: Geo, up: bool, atr: float, n: int = N_IMPULSE):
    """``(sizes_in_atr, expanding, contracting)`` for the last ``n`` impulses."""
    imp = g.impulses(up)[-n:]
    if len(imp) < n:
        return None
    sz = [l.size / atr for l in imp]
    if sz[-1] < MIN_ATR:
        return None
    exp = all(sz[j] > sz[j - 1] for j in range(1, len(sz)))
    con = all(sz[j] < sz[j - 1] for j in range(1, len(sz)))
    return sz, exp, con


@condition("legs_expanding",
           description="Each impulse leg longer than the last (ATR-normalised)")
def _legs_expanding(snap, tf):
    c = _ctx(snap, tf, "legs_expanding")
    if c is None:
        return ConditionResult.no()
    _s, up, atr, g = c
    r = _leg_trend(g, up, atr)
    if r is None or not r[1]:
        return _no("legs_expanding")
    sz = r[0]
    return _fire("legs_expanding", LONG if up else SHORT,
                 "impulses " + "<".join(f"{x:.2f}" for x in sz) + " ATR",
                 round(sz[-1], 3))


@condition("legs_contracting",
           description="Each impulse leg shorter than the last - trade WITH the label")
def _legs_contracting(snap, tf):
    c = _ctx(snap, tf, "legs_contracting")
    if c is None:
        return ConditionResult.no()
    _s, up, atr, g = c
    r = _leg_trend(g, up, atr)
    if r is None or not r[2]:
        return _no("legs_contracting")
    sz = r[0]
    return _fire("legs_contracting", LONG if up else SHORT,
                 "impulses " + ">".join(f"{x:.2f}" for x in sz) + " ATR",
                 round(sz[-1], 3))


@condition("legs_contracting_fade",
           description="Contracting impulses traded AGAINST the label - exhaustion")
def _legs_contracting_fade(snap, tf):
    c = _ctx(snap, tf, "legs_contracting_fade")
    if c is None:
        return ConditionResult.no()
    _s, up, atr, g = c
    r = _leg_trend(g, up, atr)
    if r is None or not r[2]:
        return _no("legs_contracting_fade")
    sz = r[0]
    return _fire("legs_contracting_fade", SHORT if up else LONG,
                 "exhausting impulses " + ">".join(f"{x:.2f}" for x in sz) + " ATR",
                 round(sz[-1], 3))


# ==========================================================================
# 2. RETRACEMENT DEPTH
# ==========================================================================

N_RATIO = 3            # three retracements => two comparisons


def _ratio_trend(g: Geo, up: bool, n: int = N_RATIO):
    rr = [r for r in g.retrace_ratios(up) if 0.0 < r < 2.0][-n:]
    if len(rr) < n:
        return None
    shallow = all(rr[j] < rr[j - 1] for j in range(1, len(rr)))
    deep = all(rr[j] > rr[j - 1] for j in range(1, len(rr)))
    return rr, shallow, deep


@condition("pullbacks_shallowing",
           description="Each retracement takes back a smaller fraction of its leg")
def _pb_shallow(snap, tf):
    c = _ctx(snap, tf, "pullbacks_shallowing")
    if c is None:
        return ConditionResult.no()
    _s, up, _atr, g = c
    r = _ratio_trend(g, up)
    if r is None or not r[1]:
        return _no("pullbacks_shallowing")
    rr = r[0]
    return _fire("pullbacks_shallowing", LONG if up else SHORT,
                 "retraces " + ">".join(f"{x:.2f}" for x in rr),
                 round(rr[-1], 3))


@condition("pullbacks_deepening",
           description="Each retracement takes back more - traded WITH the label")
def _pb_deep(snap, tf):
    c = _ctx(snap, tf, "pullbacks_deepening")
    if c is None:
        return ConditionResult.no()
    _s, up, _atr, g = c
    r = _ratio_trend(g, up)
    if r is None or not r[2]:
        return _no("pullbacks_deepening")
    rr = r[0]
    return _fire("pullbacks_deepening", LONG if up else SHORT,
                 "retraces " + "<".join(f"{x:.2f}" for x in rr),
                 round(rr[-1], 3))


@condition("pullbacks_deepening_fade",
           description="Deepening retracements traded AGAINST the label")
def _pb_deep_fade(snap, tf):
    c = _ctx(snap, tf, "pullbacks_deepening_fade")
    if c is None:
        return ConditionResult.no()
    _s, up, _atr, g = c
    r = _ratio_trend(g, up)
    if r is None or not r[2]:
        return _no("pullbacks_deepening_fade")
    rr = r[0]
    return _fire("pullbacks_deepening_fade", SHORT if up else LONG,
                 "failing trend, retraces " + "<".join(f"{x:.2f}" for x in rr),
                 round(rr[-1], 3))


# ==========================================================================
# 3. SWING SYMMETRY (time in impulse vs time in retracement)
# ==========================================================================

SYM_HI = 0.55
SYM_LO = 0.45


@condition("swing_symmetry_impulse",
           description="Most recent time spent impulsing rather than retracing")
def _sym_imp(snap, tf):
    c = _ctx(snap, tf, "swing_symmetry_impulse")
    if c is None:
        return ConditionResult.no()
    _s, up, _atr, g = c
    v = g.symmetry(up)
    if v is None or v < SYM_HI:
        return _no("swing_symmetry_impulse")
    return _fire("swing_symmetry_impulse", LONG if up else SHORT,
                 f"{v:.0%} of recent time in impulse", round(v, 3))


@condition("swing_symmetry_retrace",
           description="Most recent time spent retracing - traded WITH the label")
def _sym_ret(snap, tf):
    c = _ctx(snap, tf, "swing_symmetry_retrace")
    if c is None:
        return ConditionResult.no()
    _s, up, _atr, g = c
    v = g.symmetry(up)
    if v is None or v > SYM_LO:
        return _no("swing_symmetry_retrace")
    return _fire("swing_symmetry_retrace", LONG if up else SHORT,
                 f"only {v:.0%} of recent time in impulse", round(v, 3))


@condition("swing_symmetry_retrace_fade",
           description="Trend spends most of its time pulling back - fade it")
def _sym_ret_fade(snap, tf):
    c = _ctx(snap, tf, "swing_symmetry_retrace_fade")
    if c is None:
        return ConditionResult.no()
    _s, up, _atr, g = c
    v = g.symmetry(up)
    if v is None or v > SYM_LO:
        return _no("swing_symmetry_retrace_fade")
    return _fire("swing_symmetry_retrace_fade", SHORT if up else LONG,
                 f"weak trend, only {v:.0%} impulse time", round(v, 3))


# ==========================================================================
# 4. CONTROL ARM
# ==========================================================================

@condition("structure_trend_ctl", group="structure",
           description="Control: the plain HH/HL vs LH/LL label, nothing else")
def _ctl(snap, tf):
    s = snap.tf(tf)
    if s is None:
        return _no("structure_trend_ctl")
    if s.structure_trend == "UPTREND":
        return _fire("structure_trend_ctl", LONG, "HH/HL structure", "UPTREND")
    if s.structure_trend == "DOWNTREND":
        return _fire("structure_trend_ctl", SHORT, "LH/LL structure", "DOWNTREND")
    return _no("structure_trend_ctl")


@condition("structure_trend_fade_ctl", group="structure",
           description="Control for the fade arms: the plain label, reversed")
def _ctl_fade(snap, tf):
    s = snap.tf(tf)
    if s is None:
        return _no("structure_trend_fade_ctl")
    if s.structure_trend == "UPTREND":
        return _fire("structure_trend_fade_ctl", SHORT, "fade HH/HL", "UPTREND")
    if s.structure_trend == "DOWNTREND":
        return _fire("structure_trend_fade_ctl", LONG, "fade LH/LL", "DOWNTREND")
    return _no("structure_trend_fade_ctl")


GEOMETRY_NAMES = ["legs_expanding", "legs_contracting", "legs_contracting_fade",
                  "pullbacks_shallowing", "pullbacks_deepening",
                  "pullbacks_deepening_fade", "swing_symmetry_impulse",
                  "swing_symmetry_retrace", "swing_symmetry_retrace_fade"]
CONTROL_NAMES = ["structure_trend_ctl", "structure_trend_fade_ctl"]
