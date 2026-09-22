"""Contract smoke tests for the domain agents.

The eight domain agents in ``futures_agents/agents/`` are written by other
people, in parallel with this suite. Every check here is therefore *guarded*:
an agent that does not exist yet must never fail this suite, only be skipped.
What is asserted is exactly the integration contract in
``docs/AGENT_CONTRACT.md`` - class name, role, constructor signature, and a
``handle`` method - because those are what make the import succeed and the
manager able to route to it.

Nothing here calls an LLM or touches the network: the deterministic path is the
only path these tests exercise.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from futures_agents.team.bus import MessageBus
from futures_agents.team.roles import ROLES, Role
from futures_agents.team.workspace import TeamFilesystem

# (module, class name, expected role) - straight from the contract table.
CONTRACT: Tuple[Tuple[str, str, Role], ...] = (
    ("news_macro", "NewsMacroAgent", Role.NEWS_MACRO),
    ("research", "StrategyResearchAgent", Role.STRATEGY_RESEARCH),
    ("analysts", "AnalystAAgent", Role.ANALYST_A),
    ("analysts", "AnalystBAgent", Role.ANALYST_B),
    ("analysts", "AnalystCAgent", Role.ANALYST_C),
    ("decision", "DecisionAgent", Role.DECISION),
    ("risk_agent", "RiskAgent", Role.RISK),
    ("journal_agent", "JournalAgent", Role.JOURNAL),
)

IDS = [f"{m}.{c}" for m, c, _ in CONTRACT]


def load(module: str, class_name: str):
    """Import an agent class, skipping cleanly if it is not written yet."""
    mod = pytest.importorskip(
        f"futures_agents.agents.{module}",
        reason=f"futures_agents/agents/{module}.py is not implemented yet")
    cls = getattr(mod, class_name, None)
    if cls is None:
        pytest.skip(f"{module}.py does not define {class_name} yet")
    return cls


@pytest.fixture
def team(tmp_path: Path):
    return TeamFilesystem(base=str(tmp_path / "workspace")), MessageBus()


# --------------------------------------------------------------------------
# The agents package itself is always importable
# --------------------------------------------------------------------------

def test_the_agents_package_imports_even_when_agents_are_missing():
    """Guarded imports mean a half-finished team still starts and says so."""
    import futures_agents.agents as agents
    report = agents.staffing_report()
    assert set(report) == {"available", "missing", "errors"}
    assert isinstance(report["available"], list)
    for role, message in report["errors"].items():
        assert message, f"{role} is missing with no stated reason"


def test_a_missing_agent_is_reported_not_raised():
    import futures_agents.agents as agents
    for name in ("NewsMacroAgent", "DecisionAgent", "RiskAgent"):
        assert hasattr(agents, name), "the name must exist even when unstaffed"


# --------------------------------------------------------------------------
# Per-agent contract
# --------------------------------------------------------------------------

@pytest.mark.parametrize("module,class_name,role", CONTRACT, ids=IDS)
def test_agent_class_exists_with_the_contracted_name(module, class_name, role):
    cls = load(module, class_name)
    assert inspect.isclass(cls)


@pytest.mark.parametrize("module,class_name,role", CONTRACT, ids=IDS)
def test_agent_subclasses_domain_agent(module, class_name, role):
    from futures_agents.agents.base import DomainAgent
    cls = load(module, class_name)
    assert issubclass(cls, DomainAgent), (
        f"{class_name} must subclass DomainAgent to be routable")


@pytest.mark.parametrize("module,class_name,role", CONTRACT, ids=IDS)
def test_agent_constructor_matches_the_contract(module, class_name, role, team):
    """``build_team`` calls ``cls(fs, bus, config=..., llm=..., context=...)``
    and falls back to omitting ``context``, so ``context`` must be keyword-only
    with a None default."""
    cls = load(module, class_name)
    fs, bus = team
    agent = cls(fs, bus, context=None, config=None, llm=None)
    assert agent.role is role, f"{class_name} declared role {agent.role}"

    params = inspect.signature(cls.__init__).parameters
    assert "context" in params
    assert params["context"].default is None
    assert params["context"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("module,class_name,role", CONTRACT, ids=IDS)
def test_agent_can_be_built_without_context(module, class_name, role, team):
    cls = load(module, class_name)
    fs, bus = team
    agent = cls(fs, bus, config=None, llm=None)
    assert agent.role is role
    assert agent.context is None


@pytest.mark.parametrize("module,class_name,role", CONTRACT, ids=IDS)
def test_agent_implements_handle(module, class_name, role):
    cls = load(module, class_name)
    assert callable(getattr(cls, "handle", None)), (
        f"{class_name} must implement handle(task) -> AgentResult")
    params = list(inspect.signature(cls.handle).parameters)
    assert params[:2] == ["self", "task"]


@pytest.mark.parametrize("module,class_name,role", CONTRACT, ids=IDS)
def test_agent_runs_without_a_network_or_an_api_key(module, class_name, role, team):
    """Construction alone must not reach out. The deterministic core is
    required to work with no key and no connection."""
    cls = load(module, class_name)
    fs, bus = team
    agent = cls(fs, bus, context=None, config=None, llm=None)
    assert agent.llm_available is False


@pytest.mark.parametrize("module,class_name,role", CONTRACT, ids=IDS)
def test_agent_role_accepts_the_kinds_the_contract_lists(module, class_name, role):
    """Routing is by declared kind, so an agent whose role accepts nothing can
    never be dispatched any work."""
    load(module, class_name)          # skip if unwritten
    assert ROLES[role].accepts, f"{role.value} accepts no task kinds"


def test_analysts_remain_mutually_unreachable():
    """Worth restating next to the agent classes: independence is the whole
    reason there are three of them."""
    for module, class_name, role in CONTRACT:
        if not class_name.startswith("Analyst"):
            continue
        others = {r for r in (Role.ANALYST_A, Role.ANALYST_B, Role.ANALYST_C)
                  if r is not role}
        assert not (ROLES[role].may_message & others)


# ---------------------------------------------------------------------------
# Regression: cross-market direction parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # Unambiguous readings keep their sign.
    ("+0.4%", 1),
    ("-0.6%", -1),
    ("yields lower", -1),
    ("dollar stronger", 1),
    ("down 0.5%", -1),
    ("0.8", 1),
    ("-1.2", -1),
    ("0", 0),
    ("", 0),
    ("unchanged", 0),
    # Contradictory readings must resolve to "no direction". The original
    # implementation scanned the token table in its own order rather than by
    # position in the string, so the first positive token anywhere outranked
    # any negative token anywhere and both of these read as risk-on.
    ("MNQ +0.8%, MES -0.6%", 0),
    ("equities lower, yields up", 0),
    ("stocks higher, dollar weaker", 0),
])
def test_cross_market_direction_never_guesses_on_a_contradictory_reading(text, expected):
    """A reading that says two opposite things has no direction.

    This sign feeds a macro bias for a live account, so a confidently wrong
    answer is materially worse than no answer.
    """
    analysts = pytest.importorskip("futures_agents.agents.analysts")
    assert analysts._direction_of_text(text) == expected, (
        f"{text!r} should parse as {expected}")
