"""The risk manager - sizing, limits and the veto.

This layer is deliberately deterministic and deliberately independent of every
prediction agent. It does not form a view on the market and it cannot be
argued with: a proposal either fits inside the account's constraints or it does
not. The decision agent proposes; this disposes.

The ordering of the checks is the ordering of the specification's hierarchy:
account failure first, drawdown second, per-trade risk third, setup quality
fourth. A check that fires early short-circuits the rest, because once the
account is at a hard limit the quality of the setup is irrelevant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import AccountConfig, ContractSpec, get_contract
from ..schema import (Direction, Evidence, HistoricalPerformance, NewsRisk,
                      RiskAssessment)
from ..timeutil import et_stamp, now_et
from .account import AccountState, OpenPosition

__all__ = ["TradingMode", "TradeProposal", "RiskManager"]


class TradingMode(str, Enum):
    """What the account is permitted to do right now."""

    NORMAL = "NORMAL"
    REDUCED = "REDUCED"              # sized down; still permitted to trade
    OBSERVATION_ONLY = "OBSERVATION_ONLY"   # analysis continues, no callouts
    HALTED = "HALTED"                # hard stop for the session

    @property
    def can_trade(self) -> bool:
        return self in (TradingMode.NORMAL, TradingMode.REDUCED)


@dataclass
class TradeProposal:
    """What the decision layer asks the risk layer to approve."""

    symbol: str
    direction: Direction
    entry: float
    stop: float
    targets: List[float] = field(default_factory=list)
    confidence: float = 0.0
    strategy_id: str = ""
    strategy_name: str = ""
    timeframe: int = 0
    regime: str = "UNKNOWN"
    volatility: str = "NORMAL"
    session: str = ""
    news_risk: NewsRisk = NewsRisk.NONE
    minutes_to_high_impact: Optional[float] = None
    historical: Optional[HistoricalPerformance] = None
    atr: Optional[float] = None
    atr_median: Optional[float] = None
    analyst_agreement: float = 0.0     # -1 (opposed) .. 1 (unanimous)

    @property
    def risk_points(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_risk(self) -> float:
        if not self.targets or self.risk_points <= 0:
            return 0.0
        return abs(self.targets[-1] - self.entry) / self.risk_points

    @property
    def first_target_rr(self) -> float:
        if not self.targets or self.risk_points <= 0:
            return 0.0
        return abs(self.targets[0] - self.entry) / self.risk_points


class RiskManager:
    """Sizes positions and vetoes anything that endangers the account."""

    def __init__(self, config: AccountConfig, state: AccountState):
        self.config = config
        self.state = state

    # ------------------------------------------------------------------
    # Mode
    # ------------------------------------------------------------------
    def mode(self, when: Optional[datetime] = None) -> Tuple[TradingMode, List[str]]:
        """Current trading mode and the reasons for it."""
        cfg = self.config
        st = self.state
        st.roll_day(when)
        day = st.day
        reasons: List[str] = []

        if st.has_failed:
            return TradingMode.HALTED, ["account has reached its failure threshold"]
        if st.usable_buffer <= 0:
            return TradingMode.HALTED, [
                f"usable risk buffer exhausted (${st.remaining_drawdown:,.0f} to "
                f"failure, all of it reserved)"]
        if day.realised_pnl <= -cfg.daily_loss_limit:
            return TradingMode.HALTED, [
                f"daily loss limit reached (${day.realised_pnl:,.0f} vs limit "
                f"${-cfg.daily_loss_limit:,.0f})"]
        if day.consecutive_losses >= cfg.max_consecutive_losses:
            return TradingMode.OBSERVATION_ONLY, [
                f"{day.consecutive_losses} consecutive losses - observation only "
                "for the rest of the session"]
        if day.trades_taken >= cfg.max_trades_per_day:
            return TradingMode.OBSERVATION_ONLY, [
                f"daily trade cap reached ({day.trades_taken}/{cfg.max_trades_per_day})"]
        # Protect a profitable day: giving back most of a good session is how a
        # winning week becomes a losing one.
        if (day.peak_pnl >= cfg.daily_profit_lockdown
                and day.giveback >= day.peak_pnl * cfg.daily_giveback_pct):
            return TradingMode.OBSERVATION_ONLY, [
                f"gave back ${day.giveback:,.0f} of a ${day.peak_pnl:,.0f} peak - "
                "protecting the day"]

        if day.realised_pnl <= -cfg.daily_soft_loss_limit:
            reasons.append(f"below the soft daily loss limit "
                           f"(${day.realised_pnl:,.0f}) - risk halved")
        if st.derisk_multiplier < 1.0:
            reasons.append(f"account drawdown has reduced risk to "
                           f"{st.derisk_multiplier:.0%} of base")
        if day.consecutive_losses >= 2:
            reasons.append(f"{day.consecutive_losses} consecutive losses - risk reduced")
        return (TradingMode.REDUCED if reasons else TradingMode.NORMAL), reasons

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------
    def risk_budget(self, proposal: Optional[TradeProposal] = None
                    ) -> Tuple[float, float, List[str]]:
        """Dollars permitted on the next trade, the multiplier, and why.

        Derived from the *usable buffer*, then capped by equity percentage and
        an absolute ceiling, then scaled down for every adverse condition. It
        is never scaled up: a winning streak is not information about edge, and
        sizing up into one is how accounts give back a month in an afternoon.
        """
        cfg = self.config
        st = self.state
        notes: List[str] = []

        base = st.usable_buffer * cfg.base_risk_pct_of_buffer
        equity_cap = st.equity * cfg.max_risk_pct_of_equity
        if equity_cap < base:
            notes.append(f"capped by equity limit ({cfg.max_risk_pct_of_equity:.2%} "
                         f"= ${equity_cap:,.0f})")
        budget = min(base, equity_cap, cfg.max_dollar_risk)

        multiplier = st.derisk_multiplier
        if multiplier < 1.0:
            notes.append(f"drawdown de-risk x{multiplier:.2f}")

        day = st.day
        if day and day.realised_pnl <= -cfg.daily_soft_loss_limit:
            multiplier *= 0.5
            notes.append("soft daily loss limit x0.50")
        if day and day.consecutive_losses >= 2:
            step = 0.75 ** (day.consecutive_losses - 1)
            multiplier *= step
            notes.append(f"{day.consecutive_losses} consecutive losses x{step:.2f}")

        if proposal is not None:
            if proposal.news_risk in (NewsRisk.HIGH, NewsRisk.BLACKOUT):
                multiplier *= cfg.high_impact_risk_multiplier
                notes.append(f"high news risk x{cfg.high_impact_risk_multiplier:.2f}")
            if proposal.volatility in ("HIGH", "EXTREME"):
                multiplier *= 0.7
                notes.append("elevated volatility x0.70")
            if proposal.analyst_agreement < 0:
                multiplier *= 0.6
                notes.append("analysts in disagreement x0.60")
            hist = proposal.historical
            if hist and hist.trades and not hist.is_live_eligible:
                multiplier *= 0.5
                notes.append("strategy not yet live-eligible x0.50")

        # Remaining daily loss budget is a hard ceiling: a single trade may
        # never be sized so that losing it breaches the day's limit.
        remaining_day = st.remaining_daily_loss_budget
        final = budget * max(0.0, multiplier)
        if remaining_day < final:
            final = remaining_day
            notes.append(f"capped by remaining daily loss budget (${remaining_day:,.0f})")
        return max(0.0, final), multiplier, notes

    def contracts_for(self, proposal: TradeProposal, budget: float,
                      spec: ContractSpec) -> int:
        """Largest whole contract count whose stop loss fits inside ``budget``."""
        risk_per_contract = proposal.risk_points * spec.point_value
        if risk_per_contract <= 0:
            return 0
        return int(math.floor(budget / risk_per_contract))

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------
    def assess(self, proposal: TradeProposal, *,
               when: Optional[datetime] = None) -> RiskAssessment:
        """Approve or veto, with the full arithmetic attached."""
        cfg = self.config
        st = self.state
        spec = get_contract(proposal.symbol)
        ra = RiskAssessment()
        ra.stop_distance_points = proposal.risk_points
        ra.stop_distance_ticks = spec.ticks_between(proposal.entry, proposal.stop)
        ra.remaining_daily_loss_budget = st.remaining_daily_loss_budget
        ra.remaining_drawdown_buffer = st.usable_buffer

        # ---- 1. account-level hard stops ------------------------------
        mode, mode_reasons = self.mode(when)
        if not mode.can_trade:
            ra.vetoes.extend(mode_reasons)
            ra.notes.append(f"trading mode: {mode.value}")
            return ra
        ra.warnings.extend(mode_reasons)
        ra.notes.append(f"trading mode: {mode.value}")

        # ---- 2. structural sanity -------------------------------------
        if proposal.direction is Direction.NEUTRAL:
            ra.vetoes.append("no direction proposed")
            return ra
        if proposal.risk_points <= 0:
            ra.vetoes.append("stop is at or beyond the entry - risk is undefined")
            return ra
        if ra.stop_distance_ticks < spec.min_stop_ticks:
            ra.vetoes.append(
                f"stop of {ra.stop_distance_ticks:.0f} ticks is inside "
                f"{spec.symbol}'s noise floor of {spec.min_stop_ticks} ticks")
            return ra
        if not proposal.targets:
            ra.vetoes.append("no target defined - reward cannot be assessed")
            return ra

        # ---- 3. exposure ----------------------------------------------
        if st.position_for(proposal.symbol) is not None:
            ra.vetoes.append(f"already holding a position in {proposal.symbol}")
            return ra
        if len(st.open_positions) >= cfg.max_concurrent_positions:
            ra.vetoes.append(
                f"at the concurrent-position limit "
                f"({len(st.open_positions)}/{cfg.max_concurrent_positions})")
            return ra
        correlated = st.correlated_exposure(proposal.symbol)
        if len(correlated) >= cfg.max_correlated_positions:
            others = ", ".join(p.symbol for p in correlated)
            ra.vetoes.append(
                f"already exposed to the {spec.correlation_group} group via "
                f"{others} - a second position there is one larger position, "
                "not a diversified one")
            return ra

        # ---- 4. news ---------------------------------------------------
        if proposal.news_risk.blocks_entry:
            ra.vetoes.append(
                "inside a scheduled high-impact event window"
                + (f" ({proposal.minutes_to_high_impact:.0f} min away)"
                   if proposal.minutes_to_high_impact is not None else ""))
            return ra

        # ---- 5. volatility band ---------------------------------------
        if proposal.atr and proposal.atr_median:
            ratio = proposal.atr / proposal.atr_median
            if ratio > cfg.max_atr_multiple_of_median:
                ra.vetoes.append(
                    f"volatility {ratio:.1f}x its median - stop distance and "
                    "slippage are both unreliable here")
                return ra
            if ratio < cfg.min_atr_multiple_of_median:
                ra.vetoes.append(
                    f"volatility {ratio:.2f}x its median - too little movement to "
                    "cover costs")
                return ra
            ra.notes.append(f"ATR {ratio:.2f}x median")

        # ---- 6. setup quality -----------------------------------------
        if proposal.confidence < cfg.min_confidence:
            ra.vetoes.append(
                f"confidence {proposal.confidence:.2f} is below the "
                f"{cfg.min_confidence:.2f} floor")
            return ra
        if proposal.reward_risk < cfg.min_reward_risk:
            ra.vetoes.append(
                f"reward/risk {proposal.reward_risk:.2f} is below the "
                f"{cfg.min_reward_risk:.2f} floor")
            return ra

        hist = proposal.historical
        if hist is None or hist.trades == 0:
            ra.vetoes.append(
                "no measured history for this strategy - an unmeasured edge is "
                "not an edge")
            return ra
        if hist.trades < cfg.min_backtest_trades:
            ra.vetoes.append(
                f"only {hist.trades} historical trades (floor "
                f"{cfg.min_backtest_trades}) - the sample cannot support a "
                "live decision")
            return ra
        if hist.expectancy_r < cfg.min_expectancy_r:
            ra.vetoes.append(
                f"historical expectancy {hist.expectancy_r:+.3f}R is below the "
                f"{cfg.min_expectancy_r:+.3f}R floor")
            return ra
        if not hist.is_live_eligible:
            ra.warnings.append(
                "strategy has not cleared full out-of-sample eligibility - "
                "size reduced")

        # ---- 7. sizing -------------------------------------------------
        budget, multiplier, notes = self.risk_budget(proposal)
        ra.risk_multiplier_applied = multiplier
        ra.notes.extend(notes)

        if budget < cfg.min_dollar_risk:
            ra.vetoes.append(
                f"permitted risk of ${budget:,.2f} is below the ${cfg.min_dollar_risk:,.0f} "
                "minimum - the account cannot take this trade at a meaningful size")
            return ra

        contracts = self.contracts_for(proposal, budget, spec)
        if contracts < 1:
            per_contract = proposal.risk_points * spec.point_value
            ra.vetoes.append(
                f"one contract risks ${per_contract:,.2f}, above the permitted "
                f"${budget:,.2f} - the stop is too wide for this account right now")
            return ra

        ra.contracts = contracts
        ra.dollar_risk = contracts * proposal.risk_points * spec.point_value
        ra.account_risk_pct = ra.dollar_risk / st.equity if st.equity else 0.0

        # ---- 8. costs against the edge ---------------------------------
        cost = 2.0 * (spec.commission_per_side + spec.exchange_fee_per_side) * contracts
        slip = spec.typical_slippage_ticks * spec.tick_value * contracts
        ra.expected_cost = cost + slip
        gross_reward = abs(proposal.targets[0] - proposal.entry) * spec.point_value * contracts
        net_reward = gross_reward - ra.expected_cost
        ra.reward_risk_after_costs = net_reward / ra.dollar_risk if ra.dollar_risk else 0.0
        if ra.reward_risk_after_costs < 1.0:
            ra.vetoes.append(
                f"after ${ra.expected_cost:,.2f} of costs the first target returns "
                f"{ra.reward_risk_after_costs:.2f}R - not worth the risk")
            return ra

        # ---- 9. effect on the buffer -----------------------------------
        ra.buffer_consumed_if_stopped_pct = (
            ra.dollar_risk / st.usable_buffer if st.usable_buffer > 0 else 1.0)
        if ra.buffer_consumed_if_stopped_pct > 0.25:
            ra.vetoes.append(
                f"a single stop would consume {ra.buffer_consumed_if_stopped_pct:.0%} "
                "of the remaining risk buffer")
            return ra
        if ra.dollar_risk > st.remaining_daily_loss_budget:
            ra.vetoes.append(
                f"${ra.dollar_risk:,.2f} at risk exceeds the ${st.remaining_daily_loss_budget:,.2f} "
                "left in today's loss budget")
            return ra

        ra.approved = True
        ra.notes.append(
            f"{contracts} contract(s), ${ra.dollar_risk:,.2f} at risk "
            f"({ra.account_risk_pct:.2%} of equity, "
            f"{ra.buffer_consumed_if_stopped_pct:.1%} of the usable buffer)")
        return ra

    # ------------------------------------------------------------------
    def render(self, assessment: RiskAssessment) -> str:
        lines = [f"RISK ASSESSMENT  [{et_stamp()}]"]
        if assessment.approved:
            lines.append(f"  APPROVED  {assessment.contracts} contract(s)  "
                         f"${assessment.dollar_risk:,.2f} at risk  "
                         f"({assessment.account_risk_pct:.2%} of equity)")
            lines.append(f"  Buffer impact if stopped: "
                         f"{assessment.buffer_consumed_if_stopped_pct:.1%} of "
                         f"${assessment.remaining_drawdown_buffer:,.2f}")
            lines.append(f"  R/R after ${assessment.expected_cost:,.2f} costs: "
                         f"{assessment.reward_risk_after_costs:.2f}")
        else:
            lines.append("  VETOED")
            for v in assessment.vetoes:
                lines.append(f"    - {v}")
        for w in assessment.warnings:
            lines.append(f"  warning: {w}")
        for n in assessment.notes:
            lines.append(f"  note:    {n}")
        return "\n".join(lines)
