"""Per-symbol strategy registries.

The specification is explicit that every contract is analysed independently:
MNQ's strategy database, rankings and parameters are its own, and nothing
learned on MES is assumed to transfer. The registry enforces that structurally -
strategies are keyed by symbol, and there is no path by which a strategy
registered for one contract can be evaluated against another.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from ..config import DEFAULT_SYMBOLS, get_contract
from .base import Strategy
from .combinator import TEMPLATES, generate_strategies

__all__ = ["STRATEGY_GROUPS", "SymbolStrategyGroups", "StrategyRegistry",
           "build_registry"]

#: Canonical group names, in the order they are reported.
STRATEGY_GROUPS: Tuple[str, ...] = tuple(t.group for t in TEMPLATES)


@dataclass
class SymbolStrategyGroups:
    """Every strategy known for one contract, bucketed by group."""

    symbol: str
    groups: Dict[str, List[Strategy]] = field(default_factory=lambda: defaultdict(list))

    def add(self, strategy: Strategy) -> None:
        if strategy.symbol != self.symbol:
            raise ValueError(
                f"Strategy {strategy.strategy_id} belongs to {strategy.symbol}, "
                f"not {self.symbol} - strategies are never shared between contracts")
        self.groups[strategy.group].append(strategy)

    def all(self) -> List[Strategy]:
        return [s for g in sorted(self.groups) for s in self.groups[g]]

    def group(self, name: str) -> List[Strategy]:
        return list(self.groups.get(name, ()))

    def by_timeframe(self, timeframe: int) -> List[Strategy]:
        return [s for s in self.all() if s.primary_tf == timeframe]

    def get(self, strategy_id: str) -> Optional[Strategy]:
        for s in self.all():
            if s.strategy_id == strategy_id:
                return s
        return None

    def counts(self) -> Dict[str, int]:
        return {g: len(v) for g, v in sorted(self.groups.items())}

    def __len__(self) -> int:
        return sum(len(v) for v in self.groups.values())

    def __iter__(self) -> Iterator[Strategy]:
        return iter(self.all())


class StrategyRegistry:
    """All symbols' strategy groups, with independent per-symbol storage."""

    def __init__(self) -> None:
        self._by_symbol: Dict[str, SymbolStrategyGroups] = {}
        self._index: Dict[str, Strategy] = {}

    def symbol(self, symbol: str) -> SymbolStrategyGroups:
        key = symbol.upper()
        if key not in self._by_symbol:
            get_contract(key)
            self._by_symbol[key] = SymbolStrategyGroups(key)
        return self._by_symbol[key]

    def add(self, strategy: Strategy) -> None:
        self.symbol(strategy.symbol).add(strategy)
        self._index[strategy.strategy_id] = strategy

    def extend(self, strategies: Iterable[Strategy]) -> None:
        for s in strategies:
            self.add(s)

    def get(self, strategy_id: str) -> Optional[Strategy]:
        return self._index.get(strategy_id)

    def symbols(self) -> List[str]:
        return sorted(self._by_symbol)

    def all(self) -> List[Strategy]:
        return [s for sym in self.symbols() for s in self._by_symbol[sym].all()]

    def counts(self) -> Dict[str, Dict[str, int]]:
        return {sym: self._by_symbol[sym].counts() for sym in self.symbols()}

    def total(self) -> int:
        return len(self._index)

    def __len__(self) -> int:
        return len(self._index)

    def __repr__(self) -> str:
        return (f"<StrategyRegistry symbols={len(self._by_symbol)} "
                f"strategies={len(self._index)}>")


def build_registry(symbols: Sequence[str] = DEFAULT_SYMBOLS,
                   timeframes: Sequence[int] = (1, 5, 15, 60),
                   *, max_per_symbol: int = 4_000,
                   groups: Optional[Sequence[str]] = None,
                   seed: int = 20260922) -> StrategyRegistry:
    """Generate an independent strategy universe for every symbol."""
    reg = StrategyRegistry()
    for sym in symbols:
        reg.extend(generate_strategies(sym, timeframes, groups=groups,
                                       max_total=max_per_symbol, seed=seed))
    return reg
