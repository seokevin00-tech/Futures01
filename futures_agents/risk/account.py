"""Account state: equity, drawdown, the daily ledger and open exposure.

The central idea is that **equity is not the risk budget**. What a $50,000
account can lose is the distance between where it is now and the level at which
it has failed, less a reserve that is never spent. That distance shrinks as the
account draws down, which is exactly when risk per trade should shrink too -
so deriving position size from it makes the system automatically defensive
without anyone having to remember to be.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..config import AccountConfig, ContractSpec, correlated_symbols, get_contract
from ..schema import Direction
from ..timeutil import et_stamp, now_et, to_et, trading_day

__all__ = ["OpenPosition", "DayState", "AccountState"]


@dataclass
class OpenPosition:
    """A live position, for exposure and correlation accounting."""

    symbol: str
    direction: Direction
    contracts: int
    entry: float
    stop: float
    targets: List[float] = field(default_factory=list)
    opened_et: str = field(default_factory=lambda: to_et(now_et()).isoformat())
    strategy_id: str = ""
    dollar_risk: float = 0.0
    callout_id: str = ""

    @property
    def correlation_group(self) -> str:
        return get_contract(self.symbol).correlation_group

    def to_dict(self) -> dict:
        d = asdict(self)
        d["direction"] = self.direction.value
        return d


@dataclass
class DayState:
    """One session's ledger. Reset at the 18:00 ET trading-day roll."""

    day: date
    realised_pnl: float = 0.0
    peak_pnl: float = 0.0          # best point of the day, for giveback protection
    trough_pnl: float = 0.0
    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    callouts_issued: int = 0
    no_trade_decisions: int = 0
    starting_equity: float = 0.0

    def record(self, pnl: float) -> None:
        self.realised_pnl += pnl
        self.peak_pnl = max(self.peak_pnl, self.realised_pnl)
        self.trough_pnl = min(self.trough_pnl, self.realised_pnl)
        self.trades_taken += 1
        if pnl > 0:
            self.wins += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        elif pnl < 0:
            self.losses += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0

    @property
    def giveback(self) -> float:
        """Profit surrendered from the day's peak."""
        return max(0.0, self.peak_pnl - self.realised_pnl)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["day"] = str(self.day)
        d["giveback"] = round(self.giveback, 2)
        return d


