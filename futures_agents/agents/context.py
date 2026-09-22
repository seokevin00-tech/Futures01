"""Shared runtime context handed to every domain agent.

Agents do not construct data providers, open databases or build feature frames
themselves - they receive one context. That keeps expensive objects (a
:class:`SymbolFrame` costs seconds to build and megabytes to hold) built once
per cycle and shared, and it means a replay run can swap in a time-limited
provider and every agent is automatically pinned to that instant without any
of them having to cooperate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from ..config import SystemConfig, get_contract
from ..data.providers import DataProvider, SyntheticProvider
from ..features import FeatureSnapshot, SymbolFrame
from ..risk.account import AccountState
from ..risk.manager import RiskManager
from ..schema import NewsContext
from ..storage import Storage
from ..strategies.registry import StrategyRegistry
from ..timeutil import now_et, to_et

__all__ = ["AgentContext", "build_context"]


@dataclass
class AgentContext:
    """Everything a domain agent needs, built once per cycle."""

    config: SystemConfig
    storage: Storage
    provider: DataProvider
    account: AccountState
    risk: RiskManager
    registry: StrategyRegistry = field(default_factory=StrategyRegistry)
    as_of: Optional[datetime] = None
    news: Optional[NewsContext] = None
    _frames: Dict[str, SymbolFrame] = field(default_factory=dict, repr=False)
    _snapshots: Dict[str, FeatureSnapshot] = field(default_factory=dict, repr=False)
    notes: List[str] = field(default_factory=list)

    # ---- data access --------------------------------------------------
    def frame(self, symbol: str, timeframes: Optional[Sequence[int]] = None
              ) -> SymbolFrame:
        """Build (and cache) the multi-timeframe frame for a symbol."""
        key = symbol.upper()
        if key not in self._frames:
            base = self.provider.base_series(key)
            tfs = tuple(timeframes or self.config.timeframes)
            self._frames[key] = SymbolFrame(base, tfs, get_contract(key))
        return self._frames[key]

    def snapshot(self, symbol: str) -> Optional[FeatureSnapshot]:
        """The current feature snapshot, pinned to ``as_of`` when replaying."""
        key = symbol.upper()
        if key in self._snapshots:
            return self._snapshots[key]
        frame = self.frame(key)
        if len(frame.base) == 0:
            return None
        index = len(frame.base) - 1
        if self.as_of is not None:
            cutoff = to_et(self.as_of)
            index = -1
            for i, bar in enumerate(frame.base.bars):
                if bar.end_ts <= cutoff:
                    index = i
                else:
                    break
            if index < 0:
                return None
        snap = frame.snapshot(index)
        if snap is not None:
            self._snapshots[key] = snap
        return snap

    def invalidate(self, symbol: Optional[str] = None) -> None:
        """Drop cached snapshots when the clock moves."""
        if symbol:
            self._snapshots.pop(symbol.upper(), None)
        else:
            self._snapshots.clear()

    # ---- convenience --------------------------------------------------
    @property
    def symbols(self) -> List[str]:
        return [s.upper() for s in self.config.symbols]

    def now(self) -> datetime:
        return to_et(self.as_of) if self.as_of else now_et()

    def strategies_for(self, symbol: str) -> List[Any]:
        return self.registry.symbol(symbol).all()

    def top_strategies(self, symbol: str, *, limit: int = 8,
                       regime: Optional[str] = None,
                       live_eligible_only: bool = True) -> List[Dict[str, Any]]:
        """Best measured strategies for a symbol, from the performance database."""
        rows = self.storage.top_strategies(
            symbol, limit=limit, regime=regime,
            live_eligible_only=live_eligible_only)
        if not rows and live_eligible_only:
            # Nothing has cleared full eligibility yet. Return the best
            # available anyway, clearly flagged, rather than pretending the
            # research produced nothing.
            rows = self.storage.top_strategies(symbol, limit=limit, regime=regime,
                                               live_eligible_only=False)
            for r in rows:
                r["_not_yet_eligible"] = True
        return rows

    def to_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "as_of": to_et(self.as_of).isoformat() if self.as_of else None,
            "account": self.account.to_dict(),
            "strategies_registered": self.registry.total(),
            "storage": self.storage.counts(),
            "notes": self.notes,
        }


def build_context(config: SystemConfig, *, provider: Optional[DataProvider] = None,
                  storage: Optional[Storage] = None,
                  account: Optional[AccountState] = None,
                  registry: Optional[StrategyRegistry] = None,
                  as_of: Optional[datetime] = None) -> AgentContext:
    """Assemble a context, defaulting to synthetic data and a fresh account.

    The synthetic default is deliberate: the entire system must be runnable end
    to end with no data vendor, so that the research and risk layers can be
    inspected and tested by anyone.
    """
    prov = provider or SyntheticProvider(config.symbols, days=120, seed=7)
    store = storage or Storage(config.db_path)
    acct = account or AccountState(config=config.account)
    return AgentContext(
        config=config, storage=store, provider=prov, account=acct,
        risk=RiskManager(config.account, acct),
        registry=registry or StrategyRegistry(), as_of=as_of,
    )
