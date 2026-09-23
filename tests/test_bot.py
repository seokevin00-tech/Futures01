"""The calculator bot: sizing arithmetic, refusals, and the trade path.

The trade path needs testing precisely BECAUSE the data never reaches it. On
the supplied bars no strategy clears the live floors - 0 of 252 on MNQ 1h
clear 40 trades at +0.08R - so LONG and SHORT rendering and the position
sizing would be untested code in the one branch that matters in production.
"""

from __future__ import annotations

import pytest

from futures_agents.bot import Calculation, _position_size, calculate, render
from futures_agents.config import AccountConfig, get_contract
from futures_agents.timeutil import ET


# ---- sizing -----------------------------------------------------------

def test_size_comes_from_room_to_failure_not_equity():
    """A $50,000 account with a $5,000 trailing drawdown has $5,000 of room.
    Sizing off the balance is how accounts die."""
    acc, spec = AccountConfig(), get_contract("MNQ")
    n, risk, budget = _position_size(spec, acc, 50_000.0, stop_points=50.0)
    # 50 points x $2/point = $100 per contract.
    assert budget < 50_000 * 0.01, "per-trade budget is a fraction of equity, not of it"
    assert risk == pytest.approx(n * 100.0)
    assert n * 100.0 <= budget


def test_size_is_capped_by_the_configured_ceilings():
    acc, spec = AccountConfig(), get_contract("MNQ")
    _, _, budget = _position_size(spec, acc, 50_000.0, stop_points=1.0)
    assert budget <= acc.max_dollar_risk
    assert budget <= 50_000.0 * acc.max_risk_pct_of_equity


def test_a_stop_too_wide_to_afford_sizes_to_zero():
    """Refusing is correct. Rounding up to one contract would silently exceed
    the per-trade budget, which is the failure this guard exists for."""
    acc, spec = AccountConfig(), get_contract("MNQ")
    n, risk, budget = _position_size(spec, acc, 50_000.0, stop_points=5_000.0)
    assert n == 0 and risk == 0.0


def test_sizing_respects_each_contract_s_point_value():
    """The same stop in points costs different money on different contracts,
    which is the whole reason symbols are sized separately."""
    acc = AccountConfig()
    mnq, _, _ = _position_size(get_contract("MNQ"), acc, 50_000.0, 20.0)
    mgc, _, _ = _position_size(get_contract("MGC"), acc, 50_000.0, 20.0)
    assert mnq != mgc, "MNQ at $2/point and MGC at $10/point cannot size alike"


# ---- the live path ----------------------------------------------------

def test_no_trade_is_a_real_answer_with_a_reason():
    calc = calculate("MNQ", timeframe=60, suffix="1h", budget=200)
    assert calc.decision == "NO TRADE"
    assert calc.reasons, "a refusal without a reason is not actionable"
    assert not calc.is_trade


def test_refusal_names_the_specific_gate():
    """Reporting "the risk layer refused" tells the operator nothing. An
    earlier draft read a reasons attribute that does not exist, so every veto
    rendered as one generic line."""
    calc = calculate("MNQ", timeframe=60, suffix="1h", at_index=4994, budget=600)
    joined = " ".join(calc.reasons).lower()
    assert any(k in joined for k in
               ("expectancy", "trades", "floor", "drawdown", "sample", "fired")), \
        f"veto is not specific: {calc.reasons}"


def test_the_callout_leads_with_eastern_time():
    calc = calculate("MNQ", timeframe=60, suffix="1h", budget=200)
    text = render(calc, use_alert=False)
    first = text.splitlines()[0]
    assert first.startswith("["), first
    assert "ED T" not in first and ("EDT" in first or "EST" in first), first
    assert calc.symbol in first


def test_trade_rendering_shows_the_whole_arithmetic():
    """The branch the data never reaches. Every number a trader acts on must
    appear, with the stop's dollar cost and the room it is drawn against."""
    spec = get_contract("MNQ")
    from datetime import datetime
    calc = Calculation(
        symbol="MNQ", timeframe=60, when=datetime(2026, 3, 17, 10, 0, tzinfo=ET),
        price=21_000.0, spec=spec, decision="LONG", entry=21_000.0,
        stop=20_950.0, targets=[21_100.0, 21_200.0], contracts=3,
        risk_dollars=300.0, reward_risk=4.0, strategy_id="MNQ-60m-abc123",
        confluences=["ema_stack: 9>21>50", "break_of_structure"],
        invalidation="close below 20950", account_room=5_000.0, bars_seen=5_000)
    text = render(calc, use_alert=False)
    assert "LONG" in text
    for fragment in ("21000", "20950", "21100", "3 contract", "$300",
                     "4.00 : 1", "MNQ-60m-abc123", "ema_stack", "wrong if"):
        assert fragment in text, f"missing {fragment!r} from the callout:\n{text}"


def test_symbols_are_calculated_independently():
    """Nothing measured on one contract may inform another."""
    from futures_agents.bot import scan
    out = scan(["MNQ", "MES"], timeframe=60, suffix="1h", budget=150)
    assert len(out) == 2
    assert {c.symbol for c in out} == {"MNQ", "MES"}
    for c in out:
        assert c.spec.symbol == c.symbol
