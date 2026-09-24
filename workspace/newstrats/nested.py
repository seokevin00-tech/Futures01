"""Nested multi-timeframe structure conditions.

The library treats timeframe disagreement as a veto (``mtf_not_conflicted``).
The hypothesis tested here is that the veto is backwards: the classic
structural trade is a *higher* timeframe making HH/HL while the *lower*
timeframe temporarily makes LH/LL - a pullback inside a trend. Under the
current design that setup is excluded.

Every condition here is a factory over an explicit ``(htf, ltf)`` pair rather
than a single registered name, because the nesting RATIO is part of the
hypothesis: 240/60 and 60/15 are different claims and must be measured
separately. The conditions are bound to ``ltf`` so that
``Condition.evaluate`` refuses to run when the lower timeframe is missing
from the snapshot; they read the higher timeframe explicitly via
``snap.tf(htf)``.

Confirmation lag is inherited, not re-implemented: ``structure_trend``,
``last_swing_*`` and ``swing_leg()`` on a ``TFSnapshot`` are already built
only from swings whose ``confirmed_index`` has passed. Nothing here touches
raw bars.
"""
from __future__ import annotations

from typing import Optional, Tuple

from futures_agents.schema import Direction
from futures_agents.strategies.base import Condition, ConditionKind, ConditionResult

LONG, SHORT, FLAT = Direction.LONG, Direction.SHORT, Direction.NEUTRAL

UP, DOWN = "UPTREND", "DOWNTREND"


def _pair(snap, htf: int, ltf: int):
    h, l = snap.tf(htf), snap.tf(ltf)
    if h is None or l is None:
        return None, None
    return h, l


def retrace_depth(snap, htf: int) -> Optional[Tuple[float, str]]:
    """How far price has retraced into the higher timeframe's last leg.

    Returns ``(depth, direction)`` where depth is 0.0 at the end of the leg
    (no retracement) and 1.0 back at its origin. Values above 1.0 mean the
    leg has been fully unwound - no longer a pullback.
    """
    h = snap.tf(htf)
    if h is None:
        return None
    leg = h.swing_leg()
    if leg is None:
        return None
    lo, hi, direction = leg
    span = hi - lo
    if span <= 0:
        return None
    px = h.close
    depth = (hi - px) / span if direction == "UP" else (px - lo) / span
    return (depth, direction)


# --------------------------------------------------------------------------
# 1. nested_pullback - the core claim
# --------------------------------------------------------------------------

def nested_pullback(htf: int, ltf: int, *, name: Optional[str] = None) -> Condition:
    """HTF trending, LTF counter-trending. Signal WITH the higher timeframe."""
    def fn(snap, tf):
        h, l = _pair(snap, htf, ltf)
        if h is None:
            return ConditionResult.no()
        if h.structure_trend == UP and l.structure_trend == DOWN:
            return ConditionResult.yes(LONG, f"{htf}m UP / {ltf}m DOWN pullback",
                                       "UP_DOWN")
        if h.structure_trend == DOWN and l.structure_trend == UP:
            return ConditionResult.yes(SHORT, f"{htf}m DOWN / {ltf}m UP pullback",
                                       "DOWN_UP")
        return ConditionResult.no()
    return Condition(name=name or f"nested_pullback_{htf}_{ltf}", group="nested",
                     fn=fn, kind=ConditionKind.SIGNAL, timeframe=ltf,
                     description="HTF trend, LTF counter-trend - pullback inside a trend")


def nested_pullback_depth(htf: int, ltf: int, lo: float, hi: float,
                          label: str) -> Condition:
    """``nested_pullback`` restricted to a band of retracement depth."""
    def fn(snap, tf):
        h, l = _pair(snap, htf, ltf)
        if h is None:
            return ConditionResult.no()
        d = retrace_depth(snap, htf)
        if d is None:
            return ConditionResult.no()
        depth, leg_dir = d
        if not (lo <= depth < hi):
            return ConditionResult.no()
        if h.structure_trend == UP and l.structure_trend == DOWN and leg_dir == "UP":
            return ConditionResult.yes(LONG, f"{label} pullback ({depth:.2f})",
                                       round(depth, 3))
        if h.structure_trend == DOWN and l.structure_trend == UP and leg_dir == "DOWN":
            return ConditionResult.yes(SHORT, f"{label} pullback ({depth:.2f})",
                                       round(depth, 3))
        return ConditionResult.no()
    return Condition(name=f"nested_pullback_{label}_{htf}_{ltf}", group="nested",
                     fn=fn, kind=ConditionKind.SIGNAL, timeframe=ltf,
                     description=f"nested pullback, {label} retracement [{lo},{hi})")


