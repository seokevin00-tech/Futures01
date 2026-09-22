"""The cycle that wires every layer together and emits the callouts.

This module is the only place in the system where the deterministic core, the
domain agents, the risk layer and the terminal meet. Everything below it is
independently testable; everything above it is a command-line verb.

Three responsibilities, and deliberately no others:

* **Assembly.** Build one :class:`~futures_agents.config.SystemConfig`, one
  :class:`~futures_agents.agents.context.AgentContext` and one
  :class:`~futures_agents.team.team.Team` from them, so that every agent in a
  cycle reads the same market snapshot, the same account and the same storage.
  Building them per-agent is how two analysts end up quoting different prices
  for the same instant.

* **Emission.** Route every callout through :mod:`futures_agents.alerts` so the
  Eastern-Time stamp comes first, the flashing banner second and the full body
  third - one renderer, one format, whether the decision is a BUY, a NO TRADE,
  a risk veto or a halt.

* **Honest degradation.** The domain agents are written independently and may
  be missing or broken. A cycle with four unstaffed roles must still finish and
  must say exactly which four, with the import error that caused it. Silence
  about a missing analyst is indistinguishable from an analyst who had nothing
  to say, and those two things must never look alike.

The orchestrator forms no opinion about the market. It schedules, merges and
prints; every number it renders was produced by an agent, the risk manager or
the deterministic core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import alerts
from .agents import IMPORT_ERRORS, staffing_report
from .agents.context import AgentContext, build_context
from .agents.llm import build_client
from .alerts import Priority
from .config import SystemConfig, tf_label
from .data.providers import DataProvider, ReplayProvider, SyntheticProvider
from .risk.account import AccountState
from .risk.manager import RiskManager, TradingMode
from .schema import (Decision, Direction, MarketRegime, NewsRisk,
                     RiskAssessment, TradeCallout, fmt_price)
from .storage import Storage
from .strategies.registry import StrategyRegistry, build_registry
from .team.agent import AgentResult
from .team.board import Task, TaskBoard, TaskStatus
from .team.bus import MessageKind
from .team.manager import RunReport
from .team.roles import Role, ROLES
from .team.team import Team, build_team
from .timeutil import et_stamp, now_et, to_et

__all__ = ["Orchestrator", "CycleReport", "SymbolOutcome", "render_callout"]


#: Which domain module staffs which role. Used to turn "analyst_b is missing"
#: into "analysts.py failed to import: SyntaxError line 12", which is the
#: difference between a mystery and a bug report.
ROLE_MODULE: Dict[Role, str] = {
    Role.NEWS_MACRO: "news_macro",
    Role.STRATEGY_RESEARCH: "research",
    Role.ANALYST_A: "analysts",
    Role.ANALYST_B: "analysts",
    Role.ANALYST_C: "analysts",
    Role.DECISION: "decision",
    Role.RISK: "risk_agent",
    Role.JOURNAL: "journal_agent",
}

#: Task kinds the risk layer owns. Completion of one of these is the point at
#: which a cycle can discover it is no longer permitted to trade.
_RISK_KINDS = ("assess_risk", "size_position", "check_limits", "veto")

_PROGRESS_PRIORITY: Dict[str, Priority] = {
    "test": Priority.INFO,
    "research_news": Priority.RESEARCH,
    "research_strategies": Priority.RESEARCH,
    "walk_forward": Priority.RESEARCH,
    "backtest": Priority.RESEARCH,
    "rank_strategies": Priority.RESEARCH,
    "robustness": Priority.RESEARCH,
    "predict": Priority.SIGNAL,
    "decide": Priority.SIGNAL,
    "assess_risk": Priority.RISK,
    "record": Priority.INFO,
}


# --------------------------------------------------------------------------
# Payload coercion
#
# The domain agents are written by different people against the same contract.
# The contract fixes the artefact *names* and the schema classes, not the exact
# shape of the dict each agent chooses to return from ``handle``. These helpers
# accept what the contract requires and everything reasonable around it, so a
# minor difference in how an agent wraps its result degrades into "field not
# reported" rather than a crashed cycle.
# --------------------------------------------------------------------------

def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float_list(value: Any) -> List[float]:
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        value = list(value.values())
    out: List[float] = []
    if isinstance(value, (list, tuple)):
        for v in value:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
    return out


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "; ".join(_as_text(v) for v in value if _as_text(v))
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_as_text(v)}" for k, v in value.items())
    return str(value)


def _looks_like_callout(d: Dict[str, Any]) -> bool:
    if "decision" in d and isinstance(d.get("decision"), str):
        return True
    return "symbol" in d and any(
        k in d for k in ("stop_loss", "targets", "entry", "callout_id"))


def _callout_from_dict(d: Dict[str, Any]) -> TradeCallout:
    """Rebuild a :class:`TradeCallout` from whatever the decision agent sent."""
    c = TradeCallout()
    for key in ("callout_id", "timestamp_et", "strategy", "strategy_group",
                "timeframe", "analyst_agreement", "trade_invalidation",
                "reason_for_entry", "reason_to_avoid", "decision_rationale"):
        if d.get(key) not in (None, ""):
            setattr(c, key, _as_text(d.get(key)))
    c.symbol = _as_text(d.get("symbol")).upper()
    c.decision = Decision.coerce(d.get("decision"))
    for key in ("entry", "stop_loss"):
        if d.get(key) is not None:
            setattr(c, key, _as_float(d.get(key), 0.0))
    zone = d.get("entry_zone")
    if isinstance(zone, (list, tuple)) and len(zone) == 2:
        c.entry_zone = (_as_float(zone[0]), _as_float(zone[1]))
    c.targets = _as_float_list(d.get("targets"))
    if d.get("expected_reward_risk") is not None:
        c.expected_reward_risk = _as_float(d.get("expected_reward_risk"))
    c.contracts = _as_int(d.get("contracts"))
    c.dollar_risk = _as_float(d.get("dollar_risk"))
    c.account_risk_pct = _as_float(d.get("account_risk_pct"))
    c.historical_win_rate = _as_float(d.get("historical_win_rate"))
    c.historical_expectancy_r = _as_float(d.get("historical_expectancy_r"))
    c.max_historical_drawdown_r = _as_float(d.get("max_historical_drawdown_r"))
    c.confidence = _as_float(d.get("confidence"))
    c.remaining_drawdown_buffer = _as_float(d.get("remaining_drawdown_buffer"))
    c.buffer_consumed_if_stopped_pct = _as_float(d.get("buffer_consumed_if_stopped_pct"))
    c.remaining_daily_loss_budget = _as_float(d.get("remaining_daily_loss_budget"))
    c.account_equity = _as_float(d.get("account_equity"))
    try:
        c.market_regime = MarketRegime(str(d.get("market_regime", "")).upper())
    except ValueError:
        c.market_regime = MarketRegime.UNKNOWN
    try:
        c.news_risk = NewsRisk(str(d.get("news_risk", "")).upper())
    except ValueError:
        c.news_risk = NewsRisk.NONE
    risk = _find_risk(d.get("risk_assessment"))
    if risk is not None:
        c.risk_assessment = risk
    return c


def _find_callout(payload: Any, depth: int = 0) -> Optional[TradeCallout]:
    """Locate a callout anywhere in an agent payload."""
    if payload is None or depth > 3:
        return None
    if isinstance(payload, TradeCallout):
        return payload
    if isinstance(payload, dict):
        for key in ("callout", "trade_callout", "final_callout"):
            found = _find_callout(payload.get(key), depth + 1)
            if found is not None:
                return found
        if _looks_like_callout(payload):
            return _callout_from_dict(payload)
        nested = payload.get("decision")
        if isinstance(nested, dict):
            return _find_callout(nested, depth + 1)
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = _find_callout(item, depth + 1)
            if found is not None:
                return found
    return None


def _risk_from_dict(d: Dict[str, Any]) -> RiskAssessment:
    ra = RiskAssessment()
    ra.approved = bool(d.get("approved", False))
    ra.contracts = _as_int(d.get("contracts"))
    for key in ("dollar_risk", "account_risk_pct", "stop_distance_points",
                "stop_distance_ticks", "expected_cost", "reward_risk_after_costs",
                "remaining_daily_loss_budget", "remaining_drawdown_buffer",
                "buffer_consumed_if_stopped_pct", "risk_multiplier_applied"):
        if d.get(key) is not None:
            setattr(ra, key, _as_float(d.get(key)))
    for key in ("vetoes", "warnings", "notes"):
        value = d.get(key) or []
        if isinstance(value, str):
            value = [value]
        setattr(ra, key, [_as_text(v) for v in value if _as_text(v)])
    if d.get("timestamp_et"):
        ra.timestamp_et = _as_text(d.get("timestamp_et"))
    return ra


def _find_risk(payload: Any, depth: int = 0) -> Optional[RiskAssessment]:
    """Locate a risk assessment anywhere in an agent payload."""
    if payload is None or depth > 3:
        return None
    if isinstance(payload, RiskAssessment):
        return payload
    if isinstance(payload, dict):
        for key in ("risk_assessment", "assessment", "risk"):
            found = _find_risk(payload.get(key), depth + 1)
            if found is not None:
                return found
        if "approved" in payload or "vetoes" in payload:
            return _risk_from_dict(payload)
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = _find_risk(item, depth + 1)
            if found is not None:
                return found
    return None


def _find_prediction(payload: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
    """Pull the reportable core out of an analyst's result."""
    if payload is None or depth > 3:
        return None
    if hasattr(payload, "to_dict") and not isinstance(payload, dict):
        payload = payload.to_dict()
    if isinstance(payload, dict):
        if "direction" in payload or "analyst_id" in payload:
            return {
                "analyst_id": _as_text(payload.get("analyst_id")),
                "analyst_name": _as_text(payload.get("analyst_name")),
                "direction": Direction.coerce(payload.get("direction")).value,
                "confidence": _as_float(payload.get("confidence")),
                "primary_reason": _as_text(payload.get("primary_reason")),
                "source": _as_text(payload.get("source")) or "deterministic",
            }
        for key in ("prediction", "result", "payload"):
            found = _find_prediction(payload.get(key), depth + 1)
            if found is not None:
                return found
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = _find_prediction(item, depth + 1)
            if found is not None:
                return found
    return None


