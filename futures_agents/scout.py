"""Which strategies work on one timeframe for one symbol - and how to see them.

A ranking on its own is not usable. ``MGC-60m-a1b2c3d4 scores 0.41`` tells a
trader nothing they can act on while looking at a chart. So every ranked
strategy here carries a **detection card**: the same rule set rewritten as
what you would actually look for on the screen, in the order you would check
it, with the risk model spelled out beside it.

Three deliberate choices, because each one is a way this could mislead:

* **Ranked by reward-for-risk, not win rate.** On this desk's own data the
  correlation between win rate and expectancy is ``+0.037`` - effectively
  zero. Every one of the sixteen strategies below a 45% win rate was
  profitable; only 69% of the sixteen above 60% were. A table sorted by win
  rate would put the worst strategies at the top.

* **Clones collapsed before counting.** Filter variants that veto nothing
  produce an identical trade set, so five "different" strategies can be one
  observation wearing five names. They are fingerprinted on realised trades
  and only the first survives, or the top five would routinely be the top one
  listed five times.

* **Deflation charged against everything screened.** Searching ``n``
  strategies buys roughly ``sqrt(2 ln n)`` free t-units of apparent edge.
  Selecting five from two thousand and then deflating against five would hide
  the search entirely, which is the easiest way to manufacture a result that
  looks significant and is not.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from futures_agents.backtest.engine import run_portfolio
from futures_agents.backtest.metrics import Metrics, compute_metrics
from futures_agents.backtest.objectives import reward_for_risk_score
from futures_agents.backtest.robustness import deflated_expectancy
from futures_agents.data.bars import BarSeries
from futures_agents.data.loader import load_csv
from futures_agents.features import build_symbol_frame
from futures_agents.strategies.base import ConditionKind, Direction, Strategy
from futures_agents.strategies.combinator import TEMPLATES, generate_strategies

__all__ = ["CHART_CUES", "RankedStrategy", "rank", "detection_card",
           "live_state", "report"]

#: Timeframes built for a given primary timeframe.
#:
#: The primary is always the LOWEST member, never the highest. ``mtf_aligned``
#: aggregates "this timeframe and everything above it", so a frame whose top
#: member is the strategy's own timeframe leaves that condition a single voter
#: - its own structural trend - and it silently becomes a duplicate of
#: ``structure_trend``. Measured before this was fixed: identical verdicts on
#: 4259/4259 MNQ bars, 3892/3892 MCL, 4255/4255 MGC.
FRAMES: Dict[int, List[int]] = {
    5: [5, 15, 60],
    15: [15, 60, 240],
    30: [30, 60, 240],
    60: [60, 240, 1440],
    240: [240, 1440],
    # Daily needs a weekly above it for the same reason every other row does.
    # 7200 minutes is five sessions.
    1440: [1440, 7200],
}

#: Minimum realised trades before a strategy is allowed into a ranking.
MIN_TRADES = 30


# ==========================================================================
# THE PLAYBOOK
# ==========================================================================
#: Condition name -> what it looks like on a chart.
#:
#: Written as what you SEE, not as what the code computes, because the point
#: of this file is to get a rule set off the screen and onto a chart. A
#: condition missing from here falls back to its own registered description,
#: which is accurate but written for a developer.
CHART_CUES: Dict[str, str] = {
    # -- trend ------------------------------------------------------------
    "ema_stack": "EMA 9, 21 and 50 stacked in order and not tangled - fast above slow for a long",
    "ema_fast_above_slow": "EMA 9 on the signal side of EMA 21",
    "price_above_ema50": "the candle closes on the signal side of the 50 EMA",
    "price_above_ema200": "the candle closes on the signal side of the 200 EMA",
    "adx_trending": "ADX at or above 22 - the tape has direction rather than chop",
    "di_direction": "+DI above -DI for a long, below for a short",
    "slope_directional": "the 20-bar trend line is visibly tilted, not flat (measured against ATR, so it is a real slope)",
    "efficiency_high": "price travelled in a line rather than a zigzag - little backtracking over the last 20 bars",
    # -- momentum ---------------------------------------------------------
    "rsi_directional": "RSI on the signal side of 50",
    "rsi_extreme_reversal": "RSI under 30 for a long, over 70 for a short - you are FADING here, not following",
    "macd_directional": "MACD line crossed to the signal side of its signal line",
    "macd_hist_direction": "MACD histogram bars on the signal side of zero",
    "stoch_directional": "Stochastic %K on the signal side of %D",
    "stoch_extreme": "Stochastic under 20 for a long, over 80 for a short",
    # -- vwap -------------------------------------------------------------
    "above_vwap": "price HOLDING beyond the first VWAP band - a tick across the VWAP line does not count",
    "vwap_proximity": "price within half an ATR of the VWAP line, close enough to risk a tight stop",
    "vwap_band_extension": "price beyond the SECOND VWAP band - stretched, so this is a fade",
    "vwap_band1_bounce": "price tagged the first VWAP band and closed back inside it",
    "vwap_reclaim": "the bar traded through VWAP and closed back across it - a failed break",
    # -- volume -----------------------------------------------------------
    "relative_volume_high": "this bar's volume above its recent average",
    "volume_surge": "volume in the top decile of the recent range - a spike you can see without measuring",
    "volume_not_thin": "volume NOT in the bottom decile - refuse dead tape",
    # -- order flow -------------------------------------------------------
    "cvd_directional": "cumulative delta for the session sloping the signal way",
    "delta_confirms_bar": "an up bar carrying buy-side delta, or a down bar carrying sell-side delta - real orders behind the move",
    "delta_divergence": "price made a new extreme but cumulative delta did not - someone is absorbing it",
    # -- structure --------------------------------------------------------
    "structure_trend": "higher highs AND higher lows for a long, lower highs and lower lows for a short, on confirmed swings",
    "break_of_structure": "a close beyond the last confirmed swing high or low",
    "pullback_to_support": "price retraced back into the nearest support or resistance level",
    "fvg_nearby": "an unfilled fair-value gap (the window between candle 1's wick and candle 3's wick) sitting at price",
    "range_position_extreme": "price at the very top or bottom edge of its own 20-bar range",
    # -- liquidity --------------------------------------------------------
    "prior_day_sweep": "price ran yesterday's high or low and then closed back inside - a stop run that failed",
    "overnight_sweep": "price ran the overnight high or low and closed back inside",
    "session_extreme_sweep": "price ran the session high or low and closed back inside",
    "prior_day_breakout": "price ACCEPTED beyond yesterday's range - closed out there, not just wicked through",
    "opening_range_breakout": "a close beyond a COMPLETED opening range - let the range finish before you act",
    "opening_range_fade": "price poked out of the completed opening range and was rejected back inside",
    "initial_balance_break": "a close beyond the first hour's high or low",
    # -- mean reversion ---------------------------------------------------
    "bollinger_extreme": "the candle closed OUTSIDE the Bollinger band",
    "bollinger_mean_pull": "%B above 0.85 or below 0.15 - stretched away from the middle band",
    "keltner_outside": "the close is outside the Keltner channel",
    # -- volatility -------------------------------------------------------
    "volatility_normal": "ATR in its normal-to-high band - not dead, not unhinged",
    "volatility_expanding": "ATR in the top quarter of its own history - ranges are widening",
    "volatility_compressed": "Bollinger bands pinched into their narrowest quarter - coiled",
    # -- regime -----------------------------------------------------------
    "regime_trending": "the regime read is TREND_UP or TREND_DOWN",
    "regime_ranging": "the regime read is RANGE",
    "regime_matches_direction": "take the trade the way the regime classifier is already pointing",
    # -- multi-timeframe --------------------------------------------------
    "mtf_aligned": "this timeframe AND the ones above it point the same way, with at least two of them actually trending",
    "mtf_strongly_aligned": "every timeframe from this one up agrees - unanimous, no dissent",
    "mtf_not_conflicted": "nothing above you is trending against you",
    # -- time of day ------------------------------------------------------
    "avoid_lunch": "not between 12:00 and 13:30 ET",
    "opening_drive_window": "inside the first 90 minutes of the RTH session",
    "power_hour": "the final hour of RTH",
    "after_opening_range": "at least 30 minutes into RTH",
    # -- volume profile ---------------------------------------------------
    "poc_reversion": "price inside the prior session's value area and rotating back toward the point of control",
    "value_area_edge": "price responding at the prior value-area high or low",
    "value_area_breakout": "price accepted beyond the prior session's value area",
    "lvn_rejection": "price at a low-volume node - a thin shelf it should travel through quickly",
    "away_from_hvn": "NOT initiating into a thick high-volume shelf",
    "open_outside_value": "the session opened outside the prior value area",
    # -- fibonacci --------------------------------------------------------
    "fib_golden_pocket": "price inside the 0.618-0.786 retracement of the last confirmed leg",
    "fib_shallow_retrace": "price in the 0.382-0.5 retracement - a strong trend barely pulling back",
    "fib_extension_reached": "price at the 1.272-1.618 extension of the last leg",
    "fib_sr_confluence": "a retracement level landing on a support or resistance level",
    # -- imbalance --------------------------------------------------------
    "imbalance_bar": "a one-sided bar: big body, little opposing wick, real volume behind it",
    "imbalance_pullback": "price pulling back into the displacement candle of the last few bars",
    "no_recent_imbalance": "no violent bar in the last three - do not initiate into one",
    # -- supply and demand ------------------------------------------------
    "zone_touch": "price trading inside a drawn supply or demand zone",
    "fresh_zone_approach": "approaching an UNTESTED zone from the correct side",
    "away_from_zone": "not initiating right on top of an untested decision level",
    # -- open interest ----------------------------------------------------
    "oi_price_confirmation": "open interest rising with the move - new money, not short covering",
    "oi_expanding": "open interest growing rather than positions being closed",
    # -- news -------------------------------------------------------------
    "outside_news_blackout": "outside the window around a scheduled high-impact release",
    "no_imminent_release": "at least 30 minutes clear of the next high-impact release",
    "post_news_window": "inside the reaction window just after a high-impact release",
    # -- candlesticks -----------------------------------------------------
    "candle_reversal": "a rejection bar - one long wick with the body pushed to the opposite end (pin bar, hammer, shooting star)",
    "candle_engulfing": "this body fully swallows the previous body and closes the other way",
    "candle_decisive_close": "the body dominates the bar - almost no wick, no argument in it",
    "candle_close_strength": "the close sits right at the high of the bar for a long, the low for a short",
    "inside_bar_compression": "this bar's entire range sits inside the previous bar's range",
}


def cue(name: str, fallback: str = "") -> str:
    """Chart language for a condition, falling back to its own description."""
    return CHART_CUES.get(name) or fallback or name


# ==========================================================================
# RANKING
# ==========================================================================

@dataclass
class RankedStrategy:
    """One strategy with everything needed to judge and to trade it."""

    strategy: Strategy
    metrics: Metrics
    score: float
    deflated: float
    rank: int = 0
    #: Distinct rule sets that produced this exact trade list. More than one
    #: means the extra filters vetoed nothing, which is worth knowing: it says
    #: the filter is decoration rather than protection.
    clones: int = 1


def _fingerprint(trades) -> str:
    key = "|".join(f"{t.entry_ts.isoformat()}:{t.direction.value}" for t in trades)
    return hashlib.sha1(key.encode()).hexdigest()[:16] if key else "empty"


def rank(symbol: str, *, timeframe: int = 60, top_n: int = 5,
         window_days: Optional[int] = None, data_dir: str = "csv/raw",
         per_template: int = 600, seed: int = 1
         ) -> Tuple[List[RankedStrategy], Dict[str, object]]:
    """Rank this symbol's strategies on one timeframe.

    Returns the top ``top_n`` and a context dict describing the search, so a
    caller can report how many candidates the winners were selected from
    rather than presenting them as if they arrived unbidden.
    """
    suffix = {1440: "1d", 240: "4h", 60: "1h", 15: "15m", 5: "5m"}[timeframe]
    full = load_csv(f"{data_dir}/{symbol}_{suffix}.csv", symbol, timeframe)
    bars = list(full.bars)
    if window_days:
        cutoff = bars[-1].ts - timedelta(days=window_days)
        bars = [b for b in bars if b.ts >= cutoff]
    series = BarSeries(symbol, timeframe, bars)
    frame = build_symbol_frame(series, FRAMES.get(timeframe, [timeframe]))

    strategies = generate_strategies(
        symbol, FRAMES.get(timeframe, [timeframe]),
        groups=[t.group for t in TEMPLATES], max_total=99999,
        max_per_template=per_template, seed=seed)
    # Only strategies anchored on the timeframe asked for. The frame carries
    # higher timeframes so alignment has something to read, but a 4-hour
    # strategy is not an answer to a question about the 1-hour chart.
    strategies = [s for s in strategies if s.primary_tf == timeframe]

    results = run_portfolio(frame, strategies)

    by_fp: Dict[str, RankedStrategy] = {}
    cleared = 0
    for s in strategies:
        trades = results[s.strategy_id].trades
        if len(trades) < MIN_TRADES:
            continue
        cleared += 1
        fp = _fingerprint(trades)
        if fp in by_fp:
            by_fp[fp].clones += 1
            continue
        m = compute_metrics(trades)
        by_fp[fp] = RankedStrategy(
            strategy=s, metrics=m,
            score=reward_for_risk_score(m).score,
            deflated=deflated_expectancy(m, len(strategies)))

    ordered = sorted(by_fp.values(), key=lambda r: -r.score)
    for i, r in enumerate(ordered, 1):
        r.rank = i

    ctx = {
        "symbol": symbol, "timeframe": timeframe,
        "frames": FRAMES.get(timeframe, [timeframe]),
        "bars": len(series),
        "span": f"{bars[0].ts:%Y-%m-%d} .. {bars[-1].ts:%Y-%m-%d}",
        "screened": len(strategies), "cleared": cleared,
        "distinct": len(by_fp),
        "collapsed": cleared - len(by_fp),
    }
    return ordered[:top_n], ctx


# ==========================================================================
# DETECTION
# ==========================================================================

def _exit_text(s: Strategy) -> List[str]:
    e = s.exit
    out = [f"stop      {e.stop_kind.value} x {e.stop_mult:g}"]
    kind = e.target_kind.value if hasattr(e.target_kind, "value") else str(e.target_kind)
    if kind == "R_MULTIPLE":
        out.append("targets   " + " / ".join(f"{m:g}R" for m in e.anchor_mult))
    else:
        out.append(f"targets   {kind}: " + " / ".join(f"{m:g}" for m in e.anchor_mult))
        out.append(f"minimum   reject the setup below {e.min_reward_risk:g}:1")
    if s.execution_tf:
        out.append(f"entry     locate on the {s.execution_tf}m chart; the thesis "
                   f"and target stay on the {s.primary_tf}m")
    else:
        out.append(f"entry     on the {s.primary_tf}m bar, no separate trigger timeframe")
    return out


def detection_card(r: RankedStrategy, *, width: int = 78) -> str:
    """The rule set as a chart checklist, in the order you would read it."""
    s = r.strategy
    signals = [c for c in s.conditions if c.kind is ConditionKind.SIGNAL]
    filters = [c for c in s.conditions if c.kind is ConditionKind.FILTER]
    m = r.metrics

    L: List[str] = []
    L.append(f"#{r.rank}  {s.strategy_id}   [{s.group} @ {s.primary_tf}m]")
    L.append(f"    score {r.score:.3f}   n={m.trades}   win {m.win_rate:.1%}   "
             f"expectancy {m.expectancy_r:+.3f}R   payoff {m.payoff_ratio:.2f}:1   "
             f"maxDD {m.max_drawdown_r:.1f}R")
    defl = f"{r.deflated:+.3f}R"
    verdict = "survives the search penalty" if r.deflated > 0 else "DOES NOT survive the search penalty"
    L.append(f"    deflated expectancy {defl}  <-  {verdict}")
    if r.clones > 1:
        L.append(f"    {r.clones} rule sets produced this exact trade list - "
                 f"their extra filters vetoed nothing")
    L.append("")
    L.append("    LOOK FOR  (every one must point the same way, long or short):")
    for i, c in enumerate(signals, 1):
        tf = c.timeframe or s.primary_tf
        tag = f"{tf}m"
        L.append(f"      {i}. [{tag:>5s}] {cue(c.name, c.description)}")
    if filters:
        L.append("")
        L.append("    ONLY THEN  (context gates - no trade unless all hold):")
        for c in filters:
            L.append(f"         - {cue(c.name, c.description)}")
    if s.trigger_conditions:
        L.append("")
        L.append(f"    TRIGGER  (on the {s.execution_tf}m chart, must agree with the thesis):")
        for c in s.trigger_conditions:
            L.append(f"         - {cue(c.name, c.description)}")
    L.append("")
    L.append("    RISK:")
    for line in _exit_text(s):
        L.append(f"      {line}")
    return "\n".join(L)


def live_state(symbol: str, r: RankedStrategy, *, data_dir: str = "csv/raw",
               lookback: int = 1) -> List[str]:
    """Which of this strategy's conditions are true on the most recent bars.

    This is the bridge between the card and the chart: it answers "how close
    am I right now" against the same data the ranking was measured on. It is
    NOT a trade signal - the last bar of a CSV is wherever the file ends, not
    wherever the market is.
    """
    s = r.strategy
    tf = s.primary_tf
    suffix = {1440: "1d", 240: "4h", 60: "1h", 15: "15m", 5: "5m"}[tf]
    series = load_csv(f"{data_dir}/{symbol}_{suffix}.csv", symbol, tf)
    frame = build_symbol_frame(series, FRAMES.get(tf, [tf]))

    out: List[str] = []
    for back in range(lookback - 1, -1, -1):
        idx = len(frame) - 1 - back
        snap = frame.snapshot(idx)
        if snap is None:
            continue
        bar = series.bars[idx]
        fired: List[str] = []
        held: List[str] = []
        for c in s.conditions:
            res = c.evaluate(snap, c.timeframe or tf)
            mark = "YES" if res.triggered else " no"
            side = ""
            if res.triggered and res.direction is not Direction.NEUTRAL:
                side = f" {res.direction.value}"
            line = f"      [{mark}]{side:>6s}  {c.name}"
            (fired if c.kind is ConditionKind.SIGNAL else held).append(line)
        out.append(f"    {bar.ts:%Y-%m-%d %H:%M}  close {bar.close:g}")
        out.extend(fired)
        if held:
            out.append("      --- gates ---")
            out.extend(held)
    return out


def report(symbol: str, *, timeframe: int = 60, top_n: int = 5,
           window_days: Optional[int] = None, data_dir: str = "csv/raw",
           per_template: int = 2400, show_live: bool = True) -> str:
    """The whole answer for one symbol on one timeframe, as printable text.

    ``per_template`` defaults high because the frame carries three timeframes
    and only the lowest is being ranked, so roughly two thirds of whatever is
    generated is discarded before a single trade is taken. At the library
    default this searched 1,217 candidates and found 9 distinct survivors;
    at 2,400 it searches 5,443 and finds 59.
    """
    ranked, ctx = rank(symbol, timeframe=timeframe, top_n=top_n,
                       window_days=window_days, data_dir=data_dir,
                       per_template=per_template)
    import math as _math
    thr = _math.sqrt(2.0 * _math.log(max(2, int(ctx["screened"]))))
    L = ["=" * 78,
         f"{symbol} - {timeframe}-minute anchor - {ctx['span']}  ({ctx['bars']:,} bars)",
         f"frame built from {ctx['frames']} so alignment has something to aggregate",
         f"screened {ctx['screened']:,} strategies - {ctx['cleared']} cleared "
         f"{MIN_TRADES} trades - {ctx['distinct']} distinct after collapsing "
         f"{ctx['collapsed']} clones",
         f"a t-statistic of {thr:.2f} is FREE at this search size - anything "
         f"below it is indistinguishable from noise",
         "=" * 78, ""]
    if not ranked:
        L.append("Nothing cleared the trade floor. That is a result, not a failure:")
        L.append("it says this timeframe did not offer this symbol enough setups.")
        return "\n".join(L)
    for r in ranked:
        L.append(detection_card(r))
        if show_live:
            L.append("")
            L.append("    WHERE THIS STANDS ON THE LAST BAR IN THE DATA:")
            L.extend(live_state(symbol, r, data_dir=data_dir))
        L.append("")
        L.append("-" * 78)
        L.append("")
    return "\n".join(L)
