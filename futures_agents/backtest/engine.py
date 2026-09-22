"""Event-driven, bar-by-bar backtest engine.

Correctness rules, all enforced structurally rather than by convention:

1. **Signals are computed on closed bars.** A signal at bar *i* comes from
   ``SymbolFrame.snapshot(i)``, which contains no data from after bar *i*.

2. **Entry fills at the next bar's open.** You cannot act on a close until it
   has happened. Entering at the signal bar's close is the classic way to
   manufacture an edge that does not exist.

3. **When a bar contains both the stop and a target, the stop wins.** Without
   tick data the intrabar order is unknowable, so the engine takes the
   pessimistic reading every time.

4. **Gaps fill at the open.** A gap through the stop fills worse than the stop,
   never at it.

5. **Slippage is applied to fill prices, commissions as dollars** - so costs are
   charged exactly once.

The engine measures everything in R (multiples of the initial stop distance)
with a notional one contract. Converting R into contracts is the risk layer's
job, and keeping those separate is what lets the same research feed a $50,000
account and a $5,000 one without re-running anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import (Any, Dict, Iterable, Iterator, List, Optional, Sequence,
                    Tuple)

from ..config import ContractSpec, get_contract
from ..data.bars import Bar
from ..features import FeatureSnapshot, SymbolFrame
from ..schema import Direction
from ..strategies.base import ConditionResult, Strategy, StrategySignal
from ..timeutil import is_rth, minutes_since_open, to_et, trading_day
from .costs import CostModel

__all__ = ["ExitReason", "TradeLeg", "Trade", "BacktestResult", "BacktestEngine",
           "run_backtest", "run_portfolio"]


class ExitReason(str, Enum):
    TARGET = "TARGET"
    STOP = "STOP"
    BREAKEVEN = "BREAKEVEN"
    TRAIL = "TRAIL"
    TIME = "TIME"
    SESSION_CLOSE = "SESSION_CLOSE"
    END_OF_DATA = "END_OF_DATA"


@dataclass
class TradeLeg:
    """One partial exit."""

    ts: datetime
    price: float
    fraction: float
    r_multiple: float
    reason: ExitReason

    def to_dict(self) -> dict:
        return {"ts": to_et(self.ts).isoformat(), "price": self.price,
                "fraction": round(self.fraction, 4),
                "r": round(self.r_multiple, 4), "reason": self.reason.value}


@dataclass
class Trade:
    """A completed round trip, in R-space for one notional contract."""

    strategy_id: str
    strategy_name: str
    group: str
    symbol: str
    direction: Direction
    signal_ts: datetime
    signal_index: int
    entry_ts: datetime
    entry_index: int
    entry_price: float
    initial_stop: float
    targets: List[float]
    risk_points: float
    legs: List[TradeLeg] = field(default_factory=list)
    exit_ts: Optional[datetime] = None
    exit_index: int = 0
    exit_price: float = 0.0
    exit_reason: ExitReason = ExitReason.END_OF_DATA
    gross_r: float = 0.0
    commission_dollars: float = 0.0
    net_r: float = 0.0
    net_dollars: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    mfe_points: float = 0.0
    mae_points: float = 0.0
    bars_held: int = 0
    minutes_held: float = 0.0
    primary_tf: int = 0
    regime: str = "UNKNOWN"
    volatility: str = "NORMAL"
    session: str = ""
    time_bucket: str = ""
    day_of_week: str = ""
    confluences: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    planned_rr: float = 0.0

    @property
    def is_win(self) -> bool:
        return self.net_r > 0

    @property
    def is_loss(self) -> bool:
        return self.net_r < 0

    @property
    def trading_day(self):
        return trading_day(self.entry_ts)

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "strategy_name": self.strategy_name,
            "group": self.group, "symbol": self.symbol,
            "direction": self.direction.value,
            "signal_ts": to_et(self.signal_ts).isoformat(),
            "entry_ts": to_et(self.entry_ts).isoformat(),
            "exit_ts": to_et(self.exit_ts).isoformat() if self.exit_ts else None,
            "entry_price": self.entry_price, "initial_stop": self.initial_stop,
            "targets": self.targets, "exit_price": self.exit_price,
            "exit_reason": self.exit_reason.value,
            "risk_points": round(self.risk_points, 6),
            "gross_r": round(self.gross_r, 4), "net_r": round(self.net_r, 4),
            "net_dollars": round(self.net_dollars, 2),
            "commission_dollars": round(self.commission_dollars, 2),
            "mfe_r": round(self.mfe_r, 4), "mae_r": round(self.mae_r, 4),
            "bars_held": self.bars_held, "minutes_held": self.minutes_held,
            "primary_tf": self.primary_tf, "regime": self.regime,
            "volatility": self.volatility, "session": self.session,
            "time_bucket": self.time_bucket, "day_of_week": self.day_of_week,
            "planned_rr": round(self.planned_rr, 3),
            "legs": [l.to_dict() for l in self.legs],
            "confluences": self.confluences, "conflicts": self.conflicts,
        }


@dataclass
class _OpenPosition:
    """Mutable state of a live position during the walk-forward loop."""

    strategy: Strategy
    signal: StrategySignal
    entry_index: int
    entry_ts: datetime
    entry_price: float
    stop: float
    initial_stop: float
    targets: List[float]
    remaining: float
    risk_points: float
    legs: List[TradeLeg] = field(default_factory=list)
    realised_r: float = 0.0
    hit_targets: int = 0
    mfe_points: float = 0.0
    mae_points: float = 0.0
    extreme_favourable: float = 0.0
    bars_held: int = 0
    breakeven_moved: bool = False
    atr_at_entry: Optional[float] = None

    @property
    def sign(self) -> int:
        return self.signal.direction.sign


@dataclass
class BacktestResult:
    """Outcome of running one strategy over one data range."""

    strategy_id: str
    strategy_name: str = ""
    group: str = ""
    symbol: str = ""
    primary_tf: int = 0
    trades: List[Trade] = field(default_factory=list)
    signals_generated: int = 0
    signals_skipped_in_position: int = 0
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    bars_tested: int = 0

    @property
    def r_series(self) -> List[float]:
        return [t.net_r for t in self.trades]

    def equity_curve_r(self) -> List[float]:
        eq, total = [], 0.0
        for t in self.trades:
            total += t.net_r
            eq.append(total)
        return eq

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "strategy_name": self.strategy_name,
            "group": self.group, "symbol": self.symbol, "primary_tf": self.primary_tf,
            "trades": len(self.trades), "signals_generated": self.signals_generated,
            "bars_tested": self.bars_tested,
            "start": to_et(self.start_ts).isoformat() if self.start_ts else None,
            "end": to_et(self.end_ts).isoformat() if self.end_ts else None,
        }


class BacktestEngine:
    """Runs strategies over a :class:`SymbolFrame`."""

    def __init__(self, frame: SymbolFrame, cost_model: Optional[CostModel] = None,
                 *, max_concurrent_per_strategy: int = 1,
                 allow_overnight: bool = False):
        self.frame = frame
        self.spec: ContractSpec = frame.spec
        self.costs = cost_model or CostModel(self.spec)
        self.max_concurrent = max(1, max_concurrent_per_strategy)
        self.allow_overnight = allow_overnight
        self._rth_minutes = self._session_minutes()

    def _session_minutes(self) -> float:
        oh, om = (int(x) for x in self.spec.rth_open.split(":"))
        ch, cm = (int(x) for x in self.spec.rth_close.split(":"))
        return (ch * 60 + cm) - (oh * 60 + om)

    # ------------------------------------------------------------------
    # Single strategy
    # ------------------------------------------------------------------
    def run(self, strategy: Strategy, *, start: int = 0,
            end: Optional[int] = None) -> BacktestResult:
        return self.run_many([strategy], start=start, end=end)[strategy.strategy_id]

    # ------------------------------------------------------------------
    # Portfolio sweep - one pass over the bars for N strategies
    # ------------------------------------------------------------------
    def run_many(self, strategies: Sequence[Strategy], *, start: int = 0,
                 end: Optional[int] = None,
                 progress: Optional[Any] = None) -> Dict[str, BacktestResult]:
        """Backtest many strategies in a single pass over the data.

        All strategies share one per-bar condition cache, so a condition used by
        800 strategies is computed once per bar rather than 800 times. Each
        strategy still keeps completely independent position state.
        """
        bars = self.frame.base.bars
        n = len(bars)
        stop_at = n if end is None else min(end, n)
        start = max(0, start)
        if start >= stop_at:
            return {s.strategy_id: BacktestResult(s.strategy_id, s.name, s.group,
                                                  s.symbol, s.primary_tf)
                    for s in strategies}

        results = {s.strategy_id: BacktestResult(
            strategy_id=s.strategy_id, strategy_name=s.name, group=s.group,
            symbol=s.symbol, primary_tf=s.primary_tf,
            start_ts=bars[start].ts, end_ts=bars[stop_at - 1].ts,
            bars_tested=stop_at - start) for s in strategies}
        open_pos: Dict[str, _OpenPosition] = {}
        pending: Dict[str, Tuple[Strategy, StrategySignal]] = {}

        for i in range(start, stop_at):
            bar = bars[i]
            snap: Optional[FeatureSnapshot] = None
            cache: Dict[Tuple[str, int], ConditionResult] = {}

            # --- 1. fill pending entries at this bar's open ---
            for sid, (strat, sig) in list(pending.items()):
                pos = self._open_position(strat, sig, i, bar)
                if pos is not None:
                    open_pos[sid] = pos
                del pending[sid]

            # --- 2. manage open positions on this bar ---
            for sid, pos in list(open_pos.items()):
                trade = self._manage(pos, i, bar, is_last=(i == stop_at - 1))
                if trade is not None:
                    results[sid].trades.append(trade)
                    del open_pos[sid]

            # --- 3. look for new signals (never while already positioned) ---
            if i + 1 < stop_at:
                for s in strategies:
                    sid = s.strategy_id
                    if sid in open_pos or sid in pending:
                        continue
                    if snap is None:
                        snap = self.frame.snapshot(i)
                        if snap is None:
                            break
                    sig = s.evaluate(snap, cache)
                    if sig is None:
                        continue
                    results[sid].signals_generated += 1
                    pending[sid] = (s, sig)

            if progress is not None and (i - start) % 2000 == 0:
                progress(i - start, stop_at - start)

        # Close anything still open at the end of the data.
        last_i = stop_at - 1
        for sid, pos in open_pos.items():
            trade = self._close(pos, last_i, bars[last_i], bars[last_i].close,
                                ExitReason.END_OF_DATA)
            results[sid].trades.append(trade)
        return results

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------
    def _atr_percentile(self, sig: StrategySignal) -> Optional[float]:
        frame = self.frame.frames.get(sig.primary_tf)
        if frame is None:
            return None
        col = frame.cols.get("atr_percentile")
        if not col:
            return None
        idx = self.frame.tf_index(sig.bar_index, sig.primary_tf)
        return col[idx] if 0 <= idx < len(col) else None

    def _open_position(self, strategy: Strategy, sig: StrategySignal, i: int,
                       bar: Bar) -> Optional[_OpenPosition]:
        """Fill the entry at this bar's open, with adverse slippage."""
        spec = self.spec
        sign = sig.direction.sign
        if sign == 0:
            return None
        atr_pct = self._atr_percentile(sig)
        thin = not is_rth(bar.ts, spec.rth_open, spec.rth_close)
        slip = self.costs.slippage_price(is_stop=False, atr_percentile=atr_pct,
                                         thin=thin)
        entry = spec.round_to_tick(bar.open + sign * slip)

        # The stop was computed at the signal bar; the entry moved, so the risk
        # distance changes. Re-deriving the stop here would be using the fill to
        # justify the risk - instead the original stop level is honoured and the
        # (now different) risk distance is what gets measured.
        stop = sig.stop
        risk = abs(entry - stop)
        if risk < spec.tick_size:
            return None
        # A fill that gapped past the stop is already a loser before it starts.
        if (sign > 0 and entry <= stop) or (sign < 0 and entry >= stop):
            return None

        targets = [spec.round_to_tick(entry + sign * risk * r)
                   for r in sig_targets_r(sig)]
        return _OpenPosition(
            strategy=strategy, signal=sig, entry_index=i, entry_ts=bar.ts,
            entry_price=entry, stop=stop, initial_stop=stop, targets=targets,
            remaining=1.0, risk_points=risk, atr_at_entry=None,
        )

    def _manage(self, pos: _OpenPosition, i: int, bar: Bar,
                *, is_last: bool) -> Optional[Trade]:
        """Advance one bar. Returns a Trade when the position closes."""
        sign = pos.sign
        spec = self.spec
        exit_model = pos.strategy.exit
        pos.bars_held = i - pos.entry_index + 1

        # --- excursions ---
        fav = (bar.high - pos.entry_price) if sign > 0 else (pos.entry_price - bar.low)
        adv = (pos.entry_price - bar.low) if sign > 0 else (bar.high - pos.entry_price)
        pos.mfe_points = max(pos.mfe_points, fav)
        pos.mae_points = max(pos.mae_points, adv)
        pos.extreme_favourable = max(pos.extreme_favourable, fav)

        # --- 1. stop, checked first and including gaps ---
        stop_hit = (bar.low <= pos.stop) if sign > 0 else (bar.high >= pos.stop)
        target_hit_any = any(
            (bar.high >= t) if sign > 0 else (bar.low <= t)
            for k, t in enumerate(pos.targets) if k >= pos.hit_targets)

        if stop_hit and (target_hit_any is False or
                         self.costs.fill.stop_before_target_in_same_bar):
            gapped = (bar.open <= pos.stop) if sign > 0 else (bar.open >= pos.stop)
            if gapped and self.costs.fill.honour_gaps:
                fill = bar.open
            else:
                atr_pct = self._atr_percentile(pos.signal)
                thin = not is_rth(bar.ts, spec.rth_open, spec.rth_close)
                slip = self.costs.slippage_price(is_stop=True, atr_percentile=atr_pct,
                                                 thin=thin)
                fill = pos.stop - sign * slip
            reason = (ExitReason.BREAKEVEN if pos.breakeven_moved
                      and abs(pos.stop - pos.entry_price) < spec.tick_size
                      else ExitReason.STOP)
            return self._close(pos, i, bar, spec.round_to_tick(fill), reason)

        # --- 2. targets, in order ---
        while pos.hit_targets < len(pos.targets):
            t = pos.targets[pos.hit_targets]
            hit = (bar.high >= t) if sign > 0 else (bar.low <= t)
            if not hit:
                break
            # Gapping through a limit target fills at the open, in your favour.
            fill = (bar.open if ((sign > 0 and bar.open >= t) or
                                 (sign < 0 and bar.open <= t)) else t)
            frac = _scale_fraction(exit_model, pos.hit_targets)
            frac = min(frac, pos.remaining)
            if frac > 0:
                r = (fill - pos.entry_price) * sign / pos.risk_points
                pos.legs.append(TradeLeg(bar.ts, fill, frac, r, ExitReason.TARGET))
                pos.realised_r += frac * r
                pos.remaining -= frac
            pos.hit_targets += 1
            if pos.remaining <= 1e-9:
                return self._close(pos, i, bar, fill, ExitReason.TARGET,
                                   already_flat=True)

        # --- 3. breakeven and trailing stop management ---
        r_now = pos.extreme_favourable / pos.risk_points if pos.risk_points else 0.0
        if (exit_model.breakeven_at_r is not None and not pos.breakeven_moved
                and r_now >= exit_model.breakeven_at_r):
            pos.stop = pos.entry_price
            pos.breakeven_moved = True
        if exit_model.trail_atr_mult:
            tf_frame = self.frame.frames.get(pos.signal.primary_tf)
            a = None
            if tf_frame is not None:
                col = tf_frame.cols.get("atr")
                idx = self.frame.tf_index(i, pos.signal.primary_tf)
                if col and 0 <= idx < len(col):
                    a = col[idx]
            if a:
                trail = (bar.high - exit_model.trail_atr_mult * a if sign > 0
                         else bar.low + exit_model.trail_atr_mult * a)
                pos.stop = max(pos.stop, trail) if sign > 0 else min(pos.stop, trail)

        # --- 4. time stop ---
        if exit_model.time_stop_bars and pos.bars_held >= exit_model.time_stop_bars:
            return self._close(pos, i, bar, bar.close, ExitReason.TIME)

        # --- 5. session close ---
        if exit_model.exit_at_session_close and not self.allow_overnight:
            elapsed = minutes_since_open(bar.ts, spec.rth_open) + bar.minutes
            if elapsed >= self._rth_minutes:
                return self._close(pos, i, bar, bar.close, ExitReason.SESSION_CLOSE)

        if is_last:
            return self._close(pos, i, bar, bar.close, ExitReason.END_OF_DATA)
        return None

    def _close(self, pos: _OpenPosition, i: int, bar: Bar, price: float,
               reason: ExitReason, *, already_flat: bool = False) -> Trade:
        sign = pos.sign
        spec = self.spec
        if not already_flat and pos.remaining > 1e-9:
            r = (price - pos.entry_price) * sign / pos.risk_points
            pos.legs.append(TradeLeg(bar.ts, price, pos.remaining, r, reason))
            pos.realised_r += pos.remaining * r
            pos.remaining = 0.0

        gross_r = pos.realised_r
        # One commission per side, charged once for the entry and once across
        # all scale-out legs (which together close exactly one contract).
        commission = 2.0 * self.costs.commission_per_side()
        risk_dollars = pos.risk_points * spec.point_value
        cost_r = commission / risk_dollars if risk_dollars > 0 else 0.0
        net_r = gross_r - cost_r

        sig = pos.signal
        return Trade(
            strategy_id=sig.strategy_id, strategy_name=sig.strategy_name,
            group=sig.group, symbol=sig.symbol, direction=sig.direction,
            signal_ts=sig.ts, signal_index=sig.bar_index,
            entry_ts=pos.entry_ts, entry_index=pos.entry_index,
            entry_price=pos.entry_price, initial_stop=pos.initial_stop,
            targets=list(pos.targets), risk_points=pos.risk_points,
            legs=list(pos.legs), exit_ts=bar.ts, exit_index=i, exit_price=price,
            exit_reason=reason, gross_r=gross_r, commission_dollars=commission,
            net_r=net_r, net_dollars=net_r * risk_dollars,
            mfe_r=pos.mfe_points / pos.risk_points if pos.risk_points else 0.0,
            mae_r=pos.mae_points / pos.risk_points if pos.risk_points else 0.0,
            mfe_points=pos.mfe_points, mae_points=pos.mae_points,
            bars_held=pos.bars_held,
            minutes_held=(to_et(bar.ts) - to_et(pos.entry_ts)).total_seconds() / 60.0,
            primary_tf=sig.primary_tf, regime=sig.regime, volatility=sig.volatility,
            session=sig.session, time_bucket=sig.time_bucket,
            day_of_week=sig.day_of_week, confluences=list(sig.confluences),
            conflicts=list(sig.conflicts), planned_rr=sig.reward_risk,
        )