def _find_mode(payload: Any, depth: int = 0) -> Optional[TradingMode]:
    """Find a trading mode the risk agent reported, in whatever field."""
    if payload is None or depth > 3:
        return None
    if isinstance(payload, TradingMode):
        return payload
    if isinstance(payload, str):
        text = payload.upper()
        for mode in (TradingMode.HALTED, TradingMode.OBSERVATION_ONLY,
                     TradingMode.REDUCED, TradingMode.NORMAL):
            if mode.value in text:
                return mode
        return None
    if isinstance(payload, dict):
        for key in ("mode", "trading_mode", "account_mode", "status"):
            found = _find_mode(payload.get(key), depth + 1)
            if found is not None:
                return found
        # Also inspect the free-text fields: the risk manager states the mode
        # in its notes, and an agent that forwards those without a "mode" key
        # has still told us.
        for key in ("notes", "vetoes", "warnings", "summary"):
            found = _find_mode(payload.get(key), depth + 1)
            if found is not None:
                return found
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = _find_mode(item, depth + 1)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------
# Callout rendering
# --------------------------------------------------------------------------

#: The field list the specification requires, in the order it requires it.
#: Rendering is table-driven so a missing field is visibly "not reported"
#: rather than absent from the callout altogether.
_CALLOUT_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("Symbol", "symbol"),
    ("Direction", "direction"),
    ("Entry", "entry"),
    ("Stop Loss", "stop_loss"),
    ("Targets", "targets"),
    ("Expected R/R", "rr"),
    ("Contracts", "contracts"),
    ("Dollar Risk", "dollar_risk"),
    ("Account Risk %", "account_risk_pct"),
    ("Strategy", "strategy"),
    ("Timeframe", "timeframe"),
    ("Market Regime", "market_regime"),
    ("News Risk", "news_risk"),
    ("Historical Win Rate", "win_rate"),
    ("Historical Expectancy", "expectancy"),
    ("Max Historical Drawdown", "max_dd"),
    ("Analyst Agreement", "agreement"),
    ("Confidence", "confidence"),
    ("Trade Invalidation", "invalidation"),
    ("Reason for Entry", "reason_for_entry"),
    ("Reason to Avoid", "reason_to_avoid"),
)

_NOT_REPORTED = "not reported"


