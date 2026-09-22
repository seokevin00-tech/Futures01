"""Backtest execution realism and metric arithmetic.

Every assertion here is about one of the four ways a backtest flatters itself:

1. filling the entry at the signal bar's close instead of the next bar's open,
2. resolving a bar that contains both the stop and a target in the trade's
   favour,
3. filling a gap *at* the level instead of through it,
4. charging costs once, or not at all.

Bars are hand-written rather than generated: the point is to construct a bar
whose range contains a specific stop and a specific target, which random data
cannot be relied upon to do.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import List

import pytest

from futures_agents.backtest.costs import CostModel, FillModel, SlippageModel
from futures_agents.backtest.engine import (BacktestEngine, ExitReason, Trade,
                                            run_backtest)
from futures_agents.backtest.metrics import (compute_metrics, drawdown_series,
                                             max_drawdown, slice_metrics)
from futures_agents.config import get_contract
from futures_agents.schema import Direction

from conftest import (SESSION_OPEN, StubStrategy, frame_from_rows, simple_exit,
                      zero_cost_model)

# A quiet approach, a signal bar, then bars the test tailors per scenario.
LEAD_IN = [
    (21_000.0, 21_005.0, 20_995.0, 21_000.0),   # 0
    (21_000.0, 21_005.0, 20_995.0, 21_000.0),   # 1
    (21_000.0, 21_005.0, 20_995.0, 21_002.0),   # 2  <- signal bar
    (21_010.0, 21_012.0, 21_008.0, 21_010.0),   # 3  <- entry bar, opens 21010
]
SIGNAL_BAR = 2
ENTRY_BAR = 3
ENTRY_OPEN = 21_010.0
STOP = 21_000.0
TARGET_2R = 21_030.0


def run(rows, strategy, costs=None):
    frame = frame_from_rows(rows)
    engine = BacktestEngine(frame, costs or zero_cost_model())
    return engine.run(strategy), frame


def long_stub(**kw) -> StubStrategy:
    params = dict(signal_at=SIGNAL_BAR, entry=ENTRY_OPEN, stop=STOP,
                  targets=[TARGET_2R], direction=Direction.LONG)
    params.update(kw)
    return StubStrategy(**params)


# --------------------------------------------------------------------------
# 1. Entry fills at the NEXT bar's open
# --------------------------------------------------------------------------

def test_entry_fills_at_the_next_bars_open_not_the_signal_close():
    rows = LEAD_IN + [
        (21_010.0, 21_035.0, 21_008.0, 21_032.0),   # 4 target
        (21_030.0, 21_032.0, 21_028.0, 21_030.0),   # 5
    ]
    result, frame = run(rows, long_stub())
    assert len(result.trades) == 1
    t = result.trades[0]

    assert t.signal_index == SIGNAL_BAR
    assert t.entry_index == ENTRY_BAR
    assert t.entry_index > t.signal_index, "entry must be strictly after the signal"
    assert t.entry_price == pytest.approx(ENTRY_OPEN)
    assert t.entry_price == pytest.approx(frame.base.bars[ENTRY_BAR].open)
    assert t.entry_price != pytest.approx(frame.base.bars[SIGNAL_BAR].close)
    assert t.entry_ts == frame.base.bars[ENTRY_BAR].ts


def test_entry_index_is_after_the_signal_for_every_signal_bar():
    """Swept, so a bug that only bites at the first or last bar is caught."""
    rows = LEAD_IN + [
        (21_010.0, 21_014.0, 21_006.0, 21_012.0),
        (21_012.0, 21_016.0, 21_008.0, 21_014.0),
        (21_014.0, 21_018.0, 21_010.0, 21_016.0),
        (21_016.0, 21_020.0, 21_012.0, 21_018.0),
    ]
    taken = 0
    for signal_at in range(0, len(rows) - 1):
        entry_open = rows[signal_at + 1][0]
        strategy = long_stub(signal_at=signal_at, entry=entry_open,
                             stop=entry_open - 10.0,
                             targets=[entry_open + 20.0])
        result, _ = run(rows, strategy)
        assert result.trades, f"no trade taken for a signal at bar {signal_at}"
        t = result.trades[0]
        assert t.entry_index == t.signal_index + 1
        assert t.entry_price == pytest.approx(entry_open)
        taken += 1
    assert taken == len(rows) - 1, "the sweep must not be vacuous"


def test_no_signal_is_taken_on_the_last_bar():
    """There is no next bar to fill against, so it must not become a trade."""
    rows = LEAD_IN
    result, _ = run(rows, long_stub(signal_at=len(rows) - 1))
    assert result.trades == []
    assert result.signals_generated == 0


# --------------------------------------------------------------------------
# 2. Risk is strictly positive
# --------------------------------------------------------------------------

@pytest.mark.parametrize("direction,stop,targets", [
    (Direction.LONG, STOP, [TARGET_2R]),
    (Direction.SHORT, 21_020.0, [20_990.0]),
])
def test_risk_is_strictly_positive_on_every_trade(direction, stop, targets):
    rows = LEAD_IN + [
        (21_010.0, 21_035.0, 20_985.0, 21_000.0),
        (21_000.0, 21_004.0, 20_996.0, 21_000.0),
    ]
    result, _ = run(rows, long_stub(direction=direction, stop=stop,
                                    targets=targets))
    assert result.trades
    for t in result.trades:
        assert t.risk_points > 0.0
        assert t.risk_points == pytest.approx(abs(t.entry_price - t.initial_stop))
        assert t.initial_stop != t.entry_price


def test_a_stop_closer_than_one_tick_produces_no_trade():
    """Risk below a tick is not risk, it is a rounding artefact."""
    rows = LEAD_IN + [(21_010.0, 21_012.0, 21_008.0, 21_010.0)]
    strategy = long_stub(stop=ENTRY_OPEN - 0.05, targets=[ENTRY_OPEN + 1.0])
    result, _ = run(rows, strategy)
    assert result.signals_generated == 1
    assert result.trades == []


def test_an_entry_that_gapped_past_its_stop_is_not_taken():
    """The trade was already a loser before it opened; taking it would book a
    fictitious negative-risk position."""
    rows = [
        (21_000.0, 21_005.0, 20_995.0, 21_000.0),
        (21_000.0, 21_005.0, 20_995.0, 21_000.0),
        (21_000.0, 21_005.0, 20_995.0, 21_002.0),
        (20_980.0, 20_985.0, 20_975.0, 20_980.0),   # opens below the stop
        (20_980.0, 20_985.0, 20_975.0, 20_980.0),
    ]
    strategy = long_stub(entry=20_980.0, stop=21_000.0, targets=[21_040.0])
    result, _ = run(rows, strategy)
    assert result.signals_generated == 1
    assert result.trades == []


# --------------------------------------------------------------------------
# 3. Stop before target when one bar contains both
# --------------------------------------------------------------------------

def test_a_bar_containing_both_levels_resolves_as_the_stop():
    """The pessimistic reading. Without tick data the intrabar order is
    unknowable, and assuming the target came first manufactures an edge."""
    both = (21_010.0, 21_035.0, 20_995.0, 21_030.0)   # high >= 21030, low <= 21000
    rows = LEAD_IN + [both, (21_000.0, 21_004.0, 20_996.0, 21_000.0)]
    result, _ = run(rows, long_stub())
    assert len(result.trades) == 1
    t = result.trades[0]

    assert both[1] >= TARGET_2R and both[2] <= STOP, "the bar must contain both"
    assert t.exit_reason is ExitReason.STOP
    assert t.exit_price == pytest.approx(STOP)
    assert t.gross_r == pytest.approx(-1.0)
    assert t.net_r < 0
    assert t.legs and t.legs[-1].reason is ExitReason.STOP


def test_a_bar_containing_both_levels_resolves_as_the_stop_for_a_short():
    both = (21_010.0, 21_035.0, 20_985.0, 20_990.0)
    rows = LEAD_IN + [both, (21_000.0, 21_004.0, 20_996.0, 21_000.0)]
    strategy = long_stub(direction=Direction.SHORT, stop=21_020.0,
                         targets=[20_990.0])
    result, _ = run(rows, strategy)
    t = result.trades[0]
    assert both[1] >= 21_020.0 and both[2] <= 20_990.0
    assert t.exit_reason is ExitReason.STOP
    assert t.gross_r == pytest.approx(-1.0)


def test_the_target_still_wins_when_the_stop_is_untouched():
    """The pessimism must be conditional, not a blanket loss."""
    rows = LEAD_IN + [
        (21_010.0, 21_035.0, 21_009.0, 21_032.0),
        (21_030.0, 21_032.0, 21_028.0, 21_030.0),
    ]
    result, _ = run(rows, long_stub())
    t = result.trades[0]
    assert t.exit_reason is ExitReason.TARGET
    assert t.exit_price == pytest.approx(TARGET_2R)
    assert t.gross_r == pytest.approx(2.0)


def test_stop_before_target_can_be_turned_off_only_deliberately():
    """The flag exists for sensitivity analysis; the default must be pessimistic."""
    assert FillModel().stop_before_target_in_same_bar is True
    assert FillModel().entry_on_next_open is True
    assert FillModel().honour_gaps is True

    optimistic = CostModel(
        spec=get_contract("MNQ"),
        slippage=SlippageModel(0.0, 0.0, 0.0, 0.0, 0.0),
        fill=FillModel(stop_before_target_in_same_bar=False),
        commission_override=0.0)
    rows = LEAD_IN + [(21_010.0, 21_035.0, 20_995.0, 21_030.0),
                      (21_000.0, 21_004.0, 20_996.0, 21_000.0)]
    result, _ = run(rows, long_stub(), costs=optimistic)
    assert result.trades[0].exit_reason is ExitReason.TARGET


# --------------------------------------------------------------------------
# 4. Gaps fill at the open, worse than the level
# --------------------------------------------------------------------------

def test_a_gap_through_the_stop_fills_at_the_open_and_worse_than_the_stop():
    gap = (20_980.0, 20_990.0, 20_970.0, 20_985.0)   # opens below the stop
    rows = LEAD_IN + [gap, (20_985.0, 20_990.0, 20_980.0, 20_985.0)]
    result, _ = run(rows, long_stub())
    t = result.trades[0]

    assert t.exit_reason is ExitReason.STOP
    assert t.exit_price == pytest.approx(gap[0])
    assert t.exit_price < STOP, "a gap must not fill at the stop price"
    assert t.gross_r == pytest.approx((gap[0] - ENTRY_OPEN) / 10.0)
    assert t.gross_r < -1.0, "a gapped stop must lose more than 1R"


def test_a_gap_through_a_short_stop_fills_above_it():
    gap = (21_040.0, 21_050.0, 21_035.0, 21_045.0)   # opens above the stop
    rows = LEAD_IN + [gap, (21_045.0, 21_050.0, 21_040.0, 21_045.0)]
    strategy = long_stub(direction=Direction.SHORT, stop=21_020.0,
                         targets=[20_990.0])
    result, _ = run(rows, strategy)
    t = result.trades[0]
    assert t.exit_price == pytest.approx(gap[0])
    assert t.exit_price > 21_020.0
    assert t.gross_r < -1.0


def test_a_gap_through_the_target_fills_at_the_open_in_your_favour():
    gap = (21_040.0, 21_045.0, 21_038.0, 21_042.0)   # opens above the target
    rows = LEAD_IN + [gap, (21_042.0, 21_046.0, 21_038.0, 21_042.0)]
    result, _ = run(rows, long_stub())
    t = result.trades[0]
    assert t.exit_reason is ExitReason.TARGET
    assert t.exit_price == pytest.approx(gap[0])
    assert t.gross_r == pytest.approx(3.0)


# --------------------------------------------------------------------------
# 5. Costs are charged exactly once
# --------------------------------------------------------------------------

def _winner_rows():
    return LEAD_IN + [(21_010.0, 21_035.0, 21_009.0, 21_032.0),
                      (21_030.0, 21_032.0, 21_028.0, 21_030.0)]


def test_commission_is_two_sides_and_reduces_net_r_below_gross_r():
    costs = CostModel(spec=get_contract("MNQ"),
                      slippage=SlippageModel(0.0, 0.0, 0.0, 0.0, 0.0),
                      commission_override=1.0)
    result, _ = run(_winner_rows(), long_stub(), costs=costs)
    t = result.trades[0]

    risk_dollars = t.risk_points * get_contract("MNQ").point_value   # 10 * 2 = 20
    assert t.commission_dollars == pytest.approx(2.0)                # one per side
    assert t.gross_r == pytest.approx(2.0)
    assert t.net_r == pytest.approx(2.0 - 2.0 / risk_dollars)
    assert t.net_r < t.gross_r
    assert t.net_dollars == pytest.approx(t.net_r * risk_dollars)


def test_a_scaled_out_trade_is_still_charged_exactly_one_round_turn():
    """Three partial exits close one contract, not three."""
    costs = CostModel(spec=get_contract("MNQ"),
                      slippage=SlippageModel(0.0, 0.0, 0.0, 0.0, 0.0),
                      commission_override=1.0)
    strategy = long_stub(targets=[21_020.0, 21_030.0],
                         exit=simple_exit(targets_r=(1.0, 2.0),
                                          scale_out=(0.5, 0.5)))
    rows = LEAD_IN + [
        (21_010.0, 21_025.0, 21_009.0, 21_022.0),   # first target only
        (21_022.0, 21_035.0, 21_020.0, 21_032.0),   # second target
        (21_030.0, 21_032.0, 21_028.0, 21_030.0),
    ]
    result, _ = run(rows, strategy, costs=costs)
    t = result.trades[0]

    assert len(t.legs) == 2
    assert t.commission_dollars == pytest.approx(2.0), "charged per leg, not per trade"
    assert t.gross_r == pytest.approx(0.5 * 1.0 + 0.5 * 2.0)
    assert t.net_r < t.gross_r


def test_slippage_lands_in_the_fill_price_on_each_side():
    """Slippage is a price offset and commission is dollars - the two must not
    both be charged for the same thing."""
    costs = CostModel(
        spec=get_contract("MNQ"),
        slippage=SlippageModel(base_ticks=1.0, stop_order_extra_ticks=1.0,
                               volatility_coefficient=0.0,
                               thin_book_extra_ticks=0.0, news_extra_ticks=0.0),
        commission_override=0.0)
    rows = LEAD_IN + [(21_010.0, 21_012.0, 20_995.0, 21_000.0),
                      (21_000.0, 21_004.0, 20_996.0, 21_000.0)]
    result, _ = run(rows, long_stub(), costs=costs)
    t = result.trades[0]

    tick = get_contract("MNQ").tick_size
    assert t.entry_price == pytest.approx(ENTRY_OPEN + tick)       # 1 tick adverse
    assert t.exit_price == pytest.approx(STOP - 2 * tick)          # stops slip more
    assert t.commission_dollars == pytest.approx(0.0)
    assert t.gross_r < -1.0, "slippage on both sides must cost more than 1R"


def test_zero_cost_model_leaves_gross_and_net_identical():
    """The control case: with no costs the two must agree exactly, so any later
    divergence is a cost and not an arithmetic slip."""
    result, _ = run(_winner_rows(), long_stub())
    t = result.trades[0]
    assert t.commission_dollars == pytest.approx(0.0)
    assert t.net_r == pytest.approx(t.gross_r)


def test_cost_in_r_scales_inversely_with_stop_distance():
    """A tight stop is a bigger cost haircut - the reason cost realism matters."""
    costs = CostModel(spec=get_contract("MNQ"))
    wide = costs.cost_in_r(20.0)
    tight = costs.cost_in_r(4.0)
    assert tight > wide > 0
    assert tight == pytest.approx(wide * 5.0)


def test_a_stop_moved_on_a_bar_is_not_also_tested_on_that_bar():
    """Ordering invariant. The breakeven trigger on bar 4 must take effect from
    bar 5 onwards; applying the new stop to the bar that moved it would exit on
    a level the position was not yet protected by - look-ahead in miniature."""
    exit_model = simple_exit(targets_r=(3.0,), scale_out=(1.0,))
    exit_model = replace(exit_model, breakeven_at_r=1.0)
    rows = LEAD_IN + [
        # Bar 4 runs to +1.5R (moving the stop to breakeven at 21010) and dips
        # to 21005 - below the new stop, above the old one.
        (21_010.0, 21_025.0, 21_005.0, 21_020.0),
        (21_020.0, 21_022.0, 21_008.0, 21_010.0),   # 5: now the breakeven hits
        (21_010.0, 21_012.0, 21_008.0, 21_010.0),
    ]
    result, _ = run(rows, long_stub(targets=[21_040.0], exit=exit_model))
    t = result.trades[0]

    assert t.exit_index == 5, "the breakeven stop fired on the bar that set it"
    assert t.exit_reason is ExitReason.BREAKEVEN
    assert t.exit_price == pytest.approx(ENTRY_OPEN)
    assert t.gross_r == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_the_same_inputs_produce_the_same_trades():
    rows = _winner_rows()
    a, _ = run(rows, long_stub())
    b, _ = run(rows, long_stub())
    assert [t.to_dict() for t in a.trades] == [t.to_dict() for t in b.trades]


def test_run_backtest_wrapper_matches_the_engine():
    frame = frame_from_rows(_winner_rows())
    direct = BacktestEngine(frame, zero_cost_model()).run(long_stub())
    wrapped = run_backtest(frame, long_stub(), cost_model=zero_cost_model())
    assert [t.to_dict() for t in direct.trades] == [t.to_dict() for t in wrapped.trades]


# --------------------------------------------------------------------------
# 6. Metric arithmetic on a hand-checked R series
# --------------------------------------------------------------------------

R_SERIES = [1.0, -1.0, 2.0, -1.0, -1.0, 3.0]
# equity   1.0   0.0  2.0   1.0   0.0   3.0
# peak     1.0   1.0  2.0   2.0   2.0   3.0
# drawdown 0.0   1.0  0.0   1.0   2.0   0.0


def make_trade(net_r: float, *, direction: Direction = Direction.LONG,
               reason: ExitReason = ExitReason.TARGET, index: int = 0) -> Trade:
    ts = SESSION_OPEN + timedelta(minutes=index)
    return Trade(
        strategy_id="s", strategy_name="s", group="g", symbol="MNQ",
        direction=direction, signal_ts=ts, signal_index=index, entry_ts=ts,
        entry_index=index + 1, entry_price=21_000.0, initial_stop=20_990.0,
        targets=[21_020.0], risk_points=10.0, exit_ts=ts, exit_index=index + 2,
        exit_price=21_010.0, exit_reason=reason, gross_r=net_r,
        commission_dollars=0.0, net_r=net_r, net_dollars=net_r * 20.0,
        bars_held=2, minutes_held=2.0, mfe_r=abs(net_r), mae_r=0.4,
        session="RTH_MORNING", regime="TREND_UP")


@pytest.fixture
def hand_checked_trades() -> List[Trade]:
    return [make_trade(r, index=i) for i, r in enumerate(R_SERIES)]


def test_expectancy_is_total_r_over_trade_count(hand_checked_trades):
    m = compute_metrics(hand_checked_trades)
    assert m.trades == 6
    assert m.total_r == pytest.approx(3.0)
    assert m.expectancy_r == pytest.approx(0.5)
    assert m.is_profitable is True


def test_profit_factor_is_gross_win_over_gross_loss(hand_checked_trades):
    m = compute_metrics(hand_checked_trades)
    assert m.wins == 3 and m.losses == 3 and m.scratches == 0
    assert m.win_rate == pytest.approx(0.5)
    assert m.avg_win_r == pytest.approx(2.0)     # mean(1, 2, 3)
    assert m.avg_loss_r == pytest.approx(-1.0)
    assert m.profit_factor == pytest.approx(6.0 / 3.0)
    assert m.payoff_ratio == pytest.approx(2.0)
    assert m.median_r == pytest.approx(0.0)


def test_max_drawdown_on_the_hand_checked_series(hand_checked_trades):
    assert drawdown_series(R_SERIES) == pytest.approx([0.0, 1.0, 0.0, 1.0, 2.0, 0.0])
    dd, longest = max_drawdown(R_SERIES)
    assert dd == pytest.approx(2.0)
    assert longest == 2, "the longest unbroken drawdown spans two trades"

    m = compute_metrics(hand_checked_trades)
    assert m.max_drawdown_r == pytest.approx(2.0)
    assert m.max_drawdown_trades == 2
    assert m.recovery_factor == pytest.approx(3.0 / 2.0)
    assert m.avg_drawdown_r == pytest.approx((1.0 + 1.0 + 2.0) / 3.0)


def test_streaks_on_the_hand_checked_series(hand_checked_trades):
    m = compute_metrics(hand_checked_trades)
    assert m.max_consecutive_wins == 1
    assert m.max_consecutive_losses == 2
    assert m.current_streak == 1


def test_profit_factor_is_capped_rather_than_infinite():
    """An unbeaten three-trade sample must not outrank a real one."""
    m = compute_metrics([make_trade(1.0, index=i) for i in range(3)])
    assert m.profit_factor == pytest.approx(999.0)
    assert m.losses == 0


def test_metrics_of_an_empty_trade_list_are_zero():
    m = compute_metrics([])
    assert m.trades == 0 and m.expectancy_r == 0.0 and m.profit_factor == 0.0
    assert m.max_drawdown_r == 0.0


def test_drawdown_of_a_monotonic_winner_is_zero():
    assert max_drawdown([1.0, 1.0, 1.0]) == (0.0, 0)


def test_long_and_short_expectancy_are_split(hand_checked_trades):
    trades = hand_checked_trades + [
        make_trade(-2.0, direction=Direction.SHORT, index=10),
        make_trade(1.0, direction=Direction.SHORT, index=11)]
    m = compute_metrics(trades)
    assert m.long_trades == 6 and m.short_trades == 2
    assert m.long_expectancy_r == pytest.approx(0.5)
    assert m.short_expectancy_r == pytest.approx(-0.5)


def test_slice_metrics_respects_a_minimum_sample(hand_checked_trades):
    trades = hand_checked_trades + [make_trade(1.0, index=20)]
    trades[-1].session = "LUNCH"
    by_session = slice_metrics(trades, "session", min_trades=2)
    assert "RTH_MORNING" in by_session
    assert "LUNCH" not in by_session, "a one-trade slice is not a finding"


def test_slice_metrics_rejects_an_unknown_key(hand_checked_trades):
    with pytest.raises(KeyError):
        slice_metrics(hand_checked_trades, "phase_of_the_moon")


def test_exit_reasons_are_counted(hand_checked_trades):
    trades = hand_checked_trades + [make_trade(-1.0, reason=ExitReason.STOP,
                                               index=30)]
    m = compute_metrics(trades)
    assert m.exit_reasons["STOP"] == 1
    assert m.exit_reasons["TARGET"] == 6
