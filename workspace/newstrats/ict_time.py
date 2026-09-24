"""ICT time-of-day (kill zones) and Optimal Trade Entry (OTE).

Two claims are tested here, both from the Inner Circle Trader corpus. Neither
definition was fetched: this container's egress refuses market-data and most
content hosts, so the windows below are my own recall of the published ICT
material and are stated as such in the write-up.

Kill zones (US/Eastern, as given in the brief)
---------------------------------------------
============ ============ ==================================================
name          window        claim
============ ============ ==================================================
london_open   02:00-05:00  European cash open; "the first real move"
ny_open       07:00-10:00  New York open drive
silver_bullet 10:00-11:00  a one-hour window said to contain a setup daily
london_close  10:00-12:00  London fix / European close flow
asian_range   20:00-00:00  used to DEFINE a range, not usually to trade
============ ============ ==================================================

Two things the shipped library gets in the way of:

* ``StrategyFilters.rth_only`` defaults to True, and RTH is 09:30-16:00 on the
  index contracts.  London open (02:00-05:00) and the Asian range
  (20:00-00:00) are entirely outside it, and most of ny_open is too.  A
  kill-zone study with rth_only on cannot see three of its five windows, so
  every strategy here is built with ``rth_only=False`` and that is stated in
  the findings.
* the four shipped time filters (``avoid_lunch``, ``opening_drive_window``,
  ``power_hour``, ``after_opening_range``) are keyed to the contract's own RTH
  clock.  ICT windows are keyed to the New York wall clock regardless of which
  contract is trading, so ``kz_*`` reads the bar's ET stamp directly.  MGC's
  RTH closes 13:30 ET and MCL's 14:30, so "10:00-12:00 ET" is mid-session on
  MES and late-session on MGC.  Results are reported per symbol for that
  reason and are not pooled across contracts.

OTE
---
ICT's Optimal Trade Entry is the 0.62-0.79 retracement of an impulse leg.  The
library already ships ``fib_golden_pocket`` at 0.618-0.786.  ``ote_zone`` here
is the literal 0.62-0.79 band so the two can be compared bar for bar; the
Jaccard overlap is the headline number, not a performance figure.
``ote_strict`` adds the two qualifiers ICT states alongside the band - the leg
must be a displacement (large relative to ATR) and the trade must be with the
higher-timeframe structure - so that "OTE" is not silently reduced to "a fib
level" if the overlap turns out to be total.

Registration
------------
We keep our own CONDITIONS dict rather than writing into ``library.CONDITIONS``:
six agents sharing that registry is how name collisions happen.
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

from futures_agents.schema import Direction
from futures_agents.strategies.base import Condition, ConditionKind, ConditionResult
from futures_agents.timeutil import to_et

LONG, SHORT, FLAT = Direction.LONG, Direction.SHORT, Direction.NEUTRAL

#: name -> (start_hour, end_hour) in ET, half-open [start, end).  A window that
#: wraps midnight is written with end <= start.
KILL_ZONES: Dict[str, Tuple[int, int]] = {
    "london_open":   (2, 5),
    "ny_open":       (7, 10),
    "silver_bullet": (10, 11),
    "london_close":  (10, 12),
    "asian_range":   (20, 24),
}

#: The union ICT actually proposes trading: London open + NY open + the
#: Silver Bullet hour.  The Asian range is a range definition, not an entry
#: window, and london_close is contained in ny_open's tail plus silver_bullet.
KZ_TRADE_UNION = ("london_open", "ny_open", "silver_bullet")

#: The one replicated negative the session study found: do not open intraday
#: positions 15:00-16:00 ET (z=-4.43, median -0.617R).  Carried here as a
#: positive control - if the machinery cannot re-find a known negative, it
#: cannot be trusted to find a positive either.
AVOID_WINDOW = (15, 16)


def et_hour(ts) -> int:
    """ET hour of a bar's START stamp.  DST-correct: ``to_et`` uses zoneinfo."""
    return to_et(ts).hour


def in_window(hour: int, lo: int, hi: int) -> bool:
    return lo <= hour < hi if lo < hi else (hour >= lo or hour < hi)


def in_zone(hour: int, zone: str) -> bool:
    lo, hi = KILL_ZONES[zone]
    return in_window(hour, lo, hi)


def zone_hours(zone: str) -> Tuple[int, ...]:
    return tuple(h for h in range(24) if in_zone(h, zone))


def union_hours(zones: Sequence[str]) -> Tuple[int, ...]:
    return tuple(sorted({h for z in zones for h in zone_hours(z)}))


# ==========================================================================
# Registry
# ==========================================================================

