"""Execution cost modelling.

Costs are not a rounding error on an intraday futures system - they are often
the whole result. A 1.2R average winner on MNQ with a 10-point stop is $24 of
edge per micro contract against roughly $1.94 of round-turn cost, so an 8%
haircut. On a 4-point stop it is a 20% haircut, and strategies that look
profitable gross are frequently negative net.

Every backtest in this system is run net of commission, exchange fees and
slippage, and the slippage model widens with volatility and thin liquidity
rather than being a flat constant, because that is when real fills degrade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..config import ContractSpec

__all__ = ["SlippageModel", "FillModel", "CostModel"]


@dataclass(frozen=True)
class SlippageModel:
    """Slippage in ticks, as a function of order type and conditions."""

    base_ticks: float = 0.5               # marketable limit into a normal book
    stop_order_extra_ticks: float = 1.0   # stops are market orders; they slip more
    volatility_coefficient: float = 0.6   # extra ticks per unit of ATR percentile
    thin_book_extra_ticks: float = 1.0    # thin/overnight liquidity penalty
    news_extra_ticks: float = 2.0         # inside a high-impact release window

    def ticks(self, *, is_stop: bool = False, atr_percentile: Optional[float] = None,
              thin: bool = False, news: bool = False) -> float:
        t = self.base_ticks
        if is_stop:
            t += self.stop_order_extra_ticks
        if atr_percentile is not None:
            # Only the upper half of the volatility distribution adds slippage.
            t += self.volatility_coefficient * max(0.0, atr_percentile - 0.5) * 2.0
        if thin:
            t += self.thin_book_extra_ticks
        if news:
            t += self.news_extra_ticks
        return max(0.0, t)


@dataclass(frozen=True)
class FillModel:
    """How and when an order is assumed to fill.

    ``entry_on_next_open`` is the honest default. A signal computed from bar
    *i*'s close cannot be acted on until bar *i+1* opens - filling at bar *i*'s
    close means the backtest traded on information it did not have, and is the
    single most common reason a system's live results diverge from its
    backtest.
    """

    entry_on_next_open: bool = True
    #: When a bar's range contains both the stop and a target, assume the stop
    #: filled first. Without tick data the order is unknowable, so the system
    #: takes the pessimistic reading rather than the flattering one.
    stop_before_target_in_same_bar: bool = True
    #: Gaps through a level fill at the open, not at the level.
    honour_gaps: bool = True


@dataclass(frozen=True)
class CostModel:
    """All-in round-turn cost for a contract, in dollars and in R."""

    spec: ContractSpec
    slippage: SlippageModel = SlippageModel()
    fill: FillModel = FillModel()
    commission_override: Optional[float] = None    # per side, per contract

    def commission_per_side(self) -> float:
        if self.commission_override is not None:
            return self.commission_override
        return self.spec.commission_per_side + self.spec.exchange_fee_per_side

    def slippage_dollars(self, *, is_stop: bool = False,
                         atr_percentile: Optional[float] = None,
                         thin: bool = False, news: bool = False) -> float:
        ticks = self.slippage.ticks(is_stop=is_stop, atr_percentile=atr_percentile,
                                    thin=thin, news=news)
        return ticks * self.spec.tick_value

    def slippage_price(self, *, is_stop: bool = False,
                       atr_percentile: Optional[float] = None,
                       thin: bool = False, news: bool = False) -> float:
        """Slippage expressed as a price offset (always adverse)."""
        ticks = self.slippage.ticks(is_stop=is_stop, atr_percentile=atr_percentile,
                                    thin=thin, news=news)
        return ticks * self.spec.tick_size

    def round_turn_dollars(self, *, contracts: int = 1,
                           atr_percentile: Optional[float] = None,
                           exit_is_stop: bool = True, thin: bool = False,
                           news: bool = False) -> float:
        """Total cost of opening and closing ``contracts`` contracts."""
        fees = 2.0 * self.commission_per_side() * contracts
        entry_slip = self.slippage_dollars(is_stop=False, atr_percentile=atr_percentile,
                                           thin=thin, news=news) * contracts
        exit_slip = self.slippage_dollars(is_stop=exit_is_stop,
                                          atr_percentile=atr_percentile,
                                          thin=thin, news=news) * contracts
        return fees + entry_slip + exit_slip

    def cost_in_r(self, risk_points: float, *, contracts: int = 1, **kw) -> float:
        """Round-turn cost expressed as a fraction of one R.

        This is the number that decides whether an edge survives. A strategy
        with 0.15R of gross expectancy and 0.12R of cost has 0.03R of real
        edge, and no amount of backtest equity-curve aesthetics changes that.
        """
        risk_dollars = abs(risk_points) * self.spec.point_value * max(1, contracts)
        if risk_dollars <= 0:
            return float("inf")
        return self.round_turn_dollars(contracts=contracts, **kw) / risk_dollars