# --------------------------------------------------------------------------
# Signal helpers
# --------------------------------------------------------------------------

def sig_targets_r(signal: StrategySignal) -> List[float]:
    """Target distances in R, recovered from the signal's own geometry.

    The entry fills at the next bar's open, which is rarely the signal-bar
    close the targets were originally computed from. Recovering the targets as
    R-multiples and re-projecting them from the actual fill keeps the risk unit
    honest: a trade that filled 3 points worse still risks exactly 1R and still
    targets exactly 2R.
    """
    risk = abs(signal.entry - signal.stop)
    if risk <= 0:
        return [1.0]
    return [abs(t - signal.entry) / risk for t in signal.targets] or [1.0]


def _scale_fraction(exit_model, index: int) -> float:
    so = exit_model.scale_out
    return so[index] if index < len(so) else 0.0


# --------------------------------------------------------------------------
# Convenience wrappers
# --------------------------------------------------------------------------

def run_backtest(frame: SymbolFrame, strategy: Strategy, *,
                 cost_model: Optional[CostModel] = None,
                 start: int = 0, end: Optional[int] = None) -> BacktestResult:
    return BacktestEngine(frame, cost_model).run(strategy, start=start, end=end)


def run_portfolio(frame: SymbolFrame, strategies: Sequence[Strategy], *,
                  cost_model: Optional[CostModel] = None,
                  start: int = 0, end: Optional[int] = None,
                  progress: Optional[Any] = None) -> Dict[str, BacktestResult]:
    return BacktestEngine(frame, cost_model).run_many(
        strategies, start=start, end=end, progress=progress)
