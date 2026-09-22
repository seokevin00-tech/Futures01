"""The risk agent: the decision layer's proposal meets the account's limits.

This module is deliberately thin. Every piece of arithmetic that protects the
account - sizing from the usable drawdown buffer, the de-risk ladder, the
daily loss and correlation limits, the cost-adjusted reward test and the veto
itself - already lives in :mod:`futures_agents.risk.manager`, which is tested
on its own, away from the agent framework. A second copy of those rules here
would be a second set of rules to keep in step, and the day they diverged the
account would be protected by whichever copy happened to run. So this file
does exactly three things:

1. assembles a :class:`~futures_agents.risk.manager.TradeProposal` out of what
   the decision layer published and what the market snapshot, the performance
   database and the account actually say;
2. hands it to :meth:`RiskManager.assess` and reports the verdict *verbatim*;
3. writes the permitted numbers back into the callout, so the journal and the
   alert layer see the size that was actually approved rather than the size
   that was requested.

Two properties of this layer are load-bearing and intentional.

**There is no LLM call in this file.** The role is registered
``llm_backed=False``. A risk verdict has to be reproducible from its inputs
and auditable months later; a model anywhere in this path could move a number
or re-word a veto into something softer, and a veto that can be argued with is
not a veto. The deterministic path is the only path.

**A veto is a successful outcome.** ``assess`` refusing a trade is the system
working, so the task returns ``ok=True`` and reports the refusal as its
result. Failure is reserved for the agent being unable to reach the account or
the market at all.

Where this agent does add strictness of its own - refusing to size a proposal
whose entry, stop or targets the decision layer never published - it says so
in the notes, so that a veto raised here is never mistaken for one raised by
the tested risk manager.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence, Tuple

from ..risk.manager import TradeProposal, TradingMode
from ..schema import (Confidence, Decision, Direction, HistoricalPerformance,
                      NewsRisk, RiskAssessment)
from ..team.agent import AgentResult
from ..team.board import Task
from ..team.roles import Role
from .base import DomainAgent
from .context import AgentContext

__all__ = ["RiskAgent"]


#: Textual agreement labels the decision layer may use, mapped onto the
#: [-1, 1] scale ``TradeProposal`` expects. Negative labels are matched first:
#: "strong disagreement" contains "strong", and reading that as agreement
#: would size *up* into exactly the situation that calls for sizing down.
_AGREEMENT_LABELS: Tuple[Tuple[str, float], ...] = (
    ("OPPOSED", -0.8),
    ("CONTRADICT", -0.7),
    ("CONFLICT", -0.6),
    ("DISAGREE", -0.5),
    ("DIVIDED", -0.3),
    ("SPLIT", 0.0),
    ("MIXED", 0.0),
    ("NEUTRAL", 0.0),
    ("UNANIMOUS", 1.0),
    ("FULL AGREEMENT", 1.0),
    ("STRONG", 0.75),
    ("MAJORITY", 0.5),
    ("2-1", 0.33),
    ("PARTIAL", 0.3),
    ("AGREE", 0.6),
)


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return default
    return f


def _as_int(value: Any, default: int = 0) -> int:
    f = _as_float(value)
    return default if f is None else int(f)


def _first(sources: Sequence[Optional[Dict[str, Any]]], *keys: str) -> Any:
    """First non-empty value found under any of ``keys``, in source order."""
    for src in sources:
        if not isinstance(src, dict):
            continue
        for key in keys:
            if key in src and src[key] not in (None, "", [], {}):
                return src[key]
    return None


class RiskAgent(DomainAgent):
    """Independent risk control: sizing, limits and the unconditional veto."""

    def __init__(self, fs, bus, *, context=None, config=None, llm=None):
        super().__init__(Role.RISK, fs, bus,
                         context=context, config=config, llm=llm)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def handle(self, task: Task) -> AgentResult:
        ctx = self.require_context()
        payload = task.payload if isinstance(task.payload, dict) else {}
        symbol = str(payload.get("symbol") or "").upper()
        if task.kind == "check_limits":
            return self._check_limits(ctx, symbol, task)
        return self._assess(ctx, symbol, task)

    # ------------------------------------------------------------------
    # check_limits - account state and trading mode, no proposal needed
    # ------------------------------------------------------------------
    def _check_limits(self, ctx: AgentContext, symbol: str,
                      task: Task) -> AgentResult:
        mode, mode_reasons = ctx.risk.mode(ctx.now())
        budget, multiplier, notes = ctx.risk.risk_budget(None)
        st = ctx.account

        ra = RiskAssessment()
        ra.remaining_daily_loss_budget = st.remaining_daily_loss_budget
        ra.remaining_drawdown_buffer = st.usable_buffer
        ra.risk_multiplier_applied = multiplier
        ra.notes.append("limits check - no trade proposal was sized")
        ra.notes.append(f"trading mode: {mode.value}")
        ra.notes.extend(notes)
        ra.notes.append(f"permitted risk on the next trade: ${budget:,.2f}")
        # Mirrors RiskManager.assess step 1: when the account may not trade,
        # that prohibition *is* the veto and applies to every proposal.
        if mode.can_trade:
            ra.warnings.extend(mode_reasons)
        else:
            ra.vetoes.extend(mode_reasons)

        headline = (
            f"{mode.value}: ${budget:,.2f} permitted per trade, "
            f"${st.usable_buffer:,.2f} usable buffer, "
            f"${st.remaining_daily_loss_budget:,.2f} left in today's loss budget")
        extra = {
            "kind": "limits_check",
            "symbol": symbol,
            "trading_mode": mode.value,
            "mode_reasons": list(mode_reasons),
            "can_trade": mode.can_trade,
            "permitted_dollar_risk": round(budget, 2),
            "headline": headline,
        }
        self._emit(ctx, ra, mode, mode_reasons, extra, task, symbol)
        return AgentResult(ok=True, summary=headline,
                           payload={**ra.to_dict(), **extra},
                           artefacts=["risk_assessment", "account_state"])

    # ------------------------------------------------------------------
    # size_position / assess_risk / veto - the full assessment path
    # ------------------------------------------------------------------
    def _assess(self, ctx: AgentContext, symbol: str, task: Task) -> AgentResult:
        decision_doc = self._read_dict(Role.DECISION, "decision")
        callout_doc = self._read_dict(Role.DECISION, "callout")

        published_symbol = str(
            _first([callout_doc, decision_doc], "symbol") or "").upper()
        if not symbol:
            symbol = published_symbol or (ctx.symbols[0] if ctx.symbols else "")
        mismatch = bool(published_symbol and symbol and published_symbol != symbol)
        if mismatch:
            # The published decision is about a different contract. Sizing it
            # against this symbol's spec would size the wrong instrument.
            nothing_reason = (f"the published decision is for {published_symbol}, "
                              f"not {symbol}")
            decision_doc = callout_doc = None
        elif decision_doc is None and callout_doc is None:
            nothing_reason = "no decision has been published yet"
        else:
            nothing_reason = "the decision layer published NO TRADE"

        decision = Decision.coerce(
            _first([callout_doc, decision_doc], "decision", "verdict", "action"))
        mode, mode_reasons = ctx.risk.mode(ctx.now())
        proposal: Optional[TradeProposal] = None
        blocker = ""

        if decision.is_actionable:
            proposal, blocker = self._build_proposal(
                ctx, symbol, decision, decision_doc, callout_doc)
            if proposal is None:
                ra = self._agent_veto(ctx, blocker, mode, mode_reasons)
            else:
                # The verdict is taken exactly as the risk manager returns it.
                ra = ctx.risk.assess(proposal, when=ctx.now())
        else:
            ra = self._nothing_to_size(ctx, mode, mode_reasons, nothing_reason)

        headline = self._headline(task.kind, decision, ra, mode)
        extra = {
            "kind": task.kind,
            "symbol": symbol,
            "decision_in": decision.value,
            "decision_out": (Decision.NO_TRADE.value
                             if not ra.approved else decision.value),
            "trading_mode": mode.value,
            "mode_reasons": list(mode_reasons),
            "can_trade": mode.can_trade,
            "vetoed": bool(ra.vetoes),
            "veto_reason": ra.veto_reason,
            "proposal": self._proposal_dict(proposal),
            "headline": headline,
        }
        self._emit(ctx, ra, mode, mode_reasons, extra, task, symbol)
        self._finalise_callout(ctx, callout_doc, ra, decision, symbol, headline)
        self.log(ctx.risk.render(ra))
        return AgentResult(ok=True, summary=headline,
                           payload={**ra.to_dict(), **extra},
                           artefacts=["risk_assessment", "account_state"])

    # ------------------------------------------------------------------
    # Proposal assembly
    # ------------------------------------------------------------------
    def _build_proposal(self, ctx: AgentContext, symbol: str, decision: Decision,
                        decision_doc: Optional[Dict[str, Any]],
                        callout_doc: Optional[Dict[str, Any]]
                        ) -> Tuple[Optional[TradeProposal], str]:
        """Build the proposal, or explain why it cannot be sized."""
        docs = [callout_doc, decision_doc]
        direction = decision.as_direction
        snap = ctx.snapshot(symbol)

        stop = _as_float(_first(docs, "stop_loss", "stop", "invalidation_price"))
        if stop is None:
            return None, ("the decision layer published no stop - risk on this "
                          "trade is undefined and cannot be sized")

        entry, entry_note = self._entry_price(docs, stop, direction)
        if entry is None:
            return None, ("the decision layer published no entry price - risk "
                          "on this trade is undefined and cannot be sized")

        targets = [t for t in (_as_float(x) for x in
                               (_first(docs, "targets", "target_prices") or []))
                   if t is not None]

        regime = str(_first(docs, "market_regime", "regime")
                     or (snap.regime.regime if snap else "") or "UNKNOWN")
        volatility = str((snap.regime.volatility if snap else None)
                         or _first(docs, "volatility", "volatility_regime")
                         or "NORMAL")
        session = str((snap.session if snap else None)
                      or _first(docs, "session") or "")
        timeframe = self._timeframe(docs)
        strategy_id = str(_first(docs, "strategy_id", "strategy") or "")
        strategy_name = str(_first(docs, "strategy_name", "strategy")
                            or strategy_id)

        atr = _as_float(snap.regime.atr if snap else None)
        atr_median = _as_float(snap.regime.atr_median if snap else None)
        if atr is None and snap is not None and timeframe:
            atr = _as_float(snap.value(timeframe, "atr"))

        proposal = TradeProposal(
            symbol=symbol,
            direction=direction,
            entry=entry,
            stop=stop,
            targets=targets,
            confidence=Confidence(_as_float(_first(docs, "confidence"), 0.0) or 0.0),
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            timeframe=timeframe,
            regime=regime,
            volatility=volatility,
            session=session,
            news_risk=self._news_risk(ctx, docs),
            minutes_to_high_impact=self._minutes_to_event(ctx, docs),
            historical=self._historical(ctx, strategy_id=strategy_id, symbol=symbol,
                                        regime=regime, session=session,
                                        timeframe=timeframe),
            atr=atr,
            atr_median=atr_median,
            analyst_agreement=self._agreement(docs, direction),
        )
        if entry_note:
            self.log(entry_note)
        return proposal, ""

    @staticmethod
    def _entry_price(docs: Sequence[Optional[Dict[str, Any]]], stop: float,
                     direction: Direction) -> Tuple[Optional[float], str]:
        """The entry to size against, preferring an explicit price.

        When only a zone was published, the end furthest from the stop is used.
        That is the widest stop distance the zone permits, so it produces the
        smallest position the zone could justify - the conservative reading,
        and a deterministic one.
        """
        entry = _as_float(_first(docs, "entry", "entry_price"))
        if entry is not None:
            return entry, ""
        zone = _first(docs, "entry_zone")
        if isinstance(zone, (list, tuple)) and len(zone) == 2:
            ends = [e for e in (_as_float(z) for z in zone) if e is not None]
            if len(ends) == 2:
                worst = max(ends, key=lambda e: abs(e - stop))
                return worst, (f"entry zone {ends} sized at {worst} - the end "
                               "furthest from the stop")
        return None, ""

    @staticmethod
    def _timeframe(docs: Sequence[Optional[Dict[str, Any]]]) -> int:
        raw = _first(docs, "timeframe", "timeframe_minutes")
        if raw is None:
            return 0
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        return int(digits) if digits else 0

    @staticmethod
    def _news_risk(ctx: AgentContext,
                   docs: Sequence[Optional[Dict[str, Any]]]) -> NewsRisk:
        raw = _first(docs, "news_risk")
        if raw is None and ctx.news is not None:
            return ctx.news.risk
        try:
            return NewsRisk(str(raw).strip().upper())
        except (TypeError, ValueError):
            return NewsRisk.NONE

    @staticmethod
    def _minutes_to_event(ctx: AgentContext,
                          docs: Sequence[Optional[Dict[str, Any]]]
                          ) -> Optional[float]:
        if ctx.news is not None and ctx.news.minutes_to_next_high_impact is not None:
            return _as_float(ctx.news.minutes_to_next_high_impact)
        return _as_float(_first(docs, "minutes_to_high_impact",
                                "minutes_to_next_high_impact"))

    def _agreement(self, docs: Sequence[Optional[Dict[str, Any]]],
                   direction: Direction) -> float:
        """Analyst agreement with the proposed direction, in [-1, 1].

        Measured from the analysts' own predictions when they are attached:
        +1 means every analyst backs this direction at full confidence, -1
        that they all oppose it. A textual label is only a fallback, because a
        label is a summary of the numbers and the numbers are available.
        """
        explicit = _as_float(_first(docs, "analyst_agreement_score",
                                    "agreement_score", "agreement"))
        if explicit is None:
            raw = _first(docs, "analyst_agreement")
            if isinstance(raw, (int, float)):
                explicit = _as_float(raw)
        if explicit is not None:
            return max(-1.0, min(1.0, explicit))

        preds = _first(docs, "analyst_predictions", "predictions")
        score = self._agreement_from_predictions(preds, direction)
        if score is not None:
            return score

        label = str(_first(docs, "analyst_agreement", "agreement_label") or "").upper()
        for token, value in _AGREEMENT_LABELS:
            if token in label:
                return value
        return 0.0

    @staticmethod
    def _agreement_from_predictions(preds: Any,
                                    direction: Direction) -> Optional[float]:
        if not isinstance(preds, (list, tuple)) or not preds:
            return None
        num = den = 0.0
        for p in preds:
            if not isinstance(p, dict):
                continue
            d = Direction.coerce(_first([p], "direction", "bias", "view"))
            weight = _as_float(_first([p], "confidence"), 1.0) or 1.0
            weight = max(0.0, min(1.0, weight))
            num += d.sign * direction.sign * weight
            den += weight
        if den <= 0:
            return None
        return max(-1.0, min(1.0, num / den))

    # ------------------------------------------------------------------
    # Measured history
    # ------------------------------------------------------------------
    def _historical(self, ctx: AgentContext, *, strategy_id: str, symbol: str,
                    regime: str, session: str, timeframe: int
                    ) -> Optional[HistoricalPerformance]:
        """The strategy's measured record, or ``None`` if it has none.

        ``None`` is a real answer: the risk manager vetoes an unmeasured edge,
        and inventing a plausible-looking record here to get past that check
        would defeat the entire point of the gate.
        """
        if not strategy_id:
            return None
        row: Optional[Dict[str, Any]] = None
        for reg, ses in ((regime, session), (regime, "ALL"),
                         ("ALL", session), ("ALL", "ALL")):
            if not reg or not ses:
                continue
            row = ctx.storage.strategy_performance(strategy_id, regime=reg,
                                                   session=ses)
            if row:
                break
        if row is None:
            for cand in ctx.top_strategies(symbol, regime=regime or None,
                                           live_eligible_only=False):
                if str(cand.get("strategy_id") or "") == strategy_id:
                    row = cand
                    break
        if row is None:
            return None
        return self._history_from_row(row, symbol=symbol, timeframe=timeframe)

    @staticmethod
    def _history_from_row(row: Dict[str, Any], *, symbol: str,
                          timeframe: int) -> HistoricalPerformance:
        extra: Dict[str, Any] = {}
        raw = row.get("payload")
        if isinstance(raw, str) and raw.strip():
            try:
                loaded = json.loads(raw)
            except (TypeError, ValueError):
                loaded = None
            if isinstance(loaded, dict):
                extra = loaded
        elif isinstance(raw, dict):
            extra = raw

        def val(*names: str) -> Any:
            return _first([row, extra], *names)

        tf = _as_int(val("timeframe"), timeframe)
        return HistoricalPerformance(
            strategy_id=str(val("strategy_id") or ""),
            symbol=str(val("symbol") or symbol).upper(),
            timeframe=tf or None,
            regime=row.get("regime"),
            session=row.get("session"),
            trades=_as_int(val("trades"), 0),
            win_rate=_as_float(val("win_rate"), 0.0) or 0.0,
            avg_win_r=_as_float(val("avg_win_r"), 0.0) or 0.0,
            avg_loss_r=_as_float(val("avg_loss_r"), 0.0) or 0.0,
            profit_factor=_as_float(val("profit_factor"), 0.0) or 0.0,
            expectancy_r=_as_float(val("expectancy_r"), 0.0) or 0.0,
            max_drawdown_r=_as_float(val("max_drawdown_r"), 0.0) or 0.0,
            max_consecutive_losses=_as_int(val("max_consecutive_losses"), 0),
            sharpe=_as_float(val("sharpe"), 0.0) or 0.0,
            sortino=_as_float(val("sortino"), 0.0) or 0.0,
            out_of_sample_trades=_as_int(
                val("out_of_sample_trades", "oos_trades"), 0),
            out_of_sample_expectancy_r=_as_float(
                val("out_of_sample_expectancy_r", "oos_expectancy_r"), 0.0) or 0.0,
            walk_forward_efficiency=_as_float(
                val("walk_forward_efficiency"), 0.0) or 0.0,
            robustness_score=_as_float(val("robustness_score"), 0.0) or 0.0,
            # The research layer's own eligibility verdict stands in when the
            # richer payload is absent. Falling back to False rather than True
            # keeps the error on the cautious side: it only ever reduces size.
            sample_is_sufficient=bool(
                extra.get("sample_is_sufficient", row.get("live_eligible"))),
        )

    # ------------------------------------------------------------------
    # Assessments that do not come from a proposal
    # ------------------------------------------------------------------
    def _base_assessment(self, ctx: AgentContext, mode: TradingMode,
                         mode_reasons: Sequence[str]) -> RiskAssessment:
        st = ctx.account
        _budget, multiplier, notes = ctx.risk.risk_budget(None)
        ra = RiskAssessment()
        ra.remaining_daily_loss_budget = st.remaining_daily_loss_budget
        ra.remaining_drawdown_buffer = st.usable_buffer
        ra.risk_multiplier_applied = multiplier
        ra.notes.append(f"trading mode: {mode.value}")
        ra.notes.extend(notes)
        if mode.can_trade:
            ra.warnings.extend(mode_reasons)
        else:
            ra.vetoes.extend(mode_reasons)
        return ra

    def _nothing_to_size(self, ctx: AgentContext, mode: TradingMode,
                         mode_reasons: Sequence[str],
                         reason: str) -> RiskAssessment:
        """NO TRADE: record the account state, size nothing.

        This is not a veto and must not read as one - ``vetoes`` stays empty
        unless the account itself is barred from trading.
        """
        ra = self._base_assessment(ctx, mode, mode_reasons)
        ra.notes.insert(0, f"nothing to size - {reason}")
        return ra

    def _agent_veto(self, ctx: AgentContext, reason: str, mode: TradingMode,
                    mode_reasons: Sequence[str]) -> RiskAssessment:
        """A veto raised here, on an input the risk manager never saw.

        Flagged as such so an auditor can tell it apart from a veto raised by
        the tested risk manager. It is strictly a refusal - it can never let a
        trade through that ``assess`` would have stopped.
        """
        ra = self._base_assessment(ctx, mode, mode_reasons)
        ra.vetoes.append(reason)
        ra.notes.append("veto raised by the risk agent's input validation, "
                        "before the risk manager was called")
        return ra

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------
    def _emit(self, ctx: AgentContext, ra: RiskAssessment, mode: TradingMode,
              mode_reasons: Sequence[str], extra: Dict[str, Any], task: Task,
              symbol: str) -> None:
        """Publish both artefacts, record equity, and alert if the account is
        no longer permitted to trade."""
        st = ctx.account
        budget, multiplier, budget_notes = ctx.risk.risk_budget(None)

        # The assessment is published flat and verbatim: every RiskAssessment
        # field keeps its own name and value, with context added alongside it.
        self.publish("risk_assessment", {**ra.to_dict(), **extra},
                     extra.get("headline", ""))

        account = {
            **st.to_dict(),
            "trading_mode": mode.value,
            "mode_reasons": list(mode_reasons),
            "can_trade": mode.can_trade,
            "permitted_dollar_risk": round(budget, 2),
            "risk_multiplier": round(multiplier, 4),
            "risk_budget_notes": list(budget_notes),
            "symbol": symbol,
            "render": st.render(),
        }
        self.publish("account_state", account,
                     f"{mode.value}: equity ${st.equity:,.2f}, usable buffer "
                     f"${st.usable_buffer:,.2f}")

        ctx.storage.record_equity(st.equity, st.peak_equity, st.daily_pnl,
                                  note=f"{task.kind} {symbol} {mode.value}".strip())

        if mode in (TradingMode.OBSERVATION_ONLY, TradingMode.HALTED):
            # The manager halts the run on this; it is the only way the rest of
            # the team learns that callouts must stop.
            self.bus.alert(self.role, f"account {mode.value}", {
                "trading_mode": mode.value,
                "reasons": list(mode_reasons),
                "halt": mode is TradingMode.HALTED,
                "can_trade": mode.can_trade,
                "symbol": symbol,
                "equity": round(st.equity, 2),
                "daily_pnl": round(st.daily_pnl, 2),
                "remaining_daily_loss_budget": round(
                    st.remaining_daily_loss_budget, 2),
                "usable_buffer": round(st.usable_buffer, 2),
            })

    def _finalise_callout(self, ctx: AgentContext,
                          callout_doc: Optional[Dict[str, Any]],
                          ra: RiskAssessment, decision: Decision, symbol: str,
                          headline: str) -> None:
        """Write the approved numbers into the callout and republish it.

        A callout the risk layer did not approve is converted to NO TRADE here.
        An actionable callout must never survive a veto: downstream, a
        ``decision`` of LONG or SHORT is an instruction to put money on.
        """
        if not isinstance(callout_doc, dict):
            return
        out = dict(callout_doc)
        st = ctx.account
        out["contracts"] = ra.contracts
        out["dollar_risk"] = round(ra.dollar_risk, 2)
        out["account_risk_pct"] = round(ra.account_risk_pct, 5)
        out["remaining_drawdown_buffer"] = round(ra.remaining_drawdown_buffer, 2)
        out["buffer_consumed_if_stopped_pct"] = round(
            ra.buffer_consumed_if_stopped_pct, 5)
        out["remaining_daily_loss_budget"] = round(
            ra.remaining_daily_loss_budget, 2)
        out["account_equity"] = round(st.equity, 2)
        out["risk_assessment"] = ra.to_dict()
        # Added, not substituted: the decision layer's gross reward/risk keeps
        # its meaning, and the net number sits beside it.
        out["expected_reward_risk_after_costs"] = round(
            ra.reward_risk_after_costs, 3)
        out["expected_cost"] = round(ra.expected_cost, 2)

        if decision.is_actionable and not ra.approved:
            veto = ra.veto_reason or "the risk layer did not approve this trade"
            out["decision"] = Decision.NO_TRADE.value
            out["contracts"] = 0
            out["dollar_risk"] = 0.0
            out["account_risk_pct"] = 0.0
            out["buffer_consumed_if_stopped_pct"] = 0.0
            previous = str(out.get("reason_to_avoid") or "").strip()
            out["reason_to_avoid"] = (f"RISK VETO: {veto}"
                                      + (f" | {previous}" if previous else ""))
            entry_reason = str(out.get("reason_for_entry") or "").strip()
            if entry_reason:
                # Kept in the rationale for the audit trail, removed from the
                # field an alert layer would render as a reason to buy.
                rationale = str(out.get("decision_rationale") or "").strip()
                out["decision_rationale"] = (
                    f"{rationale}\n[risk] entry rationale withdrawn after veto: "
                    f"{entry_reason}").strip()
            out["reason_for_entry"] = ""

        self.publish("callout", out, headline)

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _headline(kind: str, decision: Decision, ra: RiskAssessment,
                  mode: TradingMode) -> str:
        if ra.approved:
            return (f"APPROVED {decision.value} {ra.contracts} contract(s), "
                    f"${ra.dollar_risk:,.2f} at risk "
                    f"({ra.account_risk_pct:.2%} of equity, "
                    f"{ra.buffer_consumed_if_stopped_pct:.1%} of the usable "
                    f"buffer) [{mode.value}]")
        if ra.vetoes:
            return f"VETOED ({mode.value}): {ra.veto_reason}"
        if not decision.is_actionable:
            return (f"NO TRADE - nothing to size [{mode.value}], "
                    f"${ra.remaining_daily_loss_budget:,.2f} left in today's "
                    f"loss budget")
        return f"NOT APPROVED ({mode.value}) on task {kind}"

    @staticmethod
    def _proposal_dict(proposal: Optional[TradeProposal]) -> Optional[dict]:
        if proposal is None:
            return None
        return {
            "symbol": proposal.symbol,
            "direction": proposal.direction.value,
            "entry": proposal.entry,
            "stop": proposal.stop,
            "targets": list(proposal.targets),
            "risk_points": round(proposal.risk_points, 5),
            "reward_risk": round(proposal.reward_risk, 3),
            "first_target_rr": round(proposal.first_target_rr, 3),
            "confidence": proposal.confidence,
            "strategy_id": proposal.strategy_id,
            "strategy_name": proposal.strategy_name,
            "timeframe": proposal.timeframe,
            "regime": proposal.regime,
            "volatility": proposal.volatility,
            "session": proposal.session,
            "news_risk": proposal.news_risk.value,
            "minutes_to_high_impact": proposal.minutes_to_high_impact,
            "atr": proposal.atr,
            "atr_median": proposal.atr_median,
            "analyst_agreement": round(proposal.analyst_agreement, 3),
            "historical": (proposal.historical.to_dict()
                           if proposal.historical else None),
        }

    def _read_dict(self, owner: Role, artefact: str) -> Optional[Dict[str, Any]]:
        doc = self.read_from(owner, artefact)
        return doc if isinstance(doc, dict) else None
