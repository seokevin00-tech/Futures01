"""The risk layer: de-risking, sizing caps and the veto.

The risk manager is the only component that can say no, so its failure modes
are asymmetric. Sizing slightly too small costs a little edge; sizing too large,
or scaling up into a winning streak, is how a $50,000 account fails in an
afternoon. These tests pin the direction of every adjustment, not just its
presence.

A veto is a *result*, never an exception: the decision layer has to be able to
report why a trade was refused, which means ``approved=False`` plus a populated
reason, every time.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List

import pytest

from futures_agents.config import AccountConfig, get_contract
from futures_agents.risk.account import AccountState, DayState, OpenPosition
from futures_agents.risk.manager import RiskManager, TradingMode, TradeProposal
from futures_agents.schema import Direction, NewsRisk, RiskAssessment

from conftest import FIXED_NOW, good_history, good_proposal

MNQ = get_contract("MNQ")


def assess(risk: RiskManager, proposal: TradeProposal) -> RiskAssessment:
    """Always assess at the pinned instant, so the day ledger is not rolled."""
    return risk.assess(proposal, when=FIXED_NOW)


def assert_vetoed(ra: RiskAssessment, *, contains: str = "") -> None:
    """A veto is data, not an exception, and it must explain itself."""
    assert isinstance(ra, RiskAssessment)
    assert ra.approved is False
    assert ra.vetoes, "a refusal with no stated reason is unusable downstream"
    assert all(v.strip() for v in ra.vetoes)
    assert ra.veto_reason.strip()
    assert ra.contracts == 0
    if contains:
        assert any(contains.lower() in v.lower() for v in ra.vetoes), (
            f"expected a veto mentioning {contains!r}, got {ra.vetoes}")


# --------------------------------------------------------------------------
# Baseline: the proposal every other test mutates one field of
# --------------------------------------------------------------------------

def test_the_baseline_proposal_is_approved(risk, proposal):
    ra = assess(risk, proposal)
    assert ra.approved is True, f"baseline unexpectedly vetoed: {ra.vetoes}"
    assert ra.vetoes == []
    assert ra.contracts >= 1
    assert ra.dollar_risk > 0
    assert ra.risk_multiplier_applied == pytest.approx(1.0)


# --------------------------------------------------------------------------
# De-risking as the account draws down
# --------------------------------------------------------------------------

def test_derisk_multiplier_is_monotonically_non_increasing_as_equity_falls(
        account_config):
    """Risk per trade must never rise as the buffer is consumed."""
    peak = 50_000.0
    previous = None
    for equity in range(50_000, 44_499, -50):
        mult = account_config.derisk_multiplier(float(equity), peak)
        assert 0.0 <= mult <= 1.0
        if previous is not None:
            assert mult <= previous + 1e-12, (
                f"multiplier rose from {previous} to {mult} at equity {equity}")
        previous = mult


def test_derisk_multiplier_is_one_at_the_peak_and_zero_at_the_limit(account_config):
    peak = 50_000.0
    failure = account_config.failure_equity(peak)
    assert failure == pytest.approx(45_000.0)
    assert account_config.derisk_multiplier(peak, peak) == pytest.approx(1.0)
    assert account_config.derisk_multiplier(failure, peak) == pytest.approx(0.0)
    assert account_config.derisk_multiplier(failure - 500.0, peak) == pytest.approx(0.0)


def test_derisk_multiplier_steps_through_the_declared_ladder(account_config):
    """The reserve is never spent, so the usable buffer is $4,000 of the
    $5,000 allowance and the ladder is keyed off that."""
    peak = 50_000.0
    for consumed, expected in account_config.derisk_ladder:
        equity = peak - consumed * 4_000.0
        assert account_config.derisk_multiplier(equity, peak) == pytest.approx(expected)


def test_usable_buffer_reserves_the_protected_portion(account_config):
    assert account_config.usable_buffer(50_000.0, 50_000.0) == pytest.approx(4_000.0)
    assert account_config.usable_buffer(46_000.0, 50_000.0) == pytest.approx(0.0)
    assert account_config.usable_buffer(45_000.0, 50_000.0) == pytest.approx(0.0)


def test_risk_budget_shrinks_with_the_account(account_config):
    budgets = []
    for equity in (50_000.0, 49_000.0, 48_000.0, 47_000.0, 46_200.0):
        state = AccountState(config=account_config, equity=equity)
        state.peak_equity = 50_000.0
        state.day = DayState(day=FIXED_NOW.date(), starting_equity=equity)
        budget, mult, _ = RiskManager(account_config, state).risk_budget()
        budgets.append(budget)
    assert budgets == sorted(budgets, reverse=True)
    assert budgets[-1] < budgets[0]


# --------------------------------------------------------------------------
# Sizing caps
# --------------------------------------------------------------------------

@pytest.mark.parametrize("equity", [50_000.0, 49_000.0, 47_500.0, 46_500.0])
@pytest.mark.parametrize("daily_pnl", [0.0, -200.0, -700.0, -950.0])
def test_sizing_never_exceeds_the_per_trade_cap_or_the_daily_budget(
        account_config, equity, daily_pnl):
    state = AccountState(config=account_config, equity=equity)
    state.peak_equity = 50_000.0
    state.day = DayState(day=FIXED_NOW.date(), starting_equity=equity)
    state.day.record(daily_pnl)
    manager = RiskManager(account_config, state)

    budget, multiplier, _ = manager.risk_budget(good_proposal())
    assert budget <= account_config.max_dollar_risk + 1e-9
    assert budget <= state.remaining_daily_loss_budget + 1e-9
    assert budget <= state.equity * account_config.max_risk_pct_of_equity + 1e-9
    assert 0.0 <= multiplier <= 1.0
    assert budget >= 0.0


def test_approved_dollar_risk_never_exceeds_the_permitted_budget(risk, proposal,
                                                                account):
    ra = assess(risk, proposal)
    budget, _, _ = risk.risk_budget(proposal)
    assert ra.approved
    assert ra.dollar_risk <= budget + 1e-9
    assert ra.dollar_risk <= account.config.max_dollar_risk
    assert ra.dollar_risk <= account.remaining_daily_loss_budget
    assert ra.contracts * proposal.risk_points * MNQ.point_value == pytest.approx(
        ra.dollar_risk)


def test_contracts_for_never_rounds_up(risk):
    """A partial contract does not exist; rounding up would breach the budget."""
    proposal = good_proposal()
    per_contract = proposal.risk_points * MNQ.point_value       # $20
    assert risk.contracts_for(proposal, 59.0, MNQ) == 2         # not 3
    assert risk.contracts_for(proposal, 60.0, MNQ) == 3
    assert risk.contracts_for(proposal, 19.0, MNQ) == 0
    assert risk.contracts_for(proposal, 0.0, MNQ) == 0
    assert per_contract == pytest.approx(20.0)


def test_a_stop_too_wide_for_the_budget_is_vetoed_not_silently_shrunk(risk):
    """MGC risks $10 a point, so a 30-point stop is $300 against a ~$240
    budget: one contract already breaches it and there is no fraction to fall
    back on."""
    proposal = good_proposal(symbol="MGC", entry=2_400.0, stop=2_370.0,
                             targets=[2_460.0])
    budget, _, _ = risk.risk_budget(proposal)
    assert proposal.risk_points * get_contract("MGC").point_value > budget
    assert_vetoed(assess(risk, proposal), contains="too wide")


# --------------------------------------------------------------------------
# Risk is never scaled UP
# --------------------------------------------------------------------------

def test_risk_is_never_scaled_up_after_consecutive_wins(account_config, account):
    """A winning streak is not information about edge. Sizing up into one is
    how a good week becomes a losing month."""
    manager = RiskManager(account_config, account)
    baseline_budget, baseline_mult, _ = manager.risk_budget(good_proposal())

    for wins in range(1, 8):
        account.day.record(300.0)
        budget, mult, notes = manager.risk_budget(good_proposal())
        assert account.day.consecutive_wins == wins
        assert mult <= baseline_mult + 1e-12, (
            f"multiplier rose to {mult} after {wins} consecutive wins")
        assert budget <= baseline_budget + 1e-9, (
            f"budget rose to {budget} after {wins} consecutive wins")
        assert not any("x1." in n or "increase" in n.lower() for n in notes)


def test_risk_is_never_scaled_up_when_equity_makes_a_new_high(account_config):
    """Equity at a new peak leaves the usable buffer unchanged under a trailing
    drawdown, so the budget must not grow with profit either."""
    flat = AccountState(config=account_config, equity=50_000.0)
    flat.peak_equity = 50_000.0
    flat.day = DayState(day=FIXED_NOW.date(), starting_equity=50_000.0)
    base_budget, _, _ = RiskManager(account_config, flat).risk_budget()

    for equity in (52_000.0, 55_000.0, 60_000.0):
        rich = AccountState(config=account_config, equity=equity)
        rich.peak_equity = equity
        rich.day = DayState(day=FIXED_NOW.date(), starting_equity=equity)
        rich.day.record(1_000.0)
        budget, mult, _ = RiskManager(account_config, rich).risk_budget()
        assert mult <= 1.0
        assert budget <= base_budget + 1e-9, (
            f"budget grew to {budget} at ${equity:,.0f} of equity")


def test_the_multiplier_never_exceeds_one_under_any_favourable_condition(
        account_config, account):
    manager = RiskManager(account_config, account)
    favourable = good_proposal(news_risk=NewsRisk.NONE, volatility="LOW",
                               analyst_agreement=1.0,
                               historical=good_history(expectancy_r=2.0,
                                                       robustness_score=1.0))
    account.day.record(5_000.0)
    _, multiplier, _ = manager.risk_budget(favourable)
    assert multiplier <= 1.0


def test_risk_is_scaled_down_after_consecutive_losses(account_config, account):
    """The asymmetry check: down is allowed, up is not."""
    manager = RiskManager(account_config, account)
    baseline, _, _ = manager.risk_budget(good_proposal())
    account.day.record(-100.0)
    account.day.record(-100.0)
    reduced, mult, notes = manager.risk_budget(good_proposal())
    assert account.day.consecutive_losses == 2
    assert mult < 1.0 and reduced < baseline
    assert any("consecutive losses" in n for n in notes)


# --------------------------------------------------------------------------
# Vetoes
# --------------------------------------------------------------------------

def test_veto_stop_inside_the_noise_floor(risk):
    """MNQ's floor is 16 ticks (4.00 points); a 2-point stop is noise."""
    assert MNQ.min_stop_ticks == 16
    ra = assess(risk, good_proposal(entry=21_000.0, stop=20_998.0,
                                    targets=[21_010.0]))
    assert_vetoed(ra, contains="noise floor")


