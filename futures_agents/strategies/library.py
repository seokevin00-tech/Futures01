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

from ..features import FeatureSnapshot, TFSnapshot
from ..schema import Direction
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
    if d == 0:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if d > 0 else SHORT, f"EMA9-EMA21={d:+.2f}", round(d, 4))


@condition("price_above_ema50", "trend", description="Close on one side of EMA50")
def _px_ema50(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("ema50"):
        return ConditionResult.no()
    d = s.close - s["ema50"]
    if d == 0:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if d > 0 else SHORT, f"close-EMA50={d:+.2f}", round(d, 4))


@condition("price_above_ema200", "trend", description="Close on one side of EMA200")
def _px_ema200(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("ema200"):
        return ConditionResult.no()
    d = s.close - s["ema200"]
    if d == 0:
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
    if d == 0:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if d > 0 else SHORT, f"MACD-signal={d:+.3f}",
                               round(d, 5))


@condition("macd_hist_direction", "momentum", description="MACD histogram sign")
def _macd_hist(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("macd_hist"):
        return ConditionResult.no()
    h = s["macd_hist"]
    if h == 0:
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

@condition("above_vwap", "vwap", description="Price on one side of session VWAP")
def _above_vwap(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("vwap"):
        return ConditionResult.no()
    d = s.close - s["vwap"]
    if d == 0:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if d > 0 else SHORT, f"close-VWAP={d:+.2f}",
                               round(d, 4))


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
           description="Volume >= 1.3x the same clock-minute average")
def _relvol(snap, tf):
    s = _s(snap, tf)
    if not s or not s.has("rel_volume"):
        return ConditionResult.no()
    rv = s["rel_volume"]
    return (ConditionResult.yes(FLAT, f"RVOL={rv:.2f}", round(rv, 2),
                                min(1.0, (rv - 1.0) / 1.5))
            if rv >= 1.3 else ConditionResult.no())


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
    if c == 0:
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
           description="Timeframes agree directionally (|alignment| >= 0.4)")
def _mtf(snap, tf):
    a = snap.alignment()
    if abs(a) < 0.4:
        return ConditionResult.no()
    return ConditionResult.yes(LONG if a > 0 else SHORT,
                               f"MTF alignment {a:+.2f}", round(a, 3), abs(a))


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