def _callout_values(callout: TradeCallout, *, vetoed: bool = False,
                    unauthorised: bool = False,
                    observation_only: bool = False) -> Dict[str, str]:
    risk = callout.risk_assessment
    direction = callout.decision.value
    if vetoed:
        direction += "  [VETOED BY RISK - DO NOT TRADE]"
    elif unauthorised:
        direction += "  [NOT AUTHORISED - no risk assessment - DO NOT TRADE]"
    elif observation_only and callout.decision.is_actionable:
        direction += "  [OBSERVATION ONLY - the account may not trade - DO NOT TRADE]"
    elif not callout.decision.is_actionable:
        direction += "  [stand down]"

    if callout.entry_zone:
        entry = (f"{fmt_price(callout.entry_zone[0])} - "
                 f"{fmt_price(callout.entry_zone[1])}")
        if callout.entry is not None:
            entry = f"{fmt_price(callout.entry)}  (zone {entry})"
    elif callout.entry is not None:
        entry = fmt_price(callout.entry)
    else:
        entry = "-" if not callout.decision.is_actionable else _NOT_REPORTED

    targets = ", ".join(fmt_price(t) for t in callout.targets) or (
        "-" if not callout.decision.is_actionable else _NOT_REPORTED)
    rr = (f"{callout.expected_reward_risk:.2f}R"
          if callout.expected_reward_risk else "-")
    if risk is not None and risk.reward_risk_after_costs:
        rr += f"  ({risk.reward_risk_after_costs:.2f}R after costs)"

    return {
        "symbol": callout.symbol or _NOT_REPORTED,
        "direction": direction,
        "entry": entry,
        "stop_loss": (fmt_price(callout.stop_loss) if callout.stop_loss is not None
                      else ("-" if not callout.decision.is_actionable else _NOT_REPORTED)),
        "targets": targets,
        "rr": rr,
        "contracts": str(callout.contracts) if callout.contracts else "0",
        "dollar_risk": f"${callout.dollar_risk:,.2f}",
        "account_risk_pct": f"{callout.account_risk_pct * 100:.3f}%",
        "strategy": (f"{callout.strategy}"
                     + (f"  [{callout.strategy_group}]" if callout.strategy_group else "")
                     ) or _NOT_REPORTED,
        "timeframe": callout.timeframe or _NOT_REPORTED,
        "market_regime": callout.market_regime.value,
        "news_risk": callout.news_risk.value,
        "win_rate": (f"{callout.historical_win_rate * 100:.1f}%"
                     if callout.historical_win_rate else _NOT_REPORTED),
        "expectancy": (f"{callout.historical_expectancy_r:+.3f}R"
                       if callout.historical_expectancy_r else _NOT_REPORTED),
        "max_dd": (f"{callout.max_historical_drawdown_r:.2f}R"
                   if callout.max_historical_drawdown_r else _NOT_REPORTED),
        "agreement": callout.analyst_agreement or _NOT_REPORTED,
        "confidence": f"{callout.confidence:.2f}",
        "invalidation": callout.trade_invalidation or _NOT_REPORTED,
        "reason_for_entry": callout.reason_for_entry or _NOT_REPORTED,
        "reason_to_avoid": callout.reason_to_avoid or _NOT_REPORTED,
    }


def render_callout(callout: TradeCallout, *,
                   account: Optional[AccountState] = None,
                   notes: Sequence[str] = (),
                   observation_only: bool = False) -> str:
    """The full callout body, every field the specification requires.

    The last block is not decoration: a callout that states a direction without
    stating what losing it costs, and what that leaves between the account and
    failure, is exactly the kind of callout that gets acted on twice in a row
    on a bad day.
    """
    risk = callout.risk_assessment
    directional = callout.decision.is_actionable
    vetoed = bool(risk is not None and not risk.approved and directional)
    unauthorised = bool(risk is None and directional)
    values = _callout_values(callout, vetoed=vetoed, unauthorised=unauthorised,
                             observation_only=observation_only)

    width = max(len(label) for label, _ in _CALLOUT_FIELDS)
    lines: List[str] = []
    for label, key in _CALLOUT_FIELDS:
        lines.append(f"  {label + ':':<{width + 2}} {values[key]}")

    lines.append("")
    buffer_before = callout.remaining_drawdown_buffer
    buffer_after = max(0.0, buffer_before - callout.dollar_risk)
    equity = callout.account_equity or (account.equity if account else 0.0)
    if callout.contracts and callout.dollar_risk:
        label = ("WOULD BE AT RISK (not authorised - observation only)"
                 if observation_only else "AT RISK")
        lines.append(
            f"  {label}: ${callout.dollar_risk:,.2f} on {callout.contracts} "
            f"contract(s) - {callout.account_risk_pct * 100:.3f}% of "
            f"${equity:,.2f} of equity.")
        lines.append(
            f"  IF STOPPED: the usable drawdown buffer falls from "
            f"${buffer_before:,.2f} to ${buffer_after:,.2f} "
            f"({callout.buffer_consumed_if_stopped_pct * 100:.1f}% of it consumed); "
            f"today's loss budget falls from "
            f"${callout.remaining_daily_loss_budget:,.2f} to "
            f"${max(0.0, callout.remaining_daily_loss_budget - callout.dollar_risk):,.2f}.")
    else:
        lines.append(
            f"  AT RISK: $0.00 - no position is authorised, so no capital is "
            f"committed. Usable drawdown buffer stays at ${buffer_before:,.2f}; "
            f"today's loss budget stays at "
            f"${callout.remaining_daily_loss_budget:,.2f}.")

    if observation_only:
        lines.append("")
        lines.append("  OBSERVATION ONLY - the account is not permitted to trade "
                     "right now. This callout is analysis, not an instruction.")

    if risk is not None:
        if risk.vetoes:
            lines.append("")
            lines.append("  RISK VETO:")
            lines.extend(f"    - {v}" for v in risk.vetoes)
        if risk.warnings:
            lines.extend(f"  risk warning: {w}" for w in risk.warnings)
        if risk.notes:
            lines.extend(f"  risk note:    {n}" for n in risk.notes)
    else:
        lines.append("")
        lines.append("  RISK: no assessment was produced for this callout - "
                     "it is not authorised for execution.")

    if callout.decision_rationale:
        lines.append("")
        lines.append(f"  Rationale: {callout.decision_rationale}")
    for note in notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Cycle results
# --------------------------------------------------------------------------

@dataclass
class SymbolOutcome:
    """What one cycle concluded about one symbol."""

    symbol: str
    callout: Optional[TradeCallout] = None
    risk: Optional[RiskAssessment] = None
    predictions: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    emitted: bool = False

    @property
    def decision(self) -> Decision:
        return self.callout.decision if self.callout else Decision.NO_TRADE

    @property
    def approved(self) -> bool:
        return bool(self.risk and self.risk.approved and self.callout
                    and self.callout.decision.is_actionable)

    def agreement_text(self) -> str:
        """Analyst agreement measured from what the analysts actually said."""
        if not self.predictions:
            return ""
        counts: Dict[str, int] = {}
        for p in self.predictions:
            counts[p["direction"]] = counts.get(p["direction"], 0) + 1
        total = len(self.predictions)
        parts = [f"{n} of {total} {d}" for d, n in sorted(counts.items(),
                                                          key=lambda kv: -kv[1])]
        return ", ".join(parts)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "decision": self.decision.value,
            "approved": self.approved,
            "callout": self.callout.to_dict() if self.callout else None,
            "risk": self.risk.to_dict() if self.risk else None,
            "predictions": list(self.predictions),
            "notes": list(self.notes),
        }