def test_veto_insufficient_historical_sample(risk, account_config):
    ra = assess(risk, good_proposal(historical=good_history(trades=12)))
    assert_vetoed(ra, contains="historical trades")
    assert str(account_config.min_backtest_trades) in ra.veto_reason


def test_veto_no_measured_history_at_all(risk):
    assert_vetoed(assess(risk, good_proposal(historical=None)),
                  contains="no measured history")
    assert_vetoed(assess(risk, good_proposal(historical=good_history(trades=0))),
                  contains="no measured history")


def test_veto_reward_risk_below_the_floor(risk, account_config):
    """Entry 21000, stop 20990, target 21010 is 1.0R against a 1.6 floor."""
    ra = assess(risk, good_proposal(targets=[21_010.0]))
    assert_vetoed(ra, contains="reward/risk")
    assert f"{account_config.min_reward_risk:.2f}" in ra.veto_reason


def test_veto_negative_expectancy_history(risk):
    ra = assess(risk, good_proposal(historical=good_history(expectancy_r=-0.05)))
    assert_vetoed(ra, contains="expectancy")


def test_veto_confidence_below_the_floor(risk, account_config):
    ra = assess(risk, good_proposal(confidence=0.10))
    assert_vetoed(ra, contains="confidence")
    assert account_config.min_confidence > 0.10


