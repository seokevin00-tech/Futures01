"""The condition library - every testable market condition the system knows.

Each entry is a small, independently verifiable predicate over a
:class:`~futures_agents.features.FeatureSnapshot`. Keeping them atomic is what
makes the combinatorial research meaningful: when a confluence of five
conditions tests well, the system can ablate any one of them and measure what
it actually contributed, rather than guessing which part of a monolithic rule
carried the edge.

Conditions are grouped so the combinator can build *diverse* confluences - a
combination of five momentum conditions is five ways of saying the same thing,
and it will look far more robust in a backtest than it is.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence

from ..config import tf_label
from ..features import (NEWS_BLACKOUT_AFTER_MIN, FeatureSnapshot,
                        TFSnapshot)
from ..schema import Direction, fmt_price
from .base import Condition, ConditionKind, ConditionResult

__all__ = ["CONDITIONS", "CONDITION_GROUPS", "condition", "get_condition",
           "conditions_in_group", "all_condition_names"]

#: name -> Condition
CONDITIONS: Dict[str, Condition] = {}

#: Diversity groups. The combinator draws from distinct groups so that a
#: "five-factor confluence" is genuinely five different kinds of evidence.
CONDITION_GROUPS: Dict[str, List[str]] = {}

LONG, SHORT, FLAT = Direction.LONG, Direction.SHORT, Direction.NEUTRAL


def condition(name: str, group: str, *, kind: ConditionKind = ConditionKind.SIGNAL,
              description: str = "", warmup: int = 50):
    """Register a condition function under ``name``."""
    def deco(fn):
        if name in CONDITIONS:
            raise ValueError(f"Duplicate condition name {name!r}")
        c = Condition(name=name, group=group, fn=fn, kind=kind,
                      description=description or (fn.__doc__ or "").strip(),
                      warmup_bars=warmup)
        CONDITIONS[name] = c
        CONDITION_GROUPS.setdefault(group, []).append(name)
        return fn
    return deco


def get_condition(name: str) -> Condition:
    if name not in CONDITIONS:
        raise KeyError(f"Unknown condition {name!r}. "
                       f"Known: {', '.join(sorted(CONDITIONS))}")
    return CONDITIONS[name]


def conditions_in_group(group: str) -> List[Condition]:
    return [CONDITIONS[n] for n in CONDITION_GROUPS.get(group, [])]


def all_condition_names() -> List[str]:
    return sorted(CONDITIONS)


def _s(snap: FeatureSnapshot, tf: int) -> Optional[TFSnapshot]:
    return snap.tf(tf)


def _deadband(s: TFSnapshot, diff: float, frac: float = 0.10) -> bool:
    """Is ``diff`` a real separation, or the coin-flip zone around zero?

    Six conditions guarded themselves with ``if diff == 0: return no()``, which
    on floating-point price data is never true - ``cvd_directional`` declined on
    0 of 24,960 bars. They are *bias* conditions by nature and that is fine:
    measured, they split 42/58 to 50/50 by direction, so inside a confluence
    that requires agreement they genuinely gate. What was wrong is that a
    close one tick from its own EMA counted as "above" it with full
    confidence. A tenth of an ATR removes the ambiguous middle without
    pretending these are selective conditions.
    """
    atr_v = s.get("atr")
    if not atr_v:
        return False
    return abs(diff) >= atr_v * frac


def _px(snap: FeatureSnapshot, price: Optional[float]) -> str:
    """Format a price at the contract's own tick resolution.

    ``fmt_price`` takes a decimal count, not a contract. Passing the spec
    straight through raises inside the f-string, and
    :meth:`Condition.evaluate` swallows that into "no signal" - so the
    condition would quietly never fire instead of failing loudly. Deriving the
    decimals here keeps the call sites honest.
    """
    tick = getattr(getattr(snap, "spec", None), "tick_size", 0.0) or 0.0
    decimals = None
    if tick > 0:
        text = f"{tick:.10f}".rstrip("0")
        decimals = len(text.split(".")[1]) if "." in text else 0
    return fmt_price(price, decimals)


# ==========================================================================
# TREND
# ==========================================================================

@condition("ema_stack", "trend", description="EMA 9/21/50 stacked in one direction")
def _ema_stack(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("ema9", "ema21", "ema50"):
        return ConditionResult.no()
    f, m, sl = s["ema9"], s["ema21"], s["ema50"]
    if f > m > sl:
        return ConditionResult.yes(LONG, "9>21>50", round(f - sl, 4))
    if f < m < sl:
        return ConditionResult.yes(SHORT, "9<21<50", round(f - sl, 4))
    return ConditionResult.no()


@condition("ema_fast_above_slow", "trend", description="EMA9 vs EMA21")
def _ema_fast(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("ema9", "ema21"):
        return ConditionResult.no()
    d = s["ema9"] - s["ema21"]
    if not _deadband(s, d):
        return ConditionResult.no()
    return ConditionResult.yes(LONG if d > 0 else SHORT, f"EMA9-EMA21={d:+.2f}", round(d, 4))


@condition("price_above_ema50", "trend", description="Close on one side of EMA50")
def _px_ema50(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("ema50"):
        return ConditionResult.no()
    d = s.close - s["ema50"]
    if not _deadband(s, d):
        return ConditionResult.no()
    return ConditionResult.yes(LONG if d > 0 else SHORT, f"close-EMA50={d:+.2f}", round(d, 4))


@condition("price_above_ema200", "trend", description="Close on one side of EMA200")
def _px_ema200(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("ema200"):
        return ConditionResult.no()
    d = s.close - s["ema200"]
    if not _deadband(s, d):
        return ConditionResult.no()
    return ConditionResult.yes(LONG if d > 0 else SHORT, f"close-EMA200={d:+.2f}", round(d, 4))


@condition("adx_trending", "trend", kind=ConditionKind.FILTER,
           description="ADX >= 22: a directional environment")
def _adx_trend(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("adx"):
        return ConditionResult.no()
    a = s["adx"]
    return (ConditionResult.yes(FLAT, f"ADX={a:.1f}", round(a, 2),
                                min(1.0, a / 40.0))
            if a >= 22 else ConditionResult.no())


@condition("di_direction", "trend", description="+DI vs -DI spread")
def _di(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("plus_di", "minus_di"):
        return ConditionResult.no()
    p, m = s["plus_di"], s["minus_di"]
    spread = p - m
    if abs(spread) < 4:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if spread > 0 else SHORT,
                               f"DI spread {spread:+.1f}", round(spread, 2),
                               min(1.0, abs(spread) / 25.0))


@condition("slope_directional", "trend",
           description="20-bar regression slope, normalised by ATR")
def _slope(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("slope_atr"):
        return ConditionResult.no()
    v = s["slope_atr"]
    if abs(v) < 0.05:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if v > 0 else SHORT, f"slope={v:+.3f} ATR/bar",
                               round(v, 4), min(1.0, abs(v) / 0.35))


@condition("efficiency_high", "trend", kind=ConditionKind.FILTER,
           description="Kaufman efficiency ratio >= 0.35: directional travel")
def _eff(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("efficiency_ratio"):
        return ConditionResult.no()
    e = s["efficiency_ratio"]
    return (ConditionResult.yes(FLAT, f"ER={e:.2f}", round(e, 3), e)
            if e >= 0.35 else ConditionResult.no())


# ==========================================================================
# MOMENTUM
# ==========================================================================

@condition("rsi_directional", "momentum", description="RSI above/below 50")
def _rsi_dir(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("rsi"):
        return ConditionResult.no()
    r = s["rsi"]
    if 47 <= r <= 53:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if r > 53 else SHORT, f"RSI={r:.1f}",
                               round(r, 2), min(1.0, abs(r - 50) / 25.0))


@condition("rsi_extreme_reversal", "momentum",
           description="RSI below 30 / above 70 - mean-reversion trigger")
def _rsi_ext(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("rsi"):
        return ConditionResult.no()
    r = s["rsi"]
    if r <= 30:
        return ConditionResult.yes(LONG, f"RSI oversold {r:.1f}", round(r, 2),
                                   min(1.0, (30 - r) / 15.0 + 0.4))
    if r >= 70:
        return ConditionResult.yes(SHORT, f"RSI overbought {r:.1f}", round(r, 2),
                                   min(1.0, (r - 70) / 15.0 + 0.4))
    return ConditionResult.no()


@condition("macd_directional", "momentum", description="MACD line vs signal line")
def _macd_dir(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("macd", "macd_signal"):
        return ConditionResult.no()
    d = s["macd"] - s["macd_signal"]
    if not _deadband(s, d, 0.05):
        return ConditionResult.no()
    return ConditionResult.yes(LONG if d > 0 else SHORT, f"MACD-signal={d:+.3f}",
                               round(d, 5))


@condition("macd_hist_direction", "momentum", description="MACD histogram sign")
def _macd_hist(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("macd_hist"):
        return ConditionResult.no()
    h = s["macd_hist"]
    if not _deadband(s, h, 0.05):
        return ConditionResult.no()
    return ConditionResult.yes(LONG if h > 0 else SHORT, f"hist={h:+.3f}", round(h, 5))


@condition("stoch_directional", "momentum", description="Stochastic %K vs %D")
def _stoch(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("stoch_k", "stoch_d"):
        return ConditionResult.no()
    k, d = s["stoch_k"], s["stoch_d"]
    if abs(k - d) < 2:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if k > d else SHORT, f"%K={k:.1f} %D={d:.1f}",
                               round(k - d, 2))


@condition("stoch_extreme", "momentum", description="Stochastic below 20 / above 80")
def _stoch_ext(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("stoch_k"):
        return ConditionResult.no()
    k = s["stoch_k"]
    if k <= 20:
        return ConditionResult.yes(LONG, f"%K oversold {k:.1f}", round(k, 2))
    if k >= 80:
        return ConditionResult.yes(SHORT, f"%K overbought {k:.1f}", round(k, 2))
    return ConditionResult.no()


# ==========================================================================
# VWAP
# ==========================================================================

@condition("above_vwap", "vwap",
           description="Price holding beyond the first VWAP band")
def _above_vwap(snap, tf):
    """Holding a side of value, not merely on one side of the line.

    As "close != vwap" this fired on 99.99% of bars at every timeframe - it
    returned no signal only when the close landed exactly on VWAP to the tick.
    A condition true of essentially every bar assigns a direction and adds no
    selectivity, and the damage is not cosmetic: the 30-trade floor exists to
    reject strategies with too little evidence, and an anchor that fires every
    bar clears that floor on any data whatever. Measured, it produced the
    largest trade counts in the VWAP family - a median 643 over 120 days
    against 28 for vwap_band1_bounce - while its rule sets averaged a negative
    expectancy.

    The boundary is the first VWAP band rather than an ATR multiple, because a
    fixed ATR threshold is not comparable across timeframes: the median
    distance from session VWAP is 2.22 ATR at 5m and 0.56 ATR at 60m, so one
    number would mean "barely off VWAP" on one chart and "stretched" on
    another. The band is one standard deviation of the session's own
    distribution and adapts by construction.
    """
    s = _s(snap, tf)
    if not s or not s.has("vwap", "vwap_u1", "vwap_l1"):
        return ConditionResult.no()
    px = s.close
    if px > s["vwap_u1"]:
        return ConditionResult.yes(LONG, "holding above the first VWAP band",
                                   round(px - s["vwap"], 4))
    if px < s["vwap_l1"]:
        return ConditionResult.yes(SHORT, "holding below the first VWAP band",
                                   round(px - s["vwap"], 4))
    return ConditionResult.no()



@condition("vwap_proximity", "vwap", kind=ConditionKind.FILTER,
           description="Within 0.5 ATR of VWAP - a defined-risk entry area")
def _vwap_near(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("vwap_dist_atr"):
        return ConditionResult.no()
    d = abs(s["vwap_dist_atr"])
    return (ConditionResult.yes(FLAT, f"{d:.2f} ATR from VWAP", round(d, 3),
                                max(0.0, 1.0 - d / 0.5))
            if d <= 0.5 else ConditionResult.no())


@condition("vwap_band_extension", "vwap",
           description="Beyond the 2nd VWAP band - stretched, fade candidate")
def _vwap_ext(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("vwap_u2", "vwap_l2"):
        return ConditionResult.no()
    if s.close >= s["vwap_u2"]:
        return ConditionResult.yes(SHORT, "above upper 2sd VWAP band",
                                   round(s.close - s["vwap_u2"], 4))
    if s.close <= s["vwap_l2"]:
        return ConditionResult.yes(LONG, "below lower 2sd VWAP band",
                                   round(s["vwap_l2"] - s.close, 4))
    return ConditionResult.no()


@condition("vwap_band1_bounce", "vwap",
           description="Tagged the 1st VWAP band and closed back inside")
def _vwap_b1(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("vwap_u1", "vwap_l1", "vwap"):
        return ConditionResult.no()
    b = s.bar
    if b.low <= s["vwap_l1"] < b.close and b.close < s["vwap"]:
        return ConditionResult.yes(LONG, "reclaimed lower VWAP band",
                                   round(s["vwap_l1"], 4))
    if b.high >= s["vwap_u1"] > b.close and b.close > s["vwap"]:
        return ConditionResult.yes(SHORT, "rejected upper VWAP band",
                                   round(s["vwap_u1"], 4))
    return ConditionResult.no()


@condition("vwap_reclaim", "vwap",
           description="Bar traded through VWAP and closed back across it")
def _vwap_reclaim(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("vwap"):
        return ConditionResult.no()
    b, w = s.bar, s["vwap"]
    if b.low < w <= b.close and b.open < w:
        return ConditionResult.yes(LONG, "reclaimed VWAP from below", round(w, 4))
    if b.high > w >= b.close and b.open > w:
        return ConditionResult.yes(SHORT, "lost VWAP from above", round(w, 4))
    return ConditionResult.no()


# ==========================================================================
# VOLUME
# ==========================================================================

@condition("relative_volume_high", "volume", kind=ConditionKind.FILTER,
           description="Participation above the recent norm")
def _relvol(snap, tf):
    """Above-average participation.

    The threshold was high enough to be a veto rather than a filter: it passed
    0.68% of 15-minute bars, so any template offering it as an *optional*
    filter was offering a treatment arm that takes no trades at all - which is
    not a control, it is an empty set. Two templates were doing exactly that.
    1.10x keeps the condition meaningful while leaving enough sample for the
    paired comparison to mean something. Calibrated against this data's own
    distribution - rel_volume has a median of 0.99 and a maximum of 1.60 at
    15m, so 1.30 sat near the very top of the range. Real futures volume has a
    much fatter right tail than the generator's, so this threshold is one to
    re-check on real bars rather than inherit.
    """
    s = _s(snap, tf)
    if not s or not s.has("rel_volume"):
        return ConditionResult.no()
    rv = s["rel_volume"]
    if rv < 1.10:
        return ConditionResult.no()
    return ConditionResult.yes(FLAT, f"relative volume {rv:.2f}x", round(rv, 2))



@condition("volume_surge", "volume", kind=ConditionKind.FILTER,
           description="Volume in the top decile of its trailing distribution")
def _volsurge(snap, tf):
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    return (ConditionResult.yes(FLAT, f"volume regime {s.volume_regime}")
            if s.volume_regime == "SURGE" else ConditionResult.no())


@condition("volume_not_thin", "volume", kind=ConditionKind.FILTER,
           description="Reject the bottom decile of volume - no liquidity, no trade")
def _volnotthin(snap, tf):
    s = _s(snap, tf)
    if not s or s.volume_regime is None:
        return ConditionResult.no()
    return (ConditionResult.yes(FLAT, f"volume {s.volume_regime}")
            if s.volume_regime != "THIN" else ConditionResult.no())


# ==========================================================================
# ORDER FLOW / DELTA
# ==========================================================================

@condition("cvd_directional", "orderflow",
           description="Cumulative volume delta sign for the session")
def _cvd(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("cvd"):
        return ConditionResult.no()
    c = s["cvd"]
    vol = s.get("volume") or 0.0
    # Net session delta smaller than a quarter of one bar's volume is not
    # "buyers in control", it is noise with a sign. As "cvd != 0" this
    # declined on 0 of 24,960 bars.
    if abs(c) < vol * 0.25:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if c > 0 else SHORT, f"CVD={c:+.0f}", round(c, 1))


@condition("delta_confirms_bar", "orderflow",
           description="Bar delta agrees with the bar's direction")
def _delta_conf(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("delta"):
        return ConditionResult.no()
    d, b = s["delta"], s.bar
    if d > 0 and b.close > b.open:
        return ConditionResult.yes(LONG, f"delta {d:+.0f} on an up bar", round(d, 1))
    if d < 0 and b.close < b.open:
        return ConditionResult.yes(SHORT, f"delta {d:+.0f} on a down bar", round(d, 1))
    return ConditionResult.no()


@condition("delta_divergence", "orderflow",
           description="Price extends but CVD does not - absorption")
def _delta_div(snap, tf):
    s = _s(snap, tf)
    if not s or not s.divergence:
        return ConditionResult.no()
    if s.divergence == "BULLISH":
        return ConditionResult.yes(LONG, "bullish CVD divergence", "BULLISH", 0.85)
    return ConditionResult.yes(SHORT, "bearish CVD divergence", "BEARISH", 0.85)


# ==========================================================================
# MARKET STRUCTURE
# ==========================================================================

@condition("structure_trend", "structure",
           description="Confirmed swing structure: HH/HL or LH/LL")
def _struct(snap, tf):
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    if s.structure_trend == "UPTREND":
        return ConditionResult.yes(LONG, "HH/HL structure", "UPTREND")
    if s.structure_trend == "DOWNTREND":
        return ConditionResult.yes(SHORT, "LH/LL structure", "DOWNTREND")
    return ConditionResult.no()


@condition("break_of_structure", "structure",
           description="Close beyond the last confirmed swing - continuation")
def _bos(snap, tf):
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    if s.last_swing_high is not None and s.close > s.last_swing_high:
        return ConditionResult.yes(LONG, f"broke swing high {s.last_swing_high:g}",
                                   s.last_swing_high)
    if s.last_swing_low is not None and s.close < s.last_swing_low:
        return ConditionResult.yes(SHORT, f"broke swing low {s.last_swing_low:g}",
                                   s.last_swing_low)
    return ConditionResult.no()


@condition("pullback_to_support", "structure",
           description="Price retraced into the nearest S/R level")
def _pullback(snap, tf):
    s = _s(snap, tf)
    if not s or not s.sr_levels or not s.has("atr"):
        return ConditionResult.no()
    atr_v, px = s["atr"], s.close
    near = min(s.sr_levels, key=lambda l: abs(l.price - px))
    if abs(near.price - px) > atr_v * 0.5:
        return ConditionResult.no()
    if near.kind == "SUPPORT" and px >= near.price:
        return ConditionResult.yes(LONG, f"at support {near.price:g} "
                                         f"({near.touches} touches)", near.price,
                                   min(1.0, near.touches / 4.0))
    if near.kind == "RESISTANCE" and px <= near.price:
        return ConditionResult.yes(SHORT, f"at resistance {near.price:g} "
                                          f"({near.touches} touches)", near.price,
                                   min(1.0, near.touches / 4.0))
    return ConditionResult.no()


@condition("fvg_nearby", "structure",
           description="An unfilled fair value gap sits at price")
def _fvg(snap, tf):
    s = _s(snap, tf)
    if not s or not s.active_fvgs:
        return ConditionResult.no()
    px = s.close
    for g in s.active_fvgs:
        if g.contains(px):
            return ConditionResult.yes(LONG if g.direction == "BULLISH" else SHORT,
                                       f"{g.direction.lower()} FVG "
                                       f"{g.bottom:g}-{g.top:g}", round(g.mid, 4))
    return ConditionResult.no()


@condition("range_position_extreme", "structure",
           description="Price at the edge of its own 20-bar range")
def _rangepos(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("range_pos"):
        return ConditionResult.no()
    p = s["range_pos"]
    if p >= 0.92:
        return ConditionResult.yes(LONG, f"at top of range ({p:.2f})", round(p, 3))
    if p <= 0.08:
        return ConditionResult.yes(SHORT, f"at bottom of range ({p:.2f})", round(p, 3))
    return ConditionResult.no()


# ==========================================================================
# LIQUIDITY / SESSION LEVELS
# ==========================================================================

def _level_sweep(snap, tf, low_level, high_level, low_name, high_name):
    """Shared logic: price pierced a reference level and closed back through it."""
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    b = s.bar
    if low_level is not None and b.low < low_level <= b.close:
        return ConditionResult.yes(LONG, f"swept {low_name} {low_level:g} and reclaimed",
                                   round(low_level, 4), 0.9)
    if high_level is not None and b.high > high_level >= b.close:
        return ConditionResult.yes(SHORT, f"swept {high_name} {high_level:g} and rejected",
                                   round(high_level, 4), 0.9)
    return ConditionResult.no()


@condition("prior_day_sweep", "liquidity",
           description="Ran previous-day high/low then closed back inside")
def _pd_sweep(snap, tf):
    lv = snap.session_levels
    return _level_sweep(snap, tf, lv.prev_day_low, lv.prev_day_high, "PDL", "PDH")


@condition("overnight_sweep", "liquidity",
           description="Ran the overnight high/low then closed back inside")
def _on_sweep(snap, tf):
    lv = snap.session_levels
    return _level_sweep(snap, tf, lv.overnight_low, lv.overnight_high, "ONL", "ONH")


@condition("session_extreme_sweep", "liquidity",
           description="Ran the session high/low then closed back inside")
def _sess_sweep(snap, tf):
    lv = snap.session_levels
    return _level_sweep(snap, tf, lv.session_low, lv.session_high, "session low",
                        "session high")


@condition("prior_day_breakout", "liquidity",
           description="Accepted beyond the previous day's range")
def _pd_break(snap, tf):
    s = _s(snap, tf)
    lv = snap.session_levels
    if not s:
        return ConditionResult.no()
    if lv.prev_day_high is not None and s.close > lv.prev_day_high:
        return ConditionResult.yes(LONG, f"above PDH {lv.prev_day_high:g}",
                                   lv.prev_day_high)
    if lv.prev_day_low is not None and s.close < lv.prev_day_low:
        return ConditionResult.yes(SHORT, f"below PDL {lv.prev_day_low:g}",
                                   lv.prev_day_low)
    return ConditionResult.no()


@condition("opening_range_breakout", "liquidity",
           description="Close beyond a COMPLETED opening range")
def _orb(snap, tf):
    s = _s(snap, tf)
    orr = snap.opening_range
    # An OR breakout signal on an incomplete range is a look-ahead artefact:
    # the range is still forming, so its boundary is not yet knowable.
    if not s or orr is None or not orr.complete:
        return ConditionResult.no()
    if s.close > orr.high:
        return ConditionResult.yes(LONG, f"above OR high {orr.high:g}", orr.high)
    if s.close < orr.low:
        return ConditionResult.yes(SHORT, f"below OR low {orr.low:g}", orr.low)
    return ConditionResult.no()


@condition("opening_range_fade", "liquidity",
           description="Rejected back inside a completed opening range")
def _orf(snap, tf):
    s = _s(snap, tf)
    orr = snap.opening_range
    if not s or orr is None or not orr.complete:
        return ConditionResult.no()
    b = s.bar
    if b.high > orr.high >= b.close:
        return ConditionResult.yes(SHORT, f"failed OR high {orr.high:g}", orr.high, 0.85)
    if b.low < orr.low <= b.close:
        return ConditionResult.yes(LONG, f"failed OR low {orr.low:g}", orr.low, 0.85)
    return ConditionResult.no()


@condition("initial_balance_break", "liquidity",
           description="Close beyond the first hour's range")
def _ib(snap, tf):
    s = _s(snap, tf)
    lv = snap.session_levels
    if not s or snap.minutes_since_open < 60:
        return ConditionResult.no()
    if lv.initial_balance_high is not None and s.close > lv.initial_balance_high:
        return ConditionResult.yes(LONG, f"above IB high {lv.initial_balance_high:g}",
                                   lv.initial_balance_high)
    if lv.initial_balance_low is not None and s.close < lv.initial_balance_low:
        return ConditionResult.yes(SHORT, f"below IB low {lv.initial_balance_low:g}",
                                   lv.initial_balance_low)
    return ConditionResult.no()


# ==========================================================================
# MEAN REVERSION / BANDS
# ==========================================================================

@condition("bollinger_extreme", "meanreversion",
           description="Close outside the Bollinger bands")
def _bb_ext(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("bb_upper", "bb_lower"):
        return ConditionResult.no()
    if s.close <= s["bb_lower"]:
        return ConditionResult.yes(LONG, "below lower Bollinger band",
                                   round(s["bb_lower"], 4))
    if s.close >= s["bb_upper"]:
        return ConditionResult.yes(SHORT, "above upper Bollinger band",
                                   round(s["bb_upper"], 4))
    return ConditionResult.no()


@condition("bollinger_mean_pull", "meanreversion",
           description="%B beyond 0.85 / below 0.15 - stretched from the mean")
def _bb_pull(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("bb_pctb"):
        return ConditionResult.no()
    p = s["bb_pctb"]
    if p <= 0.15:
        return ConditionResult.yes(LONG, f"%B={p:.2f}", round(p, 3))
    if p >= 0.85:
        return ConditionResult.yes(SHORT, f"%B={p:.2f}", round(p, 3))
    return ConditionResult.no()


@condition("keltner_outside", "meanreversion",
           description="Close outside the Keltner channel")
def _kelt(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("keltner_upper", "keltner_lower"):
        return ConditionResult.no()
    if s.close <= s["keltner_lower"]:
        return ConditionResult.yes(LONG, "below lower Keltner", round(s["keltner_lower"], 4))
    if s.close >= s["keltner_upper"]:
        return ConditionResult.yes(SHORT, "above upper Keltner", round(s["keltner_upper"], 4))
    return ConditionResult.no()


# ==========================================================================
# VOLATILITY
# ==========================================================================

@condition("volatility_normal", "volatility", kind=ConditionKind.FILTER,
           description="Volatility is LOW/NORMAL/HIGH - not dead, not unhinged")
def _vol_ok(snap, tf):
    v = snap.regime.volatility
    return (ConditionResult.yes(FLAT, f"volatility {v}")
            if v in ("LOW", "NORMAL", "HIGH") else ConditionResult.no())


@condition("volatility_expanding", "volatility", kind=ConditionKind.FILTER,
           description="ATR in the top quartile of its own history")
def _vol_exp(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("atr_percentile"):
        return ConditionResult.no()
    p = s["atr_percentile"]
    return (ConditionResult.yes(FLAT, f"ATR pct {p:.2f}", round(p, 3), p)
            if p >= 0.70 else ConditionResult.no())


@condition("volatility_compressed", "volatility", kind=ConditionKind.FILTER,
           description="Bollinger width in the bottom quartile - coiled")
def _vol_comp(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("atr_percentile"):
        return ConditionResult.no()
    p = s["atr_percentile"]
    return (ConditionResult.yes(FLAT, f"ATR pct {p:.2f}", round(p, 3), 1.0 - p)
            if p <= 0.30 else ConditionResult.no())


# ==========================================================================
# REGIME
# ==========================================================================

@condition("regime_trending", "regime", kind=ConditionKind.FILTER,
           description="Regime classifier says TREND_UP or TREND_DOWN")
def _reg_trend(snap, tf):
    r = snap.regime.regime
    return (ConditionResult.yes(FLAT, f"regime {r}", r, snap.regime.confidence)
            if r in ("TREND_UP", "TREND_DOWN") else ConditionResult.no())


@condition("regime_ranging", "regime", kind=ConditionKind.FILTER,
           description="Regime classifier says RANGE")
def _reg_range(snap, tf):
    r = snap.regime.regime
    return (ConditionResult.yes(FLAT, f"regime {r}", r, snap.regime.confidence)
            if r == "RANGE" else ConditionResult.no())


@condition("regime_matches_direction", "regime",
           description="Trade in the direction the regime classifier identifies")
def _reg_dir(snap, tf):
    r = snap.regime.regime
    if r == "TREND_UP":
        return ConditionResult.yes(LONG, "regime TREND_UP", r, snap.regime.confidence)
    if r == "TREND_DOWN":
        return ConditionResult.yes(SHORT, "regime TREND_DOWN", r, snap.regime.confidence)
    return ConditionResult.no()


# ==========================================================================
# MULTI-TIMEFRAME
# ==========================================================================

@condition("mtf_aligned", "multitimeframe",
           description="This timeframe and those above it agree directionally")
def _mtf(snap, tf):
    """Alignment measured from the strategy's own timeframe UPWARD.

    Previously this called ``snap.alignment()`` with no argument, which
    aggregates every timeframe in the snapshot. Bound to 60m and bound to 240m
    it therefore returned the identical answer on all 428 sampled bars of a
    5,000-bar series, while a control condition differed on 206 of them - the
    timeframe binding was inert, and this is the group that went on to
    dominate the daily rankings.
    """
    agree, voting = snap.agreeing_timeframes(from_tf=tf)
    if voting < 2:
        # Nothing to align WITH. At the top timeframe of the frame this used to
        # aggregate a single vote - its own structural trend - so it returned
        # an identical verdict to structure_trend on 4259/4259 MNQ bars,
        # 3892/3892 MCL and 4255/4255 MGC. The template requires a
        # multitimeframe signal AND a structure signal, so those strategies
        # were counting one reading twice and calling it confluence.
        return ConditionResult.no()
    a = snap.alignment(from_tf=tf)
    if abs(a) < 0.4:
        return ConditionResult.no()
    return ConditionResult.yes(
        LONG if a > 0 else SHORT,
        f"{agree}/{voting} timeframes from {tf_label(tf)} up, alignment {a:+.2f}",
        round(a, 3), abs(a))


@condition("mtf_strongly_aligned", "multitimeframe",
           description="Every timeframe from this one up agrees - maximum linkage")
def _mtf_strong(snap, tf):
    """The graded half of the linkage.

    "More timeframes bullish means more bullish" needs a condition that can
    tell three-of-three from two-of-three. A weighted average blurs that: two
    strong agreeing timeframes and three weak ones can score the same. This
    fires only on unanimity among the timeframes at or above the strategy's
    own, and carries the count in its detail so the journal records which.
    """
    agree, voting = snap.agreeing_timeframes(from_tf=tf)
    if voting < 2 or agree != voting:
        return ConditionResult.no()
    a = snap.alignment(from_tf=tf)
    if a == 0:
        return ConditionResult.no()
    return ConditionResult.yes(
        LONG if a > 0 else SHORT,
        f"all {voting} timeframes from {tf_label(tf)} up agree ({a:+.2f})",
        round(a, 3), 1.0)



@condition("mtf_not_conflicted", "multitimeframe", kind=ConditionKind.FILTER,
           description="Timeframes are not in outright disagreement")
def _mtf_ok(snap, tf):
    trends = {s.structure_trend for s in snap.tfs.values()}
    conflicted = "UPTREND" in trends and "DOWNTREND" in trends
    return (ConditionResult.no() if conflicted
            else ConditionResult.yes(FLAT, "no timeframe conflict"))


# ==========================================================================
# TIME OF DAY
# ==========================================================================

@condition("avoid_lunch", "time", kind=ConditionKind.FILTER,
           description="Skip 12:00-13:30 ET - breakouts fail disproportionately")
def _lunch(snap, tf):
    return (ConditionResult.no() if snap.session == "LUNCH"
            else ConditionResult.yes(FLAT, f"session {snap.session}"))


@condition("opening_drive_window", "time", kind=ConditionKind.FILTER,
           description="First 90 minutes of RTH")
def _open_win(snap, tf):
    m = snap.minutes_since_open
    return (ConditionResult.yes(FLAT, f"{m:.0f}m since open")
            if 0 <= m <= 90 else ConditionResult.no())


@condition("power_hour", "time", kind=ConditionKind.FILTER,
           description="Final hour of RTH")
def _power(snap, tf):
    return (ConditionResult.yes(FLAT, f"session {snap.session}")
            if snap.session == "RTH_CLOSE" else ConditionResult.no())


@condition("after_opening_range", "time", kind=ConditionKind.FILTER,
           description="At least 30 minutes into RTH")
def _after_or(snap, tf):
    m = snap.minutes_since_open
    return (ConditionResult.yes(FLAT, f"{m:.0f}m since open")
            if m >= 30 else ConditionResult.no())


# ==========================================================================
# VOLUME PROFILE / MARKET PROFILE
#
# Volume profile and market profile share one group on purpose. They are two
# renderings of the same observation - where the market spent its effort - and
# the combinator's diversity rule exists to stop a "five-factor confluence"
# from being five restatements of one idea. Splitting them into two groups
# would let a strategy claim location twice and look more corroborated than it
# is. Every profile here is the PRIOR session's, so the levels were knowable
# before the bar being traded.
# ==========================================================================

def _profile(snap: FeatureSnapshot, tf: int):
    s = _s(snap, tf)
    return (s, s.prior_profile) if s is not None else (None, None)


@condition("poc_reversion", "profile",
           description="Inside prior value, rotating back toward the POC")
def _poc_revert(snap, tf):
    s, prof = _profile(snap, tf)
    if not s or prof is None:
        return ConditionResult.no()
    atr_v = s.get("atr")
    if not atr_v:
        return ConditionResult.no()
    px = s.close
    if not prof.in_value_area(px):
        return ConditionResult.no()          # outside value is a different trade
    gap = prof.poc - px
    if abs(gap) < atr_v * 0.5:
        return ConditionResult.no()          # already at the magnet
    direction = LONG if gap > 0 else SHORT
    return ConditionResult.yes(
        direction, f"in value, POC {_px(snap, prof.poc)} "
                   f"{abs(gap) / atr_v:.1f} ATR away",
        round(prof.poc, 4), min(1.0, abs(gap) / atr_v / 2.0))


@condition("value_area_edge", "profile",
           description="Responsive trade at the prior value-area high or low")
def _va_edge(snap, tf):
    s, prof = _profile(snap, tf)
    if not s or prof is None:
        return ConditionResult.no()
    atr_v = s.get("atr")
    if not atr_v:
        return ConditionResult.no()
    b, px = s.bar, s.close
    band = atr_v * 0.35
    # Tagged the edge and closed back inside: the value area held.
    if abs(px - prof.val) <= band and b.low <= prof.val and px >= prof.val:
        return ConditionResult.yes(LONG, f"held VAL {_px(snap, prof.val)}",
                                   round(prof.val, 4), 0.8)
    if abs(px - prof.vah) <= band and b.high >= prof.vah and px <= prof.vah:
        return ConditionResult.yes(SHORT, f"held VAH {_px(snap, prof.vah)}",
                                   round(prof.vah, 4), 0.8)
    return ConditionResult.no()


@condition("value_area_breakout", "profile",
           description="Accepted beyond the prior session's value area")
def _va_break(snap, tf):
    s, prof = _profile(snap, tf)
    if not s or prof is None:
        return ConditionResult.no()
    atr_v = s.get("atr")
    if not atr_v:
        return ConditionResult.no()
    px = s.close
    # Acceptance, not a tag: the close has to clear the edge by a real margin.
    margin = atr_v * 0.25
    if px > prof.vah + margin:
        return ConditionResult.yes(LONG, f"accepted above VAH {_px(snap, prof.vah)}",
                                   round(prof.vah, 4),
                                   min(1.0, (px - prof.vah) / atr_v / 2.0))
    if px < prof.val - margin:
        return ConditionResult.yes(SHORT, f"accepted below VAL {_px(snap, prof.val)}",
                                   round(prof.val, 4),
                                   min(1.0, (prof.val - px) / atr_v / 2.0))
    return ConditionResult.no()


@condition("lvn_rejection", "profile",
           description="At a low-volume node - price does not linger there")
def _lvn(snap, tf):
    s, prof = _profile(snap, tf)
    if not s or prof is None or not prof.lvn:
        return ConditionResult.no()
    atr_v = s.get("atr")
    if not atr_v:
        return ConditionResult.no()
    px = s.close
    near = min(prof.lvn, key=lambda x: abs(x - px))
    if abs(near - px) > atr_v * 0.3:
        return ConditionResult.no()
    b = s.bar
    if b.close == b.open:
        return ConditionResult.no()
    # An LVN is traversed, not held; the bar's own resolution says which way.
    direction = LONG if b.close > b.open else SHORT
    return ConditionResult.yes(direction, f"at LVN {_px(snap, near)}",
                               round(near, 4), 0.7)


@condition("away_from_hvn", "profile", kind=ConditionKind.FILTER,
           description="Not initiating into a high-volume node")
def _hvn_clear(snap, tf):
    s, prof = _profile(snap, tf)
    if not s or prof is None:
        return ConditionResult.no()
    if not prof.hvn:
        return ConditionResult.yes(FLAT, "no HVN in prior profile")
    atr_v = s.get("atr")
    if not atr_v:
        return ConditionResult.no()
    px = s.close
    near = min(prof.hvn, key=lambda x: abs(x - px))
    if abs(near - px) <= atr_v * 0.25:
        return ConditionResult.no()          # an HVN absorbs; do not start there
    return ConditionResult.yes(FLAT, f"nearest HVN {abs(near - px) / atr_v:.1f} ATR away")


@condition("open_outside_value", "profile", kind=ConditionKind.FILTER,
           description="Session opened outside the prior value area (open-drive day type)")
def _open_out(snap, tf):
    s, prof = _profile(snap, tf)
    if not s or prof is None:
        return ConditionResult.no()
    day_open = snap.session_levels.day_open
    if day_open is None:
        return ConditionResult.no()
    if prof.in_value_area(day_open):
        return ConditionResult.no()
    side = "above" if day_open > prof.vah else "below"
    return ConditionResult.yes(FLAT, f"opened {side} prior value")


# ==========================================================================
# FIBONACCI
#
# Measured against the last CONFIRMED swing leg only. A retracement drawn from
# a swing that is not yet fractal-confirmed is drawn from a point the market
# had not yet made, and every level it produces is a look-ahead.
# ==========================================================================

def _fib_pullback(snap, tf, lower: float, upper: float, label: str):
    """Shared: price inside a retracement band of the last leg."""
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    zone = s.fib_zone(lower, upper)
    if zone is None:
        return ConditionResult.no()
    lo, hi, direction = zone
    px = s.close
    if not (lo <= px <= hi):
        return ConditionResult.no()
    # A retracement is a pullback: it argues for the leg, not against it.
    side = LONG if direction == "UP" else SHORT
    return ConditionResult.yes(
        side, f"{label} of {direction.lower()} leg "
              f"({_px(snap, lo)}-{_px(snap, hi)})",
        round((lo + hi) / 2.0, 4), 0.8)


@condition("fib_golden_pocket", "fibonacci",
           description="Price in the 0.618-0.786 retracement of the last confirmed leg")
def _fib_golden(snap, tf):
    return _fib_pullback(snap, tf, 0.618, 0.786, "golden pocket")


@condition("fib_shallow_retrace", "fibonacci",
           description="Price in the 0.382-0.5 retracement - a strong-trend pullback")
def _fib_shallow(snap, tf):
    return _fib_pullback(snap, tf, 0.382, 0.5, "shallow retracement")


@condition("fib_extension_reached", "fibonacci",
           description="Price at the 1.272-1.618 extension of the last leg")
def _fib_ext(snap, tf):
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    leg = s.swing_leg()
    if leg is None:
        return ConditionResult.no()
    lo, hi, direction = leg
    span = hi - lo
    if span <= 0:
        return ConditionResult.no()
    px = s.close
    if direction == "UP":
        a, b = lo + 1.272 * span, lo + 1.618 * span
        side = SHORT                 # extension of an up leg is exhaustion
    else:
        a, b = hi - 1.618 * span, hi - 1.272 * span
        side = LONG
    if not (min(a, b) <= px <= max(a, b)):
        return ConditionResult.no()
    return ConditionResult.yes(
        side, f"at {direction.lower()}-leg extension "
              f"{_px(snap, min(a, b))}-{_px(snap, max(a, b))}",
        round(px, 4), 0.7)


@condition("fib_sr_confluence", "fibonacci", kind=ConditionKind.FILTER,
           description="A retracement level coincides with a support/resistance level")
def _fib_sr(snap, tf):
    s = _s(snap, tf)
    if not s or not s.sr_levels:
        return ConditionResult.no()
    leg = s.swing_leg()
    atr_v = s.get("atr")
    if leg is None or not atr_v:
        return ConditionResult.no()
    lo, hi, direction = leg
    span = hi - lo
    if span <= 0:
        return ConditionResult.no()
    tol = atr_v * 0.4
    for ratio in (0.382, 0.5, 0.618, 0.786):
        # A retracement is measured from the END of the leg back towards its
        # start - the same convention as TFSnapshot.fib_zone. Measuring every
        # ratio from ``lo`` regardless of direction mirrors the whole set on an
        # up leg: it tested the 0.618 level and called it 0.382, and it tested
        # the 0.214 level (which nobody draws) in place of the 0.786.
        level = hi - ratio * span if direction == "UP" else lo + ratio * span
        for lv in s.sr_levels:
            if abs(lv.price - level) <= tol:
                return ConditionResult.yes(
                    FLAT, f"{ratio:.3f} fib at {lv.kind.lower()} "
                          f"{_px(snap, lv.price)}", round(level, 4))
    return ConditionResult.no()


# ==========================================================================
# IMBALANCES
#
# Bar-level aggressive participation: range and volume both far above the
# recent norm. Distinct from ``fvg_nearby``, which is about *location* (an
# unfilled gap acting as a level); this group is about *initiative* (someone
# paying up right now).
# ==========================================================================

@condition("imbalance_bar", "imbalance",
           description="This bar shows one-sided aggressive participation")
def _imb_bar(snap, tf):
    s = _s(snap, tf)
    if not s or not s.imbalance:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if s.imbalance == "BULLISH" else SHORT,
                               f"{s.imbalance.lower()} imbalance bar", None, 0.9)


@condition("imbalance_pullback", "imbalance",
           description="Pullback into a displacement of the last few bars")
def _imb_pull(snap, tf):
    s = _s(snap, tf)
    if not s or not s.recent_imbalance:
        return ConditionResult.no()
    since = s.bars_since_imbalance
    if since is None or not (1 <= since <= 10):
        return ConditionResult.no()
    return ConditionResult.yes(
        LONG if s.recent_imbalance == "BULLISH" else SHORT,
        f"{s.recent_imbalance.lower()} displacement {since} bars back",
        since, max(0.4, 1.0 - since / 12.0))


@condition("no_recent_imbalance", "imbalance", kind=ConditionKind.FILTER,
           description="No violent bar in the last three - do not initiate into one")
def _imb_quiet(snap, tf):
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    since = s.bars_since_imbalance
    if since is not None and since <= 2:
        return ConditionResult.no()
    return ConditionResult.yes(FLAT, "no displacement in the last 3 bars")


# ==========================================================================
# SUPPLY AND DEMAND ZONES
# ==========================================================================

@condition("zone_touch", "supplydemand",
           description="Price trading inside a visible supply or demand zone")
def _zone_touch(snap, tf):
    s = _s(snap, tf)
    if not s or not s.sd_zones:
        return ConditionResult.no()
    px = s.close
    for z in s.sd_zones:
        if z.contains(px):
            return ConditionResult.yes(
                LONG if z.kind == "DEMAND" else SHORT,
                f"in {z.kind.lower()} zone {_px(snap, z.bottom)}-"
                f"{_px(snap, z.top)} ({z.touches} prior touches)",
                round(z.proximal, 4),
                0.9 if z.fresh else max(0.3, 0.9 - 0.2 * z.touches))
    return ConditionResult.no()


@condition("fresh_zone_approach", "supplydemand",
           description="Approaching an untested zone from the correct side")
def _zone_fresh(snap, tf):
    s = _s(snap, tf)
    if not s or not s.sd_zones:
        return ConditionResult.no()
    atr_v = s.get("atr")
    if not atr_v:
        return ConditionResult.no()
    px = s.close
    for z in s.sd_zones:
        if not z.fresh:
            continue
        gap = z.proximal - px
        if z.kind == "DEMAND" and -atr_v * 0.1 <= -gap <= atr_v * 0.75:
            return ConditionResult.yes(
                LONG, f"approaching fresh demand {_px(snap, z.proximal)}",
                round(z.proximal, 4), 0.85)
        if z.kind == "SUPPLY" and -atr_v * 0.1 <= gap <= atr_v * 0.75:
            return ConditionResult.yes(
                SHORT, f"approaching fresh supply {_px(snap, z.proximal)}",
                round(z.proximal, 4), 0.85)
    return ConditionResult.no()


@condition("away_from_zone", "supplydemand", kind=ConditionKind.FILTER,
           description="Not initiating on top of an untested decision level")
def _zone_clear(snap, tf):
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    atr_v = s.get("atr")
    if not atr_v:
        return ConditionResult.no()
    px = s.close
    for z in s.sd_zones:
        if abs(z.proximal - px) <= atr_v * 0.2 or z.contains(px):
            return ConditionResult.no()
    return ConditionResult.yes(FLAT, "clear of supply/demand zones")


# ==========================================================================
# OPEN INTEREST
#
# Every condition here returns "no signal" when the feed carries no open
# interest, which is most intraday exports. That is deliberate: a strategy
# built on an absent column would backtest as never firing, which is the
# honest result, rather than firing on a default of zero.
# ==========================================================================

@condition("oi_price_confirmation", "openinterest",
           description="Open interest expanding with the price move - new money, not covering")
def _oi_confirm(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("oi_change", "open_interest"):
        return ConditionResult.no()
    doi = s["oi_change"]
    base = s["open_interest"]
    if not base or doi is None:
        return ConditionResult.no()
    pct = doi / base * 100.0
    if pct <= 0.1:
        return ConditionResult.no()          # flat or falling OI: not initiative
    # The price move over the same 20 bars the OI change spans. Position in the
    # 20-bar range is a different statement - a bar can sit high in its range
    # while the net move over the window is down - and it disagreed with the
    # actual move on about 7.5% of bars, emitting the opposite direction to
    # this condition's own thesis.
    move = s.get("roc20")
    if move is None or move == 0.0:
        return ConditionResult.no()
    if move > 0:
        return ConditionResult.yes(LONG, f"OI +{pct:.2f}% while price rose "
                                         f"{move:+.2f}%", round(pct, 3))
    return ConditionResult.yes(SHORT, f"OI +{pct:.2f}% while price fell "
                                      f"{move:+.2f}%", round(pct, 3))


@condition("oi_expanding", "openinterest", kind=ConditionKind.FILTER,
           description="Participation is growing rather than positions being closed")
def _oi_rising(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("oi_change", "open_interest"):
        return ConditionResult.no()
    doi, base = s["oi_change"], s["open_interest"]
    if not base or doi is None or doi <= 0:
        return ConditionResult.no()
    return ConditionResult.yes(FLAT, f"OI +{doi / base * 100.0:.2f}% over 20 bars")


# ==========================================================================
# NEWS CONDITIONS
#
# Driven by the rule-based economic calendar, not by headlines. A recurrence
# rule projects identically backwards and forwards, so these filters evaluate
# the same way in a 2019 backtest and at the live edge. Conditioning on a
# scraped headline would mean reading an article written after the bar.
# ==========================================================================

@condition("outside_news_blackout", "news", kind=ConditionKind.FILTER,
           description="Not inside the window around a scheduled high-impact release")
def _news_clear(snap, tf):
    if snap.in_news_blackout:
        return ConditionResult.no()
    return ConditionResult.yes(FLAT, "outside the release blackout")


@condition("no_imminent_release", "news", kind=ConditionKind.FILTER,
           description="At least 30 minutes before the next high-impact release")
def _news_far(snap, tf):
    mins = snap.minutes_to_high_impact
    if mins < 30.0:
        return ConditionResult.no()
    return ConditionResult.yes(
        FLAT, "no release within 30m"
              if mins == float("inf") else f"next release in {mins:.0f}m")


@condition("post_news_window", "news", kind=ConditionKind.FILTER,
           description="In the reaction window after a high-impact release, "
                       "starting where the blackout ends")
def _news_after(snap, tf):
    # The lower bound tracks the blackout rather than restating a number.
    # At a fixed 5 minutes it overlapped a 15-minute blackout for ten of them,
    # so a strategy carrying both filters was asking to trade inside a window
    # the risk manager had already closed - two conditions in one confluence
    # contradicting each other rather than confirming.
    #
    # Strictly greater, not >=: the blackout's own bound is inclusive, so
    # sharing the endpoint left exactly one bar per event inside both.
    since = snap.minutes_since_high_impact
    lo = float(NEWS_BLACKOUT_AFTER_MIN)
    if since == float("inf") or not (lo < since <= 60.0):
        return ConditionResult.no()
    return ConditionResult.yes(FLAT, f"{since:.0f}m after a high-impact release")


# ==========================================================================
# CANDLESTICK PATTERNS
#
# The bar-form evidence the library never read. Bar has carried body,
# upper_wick, lower_wick and is_up from the start and no condition used them,
# so "price action" was covered on paper by a range-position indicator, an
# order-flow check and an imbalance detector - three unrelated things wearing
# the name, none of which look at the shape of a bar.
#
# Every threshold is a fraction of the bar's own range, never a point value,
# so the same definition behaves identically on a 0.25-tick index future and
# a 0.01-tick crude contract. Measured, the firing rates differ by under two
# percentage points between MNQ and MGC, which is what that normalisation is
# for.
# ==========================================================================

def _patterns(snap: FeatureSnapshot, tf: int):
    """Patterns completing on this timeframe's current bar.

    Read off the precomputed column. The first version reached for the frame
    through the snapshot, which has no such handle: every pattern condition
    returned nothing on every bar and reported a 0.00% firing rate rather than
    an error.
    """
    s = _s(snap, tf)
    return (s, list(s.candles)) if s else (None, [])


@condition("candle_reversal", "candlestick",
           description="Rejection bar - one wick dominates and the body sits opposite")
def _candle_reversal(snap, tf):
    s, pats = _patterns(snap, tf)
    if not s:
        return ConditionResult.no()
    for p in pats:
        if p.name == "hammer":
            return ConditionResult.yes(LONG, f"hammer: {p.detail}", None, p.strength)
        if p.name == "shooting_star":
            return ConditionResult.yes(SHORT, f"shooting star: {p.detail}",
                                       None, p.strength)
    return ConditionResult.no()


@condition("candle_engulfing", "candlestick",
           description="This body swallows the previous one and reverses its sign")
def _candle_engulf(snap, tf):
    s, pats = _patterns(snap, tf)
    if not s:
        return ConditionResult.no()
    for p in pats:
        if p.name == "bullish_engulfing":
            return ConditionResult.yes(LONG, p.detail, None, p.strength)
        if p.name == "bearish_engulfing":
            return ConditionResult.yes(SHORT, p.detail, None, p.strength)
    return ConditionResult.no()


@condition("candle_decisive_close", "candlestick",
           description="Body dominates the range - a bar with no argument in it")
def _candle_marubozu(snap, tf):
    s, pats = _patterns(snap, tf)
    if not s:
        return ConditionResult.no()
    for p in pats:
        if p.name == "marubozu":
            return ConditionResult.yes(
                LONG if p.direction == "BULLISH" else SHORT, p.detail,
                None, p.strength)
    return ConditionResult.no()


@condition("candle_close_strength", "candlestick",
           description="Close located near the extreme of its own bar")
def _candle_clv(snap, tf):
    """Close location value, which carries most of what a candlestick NAME
    encodes and is continuous - far easier to test than a taxonomy."""
    from ..indicators.candles import close_location_value
    s = _s(snap, tf)
    if not s:
        return ConditionResult.no()
    clv = close_location_value(s.bar)
    if clv is None or abs(clv) < 0.6:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if clv > 0 else SHORT,
                               f"close at {clv:+.2f} of range", round(clv, 3),
                               abs(clv))


@condition("inside_bar_compression", "candlestick", kind=ConditionKind.FILTER,
           description="Prior bar's range contains this one - coiled, not trending")
def _candle_inside(snap, tf):
    s, pats = _patterns(snap, tf)
    if not s:
        return ConditionResult.no()
    for p in pats:
        if p.name == "inside_bar":
            return ConditionResult.yes(FLAT, p.detail, None, p.strength)
    return ConditionResult.no()
