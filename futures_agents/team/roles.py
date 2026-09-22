"""Role definitions - who is on the team and what each one owns.

A role is a contract: a stable id, the folder the agent owns, the kinds of task
it accepts, who it is allowed to talk to, and whether it may write code. The
manager routes by these declarations rather than by hard-coded branching, so
adding a role is a data change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

__all__ = ["Role", "RoleSpec", "ROLES", "role_for_task", "roles_for_task",
           "TASK_ROUTING", "is_ambiguous", "may_message"]


class Role(str, Enum):
    """Every seat on the team."""

    MANAGER = "manager"
    DEVELOPER = "developer"
    NEWS_MACRO = "news_macro"
    STRATEGY_RESEARCH = "strategy_research"
    ANALYST_A = "analyst_a"          # technical / market structure
    ANALYST_B = "analyst_b"          # quantitative / statistical
    ANALYST_C = "analyst_c"          # macro / news / context
    DECISION = "decision"
    RISK = "risk"
    JOURNAL = "journal"

    @property
    def folder(self) -> str:
        return self.value


@dataclass(frozen=True)
class RoleSpec:
    """Declarative description of one role."""

    role: Role
    title: str
    mandate: str
    #: Task kinds this role accepts. The manager routes on these.
    accepts: FrozenSet[str]
    #: Roles this one may address directly. The bus enforces it.
    may_message: FrozenSet[Role]
    #: Artefact names this role publishes for others to read.
    publishes: Tuple[str, ...] = ()
    #: Artefacts it expects to consume.
    consumes: Tuple[str, ...] = ()
    writes_code: bool = False
    #: Independence flag: analysts must not read each other's conclusions
    #: before forming their own, or the "three independent analysts" design
    #: collapses into one analyst with two echoes.
    independent: bool = False
    llm_backed: bool = True

    @property
    def id(self) -> str:
        return self.role.value


_A = frozenset


ROLES: Dict[Role, RoleSpec] = {
    Role.MANAGER: RoleSpec(
        role=Role.MANAGER,
        title="Manager",
        mandate=(
            "Owns the task board. Decomposes incoming requests into tasks, routes "
            "each to the role that owns it, tracks status, resolves blockers and "
            "reports progress. Never performs domain work itself."
        ),
        accepts=_A({"plan", "dispatch", "status", "review", "decompose"}),
        may_message=_A(set(Role)),
        publishes=("task_board", "run_report"),
        consumes=("*",),
        llm_backed=True,
    ),
    Role.DEVELOPER: RoleSpec(
        role=Role.DEVELOPER,
        title="Developer",
        mandate=(
            "Backend engineering. Implements and maintains the data layer, "
            "indicators, feature engine, strategy framework, backtester, risk "
            "engine and storage. Writes tests and keeps the deterministic core "
            "dependency-free and reproducible."
        ),
        accepts=_A({"implement", "fix", "refactor", "test", "benchmark", "migrate"}),
        may_message=_A({Role.MANAGER, Role.STRATEGY_RESEARCH, Role.RISK, Role.JOURNAL}),
        publishes=("code", "test_report", "benchmark"),
        consumes=("spec", "defect_report"),
        writes_code=True,
        llm_backed=True,
    ),
    Role.NEWS_MACRO: RoleSpec(
        role=Role.NEWS_MACRO,
        title="News & Macro Research",
        mandate=(
            "Continuously researches news, economic releases, central-bank "
            "activity, geopolitics and overnight global markets. Maintains the "
            "news -> market-reaction database and measures what actually happened "
            "after comparable past events, per symbol and per horizon."
        ),
        accepts=_A({"research_news", "update_calendar", "record_reaction",
                    "assess_news_risk"}),
        may_message=_A({Role.MANAGER, Role.ANALYST_C, Role.DECISION,
                        Role.STRATEGY_RESEARCH, Role.RISK}),
        publishes=("news_context", "news_reaction_db", "event_calendar"),
        consumes=("market_data",),
    ),
    Role.STRATEGY_RESEARCH: RoleSpec(
        role=Role.STRATEGY_RESEARCH,
        title="Strategy Research & Backtesting",
        mandate=(
            "Discovers, composes and tests strategies and confluence "
            "combinations per symbol and per timeframe. Runs walk-forward, "
            "Monte Carlo and robustness analysis, and publishes only what "
            "survives out of sample."
        ),
        accepts=_A({"research_strategies", "backtest", "walk_forward",
                    "optimise", "rank_strategies", "robustness"}),
        may_message=_A({Role.MANAGER, Role.DEVELOPER, Role.ANALYST_B,
                        Role.DECISION, Role.JOURNAL, Role.RISK}),
        publishes=("strategy_rankings", "performance_db", "robustness_report"),
        consumes=("market_data", "news_context", "journal_feedback"),
    ),
    Role.ANALYST_A: RoleSpec(
        role=Role.ANALYST_A,
        title="Live Analyst A - Technical & Market Structure",
        mandate=(
            "Reads price action, market structure, liquidity, support and "
            "resistance, trend, breakouts, reversals, multi-timeframe alignment, "
            "volume, VWAP and order flow. Forms an independent view."
        ),
        accepts=_A({"predict", "reassess"}),
        may_message=_A({Role.MANAGER, Role.DECISION}),
        publishes=("prediction_a",),
        consumes=("market_data", "strategy_rankings"),
        independent=True,
    ),
    Role.ANALYST_B: RoleSpec(
        role=Role.ANALYST_B,
        title="Live Analyst B - Quantitative & Statistical",
        mandate=(
            "Reasons from historical probabilities, backtested statistics, "
            "market regimes, volatility, time-of-day behaviour and expected "
            "value. Relies on measured data rather than chart interpretation."
        ),
        accepts=_A({"predict", "reassess"}),
        may_message=_A({Role.MANAGER, Role.DECISION, Role.STRATEGY_RESEARCH}),
        publishes=("prediction_b",),
        consumes=("market_data", "strategy_rankings", "performance_db"),
        independent=True,
    ),
    Role.ANALYST_C: RoleSpec(
        role=Role.ANALYST_C,
        title="Live Analyst C - Macro, News & Market Context",
        mandate=(
            "Judges whether the fundamental and cross-market environment "
            "supports or contradicts the technical and quantitative setups: "
            "releases, central banks, yields, the dollar, commodities, "
            "correlations and upcoming catalysts."
        ),
        accepts=_A({"predict", "reassess"}),
        may_message=_A({Role.MANAGER, Role.DECISION, Role.NEWS_MACRO}),
        publishes=("prediction_c",),
        consumes=("market_data", "news_context"),
        independent=True,
    ),
    Role.DECISION: RoleSpec(
        role=Role.DECISION,
        title="Trade Decision & Confluence",
        mandate=(
            "Receives all agent output, weighs it against measured historical "
            "accuracy under the current regime, and concludes LONG, SHORT or "
            "NO TRADE. Never takes a majority vote. NO TRADE is a legitimate "
            "conclusion."
        ),
        accepts=_A({"decide", "review_setup"}),
        may_message=_A({Role.MANAGER, Role.RISK, Role.JOURNAL, Role.ANALYST_A,
                        Role.ANALYST_B, Role.ANALYST_C, Role.NEWS_MACRO,
                        Role.STRATEGY_RESEARCH}),
        publishes=("decision", "callout"),
        consumes=("prediction_a", "prediction_b", "prediction_c", "news_context",
                  "strategy_rankings", "risk_assessment"),
    ),
    Role.RISK: RoleSpec(
        role=Role.RISK,
        title="Risk Management",
        mandate=(
            "Independent of every prediction agent. Sizes positions, enforces "
            "daily and drawdown limits, monitors correlation and consecutive "
            "losses, and holds an unconditional veto over any trade. Its first "
            "duty is preventing account failure, not finding trades."
        ),
        accepts=_A({"size_position", "assess_risk", "check_limits", "veto"}),
        may_message=_A({Role.MANAGER, Role.DECISION, Role.JOURNAL}),
        publishes=("risk_assessment", "account_state"),
        consumes=("decision", "account_state", "news_context"),
        llm_backed=False,          # risk decisions stay deterministic and auditable
    ),
    Role.JOURNAL: RoleSpec(
        role=Role.JOURNAL,
        title="Journal & Continuous Learning",
        mandate=(
            "Records every prediction, decision and outcome with full context. "
            "Measures which agents and strategies are actually accurate under "
            "which conditions, and feeds that back into strategy weighting."
        ),
        accepts=_A({"record", "resolve_trade", "evaluate_agents", "learn"}),
        may_message=_A({Role.MANAGER, Role.STRATEGY_RESEARCH, Role.DECISION,
                        Role.RISK}),
        publishes=("journal", "agent_scorecard", "journal_feedback"),
        consumes=("callout", "decision", "market_data"),
        llm_backed=False,
    ),
}


def _build_routing() -> Dict[str, Tuple[Role, ...]]:
    table: Dict[str, List[Role]] = {}
    for spec in ROLES.values():
        for kind in spec.accepts:
            table.setdefault(kind, []).append(spec.role)
    return {k: tuple(v) for k, v in sorted(table.items())}


#: Task kind -> every role that accepts it. Several kinds are intentionally
#: ambiguous: all three live analysts accept "predict", because there is no
#: single correct analyst for a prediction - the point is that all three form
#: one independently.
TASK_ROUTING: Dict[str, Tuple[Role, ...]] = _build_routing()


def roles_for_task(kind: str) -> Tuple[Role, ...]:
    """Every role that accepts this task kind."""
    return TASK_ROUTING.get(kind, ())


def role_for_task(kind: str) -> Optional[Role]:
    """The single owner of a task kind.

    Returns ``None`` when nothing accepts the kind *or* when more than one role
    does. An ambiguous kind must be dispatched with an explicit assignee -
    silently handing "predict" to whichever analyst happened to be last in the
    dictionary would quietly reduce three independent opinions to one.
    """
    owners = TASK_ROUTING.get(kind, ())
    return owners[0] if len(owners) == 1 else None


def is_ambiguous(kind: str) -> bool:
    return len(TASK_ROUTING.get(kind, ())) > 1


def may_message(sender: Role, recipient: Role) -> bool:
    """Whether ``sender`` is permitted to address ``recipient`` directly."""
    spec = ROLES.get(sender)
    if spec is None:
        return False
    return recipient in spec.may_message