def test_veto_news_blackout(risk):
    ra = assess(risk, good_proposal(news_risk=NewsRisk.BLACKOUT,
                                    minutes_to_high_impact=3.0))
    assert_vetoed(ra, contains="high-impact event")
    assert "3 min" in ra.veto_reason


def test_high_news_risk_sizes_down_without_vetoing(risk, account_config):
    """Only BLACKOUT blocks entry; HIGH halves the size instead."""
    assert NewsRisk.HIGH.blocks_entry is False
    assert NewsRisk.BLACKOUT.blocks_entry is True
    ra = assess(risk, good_proposal(news_risk=NewsRisk.HIGH))
    assert ra.approved is True
    assert ra.risk_multiplier_applied == pytest.approx(
        account_config.high_impact_risk_multiplier)


def test_veto_correlated_exposure(risk, account):
    """MES and MNQ are two expressions of one index position, not two trades."""
    account.open_positions.append(OpenPosition(
        symbol="MES", direction=Direction.LONG, contracts=1, entry=5_900.0,
        stop=5_890.0, dollar_risk=50.0))
    proposal = good_proposal(symbol="MES", entry=5_900.0, stop=5_880.0,
                             targets=[5_940.0])
    ra = assess(risk, proposal)
    assert_vetoed(ra)
    assert any("already holding" in v or "group" in v for v in ra.vetoes)


