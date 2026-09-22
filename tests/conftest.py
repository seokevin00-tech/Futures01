"""Shared fixtures for the deterministic-core test suite.

Everything here is deliberately *small*. The invariants these tests pin down
(look-ahead, alignment lag, fill pessimism, sizing caps) are structural, not
statistical, so they are provable on a few hundred synthetic bars. A 120-day
series would make the suite slow without making any assertion stronger.

Two construction helpers carry most of the weight:

* :func:`make_series` - a deterministic pseudo-random 1-minute series. Same
  seed, same bars, on every machine: no ``numpy``, no clock, no feed.
* :func:`bars_from_ohlc` - an *exact* series built from hand-written OHLC
  tuples. The backtest tests need bars whose ranges contain a specific stop and
  a specific target, which random data cannot be relied on to produce.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from futures_agents.backtest.costs import CostModel, FillModel, SlippageModel
from futures_agents.config import AccountConfig, get_contract
from futures_agents.data.bars import Bar, BarSeries
from futures_agents.features import SymbolFrame
from futures_agents.risk.account import AccountState, DayState
from futures_agents.risk.manager import RiskManager, TradeProposal
from futures_agents.schema import Direction, HistoricalPerformance
from futures_agents.strategies.base import ExitModel, StopKind, StrategySignal
from futures_agents.timeutil import ET, trading_day

# A Tuesday in March 2026 - inside EDT, well clear of any DST transition, so
# nothing in the suite depends on which side of a clock change it runs on.
SESSION_OPEN = datetime(2026, 3, 17, 9, 30, tzinfo=ET)
FIXED_NOW = datetime(2026, 3, 17, 10, 30, tzinfo=ET)


# --------------------------------------------------------------------------
# Bar construction
# --------------------------------------------------------------------------

def make_bars(n: int, *, start: datetime = SESSION_OPEN, minutes: int = 1,
              seed: int = 11, price: float = 21_000.0,
              tick: float = 0.25) -> List[Bar]:
    """``n`` deterministic OHLCV bars on a tick grid, with order flow."""
    rnd = random.Random(seed)
    out: List[Bar] = []
    p = price

    def snap(x: float) -> float:
        return round(round(x / tick) * tick, 10)

    for i in range(n):
        o = snap(p)
        c = snap(o + rnd.uniform(-6.0, 6.0))
        h = snap(max(o, c) + abs(rnd.uniform(0.0, 4.0)))
        l = snap(min(o, c) - abs(rnd.uniform(0.0, 4.0)))
        vol = 100.0 + rnd.randint(0, 120)
        ask = round(vol * rnd.uniform(0.35, 0.65), 2)
        out.append(Bar(ts=start + timedelta(minutes=i * minutes), open=o, high=h,
                       low=l, close=c, volume=vol, minutes=minutes,
                       bid_volume=round(vol - ask, 2), ask_volume=ask))
        p = c
    return out


def make_series(n: int = 390, *, symbol: str = "MNQ", minutes: int = 1,
                start: datetime = SESSION_OPEN, seed: int = 11) -> BarSeries:
    """A deterministic :class:`BarSeries`. 390 1m bars = one RTH session."""
    return BarSeries(symbol, minutes,
                     make_bars(n, start=start, minutes=minutes, seed=seed))


def bars_from_ohlc(rows: Sequence[Tuple[float, float, float, float]], *,
                   start: datetime = SESSION_OPEN, minutes: int = 1,
                   volume: float = 500.0) -> List[Bar]:
    """Exact bars from ``(open, high, low, close)`` tuples."""
    return [Bar(ts=start + timedelta(minutes=i * minutes), open=o, high=h,
                low=l, close=c, volume=volume, minutes=minutes)
            for i, (o, h, l, c) in enumerate(rows)]


def frame_from_rows(rows: Sequence[Tuple[float, float, float, float]], *,
                    symbol: str = "MNQ",
                    start: datetime = SESSION_OPEN) -> SymbolFrame:
    """A single-timeframe :class:`SymbolFrame` over hand-written bars."""
    series = BarSeries(symbol, 1, bars_from_ohlc(rows, start=start))
    return SymbolFrame(series, (1,))


# --------------------------------------------------------------------------
# Series / frame fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def series() -> BarSeries:
    """One session of synthetic 1-minute MNQ bars."""
    return make_series(390)


@pytest.fixture(scope="session")
def frame(series: BarSeries) -> SymbolFrame:
    """A multi-timeframe frame: 1m base with 5m / 15m / 30m above it."""
    return SymbolFrame(series, (1, 5, 15, 30))


@pytest.fixture(scope="session")
def closes(series: BarSeries) -> List[float]:
    return series.closes()


@pytest.fixture(scope="session")
def hlc(series: BarSeries) -> Tuple[List[float], List[float], List[float]]:
    return series.highs(), series.lows(), series.closes()


# --------------------------------------------------------------------------
# Account / risk fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def account_config() -> AccountConfig:
    return AccountConfig()


@pytest.fixture
def account(account_config: AccountConfig) -> AccountState:
    """A clean $50,000 account whose day ledger is pinned to ``FIXED_NOW``.

    The day is pinned so that ``RiskManager.assess(..., when=FIXED_NOW)`` does
    not roll the ledger away and silently discard a test's setup.
    """
    state = AccountState(config=account_config, equity=50_000.0)
    state.peak_equity = 50_000.0
    state.day = DayState(day=trading_day(FIXED_NOW), starting_equity=50_000.0)
    return state


@pytest.fixture
def risk(account_config: AccountConfig, account: AccountState) -> RiskManager:
    return RiskManager(account_config, account)


def good_history(**overrides: Any) -> HistoricalPerformance:
    """A track record that clears every quality gate, so a test can knock out
    exactly one variable at a time."""
    base = dict(strategy_id="s1", symbol="MNQ", trades=120, win_rate=0.45,
                avg_win_r=1.9, avg_loss_r=-1.0, profit_factor=1.55,
                expectancy_r=0.22, max_drawdown_r=6.0, out_of_sample_trades=40,
                out_of_sample_expectancy_r=0.18, walk_forward_efficiency=0.8,
                robustness_score=0.72, sample_is_sufficient=True)
    base.update(overrides)
    return HistoricalPerformance(**base)


def good_proposal(**overrides: Any) -> TradeProposal:
    """An MNQ long that every gate in :meth:`RiskManager.assess` approves.

    Entry 21000 / stop 20990 -> 10 points = 40 ticks, comfortably outside MNQ's
    16-tick noise floor; target 21020 -> 2.0 R/R, above the 1.6 floor.
    """
    base: Dict[str, Any] = dict(
        symbol="MNQ", direction=Direction.LONG, entry=21_000.0, stop=20_990.0,
        targets=[21_020.0], confidence=0.72, strategy_id="s1",
        strategy_name="stub", timeframe=5, regime="TREND_UP",
        volatility="NORMAL", session="RTH_MORNING",
        historical=good_history(), analyst_agreement=0.8)
    base.update(overrides)
    return TradeProposal(**base)


@pytest.fixture
def proposal() -> TradeProposal:
    return good_proposal()


# --------------------------------------------------------------------------
# Backtest helpers
# --------------------------------------------------------------------------

def zero_cost_model(symbol: str = "MNQ") -> CostModel:
    """Frictionless fills, so a test can assert on *mechanics* alone.

    Slippage and commission are separately exercised by the cost tests; mixing
    them into the fill-location tests would make an off-by-one-tick fill bug
    indistinguishable from a slippage change.
    """
    return CostModel(
        spec=get_contract(symbol),
        slippage=SlippageModel(base_ticks=0.0, stop_order_extra_ticks=0.0,
                               volatility_coefficient=0.0,
                               thin_book_extra_ticks=0.0, news_extra_ticks=0.0),
        fill=FillModel(),
        commission_override=0.0,
    )


def simple_exit(targets_r: Sequence[float] = (2.0,),
                scale_out: Sequence[float] = (1.0,)) -> ExitModel:
    """An exit model with every optional management feature switched off."""
    return ExitModel(stop_kind=StopKind.FIXED_TICKS, stop_mult=1.0,
                     targets_r=tuple(targets_r), scale_out=tuple(scale_out),
                     breakeven_at_r=None, trail_atr_mult=None,
                     time_stop_bars=None, exit_at_session_close=False)


@dataclass
class StubStrategy:
    """A strategy that fires once, at a chosen bar, with chosen levels.

    The real :class:`~futures_agents.strategies.base.Strategy` derives its
    levels from indicator state, which is exactly what these tests must not
    depend on: the engine's fill rules have to be assertable against levels the
    test chose. It satisfies the duck-typed surface the engine actually uses -
    ``strategy_id``/``name``/``group``/``symbol``/``primary_tf``/``exit`` and
    ``evaluate(snapshot, cache)``.
    """

    signal_at: int
    entry: float
    stop: float
    targets: List[float]
    direction: Direction = Direction.LONG
    strategy_id: str = "stub"
    name: str = "Stub"
    group: str = "test"
    symbol: str = "MNQ"
    primary_tf: int = 1
    exit: ExitModel = field(default_factory=simple_exit)

    def evaluate(self, snap, cache: Optional[dict] = None) -> Optional[StrategySignal]:
        if snap.base_index != self.signal_at:
            return None
        return StrategySignal(
            strategy_id=self.strategy_id, strategy_name=self.name,
            group=self.group, symbol=self.symbol, ts=snap.ts,
            bar_index=snap.base_index, direction=self.direction,
            entry=self.entry, stop=self.stop, targets=list(self.targets),
            primary_tf=self.primary_tf, timeframes=[self.primary_tf],
            regime=snap.regime.regime, volatility=snap.regime.volatility,
            session=snap.session, time_bucket=snap.time_bucket,
            day_of_week=snap.day_of_week,
        )
