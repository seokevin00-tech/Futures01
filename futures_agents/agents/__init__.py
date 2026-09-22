"""The six domain agent families that staff the team.

Each module in this package is owned by one agent and implements one role's
reasoning. The integration contract they all satisfy is documented in
``docs/AGENT_CONTRACT.md``.

Imports are individually guarded: a domain agent that fails to import leaves
its role unstaffed and is reported as such by ``build_team``, rather than
taking down the whole package. A partially staffed team that says so is far
more useful than an import error at startup.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .context import AgentContext, build_context
from .base import DomainAgent
from .llm import LLMClient, LLMResponse, build_client

#: Roles whose implementation could not be imported, with the reason.
IMPORT_ERRORS: Dict[str, str] = {}

_OPTIONAL = (
    ("news_macro", ("NewsMacroAgent",)),
    ("research", ("StrategyResearchAgent",)),
    ("research_trend", ("ResearchTrendAgent",)),
    ("research_reversion", ("ResearchReversionAgent",)),
    ("research_liquidity", ("ResearchLiquidityAgent",)),
    ("analysts", ("AnalystAAgent", "AnalystBAgent", "AnalystCAgent")),
    ("decision", ("DecisionAgent",)),
    ("risk_agent", ("RiskAgent",)),
    ("journal_agent", ("JournalAgent",)),
    ("budget_agent", ("BudgetAgent",)),
)

for _module, _names in _OPTIONAL:
    try:
        _mod = __import__(f"{__name__}.{_module}", fromlist=list(_names))
    except Exception as _exc:                          # noqa: BLE001
        IMPORT_ERRORS[_module] = f"{type(_exc).__name__}: {_exc}"
        for _n in _names:
            globals()[_n] = None
        continue
    for _n in _names:
        globals()[_n] = getattr(_mod, _n, None)
        if globals()[_n] is None:
            IMPORT_ERRORS[_module] = f"{_module} does not define {_n}"

__all__ = [
    "AgentContext", "build_context", "DomainAgent",
    "LLMClient", "LLMResponse", "build_client",
    "NewsMacroAgent", "StrategyResearchAgent",
    "ResearchTrendAgent", "ResearchReversionAgent", "ResearchLiquidityAgent",
    "AnalystAAgent", "AnalystBAgent", "AnalystCAgent",
    "DecisionAgent", "RiskAgent", "JournalAgent", "BudgetAgent",
    "IMPORT_ERRORS", "staffing_report",
]


def staffing_report() -> Dict[str, Any]:
    """Which domain agents are available, and why any are missing."""
    available = [n for n in ("NewsMacroAgent", "StrategyResearchAgent",
                             "ResearchTrendAgent", "ResearchReversionAgent",
                             "ResearchLiquidityAgent",
                             "AnalystAAgent", "AnalystBAgent", "AnalystCAgent",
                             "DecisionAgent", "RiskAgent", "JournalAgent",
                             "BudgetAgent")
                 if globals().get(n) is not None]
    return {"available": available, "missing": sorted(IMPORT_ERRORS),
            "errors": dict(IMPORT_ERRORS)}