CONDITIONS: Dict[str, Condition] = {}


def condition(name: str, group: str = "ict", *,
              kind: ConditionKind = ConditionKind.FILTER,
              description: str = "", warmup: int = 50):
    def deco(fn):
        CONDITIONS[name] = Condition(
            name=name, group=group, fn=fn, kind=kind,
            description=description or (fn.__doc__ or "").strip(),
            warmup_bars=warmup)
        return fn
    return deco


def get(name: str) -> Condition:
    return CONDITIONS[name]


# ==========================================================================
# Kill-zone filters
#
# A bar whose duration is longer than the window cannot be inside it in any
# meaningful sense.  A 240m bar stamped 08:00 ET covers 08:00-12:00 and spans
# ny_open, silver_bullet and london_close at once, so the filter is declared
# INERT above 120m rather than quietly re-labelling a 4-hour bar as a 1-hour
# kill zone.  The shipped time filters had the mirror-image bug (no session
# guard, vetoing 100% of daily bars) and it zeroed whole groups.
# ==========================================================================

#: Above this bar length a wall-clock window is not a property of the bar.
MAX_TF_FOR_WINDOW = 120


def _window_filter(snap, tf, lo: int, hi: int, label: str, *, invert: bool = False):
    if tf is not None and tf > MAX_TF_FOR_WINDOW:
        return ConditionResult.yes(FLAT, f"inert at {tf}m: bar is longer than the window")
    h = et_hour(snap.ts)
    inside = in_window(h, lo, hi)
    ok = (not inside) if invert else inside
    if ok:
        return ConditionResult.yes(FLAT, f"{h:02d}:00 ET {'outside' if invert else 'in'} {label}")
    return ConditionResult.no()


def _make_zone_condition(zone: str) -> None:
    lo, hi = KILL_ZONES[zone]

    @condition(f"kz_{zone}", "ict_time",
               description=f"Bar starts inside the {zone} kill zone ({lo:02d}:00-{hi:02d}:00 ET)")
    def _fn(snap, tf, _lo=lo, _hi=hi, _z=zone):
        return _window_filter(snap, tf, _lo, _hi, _z)


for _z in KILL_ZONES:
    _make_zone_condition(_z)


@condition("kz_union", "ict_time",
           description="Bar starts inside london_open, ny_open or silver_bullet")
def _kz_union(snap, tf):
    if tf is not None and tf > MAX_TF_FOR_WINDOW:
        return ConditionResult.yes(FLAT, f"inert at {tf}m: bar is longer than the window")
    h = et_hour(snap.ts)
    if h in union_hours(KZ_TRADE_UNION):
        return ConditionResult.yes(FLAT, f"{h:02d}:00 ET in a kill zone")
    return ConditionResult.no()


@condition("kz_outside_all", "ict_time",
           description="Bar starts OUTSIDE every kill zone - the mirror arm")
def _kz_outside(snap, tf):
    if tf is not None and tf > MAX_TF_FOR_WINDOW:
        return ConditionResult.yes(FLAT, f"inert at {tf}m: bar is longer than the window")
    h = et_hour(snap.ts)
    if h not in union_hours(KZ_TRADE_UNION):
        return ConditionResult.yes(FLAT, f"{h:02d}:00 ET outside every kill zone")
    return ConditionResult.no()


@condition("avoid_1500_1600", "ict_time",
           description="Positive control: skip 15:00-16:00 ET (x_session's replicated negative)")
def _avoid_late(snap, tf):
    lo, hi = AVOID_WINDOW
    return _window_filter(snap, tf, lo, hi, "15:00-16:00 ET", invert=True)


# ==========================================================================
# Optimal Trade Entry
# ==========================================================================

def _fib_band(snap, tf, lower: float, upper: float, label: str):
    """Price inside a retracement band of the last CONFIRMED leg.

    Deliberately identical in mechanism to ``library._fib_pullback`` so the
    Jaccard overlap measures the BAND, not two different implementations of a
    retracement.
    """
    s = snap.tf(tf)
    if not s:
        return ConditionResult.no()
    zone = s.fib_zone(lower, upper)
    if zone is None:
        return ConditionResult.no()
    lo, hi, direction = zone
    if not (lo <= s.close <= hi):
        return ConditionResult.no()
    side = LONG if direction == "UP" else SHORT
    return ConditionResult.yes(side, f"{label} of {direction.lower()} leg",
                               round((lo + hi) / 2.0, 4), 0.8)


@condition("ote_zone", "ict_ote", kind=ConditionKind.SIGNAL,
           description="ICT Optimal Trade Entry: 0.62-0.79 retracement of the last confirmed leg")
