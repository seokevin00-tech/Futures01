"""Multi-timeframe feature assembly.

This module turns raw bars into the single object that strategies, analysts and
the backtester all read: a :class:`SymbolFrame`. Two design choices matter.

**Precompute once, slice per bar.** A backtest evaluates tens of thousands of
bars. Recomputing a 200-period EMA at every one is quadratic and pointless, so
every indicator is computed once across the whole series into an aligned column,
and a bar's snapshot is an O(1) index into those columns.

**Alignment is explicit and lagged.** ``SymbolFrame`` stores, for every base bar,
the index of the last *completed* bar on each higher timeframe. At 10:07 the
15-minute row points at the 09:45 bar, because that is the newest 15-minute bar
that had closed. This is the mechanism that makes multi-timeframe confluence
honest: no strategy can accidentally read a higher-timeframe bar that had not
yet formed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import AccountConfig, ContractSpec, get_contract, tf_label
from .data.bars import Bar, BarSeries, resample
from .indicators.core import (adx, atr, bollinger, ema, keltner, linreg_slope,
                              macd, percent_rank, roc, rolling_max, rolling_min,
                              rsi, sma, stdev, stochastic)
from .indicators.regime import RegimeSnapshot, classify_regime, efficiency_ratio
from .indicators.structure import (FVG, SDZone, Swing, SRLevel, SessionLevels,
                                   Sweep, fair_value_gaps, find_swings,
                                   opening_range, support_resistance,
                                   supply_demand_zones)
from .econ_calendar import Impact, event_proximity, project_events
from .indicators.volume import (VolumeProfile, cumulative_delta,
                                delta_divergence, relative_volume,
                                volume_profile, vwap_bands)
from .timeutil import (classify_session, day_of_week_name, is_rth,
                       minutes_since_open, time_bucket, to_et, trading_day)

Num = Optional[float]

__all__ = [
    "TFSnapshot", "TimeframeFrame", "FeatureSnapshot", "SymbolFrame",
    "build_symbol_frame", "FEATURE_NAMES",
]

#: Every column a strategy rule may reference. The combinator validates rule
#: names against this list, so a typo fails loudly at construction instead of
#: silently evaluating to None for an entire backtest.
FEATURE_NAMES: Tuple[str, ...] = (
    "open", "high", "low", "close", "volume", "delta", "cvd",
    "ema9", "ema21", "ema50", "ema200", "sma20", "sma50",
    "rsi", "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_mid", "bb_lower", "bb_width_pct", "bb_pctb",
    "atr", "atr_pct", "atr_percentile",
    "vwap", "vwap_u1", "vwap_l1", "vwap_u2", "vwap_l2", "vwap_dist_atr",
    "adx", "plus_di", "minus_di", "stoch_k", "stoch_d",
    "rel_volume", "efficiency_ratio", "slope_atr",
    "hh20", "ll20", "range_pos", "keltner_upper", "keltner_lower",
    "open_interest", "oi_change", "roc20",
)

#: Blackout window around a scheduled high-impact release, in minutes. Read off
#: :class:`AccountConfig` rather than restated here, so the window the
#: backtester stands aside for is by construction the window the live risk
#: manager stands aside for. A backtest that measured a different window has
#: measured a different strategy.
NEWS_BLACKOUT_BEFORE_MIN: int = AccountConfig.news_blackout_before_min
NEWS_BLACKOUT_AFTER_MIN: int = AccountConfig.news_blackout_after_min


# --------------------------------------------------------------------------
# Per-timeframe frame
# --------------------------------------------------------------------------

@dataclass
class TFSnapshot:
    """Indicator values for one timeframe at one bar. All fields may be None."""

    timeframe: int
    index: int
    bar: Bar
    values: Dict[str, Num] = field(default_factory=dict)
    structure_trend: str = "UNDEFINED"
    structure_event: str = ""
    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None
    prior_swing_high: Optional[float] = None
    prior_swing_low: Optional[float] = None
    sr_levels: List[SRLevel] = field(default_factory=list)
    divergence: Optional[str] = None
    volume_regime: Optional[str] = None
    active_fvgs: List[FVG] = field(default_factory=list)
    #: Price-by-volume profile of the PRIOR completed session. Backward
    #: looking by construction, so POC/VAH/VAL are usable as reference
    #: levels without leaking the session being traded.
    prior_profile: Optional[VolumeProfile] = None
    open_interest: Optional[float] = None
    imbalance: Optional[str] = None      # BULLISH | BEARISH on this bar
    recent_imbalance: Optional[str] = None
    bars_since_imbalance: Optional[int] = None
    #: Untested-or-tested supply and demand zones visible at this bar, most
    #: recent first. Already masked to this bar: ``fresh`` and ``touches``
    #: answer for now, not for the end of the series.
    sd_zones: List[SDZone] = field(default_factory=list)
    #: Candlestick patterns completing on this bar. Reads this bar and those
    #: before it, never the next one.
    candles: List[Any] = field(default_factory=list)
    #: Bar index of the most recent confirmed swing of each kind. Needed to
    #: know which way the last impulse leg ran, which is what a Fibonacci
    #: retracement is measured against.
    last_swing_high_index: Optional[int] = None
    last_swing_low_index: Optional[int] = None

    # ---- derived reference levels -----------------------------------
    def swing_leg(self) -> Optional[Tuple[float, float, str]]:
        """``(low, high, direction)`` of the last confirmed impulse leg.

        ``direction`` is ``"UP"`` when the high was made after the low - a leg
        whose retracement is a pullback to buy - and ``"DOWN"`` otherwise.
        Returns ``None`` until both a high and a low have been confirmed, which
        is the honest answer rather than a leg invented from one swing.
        """
        hi, lo = self.last_swing_high, self.last_swing_low
        hi_i, lo_i = self.last_swing_high_index, self.last_swing_low_index
        if hi is None or lo is None or hi_i is None or lo_i is None or hi <= lo:
            return None
        return (lo, hi, "UP" if hi_i > lo_i else "DOWN")

    def fib_zone(self, lower: float = 0.5, upper: float = 0.786
                 ) -> Optional[Tuple[float, float, str]]:
        """Price band between two retracement ratios of the last leg.

        Ratios are measured from the *end* of the leg back towards its start,
        the way a trader draws them: 0.618 of an up leg sits above 0.786.
        """
        leg = self.swing_leg()
        if leg is None:
            return None
        lo, hi, direction = leg
        span = hi - lo
        if span <= 0:
            return None
        if direction == "UP":
            a, b = hi - upper * span, hi - lower * span
        else:
            a, b = lo + lower * span, lo + upper * span
        return (min(a, b), max(a, b), direction)

    def __getitem__(self, key: str) -> Num:
        return self.values.get(key)

    def get(self, key: str, default: Num = None) -> Num:
        v = self.values.get(key)
        return default if v is None else v

    def has(self, *keys: str) -> bool:
        """True when every named value is available (not warming up)."""
        return all(self.values.get(k) is not None for k in keys)

    @property
    def close(self) -> float:
        return self.bar.close

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "ts": to_et(self.bar.ts).isoformat(),
            "close": self.bar.close,
            "structure_trend": self.structure_trend,
            "structure_event": self.structure_event,
            "last_swing_high": self.last_swing_high,
            "last_swing_low": self.last_swing_low,
            "divergence": self.divergence,
            "volume_regime": self.volume_regime,
            "imbalance": self.imbalance,
            "open_interest": self.open_interest,
            "prior_profile": self.prior_profile.to_dict() if self.prior_profile else None,
            "sd_zones": [z.to_dict() for z in self.sd_zones[:4]],
            "sr_levels": [l.to_dict() for l in self.sr_levels[:5]],
            "values": {k: (round(v, 6) if isinstance(v, float) else v)
                       for k, v in self.values.items() if v is not None},
        }


class TimeframeFrame:
    """Precomputed indicator columns for one timeframe of one symbol."""

    def __init__(self, series: BarSeries, spec: ContractSpec, *,
                 swing_left: int = 3, swing_right: int = 3,
                 sr_window_swings: int = 30):
        self.series = series
        self.timeframe = series.minutes
        self.spec = spec
        self.swing_left = swing_left
        self.swing_right = swing_right
        self.sr_window_swings = sr_window_swings
        self.cols: Dict[str, List[Num]] = {}
        self._build()

    # ---- construction ------------------------------------------------
    def _build(self) -> None:
        bars = self.series.bars
        n = len(bars)
        if n == 0:
            self._swings, self._fvgs, self._zones = [], [], []
            self._swing_ptr = []
            return

        o = [b.open for b in bars]
        h = [b.high for b in bars]
        l = [b.low for b in bars]
        c = [b.close for b in bars]
        v = [b.volume for b in bars]
        d = [b.delta for b in bars]

        C = self.cols
        C["open"], C["high"], C["low"], C["close"], C["volume"] = o, h, l, c, v
        C["delta"] = d
        C["cvd"] = cumulative_delta(bars, "session")

        C["ema9"] = ema(c, 9)
        C["ema21"] = ema(c, 21)
        C["ema50"] = ema(c, 50)
        C["ema200"] = ema(c, 200)
        C["sma20"] = sma(c, 20)
        C["sma50"] = sma(c, 50)

        C["rsi"] = rsi(c, 14)
        C["macd"], C["macd_signal"], C["macd_hist"] = macd(c, 12, 26, 9)

        bu, bm, bl = bollinger(c, 20, 2.0)
        C["bb_upper"], C["bb_mid"], C["bb_lower"] = bu, bm, bl
        C["bb_width_pct"] = [None if (u is None or m in (None, 0) or lo is None)
                             else (u - lo) / m * 100.0 for u, m, lo in zip(bu, bm, bl)]
        C["bb_pctb"] = [None if (u is None or lo is None or u == lo)
                        else (cc - lo) / (u - lo) for cc, u, lo in zip(c, bu, bl)]

        a = atr(h, l, c, 14)
        C["atr"] = a
        C["atr_pct"] = [None if (x is None or not cc) else x / cc * 100.0
                        for x, cc in zip(a, c)]
        C["atr_percentile"] = self._percentile_of(a, 250)

        vb = vwap_bands(bars, "session", (1.0, 2.0))
        C["vwap"] = vb["vwap"]
        C["vwap_u1"], C["vwap_l1"] = vb["upper_1"], vb["lower_1"]
        C["vwap_u2"], C["vwap_l2"] = vb["upper_2"], vb["lower_2"]
        C["vwap_dist_atr"] = [
            None if (w is None or x in (None, 0)) else (cc - w) / x
            for cc, w, x in zip(c, vb["vwap"], a)]

        C["adx"], C["plus_di"], C["minus_di"] = adx(h, l, c, 14)
        C["stoch_k"], C["stoch_d"] = stochastic(h, l, c, 14, 3, 3)

        C["rel_volume"] = relative_volume(bars, 20)
        C["efficiency_ratio"] = efficiency_ratio(c, 20)
        sl = linreg_slope(c, 20)
        C["slope_atr"] = [None if (s is None or x in (None, 0)) else s / x
                          for s, x in zip(sl, a)]

        C["hh20"] = rolling_max(h, 20)
        C["ll20"] = rolling_min(l, 20)
        C["range_pos"] = [
            None if (hi is None or lo is None or hi == lo) else (cc - lo) / (hi - lo)
            for cc, hi, lo in zip(c, C["hh20"], C["ll20"])]

        ku, _, kl = keltner(h, l, c, 20, 1.5)
        C["keltner_upper"], C["keltner_lower"] = ku, kl

        # Open interest, when the feed carries it. Most intraday exports do
        # not, so every consumer has to treat None as "unknown" rather than
        # "unchanged" - an OI strategy validated on a column of zeros has not
        # been validated.
        oi = [b.open_interest for b in bars]
        C["open_interest"] = list(oi)
        C["oi_change"] = self._oi_change(oi, 20)
        # The price move over the SAME window the OI change is measured over.
        # "Open interest expanded while price rose" is a statement about one
        # interval, and pairing a 20-bar OI change with a position-in-range
        # reading answered a different question on about 7.5% of bars - often
        # with the opposite sign to its own thesis.
        C["roc20"] = roc(c, 20)

        self.divergence = delta_divergence(bars, 20, "session")
        self.volume_regime_col = self._volume_regime(v)

        # Structure: computed once for the whole series, then exposed through a
        # per-bar pointer that only reveals CONFIRMED swings.
        self._swings: List[Swing] = find_swings(bars, self.swing_left, self.swing_right)
        self._swing_ptr = self._build_swing_pointers(n)
        self._fvgs: List[FVG] = fair_value_gaps(bars, as_of=n - 1, track_fills=True)
        self._fvg_ptr = self._build_fvg_pointers(n)
        self._sr_cache: Dict[int, List[SRLevel]] = {}
        self._profile_cache: Dict[Any, Optional[VolumeProfile]] = {}
        self._build_session_index()
        self._build_imbalances()
        self._build_candle_patterns()
        self._zones: List[SDZone] = supply_demand_zones(bars, as_of=n - 1)
        self._zone_ptr = self._build_zone_pointers(n)

    @staticmethod
    def _oi_change(values: Sequence[Num], lookback: int) -> List[Num]:
        """Open-interest change over ``lookback`` bars, None where unknown."""
        out: List[Num] = [None] * len(values)
        for i in range(lookback, len(values)):
            now, then = values[i], values[i - lookback]
            if now is not None and then is not None:
                out[i] = now - then
        return out

    @staticmethod
    def _percentile_of(values: Sequence[Num], window: int) -> List[Num]:
        out: List[Num] = [None] * len(values)
        buf: List[float] = []
        for i, val in enumerate(values):
            if val is None:
                continue
            if buf:
                out[i] = sum(1 for x in buf if x < val) / len(buf)
            buf.append(val)
            if len(buf) > window:
                buf.pop(0)
        return out

    @staticmethod
    def _volume_regime(vols: Sequence[float]) -> List[Optional[str]]:
        pr = percent_rank(vols, 60)
        out: List[Optional[str]] = [None] * len(vols)
        for i, p in enumerate(pr):
            if p is None:
                continue
            out[i] = ("SURGE" if p >= 0.90 else "ABOVE_AVERAGE" if p >= 0.70
                      else "AVERAGE" if p >= 0.30 else "BELOW_AVERAGE" if p >= 0.10
                      else "THIN")
        return out

    def _build_swing_pointers(self, n: int) -> List[int]:
        """For each bar index: how many swings were confirmed by that bar, plus
        the last two confirmed highs and lows.

        The last-two arrays are built in this single forward pass on purpose.
        Deriving them per snapshot by filtering the whole swing list is O(n) per
        bar and O(n^2) over a backtest - it was measured at 25 million calls for
        a 20-day run, and it dominated everything else.
        """
        ptr = [0] * n
        self._last_high: List[Num] = [None] * n
        self._prior_high: List[Num] = [None] * n
        self._last_low: List[Num] = [None] * n
        self._prior_low: List[Num] = [None] * n
        self._last_high_i: List[Optional[int]] = [None] * n
        self._last_low_i: List[Optional[int]] = [None] * n
        k = 0
        ordered = sorted(self._swings, key=lambda s: s.confirmed_index)
        self._swings_by_confirm = ordered
        lh = ph = ll = pl = None
        lh_i = ll_i = None
        for i in range(n):
            while k < len(ordered) and ordered[k].confirmed_index <= i:
                s = ordered[k]
                if s.is_high:
                    ph, lh = lh, s.price
                    lh_i = s.index
                else:
                    pl, ll = ll, s.price
                    ll_i = s.index
                k += 1
            ptr[i] = k
            self._last_high[i], self._prior_high[i] = lh, ph
            self._last_low[i], self._prior_low[i] = ll, pl
            self._last_high_i[i], self._last_low_i[i] = lh_i, ll_i
        return ptr

    def _build_zone_pointers(self, n: int) -> List[int]:
        """For each bar: how many supply/demand zones had formed by then.

        A zone is knowable once its departure bar has closed, so the pointer
        advances at the departure index itself - unlike an FVG, which needs the
        bar after the displacement before its geometry exists.
        """
        ptr = [0] * n
        lo_ptr = [0] * n
        k = 0
        lo = 0
        ordered = sorted(self._zones, key=lambda z: z.index)
        self._zones_by_index = ordered
        for i in range(n):
            while k < len(ordered) and ordered[k].index <= i:
                k += 1
            # Zones older than the scan window are dead to every later bar, so
            # the lower edge can advance monotonically too. Without it
            # active_zones re-walked the whole zone list on every bar and threw
            # almost all of it away on an age check - 8.5us a bar on 1-minute
            # data, for a window that holds about six zones.
            while lo < k and i - ordered[lo].index > self.ZONE_SCAN_WINDOW:
                lo += 1
            ptr[i] = k
            lo_ptr[i] = lo
        self._zone_lo_ptr = lo_ptr
        return ptr

    def _build_fvg_pointers(self, n: int) -> List[int]:
        ptr = [0] * n
        k = 0
        ordered = sorted(self._fvgs, key=lambda g: g.index + 1)
        self._fvgs_by_visible = ordered
        for i in range(n):
            while k < len(ordered) and ordered[k].index + 1 <= i:
                k += 1
            ptr[i] = k
        return ptr

    # ---- access -------------------------------------------------------
    def __len__(self) -> int:
        return len(self.series)

    def visible_swings(self, index: int) -> List[Swing]:
        if not self._swings or index < 0:
            return []
        k = self._swing_ptr[min(index, len(self._swing_ptr) - 1)]
        return self._swings_by_confirm[:k]

    #: Width of the S/R cache bucket, in bars.
    SR_BUCKET = 5

    def sr_levels(self, index: int) -> List[SRLevel]:
        """S/R clustered from the most recent confirmed swings.

        Cached per 5-bar bucket: levels do not meaningfully change bar to bar,
        and recomputing them for every bar of an 80,000-bar backtest is the
        difference between a 20-second run and a 20-minute one.

        The bucket is evaluated **at its own first bar**, not at whichever bar
        happened to ask first. Keying the cache on the bucket while computing
        it from the caller's index made the answer depend on access order: a
        backtest walking forward warmed bucket 400 at bar 2000 and got bar
        2000's swings, but anything that asked bar 2004 first - a walk-forward
        re-evaluation, a live single-bar query, any random access - warmed the
        same bucket with bar 2004's swings and then handed them to bar 2000.
        That is four bars of look-ahead, and it made the frame's output a
        function of the query order rather than of the data.
        """
        bucket = index // self.SR_BUCKET
        hit = self._sr_cache.get(bucket)
        if hit is not None:
            return hit
        at = bucket * self.SR_BUCKET
        vis = self.visible_swings(at)[-self.sr_window_swings:]
        levels: List[SRLevel] = []
        if vis:
            bars = self.series.bars
            ref = bars[min(at, len(bars) - 1)].close or 1.0
            band = abs(ref) * 0.0015
            clusters: List[List[Swing]] = []
            for s in sorted(vis, key=lambda x: x.price):
                if clusters and abs(s.price - clusters[-1][0].price) <= band:
                    clusters[-1].append(s)
                else:
                    clusters.append([s])
            for cl in clusters:
                if len(cl) < 2:
                    continue
                price = sum(s.price for s in cl) / len(cl)
                n_high = sum(1 for s in cl if s.is_high)
                kind = ("RESISTANCE" if n_high > len(cl) - n_high
                        else "SUPPORT" if n_high < len(cl) - n_high else "PIVOT")
                levels.append(SRLevel(price, len(cl), kind,
                                      min(s.index for s in cl),
                                      max(s.index for s in cl),
                                      float(len(cl))))
            levels.sort(key=lambda lv: lv.strength, reverse=True)
        self._sr_cache[bucket] = levels
        return levels

    #: How many recently-visible gaps to consider. An FVG a thousand bars old
    #: that price has never revisited is not a level anyone trades off, and
    #: scanning the full list every bar is quadratic.
    FVG_SCAN_WINDOW = 60

    def active_fvgs(self, index: int, limit: int = 6) -> List[FVG]:
        """Unfilled FVGs visible at ``index``, most recent first."""
        if not self._fvgs:
            return []
        # Clamp below zero as well as above. Python's negative indexing turns
        # a stray -1 into "the last row of the pointer table", i.e. every gap
        # in the series - the end of the data handed to a bar that has not
        # reached it. Clamping to 0 means an out-of-range bar sees nothing.
        k = self._fvg_ptr[min(max(0, index), len(self._fvg_ptr) - 1)]
        start = max(0, k - self.FVG_SCAN_WINDOW)
        out: List[FVG] = []
        for g in self._fvgs_by_visible[start:k]:
            if g.filled_index is None:
                out.append(g)
            elif g.filled_index > index:
                # The gap is unfilled AS OF THIS BAR, but the stored object
                # knows which future bar eventually fills it. Handing that
                # object out verbatim leaks the future: FVG.filled and
                # FVG.to_dict() report filled=True at a bar where the fill has
                # not happened, and that value reaches the feature snapshot and
                # every agent reading it. Mask it to what is knowable now.
                out.append(replace(g, filled_index=None))
            # else: already filled at or before this bar - not active.
        return out[-limit:][::-1]


    #: How far back to look for a still-relevant zone. Matches the forward
    #: scan in :func:`supply_demand_zones`, so a zone the detector stopped
    #: tracking is not reported as untouched.
    ZONE_SCAN_WINDOW = 400

    def active_zones(self, index: int, limit: int = 6) -> List[SDZone]:
        """Zones visible and not yet invalidated at ``index``, newest first.

        Every zone is passed through :meth:`SDZone.as_of` before it leaves this
        method. The stored object knows which future bars will test and break
        it; handing that out would let a strategy prefer the zones that are
        about to hold.
        """
        if not self._zones:
            return []
        j = min(max(0, index), len(self._zone_ptr) - 1)
        k = self._zone_ptr[j]
        start = self._zone_lo_ptr[j]
        out: List[SDZone] = []
        for z in self._zones_by_index[start:k]:
            if index - z.index > self.ZONE_SCAN_WINDOW:
                continue
            inv = z.invalidated_index
            if inv is not None and inv <= index:
                continue                    # broken at or before this bar
            touches = z.touch_indices
            if inv is None and (not touches or touches[-1] <= index):
                # Nothing in this zone postdates the asking bar, so the stored
                # object already answers for it. as_of() would return an equal
                # copy; SDZone is frozen precisely so sharing it is safe.
                out.append(z)
            else:
                out.append(z.as_of(index))
        return out[-limit:][::-1]

    def _build_session_index(self) -> None:
        """Map each bar to its trading day, and each day to its bar range.

        Needed so the prior session's volume profile can be built once per day
        rather than rescanned per bar.
        """
        from .timeutil import trading_day
        self._day_of: List[Any] = []
        self._day_bars: Dict[Any, List[int]] = {}
        self._day_order: List[Any] = []
        for i, bar in enumerate(self.series.bars):
            day = trading_day(bar.ts)
            self._day_of.append(day)
            if day not in self._day_bars:
                self._day_bars[day] = []
                self._day_order.append(day)
            self._day_bars[day].append(i)

    def _build_candle_patterns(self) -> None:
        """Bar-form patterns for every bar, precomputed once.

        Same shape as the imbalance column: classifying per snapshot would
        rescan the series, and a condition cannot reach back to the prior bar
        from a snapshot alone - the first version of the candlestick
        conditions tried and silently returned nothing on every bar.
        """
        from .indicators.candles import classify_candle
        bars = self.series.bars
        self.candle_col: List[List[Any]] = [[] for _ in bars]
        for i in range(len(bars)):
            self.candle_col[i] = classify_candle(bars, i)

    def _build_imbalances(self) -> None:
        """Per-bar aggressive-participation flag, precomputed once."""
        from .indicators.structure import detect_imbalances
        n = len(self.series)
        self.imbalance_col: List[Optional[str]] = [None] * n
        #: Direction of the most recent imbalance at or before each bar, and
        #: how many bars ago it was. A displacement two bars back is still
        #: shaping the tape; one four hundred bars back is history.
        self.recent_imbalance_col: List[Optional[str]] = [None] * n
        self.bars_since_imbalance_col: List[Optional[int]] = [None] * n
        if n:
            for idx, direction, _mult in detect_imbalances(self.series.bars):
                if 0 <= idx < n:
                    self.imbalance_col[idx] = direction
            last_dir: Optional[str] = None
            last_at: Optional[int] = None
            for i in range(n):
                if self.imbalance_col[i] is not None:
                    last_dir, last_at = self.imbalance_col[i], i
                self.recent_imbalance_col[i] = last_dir
                self.bars_since_imbalance_col[i] = (
                    None if last_at is None else i - last_at)

    def prior_session_profile(self, index: int) -> Optional[VolumeProfile]:
        """Volume profile of the last COMPLETED session before ``index``.

        Strictly backward looking: the session being traded never contributes,
        so POC, VAH and VAL are reference levels the bar could actually have
        known. Cached per day, because a profile is a property of the session,
        not of the bar asking about it.
        """
        if not self._day_of or index < 0:
            return None
        day = self._day_of[min(index, len(self._day_of) - 1)]
        if day in self._profile_cache:
            return self._profile_cache[day]
        try:
            position = self._day_order.index(day)
        except ValueError:
            return None
        profile = None
        if position > 0:
            prior_bars = [self.series.bars[i]
                          for i in self._day_bars[self._day_order[position - 1]]]
            if len(prior_bars) >= 10:
                profile = volume_profile(prior_bars, bins=40)
        self._profile_cache[day] = profile
        return profile

    def snapshot(self, index: int) -> Optional[TFSnapshot]:
        bars = self.series.bars
        if not bars or index < 0:
            return None
        i = min(index, len(bars) - 1)
        vals: Dict[str, Num] = {}
        for name in FEATURE_NAMES:
            col = self.cols.get(name)
            vals[name] = col[i] if col is not None and i < len(col) else None

        last_high, prior_high = self._last_high[i], self._prior_high[i]
        last_low, prior_low = self._last_low[i], self._prior_low[i]

        trend = "UNDEFINED"
        event = ""
        if None not in (last_high, prior_high, last_low, prior_low):
            hh = last_high > prior_high
            hl = last_low > prior_low
            lh = last_high < prior_high
            ll = last_low < prior_low
            if hh and hl:
                trend, event = "UPTREND", "BOS_UP"
            elif lh and ll:
                trend, event = "DOWNTREND", "BOS_DOWN"
            elif hh and ll:
                trend, event = "RANGE", "EXPANSION"
            else:
                trend = "RANGE"

        return TFSnapshot(
            timeframe=self.timeframe, index=i, bar=bars[i], values=vals,
            structure_trend=trend, structure_event=event,
            last_swing_high=last_high, last_swing_low=last_low,
            prior_swing_high=prior_high, prior_swing_low=prior_low,
            sr_levels=self.sr_levels(i),
            divergence=self.divergence[i] if i < len(self.divergence) else None,
            volume_regime=(self.volume_regime_col[i]
                           if i < len(self.volume_regime_col) else None),
            active_fvgs=self.active_fvgs(i),
            prior_profile=self.prior_session_profile(i),
            open_interest=bars[i].open_interest,
            imbalance=(self.imbalance_col[i]
                       if i < len(self.imbalance_col) else None),
            recent_imbalance=(self.recent_imbalance_col[i]
                              if i < len(self.recent_imbalance_col) else None),
            bars_since_imbalance=(self.bars_since_imbalance_col[i]
                                  if i < len(self.bars_since_imbalance_col) else None),
            sd_zones=self.active_zones(i),
            candles=(self.candle_col[i] if i < len(self.candle_col) else []),
            last_swing_high_index=self._last_high_i[i],
            last_swing_low_index=self._last_low_i[i],
        )


# --------------------------------------------------------------------------
# Symbol-level frame
# --------------------------------------------------------------------------

@dataclass
class FeatureSnapshot:
    """Everything known about one symbol at one instant, across all timeframes."""

    symbol: str
    ts: datetime
    base_index: int
    price: float
    spec: ContractSpec
    tfs: Dict[int, TFSnapshot] = field(default_factory=dict)
    regime: RegimeSnapshot = field(default_factory=RegimeSnapshot)
    session: str = ""
    time_bucket: str = ""
    day_of_week: str = ""
    minutes_since_open: float = 0.0
    is_rth: bool = False
    session_levels: SessionLevels = field(default_factory=SessionLevels)
    opening_range: Optional[Any] = None
    trading_day: Optional[date] = None
    #: Minutes to the next high-impact scheduled release, from the
    #: rule-based economic calendar. Deterministic and identical in a
    #: backtest and live, which is what makes "news conditions" a testable
    #: variable rather than a live-only annotation.
    minutes_to_high_impact: float = float("inf")
    #: Minutes since the last high-impact release. The reaction window after a
    #: print behaves nothing like the drift before one, so a strategy has to be
    #: able to condition on which side of the event it is standing.
    minutes_since_high_impact: float = float("inf")
    in_news_blackout: bool = False

    def tf(self, timeframe: int) -> Optional[TFSnapshot]:
        return self.tfs.get(int(timeframe))

    def value(self, timeframe: int, name: str) -> Num:
        s = self.tfs.get(int(timeframe))
        return s[name] if s else None

    @property
    def timeframes(self) -> List[int]:
        return sorted(self.tfs)

    def alignment(self, from_tf: Optional[int] = None) -> float:
        """Directional agreement across timeframes, in [-1, 1].

        Each timeframe votes with its structural trend, weighted by its length -
        a 4-hour trend is worth more than a 1-minute one. Near zero means the
        timeframes disagree, which is itself a tradeable piece of information
        (and usually an argument for standing aside).

        ``from_tf`` restricts the vote to that timeframe and everything ABOVE
        it, which is what a strategy trading a given timeframe actually wants
        to know: a 4-hour trader is not helped by the 1-minute chart agreeing.
        Without it this aggregated every timeframe in the snapshot regardless
        of the caller, so ``mtf_aligned`` bound to 60m and bound to 240m
        returned an identical answer on every bar of a 5,000-bar series - the
        binding was inert and the condition was blind to the timeframe it was
        supposed to be reading.
        """
        num = den = 0.0
        for tf, snap in self.tfs.items():
            if from_tf is not None and tf < from_tf:
                continue
            w = math.log(tf + 1.0)
            vote = {"UPTREND": 1.0, "DOWNTREND": -1.0}.get(snap.structure_trend, 0.0)
            num += vote * w
            den += w
        return (num / den) if den else 0.0

    def agreeing_timeframes(self, from_tf: Optional[int] = None) -> Tuple[int, int]:
        """``(agreeing, voting)`` - how many timeframes share the majority view.

        The linkage a trader means by "more timeframes agree, so I am more
        confident": a count, not a weighted average. Three of three carries a
        different conviction from two of three even when the weighted score is
        similar.
        """
        votes = []
        for tf, snap in self.tfs.items():
            if from_tf is not None and tf < from_tf:
                continue
            v = {"UPTREND": 1, "DOWNTREND": -1}.get(snap.structure_trend, 0)
            if v:
                votes.append(v)
        if not votes:
            return 0, 0
        ups = sum(1 for v in votes if v > 0)
        return max(ups, len(votes) - ups), len(votes)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "ts": to_et(self.ts).isoformat(),
            "price": self.price,
            "session": self.session,
            "time_bucket": self.time_bucket,
            "day_of_week": self.day_of_week,
            "minutes_since_open": round(self.minutes_since_open, 1),
            "is_rth": self.is_rth,
            "regime": self.regime.to_dict(),
            "alignment": round(self.alignment(), 3),
            "session_levels": self.session_levels.to_dict(),
            "opening_range": self.opening_range.to_dict() if self.opening_range else None,
            "minutes_to_high_impact": (None if self.minutes_to_high_impact == float("inf")
                                       else round(self.minutes_to_high_impact, 1)),
            "minutes_since_high_impact": (None if self.minutes_since_high_impact == float("inf")
                                          else round(self.minutes_since_high_impact, 1)),
            "in_news_blackout": self.in_news_blackout,
            "timeframes": {str(tf): s.to_dict() for tf, s in sorted(self.tfs.items())},
        }


class SymbolFrame:
    """All timeframes of one symbol, with look-ahead-safe cross-timeframe alignment."""

    def __init__(self, base: BarSeries, timeframes: Sequence[int],
                 spec: Optional[ContractSpec] = None, *,
                 regime_timeframe: Optional[int] = None):
        self.symbol = base.symbol
        self.spec = spec or get_contract(base.symbol)
        self.base = base
        self.timeframes = tuple(sorted({int(t) for t in timeframes}))
        if any(tf < base.minutes for tf in self.timeframes):
            raise ValueError(
                f"{self.symbol}: requested a timeframe finer than the {base.minutes}m source")

        self.frames: Dict[int, TimeframeFrame] = {}
        for tf in self.timeframes:
            series = base if tf == base.minutes else resample(base, tf, keep_partial=False)
            self.frames[tf] = TimeframeFrame(series, self.spec)

        self.regime_tf = regime_timeframe or self._default_regime_tf()
        self._align = self._build_alignment()
        self._session_state = self._build_session_state()
        self._regime_cache: Dict[int, RegimeSnapshot] = {}
        self._news = self._build_news_proximity()

    def _default_regime_tf(self) -> int:
        """Regime is read off an intermediate timeframe - 1-minute regime labels
        are noise, daily ones are stale."""
        for pref in (15, 30, 5, 60, 10, 3, 1):
            if pref in self.frames:
                return pref
        return self.timeframes[-1]

    def _build_alignment(self) -> Dict[int, List[int]]:
        """base bar index -> index of the last COMPLETED bar on each timeframe.

        ``-1`` means no bar on that timeframe had closed yet.
        """
        out: Dict[int, List[int]] = {}
        base_bars = self.base.bars
        for tf, frame in self.frames.items():
            tf_bars = frame.series.bars
            ptr: List[int] = []
            k = -1
            j = 0
            for b in base_bars:
                end = b.end_ts
                while j < len(tf_bars) and tf_bars[j].end_ts <= end:
                    k = j
                    j += 1
                ptr.append(k)
            out[tf] = ptr
        return out

    def _build_session_state(self) -> List[Tuple[SessionLevels, Optional[Any]]]:
        """Running session levels and opening range for every base bar.

        Computed in one forward pass: each bar updates the running extremes, so
        the value at bar *i* contains only information available at bar *i*.
        """
        out: List[Tuple[SessionLevels, Optional[Any]]] = []
        spec = self.spec
        cur_day: Optional[date] = None
        prev_day_summary: Tuple[Optional[float], Optional[float], Optional[float]] = (None, None, None)
        day_rth: List[float] = []      # running [high, low, close]
        on_high = on_low = None
        rth_high = rth_low = day_open = None
        ib_high = ib_low = None
        or_high = or_low = None
        or_complete = False
        or_minutes = 30

        for b in self.base.bars:
            day = trading_day(b.ts)
            if day != cur_day:
                if cur_day is not None and rth_high is not None:
                    prev_day_summary = (rth_high, rth_low, day_rth[-1] if day_rth else None)
                cur_day = day
                on_high = on_low = None
                rth_high = rth_low = day_open = None
                ib_high = ib_low = None
                or_high = or_low = None
                or_complete = False
                day_rth = []

            in_rth = is_rth(b.ts, spec.rth_open, spec.rth_close)
            if in_rth:
                if day_open is None:
                    day_open = b.open
                rth_high = b.high if rth_high is None else max(rth_high, b.high)
                rth_low = b.low if rth_low is None else min(rth_low, b.low)
                day_rth.append(b.close)
                mso = minutes_since_open(b.ts, spec.rth_open)
                if mso < 60:
                    ib_high = b.high if ib_high is None else max(ib_high, b.high)
                    ib_low = b.low if ib_low is None else min(ib_low, b.low)
                if mso < or_minutes:
                    or_high = b.high if or_high is None else max(or_high, b.high)
                    or_low = b.low if or_low is None else min(or_low, b.low)
                elif or_high is not None:
                    or_complete = True
            else:
                on_high = b.high if on_high is None else max(on_high, b.high)
                on_low = b.low if on_low is None else min(on_low, b.low)

            lv = SessionLevels(
                prev_day_high=prev_day_summary[0], prev_day_low=prev_day_summary[1],
                prev_day_close=prev_day_summary[2],
                overnight_high=on_high, overnight_low=on_low,
                session_high=rth_high, session_low=rth_low,
                initial_balance_high=ib_high, initial_balance_low=ib_low,
                day_open=day_open,
            )
            orr = None
            if or_high is not None and or_low is not None:
                from .indicators.structure import OpeningRange
                orr = OpeningRange(day=day, minutes=or_minutes, high=or_high,
                                   low=or_low, complete=or_complete,
                                   open_price=day_open or or_high)
            out.append((lv, orr))
        return out

    # ---- access -------------------------------------------------------
    def __len__(self) -> int:
        return len(self.base)

    def tf_index(self, base_index: int, timeframe: int) -> int:
        """Index of the newest completed ``timeframe`` bar at ``base_index``."""
        ptr = self._align.get(int(timeframe))
        if ptr is None:
            raise KeyError(f"{self.symbol}: timeframe {timeframe}m not in this frame")
        return ptr[min(max(0, base_index), len(ptr) - 1)]

    def regime_at(self, base_index: int) -> RegimeSnapshot:
        """Regime read off the regime timeframe, cached per regime bar.

        Recomputing the regime for every base bar would dominate backtest
        runtime and would produce the same answer within a single higher
        timeframe bar anyway.
        """
        ri = self.tf_index(base_index, self.regime_tf)
        if ri < 0:
            return RegimeSnapshot()
        hit = self._regime_cache.get(ri)
        if hit is None:
            bars = self.frames[self.regime_tf].series.bars[: ri + 1]
            hit = classify_regime(bars[-400:]) if len(bars) >= 60 else RegimeSnapshot()
            self._regime_cache[ri] = hit
        return hit


    def _build_news_proximity(self) -> List[Tuple[float, float, bool]]:
        """Minutes-to-next-high-impact and blackout flag for every base bar.

        Projected ONCE across the whole series from the recurrence rules, then
        walked with a pointer - calling event_proximity per bar would re-project
        a 72-hour horizon tens of thousands of times.
        """
        bars = self.base.bars
        if not bars:
            return []
        from datetime import timedelta
        start = bars[0].ts - timedelta(days=2)
        # Far enough forward that the last bar of any series still sees a
        # qualifying release ahead of it. At +5 days a run ending mid-month
        # reported "no high-impact event ahead" purely because CPI, payrolls
        # and PCE all sat outside the projection - an artefact of the horizon
        # that a strategy would have read as a quiet calendar.
        end = bars[-1].ts + timedelta(days=45)
        # Pass the symbol: the calendar carries symbol-scoped rules, and
        # without this an MCL frame never sees the EIA inventory report - the
        # single largest scheduled mover of WTI, and the only high-impact US
        # release that lands INSIDE the RTH session. Every other HIGH rule
        # prints at 08:30, before the 09:30 open, so for an rth_only strategy
        # the news dimension was conditioning on four FOMC days in a hundred
        # and twenty and nothing else.
        events = [e for e in project_events(start, end, symbol=self.symbol)
                  if e.impact.rank >= Impact.HIGH.rank]
        out: List[Tuple[float, float, bool]] = []
        if not events:
            return [(float("inf"), float("inf"), False)] * len(bars)

        # The blackout runs from ``before`` minutes AHEAD of a release to
        # ``after`` minutes past it, matching AccountConfig's two knobs. The
        # sign convention is the trap: ``ts - e.when`` is negative before the
        # event, so the window is [-before, +after] and not [-after, +before].
        # Written the wrong way round it lets a strategy initiate into the
        # 08:30 print - exactly what the filter exists to stop - and stands it
        # aside for twice as long afterwards as the risk config says.
        before = float(NEWS_BLACKOUT_BEFORE_MIN)
        after = float(NEWS_BLACKOUT_AFTER_MIN)
        k = 0
        for bar in bars:
            ts = bar.ts
            while k < len(events) - 1 and events[k].when < ts - timedelta(minutes=after):
                k += 1
            local = events[max(0, k - 1):k + 2]
            ahead = [e for e in local if e.when >= ts]
            behind = [e for e in local if e.when <= ts]
            minutes = ((ahead[0].when - ts).total_seconds() / 60.0
                       if ahead else float("inf"))
            since = ((ts - behind[-1].when).total_seconds() / 60.0
                     if behind else float("inf"))
            blackout = any(
                -before <= (ts - e.when).total_seconds() / 60.0 <= after
                for e in local)
            out.append((minutes, since, blackout))
        return out

    def snapshot(self, base_index: int) -> Optional[FeatureSnapshot]:
        """Full cross-timeframe snapshot at ``base_index``."""
        bars = self.base.bars
        if not bars:
            return None
        i = min(max(0, base_index), len(bars) - 1)
        bar = bars[i]

        tfs: Dict[int, TFSnapshot] = {}
        for tf, frame in self.frames.items():
            idx = self.tf_index(i, tf)
            if idx < 0:
                continue
            snap = frame.snapshot(idx)
            if snap is not None:
                tfs[tf] = snap

        lv, orr = self._session_state[i]
        return FeatureSnapshot(
            symbol=self.symbol, ts=bar.ts, base_index=i, price=bar.close,
            spec=self.spec, tfs=tfs, regime=self.regime_at(i),
            session=classify_session(bar.ts), time_bucket=time_bucket(bar.ts, 30),
            day_of_week=day_of_week_name(bar.ts),
            minutes_since_open=minutes_since_open(bar.ts, self.spec.rth_open),
            is_rth=is_rth(bar.ts, self.spec.rth_open, self.spec.rth_close),
            session_levels=lv, opening_range=orr, trading_day=trading_day(bar.ts),
            minutes_to_high_impact=(self._news[i][0] if i < len(self._news)
                                    else float("inf")),
            minutes_since_high_impact=(self._news[i][1] if i < len(self._news)
                                       else float("inf")),
            in_news_blackout=(self._news[i][2] if i < len(self._news) else False),
        )

    def iter_snapshots(self, start: int = 0, end: Optional[int] = None,
                       step: int = 1) -> Iterable[FeatureSnapshot]:
        stop = len(self.base) if end is None else min(end, len(self.base))
        for i in range(max(0, start), stop, max(1, step)):
            snap = self.snapshot(i)
            if snap is not None:
                yield snap

    def __repr__(self) -> str:
        tfs = ",".join(tf_label(t) for t in self.timeframes)
        return f"<SymbolFrame {self.symbol} [{tfs}] bars={len(self.base)}>"


def build_symbol_frame(series: BarSeries, timeframes: Sequence[int],
                       spec: Optional[ContractSpec] = None) -> SymbolFrame:
    return SymbolFrame(series, timeframes, spec)