@dataclass
class AccountState:
    """Everything the risk layer needs to know about the account right now."""

    config: AccountConfig
    equity: float = 0.0
    peak_equity: float = 0.0
    open_positions: List[OpenPosition] = field(default_factory=list)
    day: Optional[DayState] = None
    closed_trades: int = 0
    lifetime_wins: int = 0
    lifetime_losses: int = 0
    consecutive_losses: int = 0
    equity_curve: List[Tuple[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.equity <= 0:
            self.equity = self.config.starting_equity
        self.peak_equity = max(self.peak_equity, self.equity)
        if self.day is None:
            self.day = DayState(day=trading_day(now_et()),
                                starting_equity=self.equity)
        if not self.equity_curve:
            self.equity_curve.append((to_et(now_et()).isoformat(), self.equity))

    # ---- drawdown accounting -----------------------------------------
    @property
    def failure_equity(self) -> float:
        """The equity level at which the account is considered failed."""
        return self.config.failure_equity(self.peak_equity)

    @property
    def drawdown(self) -> float:
        return max(0.0, self.peak_equity - self.equity)

    @property
    def drawdown_pct(self) -> float:
        return self.drawdown / self.peak_equity if self.peak_equity else 0.0

    @property
    def remaining_drawdown(self) -> float:
        """Raw dollars between here and account failure."""
        return max(0.0, self.equity - self.failure_equity)

    @property
    def usable_buffer(self) -> float:
        """Risk budget after reserving the protected buffer.

        This - not equity - is what position size is derived from.
        """
        return self.config.usable_buffer(self.equity, self.peak_equity)

    @property
    def buffer_consumed_pct(self) -> float:
        full = self.config.max_total_drawdown * (1.0 - self.config.protected_buffer_pct)
        return 1.0 - (self.usable_buffer / full) if full > 0 else 1.0

    @property
    def derisk_multiplier(self) -> float:
        return self.config.derisk_multiplier(self.equity, self.peak_equity)

    @property
    def has_failed(self) -> bool:
        return self.equity <= self.failure_equity

    # ---- daily accounting --------------------------------------------
    def roll_day(self, when: Optional[datetime] = None) -> DayState:
        """Start a new session ledger if the trading day has rolled."""
        today = trading_day(when or now_et())
        if self.day is None or self.day.day != today:
            self.day = DayState(day=today, starting_equity=self.equity)
        return self.day

    @property
    def daily_pnl(self) -> float:
        return self.day.realised_pnl if self.day else 0.0

    @property
    def remaining_daily_loss_budget(self) -> float:
        return max(0.0, self.config.daily_loss_limit + min(0.0, self.daily_pnl))

    # ---- exposure -----------------------------------------------------
    def position_for(self, symbol: str) -> Optional[OpenPosition]:
        key = symbol.upper()
        for p in self.open_positions:
            if p.symbol == key:
                return p
        return None

    def correlated_exposure(self, symbol: str) -> List[OpenPosition]:
        """Open positions in the same correlation group.

        Two longs in MNQ and MES are not two positions, they are one larger
        position on the same underlying risk.
        """
        group = get_contract(symbol).correlation_group
        if not group:
            return []
        return [p for p in self.open_positions if p.correlation_group == group]

    @property
    def open_risk(self) -> float:
        return sum(p.dollar_risk for p in self.open_positions)

    # ---- mutation -----------------------------------------------------
    def open_position(self, position: OpenPosition) -> None:
        self.open_positions.append(position)
        if self.day:
            self.day.trades_taken += 0      # counted on close, not on open

    def close_position(self, symbol: str, pnl: float,
                       when: Optional[datetime] = None) -> Optional[OpenPosition]:
        """Realise a position's P&L into equity and the daily ledger."""
        pos = self.position_for(symbol)
        if pos is None:
            return None
        self.open_positions.remove(pos)
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.closed_trades += 1
        if pnl > 0:
            self.lifetime_wins += 1
            self.consecutive_losses = 0
        elif pnl < 0:
            self.lifetime_losses += 1
            self.consecutive_losses += 1
        self.roll_day(when).record(pnl)
        self.equity_curve.append((to_et(when or now_et()).isoformat(), self.equity))
        return pos

    def apply_pnl(self, pnl: float, when: Optional[datetime] = None) -> None:
        """Record a realised result with no tracked position (e.g. replay)."""
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.closed_trades += 1
        if pnl > 0:
            self.lifetime_wins += 1
            self.consecutive_losses = 0
        elif pnl < 0:
            self.lifetime_losses += 1
            self.consecutive_losses += 1
        self.roll_day(when).record(pnl)
        self.equity_curve.append((to_et(when or now_et()).isoformat(), self.equity))

    # ---- reporting ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "failure_equity": round(self.failure_equity, 2),
            "drawdown": round(self.drawdown, 2),
            "drawdown_pct": round(self.drawdown_pct, 5),
            "remaining_drawdown": round(self.remaining_drawdown, 2),
            "usable_buffer": round(self.usable_buffer, 2),
            "buffer_consumed_pct": round(self.buffer_consumed_pct, 4),
            "derisk_multiplier": round(self.derisk_multiplier, 3),
            "daily_pnl": round(self.daily_pnl, 2),
            "remaining_daily_loss_budget": round(self.remaining_daily_loss_budget, 2),
            "open_positions": [p.to_dict() for p in self.open_positions],
            "open_risk": round(self.open_risk, 2),
            "closed_trades": self.closed_trades,
            "consecutive_losses": self.consecutive_losses,
            "day": self.day.to_dict() if self.day else None,
            "has_failed": self.has_failed,
        }

    def render(self) -> str:
        return "\n".join([
            f"ACCOUNT  [{et_stamp()}]",
            f"  Equity                 ${self.equity:>10,.2f}   "
            f"(peak ${self.peak_equity:,.2f})",
            f"  Failure threshold      ${self.failure_equity:>10,.2f}   "
            f"({'TRAILING' if self.config.trailing_drawdown else 'STATIC'})",
            f"  Distance to failure    ${self.remaining_drawdown:>10,.2f}",
            f"  Usable risk buffer     ${self.usable_buffer:>10,.2f}   "
            f"({self.buffer_consumed_pct:.0%} consumed)",
            f"  Risk multiplier         {self.derisk_multiplier:>10.2f}x",
            f"  Today P&L              ${self.daily_pnl:>10,.2f}   "
            f"(loss budget left ${self.remaining_daily_loss_budget:,.2f})",
            f"  Trades today            {self.day.trades_taken if self.day else 0:>10}   "
            f"(consecutive losses {self.day.consecutive_losses if self.day else 0})",
            f"  Open positions          {len(self.open_positions):>10}   "
            f"(open risk ${self.open_risk:,.2f})",
        ])
