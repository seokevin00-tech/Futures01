"""Selection objectives: what "best" means when you rank strategies.

``robust_score`` ranks on expectancy in R with penalties for thin samples,
drawdown and losing streaks. That is the right default - it asks "will this
keep working" - but it is close to indifferent between two strategies with the
same expectancy built very differently: a 70%-win-rate scalper taking 0.4R
wins against 0.9R losses, and a 35% swing taker holding for 3R. Those are not
the same instrument for a $50,000 account, and an operator who wants the most
reward per unit of risk is asking for the second.

**The trap this module is built to avoid.** Ranking on reward:risk alone
selects lottery tickets. A 5:1 payoff at a 12% win rate has an expectancy of
-0.28R and will look magnificent on a ranking that reads payoff_ratio and
stops. So payoff ratio is never a term on its own here: it only ever
*multiplies* an edge that is already positive and already survived a sample
floor. A strategy with no edge cannot climb this ranking by having a pretty
ratio, because anything times a non-positive edge stays non-positive.

**Why Sortino and Ulcer rather than Sharpe.** Sharpe punishes upside variance,
which is the variance a high-payoff strategy is *trying* to have - a runner
that occasionally returns 8R is penalised by Sharpe for the thing that makes
it worth holding. Sortino divides by downside deviation only. The Ulcer index
measures the depth and duration of drawdown together, which is what actually
ends an account: a 12R drawdown recovered in five trades and a 12R drawdown
that grinds for eighty are the same number to ``max_drawdown_r`` and very
different experiences at $50,000.
"""

from __future__ import annotations

import math
from typing import Optional

from .metrics import Metrics

__all__ = ["reward_for_risk_score", "risk_profile", "RewardRiskBreakdown"]


class RewardRiskBreakdown(dict):
    """The score with every component visible, so a ranking can be argued with."""

    @property
    def score(self) -> float:
        return float(self.get("score", 0.0))

    def explain(self) -> str:
        parts = [f"score={self.score:.4f}"]
        for k in ("edge_r", "payoff", "sample", "sortino_term", "ulcer_term",
                  "streak_term", "efficiency_term"):
            if k in self:
                parts.append(f"{k}={self[k]:.3f}")
        return "  ".join(parts)


def reward_for_risk_score(m: Metrics, *, min_trades: int = 30,
                          payoff_target: float = 2.0,
                          account_dd_budget_r: float = 10.0) -> RewardRiskBreakdown:
    """Rank by reward earned per unit of risk actually borne.

    ``payoff_target`` is the reward:risk at which the payoff term saturates.
    Set to 2.0 because beyond roughly 2:1 the marginal benefit of a wider
    target is usually paid for in win rate, and a score that kept rewarding
    ratio without limit would walk straight back into the lottery-ticket trap.

    ``account_dd_budget_r`` is the drawdown, in R, that this account treats as
    its working budget. The penalty bites from there rather than from an
    abstract scale, because 10R at $250 a trade is $2,500 against a $50,000
    account with a real failure threshold beneath it.
    """
    out = RewardRiskBreakdown(score=0.0, trades=m.trades)
    if m.trades <= 0:
        return out

    # ---- sample first. Nothing below the floor is ranked on its merits.
    sample = min(1.0, m.trades / float(min_trades))
    if m.trades < min_trades * 0.5:
        sample *= 0.4
    out["sample"] = sample

    edge = m.expectancy_r
    out["edge_r"] = edge
    if edge <= 0:
        # A negative edge stays negative and is never rescued by a ratio.
        out["score"] = edge * sample
        return out

    # ---- reward per unit risked. Multiplies the edge; never stands alone.
    payoff = m.payoff_ratio if m.payoff_ratio > 0 else (
        abs(m.avg_win_r / m.avg_loss_r) if m.avg_loss_r else 0.0)
    out["payoff"] = payoff
    payoff_term = 0.6 + 0.4 * min(1.0, payoff / payoff_target)

    # ---- downside-only volatility. Upside variance is the point of a runner.
    sortino_term = 0.5 + 0.5 * min(1.0, max(0.0, m.sortino) / 2.0)
    out["sortino_term"] = sortino_term

    # ---- drawdown depth AND duration, which max_drawdown_r cannot see.
    ulcer = m.ulcer_index if m.ulcer_index > 0 else max(0.0, m.max_drawdown_r) / 2.0
    ulcer_term = 1.0 / (1.0 + ulcer / max(1e-9, account_dd_budget_r / 2.0))
    out["ulcer_term"] = ulcer_term

    streak_term = 1.0 / (1.0 + max(0, m.max_consecutive_losses - 4) / 5.0)
    out["streak_term"] = streak_term

    # ---- did the trade capture what it was offered? edge_ratio is MFE/MAE:
    # below 1 the strategy consistently gives back more than it takes, which a
    # win rate can hide entirely.
    eff = m.edge_ratio if m.edge_ratio > 0 else 1.0
    efficiency_term = 0.8 + 0.2 * min(1.0, eff / 1.5)
    out["efficiency_term"] = efficiency_term

    significance = max(0.0, min(2.0, m.t_statistic)) / 2.0
    out["significance"] = significance

    out["score"] = (edge * sample * payoff_term * sortino_term
                    * ulcer_term * streak_term * efficiency_term
                    * (0.6 + 0.4 * significance))
    return out


def risk_profile(m: Metrics, *, risk_per_trade: float,
                 account_equity: float = 50_000.0,
                 failure_equity: Optional[float] = None) -> dict:
    """Translate an R-denominated record into what it means for the account.

    R is a clean unit for comparing strategies and a poor one for deciding
    whether to trade. The same +0.3R expectancy is a rounding error at $50 a
    trade and an existential risk at $1,000, and the drawdown is where that
    shows up first - so this reports the observed drawdown in dollars against
    the distance to the account's real failure threshold.
    """
    floor = failure_equity if failure_equity is not None else account_equity * 0.9
    room = max(0.0, account_equity - floor)
    dd_dollars = max(0.0, m.max_drawdown_r) * risk_per_trade
    return {
        "risk_per_trade": risk_per_trade,
        "expectancy_dollars": m.expectancy_r * risk_per_trade,
        "max_drawdown_dollars": dd_dollars,
        "distance_to_failure": room,
        # The observed worst drawdown as a share of the room available. Above
        # 1.0 the strategy has already, in its own backtest, produced a
        # drawdown large enough to end the account at this size.
        "drawdown_vs_room": (dd_dollars / room) if room else float("inf"),
        "survivable_at_this_size": bool(room and dd_dollars < room * 0.5),
        "trades_to_recover_worst_dd": (
            math.ceil(m.max_drawdown_r / m.expectancy_r)
            if m.expectancy_r > 0 and m.max_drawdown_r > 0 else None),
    }
