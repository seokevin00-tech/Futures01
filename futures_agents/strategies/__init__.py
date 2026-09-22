"""Strategy primitives, the confluence combinator and per-symbol registries."""

from .base import (
    Condition, ConditionKind, ConditionResult, ExitModel, Strategy,
    StrategySignal, StrategyFilters, StopKind,
)
from .library import (
    CONDITIONS, CONDITION_GROUPS, condition, get_condition, conditions_in_group,
)
from .combinator import (
    generate_combinations, generate_strategies, CombinationSpec, expand_exit_models,
)
from .registry import (
    StrategyRegistry, SymbolStrategyGroups, build_registry, STRATEGY_GROUPS,
)

__all__ = [
    "Condition", "ConditionKind", "ConditionResult", "ExitModel", "Strategy",
    "StrategySignal", "StrategyFilters", "StopKind",
    "CONDITIONS", "CONDITION_GROUPS", "condition", "get_condition",
    "conditions_in_group",
    "generate_combinations", "generate_strategies", "CombinationSpec",
    "expand_exit_models",
    "StrategyRegistry", "SymbolStrategyGroups", "build_registry", "STRATEGY_GROUPS",
]