def test_veto_a_second_position_in_the_same_correlation_group(risk, account,
                                                              account_config):
    """Same group, different symbol - the case a per-symbol check would miss."""
    group = get_contract("MNQ").correlation_group
    sibling = next((s for s in ("MNQ", "NQ", "MES", "MGC", "MCL", "M2K")
                    if s != "MNQ" and get_contract(s).correlation_group == group),
                   None)
    if sibling is None:
        pytest.skip(f"no second symbol registered in the {group} group")
    account.open_positions.append(OpenPosition(
        symbol=sibling, direction=Direction.LONG, contracts=1, entry=100.0,
        stop=99.0, dollar_risk=50.0))
    assert len(account.correlated_exposure("MNQ")) >= account_config.max_correlated_positions
    assert_vetoed(assess(risk, good_proposal()), contains="group")


def test_veto_daily_loss_limit(risk, account, account_config):
    account.day.record(-account_config.daily_loss_limit)
    mode, reasons = risk.mode(FIXED_NOW)
    assert mode is TradingMode.HALTED
    assert mode.can_trade is False
    ra = assess(risk, good_proposal())
    assert_vetoed(ra, contains="daily loss limit")


def test_veto_three_consecutive_losses(risk, account, account_config):
    assert account_config.max_consecutive_losses == 3
    for _ in range(3):
        account.day.record(-50.0)
    assert account.day.consecutive_losses == 3
    mode, reasons = risk.mode(FIXED_NOW)
    assert mode is TradingMode.OBSERVATION_ONLY
    assert mode.can_trade is False
    assert_vetoed(assess(risk, good_proposal()), contains="consecutive losses")


def test_two_consecutive_losses_reduce_but_do_not_veto(risk, account):
    account.day.record(-50.0)
    account.day.record(-50.0)
    mode, reasons = risk.mode(FIXED_NOW)
    assert mode is TradingMode.REDUCED
    assert mode.can_trade is True
    ra = assess(risk, good_proposal())
    assert ra.approved is True
    assert ra.risk_multiplier_applied < 1.0
    assert ra.warnings


def test_veto_account_failure(risk, account):
    account.equity = account.failure_equity
    assert account.has_failed
    mode, _ = risk.mode(FIXED_NOW)
    assert mode is TradingMode.HALTED
    assert_vetoed(assess(risk, good_proposal()), contains="failure")


def test_veto_daily_trade_cap(risk, account, account_config):
    for _ in range(account_config.max_trades_per_day):
        account.day.record(10.0)
    mode, _ = risk.mode(FIXED_NOW)
    assert mode is TradingMode.OBSERVATION_ONLY
    assert_vetoed(assess(risk, good_proposal()), contains="trade cap")


