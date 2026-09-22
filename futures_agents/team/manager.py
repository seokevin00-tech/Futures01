"""The Manager agent - owns the task board and distributes work.

The manager does no domain work. It decomposes a request into tasks, routes
each to the role that declares it can do it, gates on dependencies, retries
transient failures, and reports. Keeping it free of trading logic is
deliberate: the moment the coordinator starts forming opinions about the market
it stops being a neutral scheduler and starts biasing which evidence gets
gathered.

The standard research-and-decide plan it builds mirrors the system
specification exactly:

    verify core -> research news -> research & backtest strategies
      -> three independent analyst predictions (no cross-reading)
      -> decision -> risk assessment (veto power) -> journal
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..timeutil import et_stamp, now_et
from .agent import AgentResult, TeamAgent
from .board import Task, TaskBoard, TaskPriority, TaskStatus
from .bus import MessageBus, MessageKind
from .roles import Role, ROLES, role_for_task
from .workspace import TeamFilesystem

__all__ = ["ManagerAgent", "RunReport"]


@dataclass
class RunReport:
    """What one managed run produced."""

    started_et: str
    finished_et: str = ""
    duration_s: float = 0.0
    tasks_total: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    tasks_skipped: int = 0
    tasks_blocked: int = 0
    results: Dict[str, Any] = field(default_factory=dict)
    board: Dict[str, Any] = field(default_factory=dict)
    messages: int = 0
    halted: bool = False
    halt_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "started_et": self.started_et, "finished_et": self.finished_et,
            "duration_s": round(self.duration_s, 2),
            "tasks": {"total": self.tasks_total, "done": self.tasks_done,
                      "failed": self.tasks_failed, "skipped": self.tasks_skipped,
                      "blocked": self.tasks_blocked},
            "messages": self.messages, "halted": self.halted,
            "halt_reason": self.halt_reason, "board": self.board,
        }

    @property
    def ok(self) -> bool:
        return self.tasks_failed == 0 and not self.halted

    def summary(self) -> str:
        return (f"{self.tasks_done}/{self.tasks_total} tasks done, "
                f"{self.tasks_failed} failed, {self.tasks_blocked} blocked, "
                f"{self.messages} messages, {self.duration_s:.1f}s"
                + (f" | HALTED: {self.halt_reason}" if self.halted else ""))


class ManagerAgent(TeamAgent):
    """Plans, routes and supervises. Performs no analysis of its own."""

    def __init__(self, fs: TeamFilesystem, bus: MessageBus, *, config: Any = None,
                 llm: Any = None):
        super().__init__(Role.MANAGER, fs, bus, config=config, llm=llm)
        self.board = TaskBoard()
        self.team: Dict[Role, TeamAgent] = {}
        self._on_task: Optional[Callable[[Task, AgentResult], None]] = None

    # ---- team assembly ------------------------------------------------
    def register(self, agent: TeamAgent) -> None:
        if agent.role is Role.MANAGER:
            return
        self.team[agent.role] = agent
        self.log(f"registered {agent.id} ({agent.title})")

    def roster(self) -> str:
        lines = [f"TEAM ROSTER  [{et_stamp()}]",
                 f"  {'ROLE':<20} {'TITLE':<46} WORKSPACE"]
        for role in Role:
            if role is Role.MANAGER:
                agent_title = ROLES[role].title + " (this agent)"
            elif role in self.team:
                agent_title = self.team[role].title
            else:
                agent_title = ROLES[role].title + "  [not staffed]"
            ws = self.fs.workspace(role).root.name
            lines.append(f"  {role.value:<20} {agent_title:<46} workspace/{ws}/")
        return "\n".join(lines)

    # ---- planning -----------------------------------------------------
    def plan_research_and_decide(
        self, symbols: Sequence[str], *, timeframes: Sequence[int] = (1, 5, 15, 60),
        include_developer_check: bool = True, include_research: bool = True,
        live_only: bool = False,
    ) -> List[Task]:
        """Build the standard end-to-end plan.

        Analyst tasks depend on the news and strategy artefacts but *not* on
        each other, so they can be run in any order without one contaminating
        the next.
        """
        board = self.board
        created: List[Task] = []

        dev_id: List[str] = []
        if include_developer_check:
            t = board.add("test", "Verify deterministic core (look-ahead, costs, invariants)",
                          detail="Run the engineering self-checks before any research "
                                 "output is trusted.",
                          priority=TaskPriority.HIGH)
            created.append(t)
            dev_id = [t.task_id]

        news = board.add("research_news", "Research current news and macro conditions",
                         detail="Economic calendar, releases, central banks, "
                                "geopolitics, overnight global markets.",
                         priority=TaskPriority.HIGH,
                         payload={"symbols": list(symbols)})
        created.append(news)

        research_ids: List[str] = []
        if include_research and not live_only:
            for sym in symbols:
                r = board.add("research_strategies",
                              f"Generate and test strategy combinations for {sym}",
                              detail="Independent per-symbol strategy universe across "
                                     "all timeframes and timeframe groups.",
                              depends_on=tuple(dev_id),
                              payload={"symbol": sym, "timeframes": list(timeframes)})
                w = board.add("walk_forward",
                              f"Walk-forward and robustness analysis for {sym}",
                              detail="In-sample selection, out-of-sample evaluation, "
                                     "Monte Carlo and anti-overfitting checks.",
                              depends_on=(r.task_id,),
                              payload={"symbol": sym})
                created += [r, w]
                research_ids.append(w.task_id)

        # Artefact names are per-role, not per-symbol: analyst_a always writes
        # prediction_a.json. Interleaving two symbols therefore lets the second
        # one overwrite the first's artefacts before the first has been
        # journalled, and the journal agent - correctly refusing to record
        # another symbol's callout as this one's - records nothing at all.
        # Chaining each symbol's first task to the previous symbol's last one
        # keeps every symbol's cycle atomic over the shared namespace.
        previous_symbol_tail: Optional[str] = None

        for sym in symbols:
            deps = tuple([news.task_id] + research_ids
                         + ([previous_symbol_tail] if previous_symbol_tail else []))
            analyst_tasks = []
            for role, label in ((Role.ANALYST_A, "technical and market structure"),
                                (Role.ANALYST_B, "quantitative and statistical"),
                                (Role.ANALYST_C, "macro, news and market context")):
                t = board.add("predict", f"{sym}: independent prediction ({label})",
                              detail=ROLES[role].mandate,
                              priority=TaskPriority.HIGH, depends_on=deps,
                              payload={"symbol": sym}, assigned_to=role)
                created.append(t)
                analyst_tasks.append(t.task_id)

            decide = board.add("decide", f"{sym}: weigh all evidence -> LONG / SHORT / NO TRADE",
                               detail="Never a majority vote. NO TRADE is a legitimate "
                                      "conclusion.",
                               priority=TaskPriority.HIGH,
                               depends_on=tuple(analyst_tasks + [news.task_id]),
                               payload={"symbol": sym})
            risk = board.add("assess_risk", f"{sym}: position sizing and limit checks",
                             detail="Independent of the prediction agents. Holds an "
                                    "unconditional veto.",
                             priority=TaskPriority.CRITICAL,
                             depends_on=(decide.task_id,),
                             payload={"symbol": sym})
            # HIGH, not NORMAL: journalling is part of this symbol's cycle, not
            # cleanup to be done once every symbol has finished. At NORMAL it
            # sorted behind the next symbol's analysts and lost the record.
            rec = board.add("record", f"{sym}: journal the prediction and decision",
                            depends_on=(risk.task_id,), priority=TaskPriority.HIGH,
                            payload={"symbol": sym})
            created += [decide, risk, rec]
            previous_symbol_tail = rec.task_id

        self.log(f"planned {len(created)} tasks for {len(symbols)} symbol(s)")
        return created

    def plan_research_debate(self, symbol: str, *,
                             timeframes: Sequence[int] = (1, 5, 15, 60),
                             max_strategies: int = 600,
                             days: Optional[int] = None) -> List[Task]:
        """Plan one adversarial research round for a single symbol.

        Four phases, gated by dependency so the order cannot be short-circuited:

        1. **Research.** Each specialist sweeps its own families. The three are
           independent of each other and may run in any order.
        2. **Cross-examination.** Each challenges the other two - possible only
           once all three sets of findings exist, or a specialist would be
           challenging an empty file and calling it agreement.
        3. **Rebuttal.** Each answers the challenges filed against it, with a
           measurement or a concession.
        4. **Pooling.** The desk lead - which holds no family and therefore no
           stake - reconciles everything into one ranking.
        """
        from .roles import RESEARCH_SPECIALISTS, families_for

        board = self.board
        created: List[Task] = []
        sym = symbol.upper()

        research_ids: List[str] = []
        for role in RESEARCH_SPECIALISTS:
            t = board.add(
                "research_family",
                f"{sym}: research {', '.join(families_for(role))}",
                detail=ROLES[role].mandate, priority=TaskPriority.NORMAL,
                assigned_to=role,
                payload={"symbol": sym, "timeframes": list(timeframes),
                         "families": list(families_for(role)),
                         "max_strategies": max_strategies, "days": days})
            created.append(t)
            research_ids.append(t.task_id)

        challenge_ids: List[str] = []
        for role in RESEARCH_SPECIALISTS:
            t = board.add(
                "challenge", f"{sym}: cross-examine the other specialists",
                detail=("Re-test their findings on a split they did not choose, "
                        "check deflation, cost fragility, regime concentration "
                        "and redundancy. A challenge without a measurement is "
                        "discarded."),
                priority=TaskPriority.NORMAL, depends_on=tuple(research_ids),
                assigned_to=role, payload={"symbol": sym})
            created.append(t)
            challenge_ids.append(t.task_id)

        rebut_ids: List[str] = []
        for role in RESEARCH_SPECIALISTS:
            t = board.add(
                "rebut", f"{sym}: answer the challenges filed against you",
                detail=("Concede, or produce a counter-measurement. An "
                        "unmeasured denial does not answer a measured "
                        "objection and is recorded as unanswered."),
                priority=TaskPriority.NORMAL, depends_on=tuple(challenge_ids),
                assigned_to=role, payload={"symbol": sym})
            created.append(t)
            rebut_ids.append(t.task_id)

        created.append(board.add(
            "pool", f"{sym}: pool the findings and score the debate",
            detail=("Deterministic and symmetric. Discard unsubstantiated "
                    "challenges, disqualify findings carrying a standing fatal "
                    "challenge, collapse redundant edges, then rank."),
            priority=TaskPriority.HIGH, depends_on=tuple(rebut_ids),
            assigned_to=Role.STRATEGY_RESEARCH, payload={"symbol": sym}))

        self.log(f"planned a {len(created)}-task research debate for {sym}")
        return created

    # ---- execution ----------------------------------------------------
    def dispatch(self, task: Task) -> AgentResult:
        """Hand one task to its owning agent."""
        if task.assigned_to is None:
            return AgentResult.failure(f"task {task.task_id} has no owner")
        agent = self.team.get(task.assigned_to)
        if agent is None:
            return AgentResult.failure(
                f"role '{task.assigned_to.value}' is not staffed on this team")
        self.send(task.assigned_to, f"task:{task.kind}",
                  {"task_id": task.task_id, "title": task.title, **task.payload})
        return agent.run(task)

    def run_board(self, *, max_tasks: int = 500,
                  on_task: Optional[Callable[[Task, AgentResult], None]] = None,
                  halt_on: Optional[Callable[[Task, AgentResult], Optional[str]]] = None
                  ) -> RunReport:
        """Drain the board in dependency and priority order.

        ``halt_on`` lets the risk layer stop a run outright - if the account
        hits its daily loss limit mid-cycle, remaining work is skipped rather
        than producing callouts nobody is permitted to act on.
        """
        started = now_et()
        report = RunReport(started_et=started.isoformat())
        processed = 0

        while processed < max_tasks:
            task = self.board.next_task()
            if task is None:
                break
            self.board.start(task)
            result = self.dispatch(task)
            if result.ok:
                self.board.complete(task, result.payload)
                report.results[task.task_id] = result.to_dict()
            else:
                self.board.fail(task, result.error or "unknown failure")
                if task.status is TaskStatus.ASSIGNED:
                    self.log(f"retrying {task.task_id} (attempt {task.attempts + 1})")
            if on_task:
                on_task(task, result)
            if self._on_task:
                self._on_task(task, result)
            if halt_on:
                reason = halt_on(task, result)
                if reason:
                    report.halted = True
                    report.halt_reason = reason
                    for t in self.board.all():
                        if not t.status.is_terminal:
                            self.board.skip(t, f"run halted: {reason}")
                    break
            processed += 1

        counts = self.board.counts()
        report.tasks_total = len(self.board)
        report.tasks_done = counts.get("DONE", 0)
        report.tasks_failed = counts.get("FAILED", 0)
        report.tasks_skipped = counts.get("SKIPPED", 0)
        report.tasks_blocked = counts.get("BLOCKED", 0)
        report.board = self.board.to_dict()
        report.messages = len(self.bus.journal)
        report.finished_et = now_et().isoformat()
        report.duration_s = (now_et() - started).total_seconds()

        self.publish("run_report", report.to_dict(), report.summary())
        self.fs.write_shared("task_board", self.board.to_dict())
        self.fs.write_shared("message_journal", self.bus.to_dict())
        return report

    # ---- TeamAgent contract -------------------------------------------
    def handle(self, task: Task) -> Optional[AgentResult]:
        """The manager accepts only coordination tasks."""
        if task.kind == "status":
            return AgentResult(ok=True, summary=self.board.render(),
                               payload=self.board.to_dict())
        if task.kind in ("plan", "decompose"):
            symbols = task.payload.get("symbols", [])
            created = self.plan_research_and_decide(symbols)
            return AgentResult(ok=True, summary=f"planned {len(created)} tasks",
                               payload=[t.task_id for t in created])
        if task.kind == "dispatch":
            tid = task.payload.get("task_id", "")
            t = self.board.get(tid)
            if t is None:
                return AgentResult.failure(f"no such task {tid}")
            return self.dispatch(t)
        return AgentResult.failure(f"manager does not handle '{task.kind}'")