@dataclass
class CycleReport:
    """Everything one cycle produced, including what it could not produce."""

    mode: str
    started_et: str
    symbols: List[str] = field(default_factory=list)
    outcomes: Dict[str, SymbolOutcome] = field(default_factory=dict)
    unstaffed: List[str] = field(default_factory=list)
    staffing_errors: Dict[str, str] = field(default_factory=dict)
    failures: List[Dict[str, str]] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""
    trading_mode: str = TradingMode.NORMAL.value
    duration_s: float = 0.0
    run: Optional[RunReport] = None

    @property
    def callouts(self) -> List[TradeCallout]:
        return [o.callout for o in self.outcomes.values() if o.callout]

    @property
    def actionable(self) -> List[SymbolOutcome]:
        return [o for o in self.outcomes.values() if o.approved]

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "started_et": self.started_et,
            "symbols": list(self.symbols),
            "trading_mode": self.trading_mode,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "duration_s": round(self.duration_s, 2),
            "unstaffed": list(self.unstaffed),
            "staffing_errors": dict(self.staffing_errors),
            "failures": list(self.failures),
            "outcomes": {k: v.to_dict() for k, v in self.outcomes.items()},
            "run": self.run.to_dict() if self.run else None,
        }

    def summary(self) -> str:
        actionable = len(self.actionable)
        no_trade = sum(1 for o in self.outcomes.values()
                       if not o.approved)
        bits = [f"{self.mode} cycle: {len(self.symbols)} symbol(s)",
                f"{actionable} actionable", f"{no_trade} stand-down"]
        if self.run:
            bits.append(self.run.summary())
        if self.unstaffed:
            bits.append(f"unstaffed: {', '.join(self.unstaffed)}")
        # RunReport.summary() already states the halt; saying it twice reads as
        # two separate halts.
        if self.halted and not (self.run and self.run.halted):
            bits.append(f"HALTED: {self.halt_reason}")
        return " | ".join(bits)


# --------------------------------------------------------------------------
# The orchestrator
# --------------------------------------------------------------------------

class _Unset:
    pass


_UNSET = _Unset()