def test_veto_no_direction(risk):
    assert_vetoed(assess(risk, good_proposal(direction=Direction.NEUTRAL)),
                  contains="no direction")


def test_veto_stop_on_the_wrong_side_of_the_entry(risk):
    ra = assess(risk, good_proposal(entry=21_000.0, stop=21_000.0,
                                    targets=[21_040.0]))
    assert_vetoed(ra, contains="risk is undefined")


def test_veto_no_target(risk):
    assert_vetoed(assess(risk, good_proposal(targets=[])), contains="no target")


def test_veto_volatility_far_above_its_median(risk, account_config):
    ra = assess(risk, good_proposal(atr=100.0, atr_median=10.0))
    assert_vetoed(ra, contains="volatility")
    assert account_config.max_atr_multiple_of_median < 10.0


def test_veto_volatility_far_below_its_median(risk):
    ra = assess(risk, good_proposal(atr=1.0, atr_median=10.0))
    assert_vetoed(ra, contains="too little movement")


def test_veto_when_a_single_stop_would_eat_the_buffer(risk, account):
    """Down to a thin buffer, even a correctly sized trade is refused."""
    account.equity = 46_100.0
    ra = assess(risk, good_proposal())
    assert_vetoed(ra)


def test_no_veto_path_raises(risk, account):
    """Whatever is thrown at it, the risk layer answers rather than crashes."""
    hostile = [
        good_proposal(entry=0.0, stop=0.0, targets=[]),
        good_proposal(direction=Direction.NEUTRAL, targets=[]),
        good_proposal(confidence=-5.0),
        good_proposal(atr=0.0, atr_median=0.0),
        good_proposal(symbol="MCL", entry=70.0, stop=69.0, targets=[73.0]),
        good_proposal(historical=None, news_risk=NewsRisk.BLACKOUT),
    ]
    for p in hostile:
        ra = assess(risk, p)
        assert isinstance(ra, RiskAssessment)
        assert isinstance(ra.approved, bool)
        if not ra.approved:
            assert ra.vetoes


def test_render_states_the_refusal(risk):
    text = risk.render(assess(risk, good_proposal(targets=[21_010.0])))
    assert "VETOED" in text
    assert "reward/risk" in text


# --------------------------------------------------------------------------
# Account ledger behaviour the risk layer depends on
# --------------------------------------------------------------------------

def test_remaining_daily_loss_budget_shrinks_with_losses_only(account,
                                                              account_config):
    assert account.remaining_daily_loss_budget == pytest.approx(
        account_config.daily_loss_limit)
    account.day.record(-250.0)
    assert account.remaining_daily_loss_budget == pytest.approx(
        account_config.daily_loss_limit - 250.0)
    account.day.record(500.0)      # a profit does not enlarge the budget
    assert account.remaining_daily_loss_budget == pytest.approx(
        account_config.daily_loss_limit)


def test_remaining_daily_loss_budget_never_goes_negative(account,
                                                         account_config):
    account.day.record(-account_config.daily_loss_limit * 3)
    assert account.remaining_daily_loss_budget == 0.0


def test_consecutive_counters_reset_on_the_opposite_result(account):
    account.day.record(-10.0)
    account.day.record(-10.0)
    assert account.day.consecutive_losses == 2
    account.day.record(10.0)
    assert account.day.consecutive_losses == 0
    assert account.day.consecutive_wins == 1


def test_day_ledger_rolls_at_the_trading_day_boundary(account):
    from datetime import timedelta
    account.day.record(-400.0)
    assert account.daily_pnl == pytest.approx(-400.0)
    next_session = FIXED_NOW + timedelta(days=1)
    rolled = account.roll_day(next_session)
    assert rolled.realised_pnl == 0.0
    assert account.remaining_daily_loss_budget > 0


def test_peak_equity_only_ratchets_upwards(account):
    account.apply_pnl(1_000.0, when=FIXED_NOW)
    assert account.peak_equity == pytest.approx(51_000.0)
    account.apply_pnl(-2_000.0, when=FIXED_NOW)
    assert account.peak_equity == pytest.approx(51_000.0)
    assert account.drawdown == pytest.approx(2_000.0)
