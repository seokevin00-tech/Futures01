"""Team assembly - wires roles, workspaces, the bus and the manager together.

``build_team`` is the single entry point. It creates every agent's private
folder, stands up the bus, instantiates whichever roles are available, and
registers them with the manager. Roles whose implementation is not present are
reported as *not staffed* rather than silently skipped: a run that produced no
analyst predictions because the analysts were never created should say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Type

from ..timeutil import et_stamp
from .agent import AgentResult, TeamAgent
from .board import Task, TaskBoard, TaskStatus
from .bus import MessageBus
from .developer import DeveloperAgent
from .manager import ManagerAgent, RunReport
from .roles import Role, ROLES
from .workspace import TeamFilesystem

__all__ = ["Team", "build_team"]


@dataclass
class Team:
    """An assembled team: manager, staffed agents, shared bus and filesystem."""

    manager: ManagerAgent
    bus: MessageBus
    fs: TeamFilesystem
    agents: Dict[Role, TeamAgent] = field(default_factory=dict)
    unstaffed: List[Role] = field(default_factory=list)
    config: Any = None

    # ---- access -------------------------------------------------------
    def agent(self, role: Role) -> Optional[TeamAgent]:
        return self.agents.get(role)

    @property
    def board(self) -> TaskBoard:
        return self.manager.board

    def is_staffed(self, role: Role) -> bool:
        return role in self.agents

    def roster(self) -> str:
        return self.manager.roster()

    # ---- running ------------------------------------------------------
    def plan(self, symbols: Sequence[str], **kw) -> List[Task]:
        return self.manager.plan_research_and_decide(symbols, **kw)

    def run(self, **kw) -> RunReport:
        return self.manager.run_board(**kw)

    def transcript(self, limit: int = 60) -> str:
        return self.bus.transcript(limit)

    def tree(self) -> str:
        return self.fs.tree()

    def summary(self) -> str:
        lines = [f"TEAM  [{et_stamp()}]",
                 f"  staffed:   {len(self.agents)} of {len(Role) - 1} roles"]
        if self.unstaffed:
            lines.append(f"  unstaffed: {', '.join(r.value for r in self.unstaffed)}")
        lines.append(f"  board:     {self.board.counts() or 'empty'}")
        lines.append(f"  messages:  {len(self.bus.journal)}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"<Team staffed={len(self.agents)} unstaffed={len(self.unstaffed)} "
                f"tasks={len(self.board)}>")


def _domain_agent_classes() -> Dict[Role, Type[TeamAgent]]:
    """Import the domain agents lazily.

    Lazy so that the team layer stays importable (and testable) even while the
    domain agents are being built, and so a broken domain agent cannot prevent
    the manager and developer from running.
    """
    out: Dict[Role, Type[TeamAgent]] = {}
    try:
        from ..agents import (AnalystAAgent, AnalystBAgent, AnalystCAgent,
                              DecisionAgent, JournalAgent, NewsMacroAgent,
                              RiskAgent, StrategyResearchAgent,
                              ResearchTrendAgent, ResearchReversionAgent,
                              ResearchLiquidityAgent)
    except ImportError:
        return out
    out.update({
        Role.NEWS_MACRO: NewsMacroAgent,
        Role.STRATEGY_RESEARCH: StrategyResearchAgent,
        Role.RESEARCH_TREND: ResearchTrendAgent,
        Role.RESEARCH_REVERSION: ResearchReversionAgent,
        Role.RESEARCH_LIQUIDITY: ResearchLiquidityAgent,
        Role.ANALYST_A: AnalystAAgent,
        Role.ANALYST_B: AnalystBAgent,
        Role.ANALYST_C: AnalystCAgent,
        Role.DECISION: DecisionAgent,
        Role.RISK: RiskAgent,
        Role.JOURNAL: JournalAgent,
    })
    return out


def build_team(config: Any = None, *, workspace_root: str = "workspace",
               llm: Any = None, enforce_permissions: bool = True,
               extra_agents: Optional[Dict[Role, Type[TeamAgent]]] = None,
               context: Optional[Dict[str, Any]] = None) -> Team:
    """Create every agent, give each its own folder, and wire the bus.

    ``context`` is passed to the domain agents and carries the shared runtime
    objects they need (data provider, strategy registry, storage, account).
    """
    fs = TeamFilesystem(workspace_root)
    bus = MessageBus(enforce_permissions=enforce_permissions)

    manager = ManagerAgent(fs, bus, config=config, llm=llm)
    agents: Dict[Role, TeamAgent] = {}
    unstaffed: List[Role] = []

    classes: Dict[Role, Type[TeamAgent]] = {Role.DEVELOPER: DeveloperAgent}
    classes.update(_domain_agent_classes())
    if extra_agents:
        classes.update(extra_agents)

    for role in Role:
        if role is Role.MANAGER:
            continue
        cls = classes.get(role)
        if cls is None:
            unstaffed.append(role)
            continue
        try:
            kwargs: Dict[str, Any] = {"config": config, "llm": llm}
            if context is not None:
                kwargs["context"] = context
            try:
                agent = cls(fs, bus, **kwargs)
            except TypeError:
                kwargs.pop("context", None)
                agent = cls(fs, bus, **kwargs)
        except Exception as exc:                        # noqa: BLE001
            manager.log(f"could not staff {role.value}: {type(exc).__name__}: {exc}")
            unstaffed.append(role)
            continue
        agents[role] = agent
        manager.register(agent)

    manager.log(f"team assembled: {len(agents)} staffed, {len(unstaffed)} unstaffed")
    return Team(manager=manager, bus=bus, fs=fs, agents=agents,
                unstaffed=unstaffed, config=config)