def _ote(snap, tf):
    return _fib_band(snap, tf, 0.62, 0.79, "OTE")


#: Displacement gate for ``ote_strict``: the leg must be this many ATR long.
#: Swept in the sensitivity run; 2.0 is the starting value, not a fitted one.
DISPLACEMENT_ATR = 2.0


def _higher_tf(snap, tf: int):
    higher = [t for t in snap.timeframes if t > tf]
    return snap.tf(higher[0]) if higher else None


@condition("ote_strict", "ict_ote", kind=ConditionKind.SIGNAL,
           description="OTE band AND the leg was a displacement AND higher-TF structure agrees")
def _ote_strict(snap, tf):
    r = _fib_band(snap, tf, 0.62, 0.79, "strict OTE")
    if not r.triggered:
        return r
    s = snap.tf(tf)
    leg = s.swing_leg() if s else None
    atr_v = s.get("atr") if s else None
    if leg is None or not atr_v:
        return ConditionResult.no()
    lo, hi, _ = leg
    if (hi - lo) < DISPLACEMENT_ATR * atr_v:          # displacement gate
        return ConditionResult.no()
    higher = _higher_tf(snap, tf)
    if higher is not None and higher.structure_trend in ("UP", "DOWN"):
        want = "UP" if r.direction is LONG else "DOWN"
        if higher.structure_trend != want:
            return ConditionResult.no()
    return r


@condition("ote_0705", "ict_ote", kind=ConditionKind.SIGNAL,
           description="The 0.705 'sweet spot' only: 0.68-0.73 retracement")
def _ote_sweet(snap, tf):
    return _fib_band(snap, tf, 0.68, 0.73, "0.705 sweet spot")


@condition("gp_mirror", "ict_ote", kind=ConditionKind.SIGNAL,
           description="Local re-implementation of fib_golden_pocket (0.618-0.786)")
def _gp_mirror(snap, tf):
    return _fib_band(snap, tf, 0.618, 0.786, "golden pocket")


@condition("shallow_mirror", "ict_ote", kind=ConditionKind.SIGNAL,
           description="Local re-implementation of fib_shallow_retrace (0.382-0.5)")
def _shallow_mirror(snap, tf):
    return _fib_band(snap, tf, 0.382, 0.5, "shallow retracement")


# ==========================================================================
# One condition per ET hour.
#
# The kill-zone test on its own cannot say whether 10:00-11:00 is SPECIAL or
# whether any one-hour window would score the same, because a filter that
# keeps 4.4% of bars changes the trade population whatever hours it keeps.
# Twenty-four matched arms, one per hour, give the whole hour-of-day
# expectancy curve under identical base rule sets - so the Silver Bullet is
# ranked against its 23 competitors rather than against "everything else".
# ==========================================================================

def _make_hour_condition(h: int) -> None:
    @condition(f"hour_{h:02d}", "ict_hour",
               description=f"Bar starts in the {h:02d}:00-{(h + 1) % 24:02d}:00 ET hour")
    def _fn(snap, tf, _h=h):
        return _window_filter(snap, tf, _h, (_h + 1) % 24 or 24, f"{_h:02d}:00 ET")


for _h in range(24):
    _make_hour_condition(_h)


# ==========================================================================
# Count-matched placebos.
#
# The 24-hour census showed that restricting entries to ANY single hour beats
# not restricting them (mean Stouffer z = +1.58 over 24 hours, 18 of 24
# positive). That could be a time-of-day effect or it could be a
# trade-frequency effect: a filter keeping 1/24 of bars turns ~180 clustered
# re-entries per slice into ~30 spaced ones. These placebos keep exactly 1/24
# of bars with no relation to the clock, so the two explanations separate.
# ==========================================================================

def _make_stride(phase: int, stride: int = 24) -> None:
    @condition(f"stride{stride}_p{phase:02d}", "ict_placebo",
               description=f"Placebo: every {stride}th bar, phase {phase}. "
                           f"Same firing rate as a one-hour filter, no clock content.")
    def _fn(snap, tf, _p=phase, _s=stride):
        if snap.base_index % _s == _p:
            return ConditionResult.yes(FLAT, f"stride {_s} phase {_p}")
        return ConditionResult.no()


for _p in (0, 5, 11, 17, 23):
    _make_stride(_p)


#: Rate-matched placebos for the FIB comparison. fib_golden_pocket fires on
#: 8.0-9.9% of bars, i.e. about one bar in eleven, so a stride-11 filter is the
#: count-matched null for "does adding the golden pocket to a rule set help".
for _p in (0, 3, 7):
    _make_stride(_p, 11)
