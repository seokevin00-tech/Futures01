"""The Usage Budget agent - measures consumption and gates the team's dispatch.

This agent is deliberately not LLM-backed. Its whole job is arithmetic over
measured data, and a language model adds nothing to a division except the risk
of getting it wrong.

Its one editorial rule: **it never reports a percentage it cannot compute.**
The Claude Code subscription limit is not readable from inside a session, so an
uncalibrated monitor reports measured consumption and says the denominator is
unknown. A budget guard that invented one would hand the operator a confident,
wrong number and would be worse than having no guard at all.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..team.agent import AgentResult, TeamAgent
from ..team.board import Task
from ..team.budget import BudgetLimits, BudgetMonitor, BudgetState, BudgetStatus
from ..team.bus import MessageBus
from ..team.roles import Role
from ..team.workspace import TeamFilesystem
from ..timeutil import et_stamp

__all__ = ["BudgetAgent"]


class BudgetAgent(TeamAgent):
    """Owns the usage budget and the decision to keep placing work."""

    def __init__(self, fs: TeamFilesystem, bus: MessageBus, *,
                 context: Any = None, config: Any = None, llm: Any = None):
        super().__init__(Role.BUDGET, fs, bus, config=config, llm=None)
        self.context = context
        limits = BudgetLimits(
            session_slow_at=float(getattr(config, "session_slow_at", 75.0)),
            session_pause_at=float(getattr(config, "session_pause_at", 80.0)),
            weekly_slow_at=float(getattr(config, "weekly_slow_at", 80.0)),
            weekly_pause_at=float(getattr(config, "weekly_pause_at", 90.0)),
        )
        self.monitor = BudgetMonitor(
            limits,
            state_path=str(self.workspace.root / "scratch" / "budget_state.json"))
        self._last: Optional[BudgetState] = None

    # ------------------------------------------------------------------
    def handle(self, task: Task) -> AgentResult:
        if task.kind == "calibrate_budget":
            return self._calibrate(task)
        if task.kind in ("check_budget", "budget_status"):
            return self._check(task)
        return AgentResult.failure(f"budget agent does not handle '{task.kind}'")

    # ------------------------------------------------------------------
    def _check(self, task: Task) -> AgentResult:
        state = self.monitor.check()
        self._last = state
        self.publish("budget_state", state.to_dict(), self._headline(state))

        # A threshold crossing is an alert, not a log line: the manager needs
        # to act on it before it places the next task.
        if state.status is BudgetStatus.PAUSED:
            self.bus.alert(self.role, "usage_budget_paused", {
                "status": state.status.value, "reasons": state.reasons,
                "may_place_work": False})
        elif state.status is BudgetStatus.SLOW_DOWN:
            self.bus.alert(self.role, "usage_budget_slow_down", {
                "status": state.status.value, "reasons": state.reasons,
                "may_place_work": True,
                "multiplier": self.monitor.budget_multiplier()})

        return AgentResult(ok=True, summary=self._headline(state),
                           payload=state.to_dict(),
                           artefacts=["budget_state.json"])

    def _calibrate(self, task: Task) -> AgentResult:
        session_pct = task.payload.get("session_pct")
        weekly_pct = task.payload.get("weekly_pct")
        if session_pct is None and weekly_pct is None:
            return AgentResult.failure(
                "calibrate_budget needs session_pct and/or weekly_pct - the "
                "figures your Claude Code client reports for /usage. The "
                "subscription limit cannot be read from inside a session, so "
                "there is nothing to calibrate against without them.")
        result = self.monitor.calibrate(
            session_pct_observed=float(session_pct) if session_pct is not None else None,
            weekly_pct_observed=float(weekly_pct) if weekly_pct is not None else None)
        state = self.monitor.check()
        self._last = state
        self.publish("budget_state", state.to_dict(), self._headline(state))
        return AgentResult(
            ok=True, payload={"calibration": result, "state": state.to_dict()},
            summary=(f"calibrated from observed usage; {self._headline(state)}"),
            artefacts=["budget_state.json"])

    # ------------------------------------------------------------------
    @staticmethod
    def _headline(state: BudgetState) -> str:
        bits = [f"[{et_stamp()}] budget {state.status.value}"]
        for window in (state.session, state.weekly):
            if window is None:
                continue
            pct = window.pct_used
            bits.append(f"{window.label} "
                        + (f"{pct:.1f}%" if pct is not None
                           else f"{window.effective_tokens:,.0f} eff tok (limit unknown)"))
        if state.reasons:
            bits.append(state.reasons[0])
        return " | ".join(bits)

    # ------------------------------------------------------------------
    def gate(self) -> bool:
        """Whether the manager may place another task right now."""
        allowed, state = self.monitor.gate()
        self._last = state
        return allowed

    @property
    def last_state(self) -> Optional[BudgetState]:
        return self._last

    def multiplier(self) -> float:
        """Fraction of normal workload to place, in [0, 1]."""
        return self.monitor.budget_multiplier()