# --------------------------------------------------------------------------
# 2. nested_resumption - the pullback ENDING, not the pullback existing
# --------------------------------------------------------------------------

def nested_resumption(htf: int, ltf: int, *, allow_range: bool = False,
                      name: Optional[str] = None) -> Condition:
    """HTF trending, LTF counter-trending AND breaking back the HTF's way.

    The reclaim is a close beyond the lower timeframe's own last confirmed
    swing in the higher timeframe's direction - the first structural evidence
    that the pullback is over rather than merely in progress.
    """
    def fn(snap, tf):
        h, l = _pair(snap, htf, ltf)
        if h is None:
            return ConditionResult.no()
        counter = {UP: DOWN, DOWN: UP}
        ok = counter.get(h.structure_trend)
        if ok is None:
            return ConditionResult.no()
        lt = l.structure_trend
        if lt != ok and not (allow_range and lt == "RANGE"):
            return ConditionResult.no()
        if h.structure_trend == UP:
            if l.last_swing_high is not None and l.close > l.last_swing_high:
                return ConditionResult.yes(
                    LONG, f"{ltf}m reclaimed swing high {l.last_swing_high:g} "
                          f"inside {htf}m uptrend", "RESUME_UP")
        else:
            if l.last_swing_low is not None and l.close < l.last_swing_low:
                return ConditionResult.yes(
                    SHORT, f"{ltf}m lost swing low {l.last_swing_low:g} "
                           f"inside {htf}m downtrend", "RESUME_DOWN")
        return ConditionResult.no()
    suffix = "_loose" if allow_range else ""
    return Condition(name=name or f"nested_resumption{suffix}_{htf}_{ltf}",
                     group="nested", fn=fn, kind=ConditionKind.SIGNAL, timeframe=ltf,
                     description="nested pullback that has just broken back")


# --------------------------------------------------------------------------
# 3. nested_aligned - the CONTROL. Roughly what the library already does.
# --------------------------------------------------------------------------

def nested_aligned(htf: int, ltf: int, *, name: Optional[str] = None) -> Condition:
    """Both timeframes agree. The incumbent, stated the same way."""
    def fn(snap, tf):
        h, l = _pair(snap, htf, ltf)
        if h is None:
            return ConditionResult.no()
        if h.structure_trend == UP and l.structure_trend == UP:
            return ConditionResult.yes(LONG, f"{htf}m and {ltf}m both UP", "UP_UP")
        if h.structure_trend == DOWN and l.structure_trend == DOWN:
            return ConditionResult.yes(SHORT, f"{htf}m and {ltf}m both DOWN", "DOWN_DOWN")
        return ConditionResult.no()
    return Condition(name=name or f"nested_aligned_{htf}_{ltf}", group="nested",
                     fn=fn, kind=ConditionKind.SIGNAL, timeframe=ltf,
                     description="both timeframes in the same structural trend")


# --------------------------------------------------------------------------
# Extra reference arms
# --------------------------------------------------------------------------

def htf_only(htf: int, ltf: int, *, name: Optional[str] = None) -> Condition:
    """Higher timeframe trend alone - the LTF is not consulted at all.

    Without this arm ``nested_pullback`` cannot be told apart from "the higher
    timeframe was trending", which is ``structure_trend`` bound upward and
    already in the library.
    """
    def fn(snap, tf):
        h, l = _pair(snap, htf, ltf)
        if h is None:
            return ConditionResult.no()
        if h.structure_trend == UP:
            return ConditionResult.yes(LONG, f"{htf}m UP", "UP")
        if h.structure_trend == DOWN:
            return ConditionResult.yes(SHORT, f"{htf}m DOWN", "DOWN")
        return ConditionResult.no()
    return Condition(name=name or f"htf_trend_{htf}_{ltf}", group="nested", fn=fn,
                     kind=ConditionKind.SIGNAL, timeframe=ltf,
                     description="higher timeframe structural trend only")


ARMS = {
    "nested_pullback": nested_pullback,
    "nested_resumption": nested_resumption,
    "nested_aligned": nested_aligned,
    "htf_only": htf_only,
}