class Orchestrator:
    """Assembles the system and runs one cycle at a time."""

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        *,
        provider: Optional[DataProvider] = None,
        storage: Optional[Storage] = None,
        account: Optional[AccountState] = None,
        registry: Optional[StrategyRegistry] = None,
        llm: Any = _UNSET,
        workspace_root: str = "workspace",
        history_days: int = 120,
        seed: int = 7,
        as_of: Optional[datetime] = None,
        build_strategies: bool = True,
        emit: bool = True,
        enforce_permissions: bool = True,
    ) -> None:
        self.config = config or SystemConfig()
        self.emit = emit
        self.history_days = history_days
        self.seed = seed

        self.provider: DataProvider = provider or SyntheticProvider(
            self.config.symbols, days=history_days, seed=seed)
        if registry is None and build_strategies:
            registry = build_registry(self.config.symbols, self.config.timeframes,
                                      max_per_symbol=self.config.max_combinations)
        self.context: AgentContext = build_context(
            self.config, provider=self.provider, storage=storage, account=account,
            registry=registry, as_of=as_of)
        self.llm = build_client(self.config) if isinstance(llm, _Unset) else llm
        self.team: Team = build_team(
            self.config, workspace_root=workspace_root, llm=self.llm,
            context=self.context, enforce_permissions=enforce_permissions)

        self._outcomes: Dict[str, SymbolOutcome] = {}
        self._failures: List[Dict[str, str]] = []
        self._observation_only = False
        #: Where this cycle's messages start in the bus journal, so a halt
        #: announced last cycle cannot halt the next one.
        self._bus_mark = 0

    # ---- shorthand ----------------------------------------------------
    @property
    def manager(self):
        return self.team.manager

    @property
    def board(self) -> TaskBoard:
        return self.team.board

    @property
    def account(self) -> AccountState:
        return self.context.account

    @property
    def risk(self) -> RiskManager:
        return self.context.risk

    @property
    def storage(self) -> Storage:
        return self.context.storage

    @property
    def llm_available(self) -> bool:
        return bool(self.llm and getattr(self.llm, "available", False))

    # ------------------------------------------------------------------
    # Staffing
    # ------------------------------------------------------------------
    def staffing(self) -> Dict[str, Any]:
        """Which roles are staffed, which are not, and why not."""
        report = staffing_report()
        unstaffed = [r for r in self.team.unstaffed if r is not Role.MANAGER]
        return {
            "staffed": [r.value for r in sorted(self.team.agents, key=lambda x: x.value)],
            "unstaffed": [r.value for r in unstaffed],
            "reasons": {
                r.value: IMPORT_ERRORS.get(ROLE_MODULE.get(r, ""),
                                           "no implementation found")
                for r in unstaffed
            },
            "llm": ("available" if self.llm_available else
                    "not available - deterministic path only"),
            "available_classes": report["available"],
        }

    def staffing_lines(self) -> List[str]:
        """Human-readable staffing report, always printed before a cycle."""
        s = self.staffing()
        total = len(Role) - 1
        lines = [f"STAFFING  [{et_stamp()}]",
                 f"  staffed:   {len(s['staffed'])} of {total} roles - "
                 f"{', '.join(s['staffed'])}"]
        if s["unstaffed"]:
            lines.append(f"  UNSTAFFED: {len(s['unstaffed'])} - "
                         f"{', '.join(s['unstaffed'])}")
            for role, reason in s["reasons"].items():
                lines.append(f"    {role:<18} {reason}")
            lines.append("  Work routed to an unstaffed role fails with that "
                         "reason; it is never silently skipped.")
        lines.append(f"  reasoning model: {s['llm']}")
        return lines

    # ------------------------------------------------------------------
    # Cycles
    # ------------------------------------------------------------------
    def run_research_cycle(self, symbols: Optional[Sequence[str]] = None, *,
                           timeframes: Optional[Sequence[int]] = None,
                           include_developer_check: bool = True) -> CycleReport:
        """The full plan: verify core, news, strategy research, then decide."""
        return self._run_cycle(
            "research", symbols,
            timeframes=tuple(timeframes or self.config.timeframes),
            include_developer_check=include_developer_check,
            include_research=True)

    def run_live_cycle(self, symbols: Optional[Sequence[str]] = None
                       ) -> CycleReport:
        """The live path: news, three analysts, decision, risk, journal.

        The heavy research is skipped deliberately - it is a scheduled job, not
        something to re-run between two ticks. The analysts read the rankings
        the last research cycle published.
        """
        return self._run_cycle("live", symbols, include_developer_check=False,
                               include_research=False, live_only=True)

    def _run_cycle(self, mode: str, symbols: Optional[Sequence[str]],
                   **plan_kwargs: Any) -> CycleReport:
        started = now_et()
        syms = [s.upper() for s in (symbols or self.config.symbols)]
        self._outcomes = {s: SymbolOutcome(symbol=s) for s in syms}
        self._failures = []
        self._bus_mark = len(self.team.bus.journal)

        # A fresh board per cycle: task ids and counts describe this cycle, not
        # the accumulated history of the process.
        self.manager.board = TaskBoard()

        trading_mode, mode_reasons = self.risk.mode(self.context.now())
        self._observation_only = not trading_mode.can_trade

        report = CycleReport(
            mode=mode, started_et=to_et(started).isoformat(), symbols=syms,
            unstaffed=[r.value for r in self.team.unstaffed if r is not Role.MANAGER],
            staffing_errors=dict(IMPORT_ERRORS),
            trading_mode=trading_mode.value)

        self._announce_start(mode, syms, trading_mode, mode_reasons)

        self.manager.plan_research_and_decide(syms, **plan_kwargs)
        run = self.manager.run_board(on_task=self._on_task, halt_on=self._halt_on)

        # Anything the risk task never reached still owes the operator a
        # callout - a symbol that silently produced nothing is indistinguishable
        # from a symbol nobody looked at.
        for outcome in self._outcomes.values():
            if not outcome.emitted:
                self._emit_outcome(outcome)

        report.run = run
        report.outcomes = dict(self._outcomes)
        report.failures = list(self._failures)
        report.halted = run.halted
        report.halt_reason = run.halt_reason
        report.duration_s = (now_et() - started).total_seconds()
        self._announce_end(report)
        try:
            self.storage.record_equity(self.account.equity, self.account.peak_equity,
                                       self.account.daily_pnl, note=f"{mode} cycle")
        except Exception as exc:                        # noqa: BLE001
            report.failures.append({"task_id": "-", "kind": "record_equity",
                                    "role": "orchestrator",
                                    "error": f"{type(exc).__name__}: {exc}"})
        return report

    # ---- manager hooks -------------------------------------------------
    def _on_task(self, task: Task, result: AgentResult) -> None:
        """Called after every task. Collects payloads and narrates progress."""
        symbol = str(task.payload.get("symbol", "")).upper()
        outcome = self._outcomes.get(symbol)

        if not result.ok:
            # The board retries once, so the same task can fail twice. Report
            # the task, not the attempts - two identical lines read as two
            # different problems.
            record = {
                "task_id": task.task_id, "kind": task.kind,
                "role": task.assigned_to.value if task.assigned_to else "unassigned",
                "attempts": str(task.attempts),
                "error": result.error or "unknown failure"}
            self._failures = [f for f in self._failures
                              if f["task_id"] != task.task_id]
            self._failures.append(record)
            if outcome is not None:
                note = (f"{task.kind} "
                        f"({task.assigned_to.value if task.assigned_to else '?'}) "
                        f"failed: {result.error}")
                if note not in outcome.notes:
                    outcome.notes.append(note)

        if task.kind == "predict" and outcome is not None and result.ok:
            pred = _find_prediction(result.payload)
            if pred is not None:
                if not pred["analyst_id"] and task.assigned_to:
                    pred["analyst_id"] = task.assigned_to.value[-1].upper()
                outcome.predictions.append(pred)

        if task.kind == "decide" and outcome is not None:
            callout = _find_callout(result.payload)
            if callout is None:
                callout = _find_callout(
                    self.team.fs.read_artefact(Role.DECISION, "callout"))
            if callout is not None:
                if not callout.symbol:
                    callout.symbol = symbol
                outcome.callout = callout
            elif result.ok:
                outcome.notes.append(
                    "the decision agent returned no callout in a recognised shape")

        if task.kind in _RISK_KINDS and outcome is not None:
            risk = _find_risk(result.payload)
            if risk is None:
                risk = _find_risk(
                    self.team.fs.read_artefact(Role.RISK, "risk_assessment"))
            if risk is not None:
                outcome.risk = risk
            elif result.ok:
                outcome.notes.append(
                    "the risk agent returned no assessment in a recognised shape")

            # The risk layer's copy of the callout is the authoritative one. The
            # decision layer's copy carries *pre-risk* sizing and still states a
            # direction even when the trade was refused; rendering that would
            # flash an unapproved BUY, with a contract count nobody authorised,
            # as though it were executable. The decision copy is a fallback for
            # the case where risk never ran - and that case is rendered as
            # NOT AUTHORISED, never as actionable.
            final = _find_callout(result.payload)
            if final is None:
                final = _find_callout(self.team.fs.read_artefact(Role.RISK, "callout"))
            if final is not None:
                if not final.symbol:
                    final.symbol = symbol
                if outcome.callout is not None and outcome.callout is not final:
                    outcome.notes.append(
                        "callout taken from the risk layer's post-veto copy")
                outcome.callout = final

            # The risk verdict is the last word on a symbol, so this is where
            # the callout is issued.
            self._emit_outcome(outcome)

        self._progress(task, result)

    def _halt_on(self, task: Task, result: AgentResult) -> Optional[str]:
        """Stop the cycle when the account may no longer trade.

        Two independent sources are consulted: what the risk agent reported,
        and what the risk manager itself says. The manager wins - it is
        deterministic and it is the layer that actually holds the veto.
        """
        if task.kind not in _RISK_KINDS:
            return None
        reported = _find_mode(result.payload)
        announced, announced_reasons = self._risk_announcement()
        actual, reasons = self.risk.mode(self.context.now())
        mode = actual
        if not actual.can_trade:
            pass
        elif announced is not None and not announced.can_trade:
            # The risk agent raised an ALERT on the bus. It is the layer that
            # holds the veto, so its announcement stands even when the account
            # arithmetic alone would still permit trading.
            mode, reasons = announced, announced_reasons
        elif reported in (TradingMode.HALTED, TradingMode.OBSERVATION_ONLY):
            mode = reported
            reasons = [f"risk agent reported {reported.value}"]
        if mode.can_trade:
            return None

        self._observation_only = True
        # The manager already prefixes its report with "HALTED:", so the reason
        # carries the cause, not the word again.
        detail = ("; ".join(reasons) if reasons
                  else "the risk layer withdrew permission to trade")
        reason = (detail if mode is TradingMode.HALTED
                  else f"{mode.value} - {detail}")
        remaining = [t for t in self.board.all() if not t.status.is_terminal]
        body = "\n".join([
            f"  Trading mode:  {mode.value}",
            *[f"  Reason:        {r}" for r in (reasons or ["not stated"])],
            f"  Account:       equity ${self.account.equity:,.2f}, "
            f"today ${self.account.daily_pnl:,.2f}, "
            f"${self.account.remaining_drawdown:,.2f} to failure",
            f"  Consequence:   {len(remaining)} remaining task(s) skipped - no "
            "further callouts will be issued this cycle.",
            "  Analysis may continue; execution may not.",
        ])
        self._alert(Priority.HALT, f"TRADING HALTED - {mode.value}", body,
                    subline=reason)
        return reason

    def _risk_announcement(self) -> Tuple[Optional[TradingMode], List[str]]:
        """Read anything the risk agent broadcast on the bus during this cycle.

        The risk agent raises an ALERT carrying ``halt`` and ``can_trade`` flags
        when it moves the account to OBSERVATION_ONLY or HALTED. Keying off the
        announcement - rather than only off its task payload - means the cycle
        stops even if the announcement arrived from a task the orchestrator was
        not watching.
        """
        found: Optional[TradingMode] = None
        reasons: List[str] = []
        for msg in self.team.bus.journal[self._bus_mark:]:
            if msg.sender is not Role.RISK or msg.kind is not MessageKind.ALERT:
                continue
            payload = msg.payload if isinstance(msg.payload, dict) else {}
            halted = bool(payload.get("halt"))
            can_trade = payload.get("can_trade")
            if not halted and can_trade is not False:
                continue
            mode = _find_mode(payload) or _find_mode(msg.subject) or TradingMode.HALTED
            found = mode
            reason = _as_text(payload.get("reason") or payload.get("reasons")
                              or msg.subject)
            reasons = [reason] if reason else [f"risk agent announced {mode.value}"]
        return found, reasons

    # ---- emission ------------------------------------------------------
    def _emit_outcome(self, outcome: Optional[SymbolOutcome]) -> None:
        """Issue the callout for one symbol, exactly once."""
        if outcome is None or outcome.emitted:
            return
        outcome.emitted = True

        callout = outcome.callout
        if callout is None:
            callout = TradeCallout(symbol=outcome.symbol, decision=Decision.NO_TRADE)
            callout.reason_to_avoid = self._why_no_callout(outcome.symbol)
            callout.decision_rationale = (
                "NO TRADE by default. The system does not emit a direction it "
                "cannot support with a completed decision and risk pass.")

        if outcome.risk is not None:
            callout.risk_assessment = outcome.risk
        self._merge_account_figures(callout)
        if not callout.analyst_agreement:
            callout.analyst_agreement = outcome.agreement_text()

        risk = callout.risk_assessment
        directional = callout.decision.is_actionable
        vetoed = bool(risk is not None and not risk.approved and directional)
        # No risk pass, no execution. A direction the risk layer never saw is
        # analysis, and it is never painted green or red.
        unauthorised = bool(risk is None and directional)
        actionable = bool(directional and risk is not None and risk.approved
                          and callout.contracts > 0 and not self._observation_only)

        if self._observation_only and directional:
            priority = Priority.HALT
            label = f"{callout.symbol}  {callout.decision.value} - OBSERVATION ONLY"
        elif vetoed:
            priority = Priority.RISK
            label = f"{callout.symbol}  {callout.decision.value} VETOED BY RISK"
        elif unauthorised:
            priority = Priority.RISK
            label = (f"{callout.symbol}  {callout.decision.value} NOT AUTHORISED "
                     "- no risk assessment")
        elif actionable:
            priority = (Priority.LONG if callout.decision is Decision.LONG
                        else Priority.SHORT)
            label = (f"{callout.symbol}  {callout.decision.value}  "
                     f"{callout.contracts} contract(s) @ {fmt_price(callout.entry)}  "
                     f"stop {fmt_price(callout.stop_loss)}")
        else:
            priority = Priority.NO_TRADE
            label = f"{callout.symbol}  NO TRADE"

        subline = self._subline(callout, outcome)
        body = render_callout(callout, account=self.account, notes=outcome.notes,
                              observation_only=self._observation_only)
        self._alert(priority, label, body, subline=subline)

        try:
            self.storage.record_callout(callout)
        except Exception as exc:                        # noqa: BLE001
            outcome.notes.append(f"callout not persisted: {type(exc).__name__}: {exc}")

    def _why_no_callout(self, symbol: str) -> str:
        """Name the task that did not finish, rather than shrugging.

        "No callout was produced" is not a finding. "The decision task never
        ran because the news task failed because news_macro is not staffed" is.
        """
        unstaffed = [r.value for r in (Role.DECISION, Role.RISK)
                     if not self.team.is_staffed(r)]
        if unstaffed:
            return (f"no callout could be formed: the {', '.join(unstaffed)} "
                    "role(s) are not staffed on this team")
        stalled: List[str] = []
        for task in self.board.all():
            if str(task.payload.get("symbol", "")).upper() != symbol:
                continue
            if task.kind in ("decide", "assess_risk") and task.status is not TaskStatus.DONE:
                stalled.append(f"{task.kind} [{task.task_id}] {task.status.value}"
                               + (f" - {task.error}" if task.error else ""))
        if stalled:
            return ("no callout could be formed: " + "; ".join(stalled))
        return "no callout was produced for this symbol in this cycle"

    @staticmethod
    def _subline(callout: TradeCallout, outcome: SymbolOutcome) -> str:
        bits = []
        if callout.strategy:
            bits.append(callout.strategy)
        if callout.timeframe:
            bits.append(callout.timeframe)
        bits.append(f"regime {callout.market_regime.value}")
        bits.append(f"news {callout.news_risk.value}")
        bits.append(f"confidence {callout.confidence:.2f}")
        if callout.dollar_risk:
            bits.append(f"${callout.dollar_risk:,.0f} at risk")
        agreement = callout.analyst_agreement or outcome.agreement_text()
        if agreement:
            bits.append(agreement)
        return "  |  ".join(bits)

    def _merge_account_figures(self, callout: TradeCallout) -> None:
        """Fill the account-level numbers from the live account state.

        The risk assessment is authoritative for size; the account is
        authoritative for what that size means to the buffer. Neither number is
        ever taken from a language model.
        """
        acct = self.account
        risk = callout.risk_assessment
        if risk is not None:
            if risk.approved:
                callout.contracts = risk.contracts or callout.contracts
                callout.dollar_risk = risk.dollar_risk or callout.dollar_risk
                if risk.account_risk_pct:
                    callout.account_risk_pct = risk.account_risk_pct
            else:
                # A vetoed trade is not a smaller trade. It is no trade.
                callout.contracts = 0
                callout.dollar_risk = 0.0
                callout.account_risk_pct = 0.0
        else:
            # Size that no risk assessment stands behind is not size. The
            # decision layer's pre-risk contract count must never survive into
            # a rendered callout.
            callout.contracts = 0
            callout.dollar_risk = 0.0
            callout.account_risk_pct = 0.0
        if not callout.decision.is_actionable:
            callout.contracts = 0
            callout.dollar_risk = 0.0
            callout.account_risk_pct = 0.0

        callout.account_equity = callout.account_equity or acct.equity
        callout.remaining_drawdown_buffer = acct.usable_buffer
        callout.remaining_daily_loss_budget = acct.remaining_daily_loss_budget
        if callout.dollar_risk and acct.usable_buffer > 0:
            callout.buffer_consumed_if_stopped_pct = (
                callout.dollar_risk / acct.usable_buffer)
        elif not callout.dollar_risk:
            callout.buffer_consumed_if_stopped_pct = 0.0
        if callout.dollar_risk and acct.equity and not callout.account_risk_pct:
            callout.account_risk_pct = callout.dollar_risk / acct.equity

    # ---- narration -----------------------------------------------------
    def _alert(self, priority: Priority, headline: str, body: str = "",
               subline: str = "") -> str:
        if not self.emit:
            return f"[{et_stamp()}]\n{headline}\n{body}"
        return alerts.console.alert(priority, headline, body, subline=subline,
                                    timestamp=self.context.now())

    def _line(self, text: str, priority: Priority = Priority.INFO) -> None:
        if self.emit:
            alerts.console.line(text, priority, timestamp=self.context.now())

    def _progress(self, task: Task, result: AgentResult) -> None:
        """One ET-stamped line per task. Every market event leads with the time."""
        who = task.assigned_to.value if task.assigned_to else "unassigned"
        if result.ok:
            priority = _PROGRESS_PRIORITY.get(task.kind, Priority.INFO)
            detail = result.summary or "completed"
            self._line(f"{task.task_id} {who:<18} {task.kind:<20} "
                       f"{detail[:100]}", priority)
        else:
            self._line(f"{task.task_id} {who:<18} {task.kind:<20} "
                       f"FAILED: {result.error[:100]}", Priority.WARNING)

    def _announce_start(self, mode: str, symbols: Sequence[str],
                        trading_mode: TradingMode,
                        reasons: Sequence[str]) -> None:
        if not self.emit:
            return
        body_lines = [
            f"  Symbols:       {', '.join(symbols)}",
            f"  Timeframes:    {', '.join(tf_label(t) for t in self.config.timeframes)}",
            f"  Data:          {type(self.provider).__name__}",
            f"  Reasoning:     {'LLM + deterministic' if self.llm_available else 'deterministic only (no API key / disabled)'}",
            f"  Account:       ${self.account.equity:,.2f} equity, "
            f"${self.account.usable_buffer:,.2f} usable buffer, "
            f"${self.account.remaining_daily_loss_budget:,.2f} daily loss budget",
            f"  Trading mode:  {trading_mode.value}",
        ]
        for r in reasons:
            body_lines.append(f"    - {r}")
        body_lines.extend("  " + line for line in self.staffing_lines()[1:])
        priority = Priority.RESEARCH if trading_mode.can_trade else Priority.WARNING
        self._alert(priority, f"{mode.upper()} CYCLE START",
                    "\n".join(body_lines),
                    subline=f"{len(symbols)} symbol(s) | mode {trading_mode.value}")

    def _announce_end(self, report: CycleReport) -> None:
        if not self.emit:
            return
        lines = [f"  {report.run.summary() if report.run else 'no board run'}"]
        for sym, outcome in report.outcomes.items():
            state = ("ACTIONABLE" if outcome.approved else
                     ("VETOED" if outcome.risk and not outcome.risk.approved
                      and outcome.decision.is_actionable else "STAND DOWN"))
            lines.append(f"  {sym:<6} {outcome.decision.value:<9} {state}")
        if report.failures:
            lines.append(f"  {len(report.failures)} task failure(s):")
            for f in report.failures[:12]:
                lines.append(f"    {f['task_id']} {f['role']:<18} {f['kind']:<18} "
                             f"{f['error'][:90]}")
        stalled = [t for t in self.board.all()
                   if t.status in (TaskStatus.BLOCKED, TaskStatus.ASSIGNED,
                                   TaskStatus.PENDING)]
        if stalled:
            lines.append(f"  {len(stalled)} task(s) never ran:")
            for t in stalled[:12]:
                who = t.assigned_to.value if t.assigned_to else "unassigned"
                lines.append(f"    {t.task_id} {who:<18} {t.kind:<18} "
                             f"{t.status.value}"
                             + (f" - {t.error[:70]}" if t.error else ""))
        if report.unstaffed:
            lines.append(f"  unstaffed roles this cycle: "
                         f"{', '.join(report.unstaffed)}")
        lines.append(f"  Account after: ${self.account.equity:,.2f} equity, "
                     f"${self.account.usable_buffer:,.2f} usable buffer")
        priority = (Priority.HALT if report.halted else
                    (Priority.WARNING if report.failures else Priority.INFO))
        self._alert(priority, f"{report.mode.upper()} CYCLE COMPLETE",
                    "\n".join(lines),
                    subline=f"{report.duration_s:.1f}s")

    # ------------------------------------------------------------------
    # One-off tasks (backtest, walk-forward, anything the board can route)
    # ------------------------------------------------------------------
    def run_task(self, kind: str, *, symbol: Optional[str] = None,
                 title: Optional[str] = None, role: Optional[Role] = None,
                 payload: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Create and dispatch a single task, returning the agent's result."""
        body = dict(payload or {})
        if symbol:
            body["symbol"] = symbol.upper()
        task = self.board.add(kind, title or f"{kind} {symbol or ''}".strip(),
                              payload=body, assigned_to=role)
        if task.assigned_to is None:
            error = task.error or f"no role accepts task kind {kind!r}"
            self._line(f"{task.task_id} {kind:<20} UNROUTABLE: {error}",
                       Priority.WARNING)
            return AgentResult.failure(error)
        self.board.start(task)
        result = self.manager.dispatch(task)
        if result.ok:
            self.board.complete(task, result.payload)
        else:
            self.board.fail(task, result.error or "unknown failure")
        self._progress(task, result)
        return result

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------
    def replay(self, symbol: str, start: Optional[datetime] = None,
               end: Optional[datetime] = None, step: int = 30, *,
               max_steps: int = 8) -> List[CycleReport]:
        """Re-run the live cycle at successive past instants.

        Every agent is pinned to the replay cursor by a
        :class:`~futures_agents.data.providers.ReplayProvider`, which physically
        cannot serve a bar later than the cursor. That is a stronger guarantee
        than asking each agent to respect an ``as_of`` field, and it is the only
        honest way to ask "what would this system have said at 10:15?".
        """
        sym = symbol.upper()
        inner = self.provider.inner if isinstance(self.provider, ReplayProvider) \
            else self.provider
        replay = ReplayProvider(inner)
        series = inner.base_series(sym)
        if len(series) == 0:
            self._alert(Priority.WARNING, f"REPLAY {sym} - no data",
                        "  The provider served an empty series for this symbol.")
            return []

        first, last = series[0].ts, series[-1].ts
        cursor = to_et(start) if start else max(first, last - timedelta(minutes=step * max_steps))
        stop_at = to_et(end) if end else last
        cursor = max(cursor, first)
        stop_at = min(stop_at, last)

        self.provider = replay
        self.context.provider = replay
        reports: List[CycleReport] = []
        self._alert(Priority.RESEARCH, f"REPLAY {sym}",
                    "\n".join([
                        f"  From:   {et_stamp(cursor)}",
                        f"  To:     {et_stamp(stop_at)}",
                        f"  Step:   {step} minute(s), at most {max_steps} step(s)",
                        f"  Bars available: {len(series)} "
                        f"({et_stamp(first)} .. {et_stamp(last)})",
                        "  Each step runs the full live cycle against history "
                        "truncated at the cursor.",
                    ]))

        steps = 0
        while cursor <= stop_at and steps < max_steps:
            replay.seek(cursor)
            self._pin(cursor)
            reports.append(self.run_live_cycle([sym]))
            cursor = cursor + timedelta(minutes=max(1, int(step)))
            steps += 1

        self._pin(None)
        self.provider = inner
        self.context.provider = inner
        return reports

    def _pin(self, when: Optional[datetime]) -> None:
        """Move the shared context to an instant.

        Both caches must be dropped, not just the snapshot cache: under a replay
        provider the *series* itself changes as the cursor advances, so a cached
        ``SymbolFrame`` built at an earlier cursor is stale rather than merely
        incomplete.
        """
        self.context.as_of = to_et(when) if when else None
        self.context.invalidate()
        self.context._frames.clear()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def status_lines(self) -> List[str]:
        """Account, staffing, board and storage - the whole system at a glance."""
        lines = [f"SYSTEM STATUS  [{et_stamp()}]", ""]
        lines.extend(self.account.render().splitlines())
        mode, reasons = self.risk.mode(self.context.now())
        lines.append(f"  Trading mode            {mode.value:>10}")
        for r in reasons:
            lines.append(f"    - {r}")
        lines.append("")
        lines.extend(self.staffing_lines())
        lines.append("")

        board = self.team.fs.read_shared("task_board")
        lines.append("TASK BOARD (last persisted run)")
        if not board or not board.get("tasks"):
            lines.append("  empty - no cycle has been run in this workspace yet")
        else:
            counts = board.get("counts", {})
            lines.append("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
            for row in board["tasks"][-10:]:
                lines.append(f"    {row['status']:<12} {row['task_id']:<6} "
                             f"{str(row.get('assigned_to')):<18} {row['title'][:60]}")
        lines.append("")
        lines.append("STORAGE")
        lines.append(f"  {self.storage.path}")
        try:
            for table, n in sorted(self.storage.counts().items()):
                lines.append(f"    {table:<24} {n:>8}")
        except Exception as exc:                        # noqa: BLE001
            lines.append(f"    unreadable: {type(exc).__name__}: {exc}")
        equity = self.storage.equity_history(limit=1)
        if equity:
            row = equity[-1]
            lines.append(f"  last persisted equity: ${row['equity']:,.2f} "
                         f"at {row['timestamp_et']}")
        lines.append("")
        lines.append("STRATEGIES")
        total = self.context.registry.total()
        if total:
            lines.append(f"  {total} registered across "
                         f"{len(self.context.registry.symbols())} symbol(s) "
                         "in this process")
        else:
            # `status` deliberately skips the ~2s universe build. Saying "0
            # strategies" would read as "the research found nothing", which is
            # a very different statement.
            lines.append("  not built in this process - the universe is "
                         "generated on demand by `research`, `backtest` and "
                         "`demo`")
        counts = self.storage.counts()
        lines.append(f"  {counts.get('strategy_performance', 0)} measured "
                     "strategy result(s) persisted in storage")
        return lines

    def journal_lines(self, *, symbol: Optional[str] = None,
                      limit: int = 25) -> List[str]:
        """The journal, newest first, with the outcome where one is known."""
        rows = self.storage.journal_entries(symbol=symbol, limit=limit)
        title = f"JOURNAL  [{et_stamp()}]"
        if symbol:
            title += f"   symbol={symbol.upper()}"
        lines = [title]
        if not rows:
            lines.append("  no entries yet - the journal agent records one per "
                         "decision, including NO TRADE")
            return lines
        header = (f"  {'DATE':<11} {'TIME':<9} {'SYM':<5} {'DECISION':<9} "
                  f"{'RESULT':<9} {'R':>7} {'P&L':>10}  STRATEGY")
        lines.append(header)
        for r in rows:
            lines.append(
                f"  {str(r.get('date_et', ''))[:10]:<11} "
                f"{str(r.get('time_et', ''))[:8]:<9} "
                f"{str(r.get('symbol', '')):<5} "
                f"{str(r.get('final_decision', '')):<9} "
                f"{str(r.get('result', '')):<9} "
                f"{_as_float(r.get('realised_r')):>7.2f} "
                f"{_as_float(r.get('profit_loss')):>10,.2f}  "
                f"{str(r.get('strategy', ''))[:40]}")
        stats = self.storage.journal_stats(symbol=symbol)
        lines.append("")
        lines.append(f"  resolved trades: {stats['trades']}, "
                     f"win rate {stats['win_rate'] * 100:.1f}%, "
                     f"avg {stats['avg_r']:+.3f}R, "
                     f"total {stats['total_r']:+.2f}R "
                     f"(${stats['total_pnl']:,.2f})")
        return lines

    def roster_lines(self) -> List[str]:
        """The team roster and the workspace tree each agent owns."""
        lines = self.team.roster().splitlines()
        lines.append("")
        lines.append("MANDATES")
        for role in Role:
            spec = ROLES[role]
            staffed = ("staffed" if role in self.team.agents or role is Role.MANAGER
                       else "NOT STAFFED")
            lines.append(f"  {role.value}  [{staffed}]")
            lines.append(f"    {spec.mandate}")
            lines.append(f"    accepts:   {', '.join(sorted(spec.accepts))}")
            lines.append(f"    publishes: {', '.join(spec.publishes) or '-'}")
        lines.append("")
        lines.append("WORKSPACE")
        lines.extend("  " + ln for ln in self.team.fs.tree().splitlines())
        return lines

    def __repr__(self) -> str:
        return (f"<Orchestrator symbols={list(self.config.symbols)} "
                f"staffed={len(self.team.agents)} "
                f"unstaffed={len(self.team.unstaffed)} "
                f"llm={'yes' if self.llm_available else 'no'}>")
