"""Journal and continuous learning - the system's memory, and its feedback loop.

Three jobs, in this order of importance:

1. **Record everything, including what was not traded.** Every callout becomes
   one :class:`~futures_agents.schema.JournalEntry` carrying the whole decision
   context: the analysts' predictions, the regime, the session, the news
   environment, the levels and the money at risk. NO TRADE decisions are
   recorded too, with ``result="NOT_TAKEN"``, because a journal of taken trades
   alone cannot learn what the system correctly avoided or wrongly passed on -
   and that is half the learning signal.

2. **Measure who is actually right, under which conditions.** Hit rate is the
   least interesting number in trading. An analyst right 70% of the time whose
   losers are three times its winners is worse than one right 45% with
   controlled losses, so every analyst is judged on risk-adjusted contribution:
   expectancy in R, loss magnitude, profit factor, false signals, unnecessary
   trades and worst losing streak - sliced by regime, session and volatility,
   and explicitly labelled when the sample is too small to mean anything.

3. **Feed it back, downwards only.** Influence weights are reduced when live
   results decay against the backtest, never raised on a winning streak. A
   run of winners over a handful of trades is noise, and treating it as signal
   is how a system drifts into overtrading its most recent luck.

Everything here is measured. Excursions, exits and R multiples come from bars,
using the same intrabar conventions as the backtester (a bar containing both
the stop and a target is read as the stop filling first; gaps fill at the open)
and the same cost model, because a live figure compared against a backtest
figure computed differently is not a comparison at all. The model is used, if
it is available, only to phrase findings that were already computed - and a
phrasing that introduces a number the arithmetic did not produce is discarded.
"""

from __future__ import annotations

import bisect
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..backtest.costs import CostModel
from ..config import get_contract
from ..data.bars import Bar
from ..schema import (Confidence, Decision, Direction, JournalEntry,
                      MarketRegime, StrategyStats, VolatilityRegime)
from ..team.agent import AgentResult
from ..team.board import Task
from ..team.roles import Role
from ..timeutil import classify_session, et_stamp_short, to_et
from .base import DomainAgent
from .context import AgentContext

__all__ = ["JournalAgent"]


#: Minimum resolved predictions before an analyst's record is judged at all.
#: Deliberately equal to ``Storage.agent_accuracy``'s own "sufficient" bar, so
#: the scorecard and the decision layer never disagree about whether a track
#: record means anything yet.
MIN_AGENT_SAMPLE = 20

#: Minimum live trades before a strategy's influence weight may be touched.
MIN_LIVE_TRADES = 20

#: A conditional slice may be *reported* on fewer trades than it may be *acted*
#: on. Findings below ``MIN_LIVE_TRADES`` are published as provisional.
MIN_SLICE_SAMPLE = 8

#: Expectancy gap below which a slice is not worth stating.
MIN_SLICE_EDGE_R = 0.10

#: How far forward a trade is measured when no explicit outcome is supplied.
DEFAULT_HORIZON_MINUTES = 240

#: How far past the exit "what happened afterwards" looks.
AFTER_WINDOW_MINUTES = 60

WEIGHT_FLOOR = 0.10
WEIGHT_CAP = 1.0

#: Results that count as a closed, real trade. NOT_TAKEN and OPEN never enter
#: live performance statistics.
RESOLVED_RESULTS: Tuple[str, ...] = ("WIN", "LOSS", "BREAKEVEN", "SCRATCH")

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_REGIMES = {r.value for r in MarketRegime}
_VOLS = {v.value for v in VolatilityRegime}
_NEWS_LEVELS = ("BLACKOUT", "HIGH", "MODERATE", "LOW", "NONE")


# --------------------------------------------------------------------------
# Small conversions. SQLite hands back JSON strings and NULLs; every one of
# these exists so a malformed column degrades to a default instead of raising
# in the middle of a resolution.
# --------------------------------------------------------------------------

def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and abs(out) != float("inf") else default


def _fo(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _parse_et(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return to_et(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError):
        return None


def _regime_of(value: Any, default: MarketRegime = MarketRegime.UNKNOWN) -> MarketRegime:
    try:
        return MarketRegime(str(value).upper())
    except (TypeError, ValueError):
        return default


def _vol_of(value: Any, default: VolatilityRegime = VolatilityRegime.NORMAL) -> VolatilityRegime:
    try:
        return VolatilityRegime(str(value).upper())
    except (TypeError, ValueError):
        return default


def _agent_id(analyst_id: Any) -> str:
    """Normalise an analyst identifier to the role id the rest of the team uses.

    The schema labels analysts "A"/"B"/"C"; ``Storage.agent_scores`` is keyed by
    a free-form agent id and the decision layer looks accuracy up by role. The
    role value is therefore the canonical form, and "A" maps to "analyst_a".
    """
    s = str(analyst_id or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return ""
    if s.startswith("analyst_"):
        return s
    if len(s) == 1 and s.isalpha():
        return f"analyst_{s}"
    return s


def _numbers(text: str) -> set:
    return set(_NUMBER.findall(text or ""))


# --------------------------------------------------------------------------
# Tolerant readers for artefacts written by agents built in parallel. The shape
# each one publishes is fixed by the contract, but a wrapper around it is
# cheap to accept and expensive to be broken by.
# --------------------------------------------------------------------------

def _extract_callout(raw: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict) or depth > 3:
        return None
    if isinstance(raw.get("decision"), str) and "symbol" in raw:
        return raw
    for key in ("callout", "final_callout", "trade_callout", "decision", "payload"):
        found = _extract_callout(raw.get(key), depth + 1)
        if found is not None:
            return found
    return None


def _extract_predictions(raw: Any, depth: int = 0) -> List[Dict[str, Any]]:
    if depth > 3 or raw is None:
        return []
    if isinstance(raw, list):
        out: List[Dict[str, Any]] = []
        for item in raw:
            out.extend(_extract_predictions(item, depth + 1))
        return out
    if not isinstance(raw, dict):
        return []
    if "direction" in raw and "analyst_id" in raw:
        return [raw]
    for key in ("prediction", "predictions", "payload"):
        found = _extract_predictions(raw.get(key), depth + 1)
        if found:
            return found
    return [raw] if "direction" in raw else []


def _evidence_names(items: Any, kinds: Sequence[str] = ()) -> List[str]:
    names: List[str] = []
    for item in _list(items):
        if not isinstance(item, dict):
            continue
        if kinds and str(item.get("kind", "")).lower() not in kinds:
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _dedupe(values: Sequence[str]) -> List[str]:
    """Order-preserving de-duplication - the order is the order of importance
    the publishing agent chose, and sorting would throw it away."""
    seen, out = set(), []
    for v in values:
        key = str(v).strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    return out


def _normalise_prediction(raw: Dict[str, Any], fallback_id: str) -> Dict[str, Any]:
    """One analyst's view, reduced to what the journal must be able to score."""
    analyst_id = str(raw.get("analyst_id") or fallback_id or "").strip().upper()
    zone = raw.get("entry_zone")
    targets = [t for t in (raw.get("target_1"), raw.get("target_2"),
                           raw.get("target_3")) if t is not None]
    return {
        "analyst_id": analyst_id,
        "agent_id": _agent_id(raw.get("agent_id") or analyst_id),
        "analyst_name": str(raw.get("analyst_name") or ""),
        "specialisation": str(raw.get("specialisation") or ""),
        "direction": Direction.coerce(raw.get("direction")).value,
        "entry_zone": list(zone) if isinstance(zone, (list, tuple)) else None,
        "stop": _fo(raw.get("stop")),
        "targets": [_f(t) for t in targets],
        "expected_reward_risk": _fo(raw.get("expected_reward_risk")),
        "confidence": Confidence(raw.get("confidence")),
        "time_horizon": str(raw.get("time_horizon") or ""),
        "primary_reason": str(raw.get("primary_reason") or ""),
        "supporting_confluences": [str(c) for c in _list(raw.get("supporting_confluences"))],
        "invalidation_conditions": [str(c) for c in _list(raw.get("invalidation_conditions"))],
        "would_change_mind_if": str(raw.get("would_change_mind_if") or ""),
        "evidence_names": _evidence_names(raw.get("evidence")),
        "timestamp_et": str(raw.get("timestamp_et") or ""),
        "source": str(raw.get("source") or ""),
    }


# --------------------------------------------------------------------------
# Measurement primitives
# --------------------------------------------------------------------------

def _max_drawdown_r(sequence: Sequence[float]) -> float:
    """Worst peak-to-trough decline of the cumulative R curve, as a positive
    number. Reported from the live journal in the same units the backtest
    reports it, so the two can be put side by side."""
    peak = cum = worst = 0.0
    for r in sequence:
        cum += r
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def _profit_factor(values: Sequence[float]) -> Optional[float]:
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses <= 0:
        return None            # undefined, not infinite - say so rather than flatter
    return gains / losses


def _trailing_losses(sequence: Sequence[float]) -> int:
    count = 0
    for r in reversed(sequence):
        if r < 0:
            count += 1
        else:
            break
    return count


def _worst_streak(flags: Sequence[bool]) -> int:
    """Longest run of ``False`` in chronological order."""
    worst = run = 0
    for ok in flags:
        run = 0 if ok else run + 1
        worst = max(worst, run)
    return worst


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------

class JournalAgent(DomainAgent):
    """Records every prediction, decision and outcome, and measures them."""

    #: Stable (and therefore cacheable) system prompt. The model never sees a
    #: task that lets it originate a number.
    NARRATIVE_ROLE = """
You are the journal of a futures trading team. Every number you are given was
computed from recorded trades; you are not able to check or improve any of
them, and you must not produce new ones.

Your only task is wording. Turn each supplied finding into one plain sentence
that states the condition, the direction of the effect and the sample size,
using only the figures given, in the style of: "MNQ 5-minute VWAP reversals
perform well in low-volatility sessions but poorly immediately after CPI."

Do not add a number, a percentage, a price or a sample size that is not in the
finding you were given. Do not add advice, forecasts or encouragement. A
finding with a small sample must read as provisional.
""".strip()

    def __init__(self, fs, bus, *, context: Optional[AgentContext] = None,
                 config: Any = None, llm: Any = None):
        super().__init__(Role.JOURNAL, fs, bus, context=context, config=config,
                         llm=llm)

    # ---- entry point ---------------------------------------------------
    def handle(self, task: Task) -> AgentResult:
        ctx = self.require_context()
        payload = task.payload if isinstance(task.payload, dict) else {}
        symbol = str(payload.get("symbol") or (ctx.symbols[0] if ctx.symbols else "")).upper()
        if not symbol:
            raise ValueError("no symbol in the task payload and none configured")
        handlers = {
            "record": self._record,
            "resolve_trade": self._resolve_trade,
            "evaluate_agents": self._evaluate_agents,
            "learn": self._learn,
        }
        handler = handlers.get(task.kind)
        if handler is None:
            raise ValueError(f"journal does not implement task kind '{task.kind}'")
        return handler(ctx, symbol, payload)

    # ==================================================================
    # record
    # ==================================================================
    def _record(self, ctx: AgentContext, symbol: str,
                payload: Dict[str, Any]) -> AgentResult:
        """Write one journal row for the final callout, taken or not."""
        callout, origin = self._read_callout(symbol)
        predictions = self._read_predictions(symbol)
        snap = ctx.snapshot(symbol)

        if callout is None:
            # Nothing has been decided yet. That is a legitimate state, not a
            # failure - but it is recorded as such rather than passed over.
            reason = (f"no callout published for {symbol} by risk or decision; "
                      f"{len(predictions)} analyst prediction(s) on file, "
                      "nothing to journal yet")
            artefact = {
                "timestamp_et": to_et(ctx.now()).isoformat(),
                "symbol": symbol,
                "recorded": False,
                "reason": reason,
                "analyst_predictions_seen": len(predictions),
                "entries_on_file": len(ctx.storage.journal_entries(symbol=symbol,
                                                                   limit=500)),
                "source": "deterministic",
            }
            self.publish("journal", artefact, f"nothing to journal for {symbol}")
            return AgentResult(ok=True,
                               summary=f"[{et_stamp_short(ctx.now())}] {reason}",
                               payload=artefact, artefacts=["journal"])

        entry = self._build_entry(ctx, symbol, callout, predictions, snap)
        entry_id = ctx.storage.record_journal(entry)

        rows = ctx.storage.journal_entries(symbol=symbol, limit=500)
        artefact = {
            "timestamp_et": to_et(ctx.now()).isoformat(),
            "symbol": symbol,
            "recorded": True,
            "callout_source": origin,
            "entry_id": entry_id,
            "entry": entry.to_dict(),
            "totals": self._totals(rows),
            "recent_entries": [_row_summary(r) for r in rows[:15]],
            "snapshot_available": snap is not None,
            "source": "deterministic",
        }
        self.publish("journal", artefact,
                     f"{entry.final_decision.value} {symbol} recorded as {entry.result}")
        summary = (f"[{et_stamp_short(ctx.now())}] journaled {entry.final_decision.value} "
                   f"{symbol} as {entry.result} ({entry_id}); "
                   f"{len(entry.analyst_predictions)} analyst view(s), "
                   f"{entry.contracts} contract(s), ${entry.dollar_risk:,.0f} at risk")
        return AgentResult(ok=True, summary=summary, payload=artefact,
                           artefacts=["journal"])

    def _build_entry(self, ctx: AgentContext, symbol: str,
                     callout: Dict[str, Any], predictions: List[Dict[str, Any]],
                     snap: Any) -> JournalEntry:
        decision = Decision.coerce(callout.get("decision"))
        ts = _parse_et(callout.get("timestamp_et")) or ctx.now()
        contracts = _i(callout.get("contracts"))
        risk_block = callout.get("risk_assessment") if isinstance(
            callout.get("risk_assessment"), dict) else {}

        # A callout carries its analysts' views; the published prediction
        # artefacts are the fallback when it does not.
        embedded = [_normalise_prediction(p, "")
                    for p in _list(callout.get("analyst_predictions"))
                    if isinstance(p, dict)]
        views = embedded or predictions

        entry_px = _fo(callout.get("entry"))
        if entry_px is None:
            zone = callout.get("entry_zone")
            if isinstance(zone, (list, tuple)) and len(zone) == 2:
                entry_px = _mean([_f(zone[0]), _f(zone[1])])

        # Regime, volatility and session are taken at the decision instant, not
        # at the latest bar. A callout journalled late - or replayed - is then
        # recorded with the conditions that actually applied when it was made,
        # and the row never contradicts its own timestamp.
        measured_regime, volatility = self._conditions_at(ctx, symbol, ts, snap)
        regime = _regime_of(callout.get("market_regime"), measured_regime)
        if regime is MarketRegime.UNKNOWN:
            regime = measured_regime
        session = classify_session(ts)

        timeframes = [int(t) for t in _NUMBER.findall(str(callout.get("timeframe") or ""))]
        if not timeframes:
            timeframes = list(getattr(snap, "timeframes", []) or
                              list(getattr(ctx.config, "timeframes", ()) or ()))

        # Indicators: named indicator evidence first; when the evidence chain is
        # not typed, every evidence name is better than an empty column.
        chain = callout.get("evidence_chain")
        indicators = _dedupe(_evidence_names(chain, ("indicator",))
                             + [n for v in views for n in v["evidence_names"]])
        if not indicators:
            indicators = _dedupe(_evidence_names(chain))

        confluences = _dedupe([c for v in views for c in v["supporting_confluences"]]
                              + [str(callout.get("reason_for_entry") or "")])

        # A LONG/SHORT decision that ends up with no contracts was stopped by
        # the risk layer: it was not taken, and the journal must say so.
        taken = decision.is_actionable and contracts > 0
        result = "OPEN" if taken else "NOT_TAKEN"
        not_taken_reason = str(callout.get("reason_to_avoid") or "")
        if not taken and not not_taken_reason:
            vetoes = [str(v) for v in _list(risk_block.get("vetoes"))]
            not_taken_reason = "; ".join(vetoes)

        existing_id = self._entry_id_for_callout(ctx, symbol,
                                                 str(callout.get("callout_id") or ""))
        entry = JournalEntry(
            date_et=to_et(ts).date().isoformat(),
            time_et=et_stamp_short(ts),
            symbol=symbol,
            direction=decision.as_direction,
            entry=entry_px,
            stop=_fo(callout.get("stop_loss")),
            targets=[_f(t) for t in _list(callout.get("targets"))],
            strategy=str(callout.get("strategy") or ""),
            strategy_group=str(callout.get("strategy_group") or ""),
            timeframes=timeframes,
            indicators=indicators,
            confluences=confluences,
            market_regime=regime,
            volatility_regime=volatility,
            session=session,
            news_environment=self._news_environment(callout),
            analyst_predictions=views,
            final_decision=decision,
            confidence=Confidence(callout.get("confidence")),
            contracts=contracts,
            dollar_risk=_f(callout.get("dollar_risk")),
            result=result,
            reward_risk_planned=_f(callout.get("expected_reward_risk")),
            what_invalidated="" if taken else not_taken_reason,
            callout_id=str(callout.get("callout_id") or ""),
        )
        if existing_id:
            # Re-recording the same callout updates its row rather than
            # creating a second, contradictory one.
            entry.entry_id = existing_id
        return entry

    def _conditions_at(self, ctx: AgentContext, symbol: str, ts: datetime,
                       snap: Any) -> Tuple[MarketRegime, VolatilityRegime]:
        """The regime and volatility in force at one instant, from the frame.

        The feature engine computes each bar's regime from data up to that bar,
        so reading it at the decision bar is look-ahead safe.
        """
        try:
            frame = ctx.frame(symbol)
            bars = frame.base.bars
            index = bisect.bisect_right([to_et(b.ts) for b in bars], to_et(ts)) - 1
            if index >= 0:
                reading = frame.regime_at(index)
                return (_regime_of(getattr(reading, "regime", None)),
                        _vol_of(getattr(reading, "volatility", None)))
        except (KeyError, ValueError, IndexError) as exc:     # noqa: BLE001
            self.log(f"could not read the regime at {ts} for {symbol}: {exc}")
        fallback = getattr(snap, "regime", None)
        return (_regime_of(getattr(fallback, "regime", None)),
                _vol_of(getattr(fallback, "volatility", None)))

    def _news_environment(self, callout: Dict[str, Any]) -> str:
        bits = [f"risk {str(callout.get('news_risk') or 'NONE').upper()}"]
        news = self._safe_read(Role.NEWS_MACRO, "news_context")
        if isinstance(news, dict):
            headline = str(news.get("headline_summary") or "").strip()
            if headline:
                bits.append(headline)
            minutes = _fo(news.get("minutes_to_next_high_impact"))
            if minutes is not None:
                bits.append(f"next high-impact event in {minutes:.0f}m")
        return " | ".join(bits)

    # ==================================================================
    # resolve_trade
    # ==================================================================
    def _resolve_trade(self, ctx: AgentContext, symbol: str,
                       payload: Dict[str, Any]) -> AgentResult:
        entry_id = str(payload.get("entry_id") or "").strip()
        row = self._find_row(ctx, symbol, entry_id)
        if row is None:
            if entry_id:
                raise ValueError(f"journal entry '{entry_id}' not found for {symbol}")
            artefact = {
                "timestamp_et": to_et(ctx.now()).isoformat(),
                "symbol": symbol,
                "resolved": False,
                "reason": f"no unresolved journal entry for {symbol}",
                "source": "deterministic",
            }
            self.publish("journal", artefact, f"nothing to resolve for {symbol}")
            return AgentResult(
                ok=True, payload=artefact, artefacts=["journal"],
                summary=f"[{et_stamp_short(ctx.now())}] no unresolved {symbol} entry")

        outcome = self._measure(ctx, symbol, row, payload)
        entry_id = str(row["entry_id"])

        updated = ctx.storage.resolve_journal(
            entry_id,
            result=outcome["result"], exit_price=_f(outcome["exit_price"]),
            exit_reason=outcome["exit_reason"], profit_loss=outcome["profit_loss"],
            realised_r=outcome["realised_r"], mfe_r=outcome["mfe_r"],
            mae_r=outcome["mae_r"], thesis_correct=outcome["thesis_correct"],
            what_invalidated=outcome["what_invalidated"],
            what_happened_after=outcome["what_happened_after"],
            exit_time_et=outcome["exit_time_et"])
        if not updated:
            raise RuntimeError(f"journal entry '{entry_id}' vanished before resolution")

        # ``resolve_journal`` does not carry the point-denominated excursions or
        # the lesson text, so the completed entry is written back in full. Both
        # writes key on entry_id, so this updates the same row.
        entry = _entry_from_row(row)
        for field, value in (("result", outcome["result"]),
                             ("exit_price", outcome["exit_price"]),
                             ("exit_time_et", outcome["exit_time_et"]),
                             ("exit_reason", outcome["exit_reason"]),
                             ("mfe_points", outcome["mfe_points"]),
                             ("mae_points", outcome["mae_points"]),
                             ("mfe_r", outcome["mfe_r"]),
                             ("mae_r", outcome["mae_r"]),
                             ("profit_loss", outcome["profit_loss"]),
                             ("realised_r", outcome["realised_r"]),
                             ("thesis_correct", outcome["thesis_correct"]),
                             ("what_invalidated", outcome["what_invalidated"]),
                             ("what_happened_after", outcome["what_happened_after"]),
                             ("lessons", outcome["lessons"])):
            setattr(entry, field, value)
        ctx.storage.record_journal(entry)

        scored = self._score_analysts(ctx, symbol, row, outcome)

        rows = ctx.storage.journal_entries(symbol=symbol, limit=500)
        artefact = {
            "timestamp_et": to_et(ctx.now()).isoformat(),
            "symbol": symbol,
            "resolved": True,
            "entry_id": entry_id,
            "entry": entry.to_dict(),
            "measurement": outcome["measurement"],
            "analysts_scored": scored,
            "totals": self._totals(rows),
            "source": "deterministic",
        }
        self.publish("journal", artefact,
                     f"{symbol} {entry_id} resolved {outcome['result']} "
                     f"{outcome['realised_r']:+.2f}R")
        summary = (f"[{et_stamp_short(ctx.now())}] resolved {symbol} {entry_id}: "
                   f"{outcome['result']} via {outcome['exit_reason']}, "
                   f"{outcome['realised_r']:+.2f}R "
                   f"(MFE {outcome['mfe_r']:+.2f}R / MAE {outcome['mae_r']:+.2f}R), "
                   f"${outcome['profit_loss']:+,.0f}; scored {len(scored)} analyst(s)")
        return AgentResult(ok=True, summary=summary, payload=artefact,
                           artefacts=["journal"])

    # ---- measurement ---------------------------------------------------
    def _measure(self, ctx: AgentContext, symbol: str, row: Dict[str, Any],
                 payload: Dict[str, Any]) -> Dict[str, Any]:
        """Fill in every outcome field, from bars where the caller supplies no
        fills and from the caller's fills where it does."""
        spec = get_contract(symbol)
        costs = CostModel(spec)
        direction = Direction.coerce(row.get("direction"))
        entry_px = _fo(row.get("entry"))
        stop = _fo(row.get("stop"))
        targets = [_f(t) for t in _list(row.get("targets"))]
        contracts = _i(row.get("contracts"))
        horizon = _i(payload.get("horizon_minutes"), DEFAULT_HORIZON_MINUTES)
        after_minutes = _i(payload.get("after_minutes"), AFTER_WINDOW_MINUTES)
        was_taken = str(row.get("result") or "").upper() != "NOT_TAKEN"

        start = _entry_time(row) or ctx.now()
        bars = self._bars_from(ctx, symbol, start, horizon + after_minutes)
        measurement: Dict[str, Any] = {
            "measured_from_bars": bool(bars),
            "bars_available": len(bars),
            "horizon_minutes": horizon,
            "after_window_minutes": after_minutes,
            "start_et": to_et(start).isoformat(),
            "intrabar_convention": (
                "stop before target in the same bar; gaps fill at the open; "
                "excursions include the whole exit bar - the backtester's own "
                "conventions, so an MAE above 1R on a stop exit is the bar's "
                "full range, not a stop that failed to fill"),
            "costs": "round-turn commission and fees, charged in R exactly as the backtester does",
        }

        if not was_taken:
            return self._measure_not_taken(row, bars, horizon, spec, costs,
                                           direction, entry_px, stop, targets,
                                           measurement)

        if entry_px is None or stop is None or entry_px == stop:
            # Without an entry and a stop there is no R to measure against. The
            # row is closed honestly rather than with an invented number.
            return {
                "result": str(payload.get("result") or "SCRATCH").upper(),
                "exit_price": _f(payload.get("exit_price"), _f(entry_px)),
                "exit_time_et": str(payload.get("exit_time_et") or to_et(ctx.now()).isoformat()),
                "exit_reason": str(payload.get("exit_reason") or "MANUAL").upper(),
                "mfe_points": 0.0, "mae_points": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
                "profit_loss": _f(payload.get("profit_loss")),
                "realised_r": _f(payload.get("realised_r")),
                "thesis_correct": None,
                "what_invalidated": str(row.get("what_invalidated") or ""),
                "what_happened_after": "no entry/stop recorded, so no R could be measured",
                "lessons": "entry and stop must both be recorded for an outcome to be measurable",
                "measurement": {**measurement, "measured_from_bars": False,
                                "note": "missing entry or stop"},
            }

        sign = direction.sign or 1
        risk_points = abs(entry_px - stop)
        horizon_bars = [b for b in bars
                        if to_et(b.ts) < to_et(start) + timedelta(minutes=horizon)]
        walk = _walk(horizon_bars, entry=entry_px, stop=stop,
                     targets=targets, sign=sign)

        exit_price = _fo(payload.get("exit_price"))
        exit_reason = str(payload.get("exit_reason") or "").upper()
        supplied_ts = _parse_et(payload.get("exit_time_et"))
        exit_ts = supplied_ts
        exit_index = walk["exit_index"]
        if exit_price is None:
            exit_price = walk["exit_price"]
            exit_reason = exit_reason or walk["exit_reason"]
            exit_ts = exit_ts or walk["exit_ts"]
        if exit_price is None:                       # no bars at all
            exit_price = entry_px
            exit_reason = exit_reason or "NO_DATA"
        exit_reason = exit_reason or "TIME"

        mfe_points = max(walk["mfe_points"], 0.0)
        mae_points = max(walk["mae_points"], 0.0)
        measured_to = "the exit the bars imply"
        if supplied_ts is not None:
            # A real fill time was reported, so the excursions are measured over
            # the period the position was actually held rather than over the one
            # the bar walk inferred.
            held = [b for b in horizon_bars if to_et(b.ts) <= to_et(supplied_ts)]
            if held:
                mfe_points, mae_points = _excursions(held, entry_px, sign)
                exit_index = len(held) - 1
                measured_to = "the reported fill time"
        gross_r = sign * (exit_price - entry_px) / risk_points
        cost_r = costs.cost_in_r(risk_points)
        realised_r = _f(payload.get("realised_r"), gross_r - cost_r)
        risk_dollars = risk_points * spec.point_value
        profit_loss = _f(payload.get("profit_loss"),
                         realised_r * risk_dollars * max(contracts, 0))

        result = str(payload.get("result") or "").upper() or _classify(realised_r, cost_r)
        mfe_r = mfe_points / risk_points
        mae_r = mae_points / risk_points

        # "Was the thesis right" is not the same question as "did the trade
        # win". A trade that ran a full R in its favour before being stopped had
        # a correct read and a management problem, and the two must not be
        # conflated or the analysts get blamed for the exit rules.
        thesis_correct = bool(realised_r > 0 or mfe_r >= 1.0)

        after = _after_window(bars, exit_index, exit_price, sign,
                              after_minutes, risk_points)
        what_invalidated = ""
        if realised_r <= 0:
            if exit_reason == "STOP":
                what_invalidated = (f"stop at {stop:g} traded through; best excursion "
                                    f"was only {mfe_r:+.2f}R")
            elif exit_reason == "TIME":
                what_invalidated = (f"no target reached within {horizon}m; closed at "
                                    f"{exit_price:g} having reached {mfe_r:+.2f}R")
            else:
                what_invalidated = f"exited {exit_reason} at {exit_price:g}"

        lessons = ""
        if realised_r <= 0 and mfe_r >= 1.0:
            lessons = (f"the read was correct - {mfe_r:+.2f}R of open profit was "
                       f"given back; this is an exit-management loss, not a bad call")
        elif realised_r > 0 and mae_r >= 0.8:
            lessons = (f"won, but drew down {mae_r:.2f}R first - the stop was nearly "
                       "hit, so the entry timing was marginal")

        return {
            "result": result,
            "exit_price": exit_price,
            "exit_time_et": to_et(exit_ts).isoformat() if exit_ts else to_et(ctx.now()).isoformat(),
            "exit_reason": exit_reason,
            "mfe_points": round(mfe_points, 4),
            "mae_points": round(mae_points, 4),
            "mfe_r": round(mfe_r, 4),
            "mae_r": round(mae_r, 4),
            "profit_loss": round(profit_loss, 2),
            "realised_r": round(realised_r, 4),
            "thesis_correct": thesis_correct,
            "what_invalidated": what_invalidated,
            "what_happened_after": after["text"],
            "lessons": lessons,
            "measurement": {**measurement, "risk_points": round(risk_points, 4),
                            "gross_r": round(gross_r, 4), "cost_r": round(cost_r, 4),
                            "bars_in_horizon": len(horizon_bars),
                            "excursions_measured_to": measured_to,
                            "exit_supplied_by_caller": _fo(payload.get("exit_price")) is not None,
                            "exit_the_bars_imply": _round(walk["exit_price"]),
                            "exit_reason_the_bars_imply": walk["exit_reason"],
                            "after": after["detail"]},
        }

    def _measure_not_taken(self, row: Dict[str, Any], bars: List[Bar], horizon: int,
                           spec: Any, costs: CostModel, direction: Direction,
                           entry_px: Optional[float], stop: Optional[float],
                           targets: Sequence[float],
                           measurement: Dict[str, Any]) -> Dict[str, Any]:
        """What happened after a decision not to trade.

        The shadow outcome is recorded in R where a level and a stop existed, so
        the system can see what it correctly avoided and what it wrongly passed
        on. ``realised_r`` and ``profit_loss`` stay zero: no money moved, and a
        hypothetical result must never be summed into live performance.
        """
        lean = direction
        if lean is Direction.NEUTRAL:
            votes = [Direction.coerce(v.get("direction"))
                     for v in _list(row.get("analyst_predictions"))
                     if isinstance(v, dict)]
            longs = sum(1 for v in votes if v is Direction.LONG)
            shorts = sum(1 for v in votes if v is Direction.SHORT)
            if longs > shorts:
                lean = Direction.LONG
            elif shorts > longs:
                lean = Direction.SHORT

        if not bars:
            return {
                "result": "NOT_TAKEN", "exit_price": _f(entry_px),
                "exit_time_et": "", "exit_reason": "NOT_TAKEN",
                "mfe_points": 0.0, "mae_points": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
                "profit_loss": 0.0, "realised_r": 0.0, "thesis_correct": None,
                "what_invalidated": str(row.get("what_invalidated") or ""),
                "what_happened_after": "no bars after the decision - nothing measurable yet",
                "lessons": "",
                "measurement": {**measurement, "measured_from_bars": False,
                                "lean": lean.value},
            }

        window = [b for b in bars
                  if to_et(b.ts) < to_et(bars[0].ts) + timedelta(minutes=horizon)]
        reference = entry_px if entry_px is not None else window[0].open
        up = max(b.high for b in window) - reference
        down = reference - min(b.low for b in window)
        drift = window[-1].close - reference

        shadow_r: Optional[float] = None
        shadow_reason = ""
        mfe_r = mae_r = 0.0
        mfe_points = max(up, 0.0) if lean is not Direction.SHORT else max(down, 0.0)
        mae_points = max(down, 0.0) if lean is not Direction.SHORT else max(up, 0.0)
        if stop is not None and entry_px is not None and lean.sign and entry_px != stop:
            sign = lean.sign
            risk_points = abs(entry_px - stop)
            walk = _walk(window, entry=entry_px, stop=stop, targets=list(targets),
                         sign=sign)
            exit_price = walk["exit_price"] if walk["exit_price"] is not None else window[-1].close
            shadow_reason = walk["exit_reason"] or "TIME"
            shadow_r = (sign * (exit_price - entry_px) / risk_points
                        - costs.cost_in_r(risk_points))
            mfe_points, mae_points = walk["mfe_points"], walk["mae_points"]
            mfe_r, mae_r = mfe_points / risk_points, mae_points / risk_points

        parts = [f"NOT TAKEN. Over the {horizon}m after the decision "
                 f"{row.get('symbol')} traded {max(up, 0.0):.2f} points above and "
                 f"{max(down, 0.0):.2f} points below {reference:g}, closing "
                 f"{drift:+.2f} points from it."]
        if shadow_r is not None:
            parts.append(f"Shadow outcome had it been taken: {shadow_r:+.2f}R via "
                         f"{shadow_reason} (MFE {mfe_r:+.2f}R, MAE {mae_r:+.2f}R).")
            parts.append("Avoiding it was correct." if shadow_r <= 0
                         else "This one was wrongly passed on.")
        else:
            parts.append("No entry and stop were published, so no shadow R is "
                         "computable - only the drift above is measured.")

        return {
            "result": "NOT_TAKEN",
            "exit_price": _f(reference),
            "exit_time_et": to_et(window[-1].end_ts).isoformat(),
            "exit_reason": "NOT_TAKEN",
            "mfe_points": round(max(mfe_points, 0.0), 4),
            "mae_points": round(max(mae_points, 0.0), 4),
            "mfe_r": round(mfe_r, 4),
            "mae_r": round(mae_r, 4),
            "profit_loss": 0.0,
            "realised_r": 0.0,
            "thesis_correct": None if shadow_r is None else bool(shadow_r <= 0),
            "what_invalidated": str(row.get("what_invalidated") or ""),
            "what_happened_after": " ".join(parts),
            "lessons": ("passed on a setup that would have paid"
                        if shadow_r is not None and shadow_r > 0 else ""),
            "measurement": {**measurement, "lean": lean.value,
                            "reference_price": round(reference, 4),
                            "shadow_r": _round(shadow_r),
                            "shadow_exit_reason": shadow_reason,
                            "bars_in_horizon": len(window)},
        }

    def _score_analysts(self, ctx: AgentContext, symbol: str, row: Dict[str, Any],
                        outcome: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Credit or debit every analyst that had a view on a *taken* trade.

        Hypothetical trades are deliberately not scored: counting counterfactual
        R would let an analyst accumulate a "measured" track record out of
        trades that never happened.
        """
        if outcome["result"] not in RESOLVED_RESULTS:
            return []
        realised_r = _f(outcome["realised_r"])
        traded = Direction.coerce(row.get("direction"))
        regime = str(row.get("market_regime") or "") or "ALL"
        if regime not in _REGIMES or regime == MarketRegime.UNKNOWN.value:
            regime = "ALL"
        session = str(row.get("session") or "") or "ALL"

        scored: List[Dict[str, Any]] = []
        for view in _list(row.get("analyst_predictions")):
            if not isinstance(view, dict):
                continue
            agent = view.get("agent_id") or _agent_id(view.get("analyst_id"))
            if not agent:
                continue
            attribution = _attribute(view, traded, realised_r)
            ctx.storage.update_agent_score(
                agent, symbol, correct=attribution["correct"],
                realised_r=attribution["r"], regime=regime, session=session,
                false_signal=attribution["false_signal"])
            scored.append({"agent_id": agent, "regime": regime, "session": session,
                           **attribution})
        return scored

    # ==================================================================
    # evaluate_agents
    # ==================================================================
    def _evaluate_agents(self, ctx: AgentContext, symbol: str,
                         payload: Dict[str, Any]) -> AgentResult:
        """Per-analyst, per-regime scorecard judged on contribution, not hit rate."""
        rows = list(reversed(ctx.storage.journal_entries(
            symbol=symbol, limit=_i(payload.get("limit"), 1000))))
        attributions: Dict[str, List[Dict[str, Any]]] = {}
        passed_on: Dict[str, Dict[str, int]] = {}

        for row in rows:
            result = str(row.get("result") or "").upper()
            traded = Direction.coerce(row.get("direction"))
            realised_r = _f(row.get("realised_r"))
            mfe_r = _f(row.get("mfe_r"))
            context = {
                "regime": str(row.get("market_regime") or "UNKNOWN"),
                "session": str(row.get("session") or "UNKNOWN"),
                "volatility": str(row.get("volatility_regime") or "UNKNOWN"),
                "date_et": str(row.get("date_et") or ""),
            }
            for view in _list(row.get("analyst_predictions")):
                if not isinstance(view, dict):
                    continue
                agent = view.get("agent_id") or _agent_id(view.get("analyst_id"))
                if not agent:
                    continue
                direction = Direction.coerce(view.get("direction"))
                if result in RESOLVED_RESULTS:
                    attribution = _attribute(view, traded, realised_r)
                    attributions.setdefault(agent, []).append({
                        **attribution, **context,
                        # An "unnecessary trade" is one the analyst pushed for
                        # that never worked even briefly - not merely one that
                        # lost after running in its favour.
                        "unnecessary": bool(direction is traded and direction.sign
                                            and realised_r <= 0 and mfe_r < 1.0),
                    })
                elif result == "NOT_TAKEN" and direction.sign:
                    bucket = passed_on.setdefault(
                        agent, {"advocated": 0, "vindicated": 0, "wrongly_passed": 0})
                    bucket["advocated"] += 1
                    correct_to_avoid = row.get("thesis_correct")
                    if correct_to_avoid is not None:
                        key = "vindicated" if _i(correct_to_avoid) else "wrongly_passed"
                        bucket[key] += 1

        analysts = []
        for agent in sorted(set(attributions) | set(passed_on)):
            atts = attributions.get(agent, [])
            overall = _finalise(atts)
            analysts.append({
                "agent_id": agent,
                "overall": overall,
                "by_regime": _slice_metrics(atts, "regime"),
                "by_session": _slice_metrics(atts, "session"),
                "by_volatility": _slice_metrics(atts, "volatility"),
                "not_taken": passed_on.get(agent, {"advocated": 0, "vindicated": 0,
                                                   "wrongly_passed": 0}),
                "verdict": _verdict(overall),
                "notes": _agent_notes(overall, passed_on.get(agent)),
            })
        # Ranked by measured contribution. An analyst is not promoted for being
        # right often if what it is right about does not pay.
        analysts.sort(key=lambda a: (a["overall"]["sample_sufficient"],
                                     a["overall"]["avg_r"]), reverse=True)

        stored = ctx.storage.agent_scores(symbol=symbol)
        warnings = _scorecard_warnings(analysts, stored, rows)
        artefact = {
            "timestamp_et": to_et(ctx.now()).isoformat(),
            "symbol": symbol,
            "journal_rows": len(rows),
            "resolved_trades": sum(1 for r in rows
                                   if str(r.get("result") or "").upper() in RESOLVED_RESULTS),
            "not_taken": sum(1 for r in rows
                             if str(r.get("result") or "").upper() == "NOT_TAKEN"),
            "minimum_sample": MIN_AGENT_SAMPLE,
            "ranked_by": "avg_r - risk-adjusted contribution per prediction, not hit rate",
            "measures": {
                "accuracy": "share of that analyst's views the outcome vindicated",
                "avg_r": "mean R attributed to the analyst: +R when it backed the "
                         "traded direction, -R when it opposed it, 0 when it stood aside",
                "false_signals": "directional calls that did not pay",
                "unnecessary_trades": "advocated trades that lost without ever "
                                      "reaching +1R in their favour",
                "worst_losing_streak": "longest run of consecutive wrong views",
            },
            "analysts": analysts,
            "stored_agent_scores": stored,
            "warnings": warnings,
            "source": "deterministic",
        }
        self.publish("agent_scorecard", artefact,
                     f"{len(analysts)} analyst(s) scored on {artefact['resolved_trades']} "
                     f"resolved {symbol} trade(s)")

        if not analysts:
            summary = (f"[{et_stamp_short(ctx.now())}] no analyst predictions have "
                       f"resolved for {symbol} yet - nothing measurable to judge "
                       f"({len(rows)} journal row(s) on file)")
        else:
            lead = analysts[0]
            summary = (f"[{et_stamp_short(ctx.now())}] scored {len(analysts)} analyst(s) "
                       f"on {artefact['resolved_trades']} resolved {symbol} trade(s); "
                       f"best contribution {lead['agent_id']} "
                       f"{lead['overall']['avg_r']:+.3f}R/prediction over "
                       f"{lead['overall']['predictions']} ({lead['verdict']})")
        return AgentResult(ok=True, summary=summary, payload=artefact,
                           artefacts=["agent_scorecard"])

    # ==================================================================
    # learn
    # ==================================================================
    def _learn(self, ctx: AgentContext, symbol: str,
               payload: Dict[str, Any]) -> AgentResult:
        """Compare live results against backtested expectation and feed it back."""
        rows = list(reversed(ctx.storage.journal_entries(
            symbol=symbol, limit=_i(payload.get("limit"), 1000))))
        resolved = [r for r in rows
                    if str(r.get("result") or "").upper() in RESOLVED_RESULTS]
        strategies = _dedupe([str(r.get("strategy") or "") for r in rows])

        comparisons: List[Dict[str, Any]] = []
        weights: Dict[str, float] = {}
        flagged: List[str] = []
        for strategy in strategies:
            comparison = self._compare(ctx, symbol, strategy, resolved)
            comparisons.append(comparison)
            weights[strategy] = comparison["influence_weight"]
            if comparison["flagged_for_research"]:
                flagged.append(strategy)

        findings = _conditional_findings(symbol, resolved)
        narrated = self._phrase(findings)

        notes: List[str] = []
        if not resolved:
            notes.append(f"no resolved {symbol} trades on file - no weight adjusted "
                         "and no finding is measurable yet")
        under = [c["strategy"] for c in comparisons
                 if c["live"]["trades"] < MIN_LIVE_TRADES]
        if under:
            notes.append(f"{len(under)} strategy(ies) below the {MIN_LIVE_TRADES}-trade "
                         "minimum: weight left at 1.00 regardless of how the recent "
                         "trades went")
        missing_backtest = [c["strategy"] for c in comparisons if c["backtest"] is None]
        if missing_backtest:
            notes.append("no backtest row on file for " + ", ".join(missing_backtest[:5])
                         + " - live results cannot be compared against expectation "
                         "until strategy research publishes one")

        artefact = {
            "timestamp_et": to_et(ctx.now()).isoformat(),
            "symbol": symbol,
            "policy": {
                "minimum_live_trades": MIN_LIVE_TRADES,
                "minimum_slice_sample": MIN_SLICE_SAMPLE,
                "weight_cap": WEIGHT_CAP,
                "weight_floor": WEIGHT_FLOOR,
                "rule": "weights are reduced when live expectancy decays against the "
                        "backtest and are never raised above 1.00 - a winning streak "
                        "is not evidence, and acting on it is how a system ends up "
                        "overtrading its most recent luck",
            },
            "influence_weights": {k: round(v, 3) for k, v in weights.items()},
            "flagged_for_research": flagged,
            "strategies": comparisons,
            "findings": findings,
            "live_totals": ctx.storage.journal_stats(symbol=symbol),
            "notes": notes,
            "source": "hybrid" if narrated else "deterministic",
        }
        self.publish("journal_feedback", artefact,
                     f"{len(comparisons)} strategy(ies) weighted, {len(flagged)} flagged, "
                     f"{len(findings)} finding(s)")
        summary = (f"[{et_stamp_short(ctx.now())}] learn {symbol}: {len(resolved)} "
                   f"resolved trade(s) across {len(strategies)} strategy(ies); "
                   f"{len(flagged)} flagged for re-research, {len(findings)} conditional "
                   f"finding(s); no weight raised above {WEIGHT_CAP:.2f}")
        if not resolved:
            summary += " - nothing measurable yet, weights untouched"
        return AgentResult(ok=True, summary=summary, payload=artefact,
                           artefacts=["journal_feedback"])

    def _compare(self, ctx: AgentContext, symbol: str, strategy: str,
                 resolved: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        live = ctx.storage.journal_stats(symbol=symbol, strategy=strategy)
        sequence = [_f(r.get("realised_r")) for r in resolved
                    if str(r.get("strategy") or "") == strategy]
        backtest = self._backtest_row(ctx, symbol, strategy)
        trades = _i(live.get("trades"))
        live_expectancy = _f(live.get("avg_r"))
        bt_expectancy = _f(backtest.get("expectancy_r")) if backtest else 0.0

        weight = WEIGHT_CAP
        status = "BASELINE"
        if trades < MIN_LIVE_TRADES:
            status = "INSUFFICIENT_LIVE_SAMPLE"
        elif backtest and bt_expectancy > 0:
            ratio = live_expectancy / bt_expectancy
            weight = max(WEIGHT_FLOOR, min(WEIGHT_CAP, ratio))
            status = ("DECAYED" if ratio < 0.8 else "TRACKING_BACKTEST")
        elif live_expectancy <= 0:
            weight = 0.5
            status = "NEGATIVE_LIVE_EXPECTANCY"

        flagged = trades >= MIN_LIVE_TRADES and (weight <= 0.6 or live_expectancy <= 0)
        stats = StrategyStats(
            strategy_id=strategy, symbol=symbol,
            live_trades=trades, live_wins=_i(live.get("wins")),
            live_expectancy_r=round(live_expectancy, 4),
            live_profit_factor=round(_profit_factor(sequence) or 0.0, 4),
            live_max_drawdown_r=round(_max_drawdown_r(sequence), 4),
            current_consecutive_losses=_trailing_losses(sequence),
            influence_weight=round(weight, 3),
            flagged_for_research=flagged,
        )
        divergence = (round(live_expectancy - bt_expectancy, 4)
                      if backtest else None)
        return {
            "strategy": strategy,
            "live": {**{k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in live.items()},
                     "max_drawdown_r": stats.live_max_drawdown_r,
                     "profit_factor": stats.live_profit_factor,
                     "consecutive_losses": stats.current_consecutive_losses},
            "backtest": ({"trades": _i(backtest.get("trades")),
                          "expectancy_r": round(bt_expectancy, 4),
                          "win_rate": round(_f(backtest.get("win_rate")), 4),
                          "profit_factor": round(_f(backtest.get("profit_factor")), 4),
                          "oos_expectancy_r": round(_f(backtest.get("oos_expectancy_r")), 4),
                          "robustness_score": round(_f(backtest.get("robustness_score")), 4)}
                         if backtest else None),
            "divergence_r": divergence,
            "influence_weight": stats.influence_weight,
            "flagged_for_research": flagged,
            "status": status,
            "reason": _weight_reason(status, trades, live_expectancy, bt_expectancy,
                                     stats.influence_weight),
            "stats": stats.to_dict(),
        }

    def _backtest_row(self, ctx: AgentContext, symbol: str,
                      strategy: str) -> Optional[Dict[str, Any]]:
        row = ctx.storage.strategy_performance(strategy)
        if row:
            return row
        # The journal stores whatever name the callout used; the performance
        # table is keyed by strategy id. Match case-insensitively before giving
        # up, rather than reporting "no backtest" for a spelling difference.
        target = strategy.strip().lower()
        for candidate in ctx.storage.top_strategies(symbol, limit=100,
                                                    live_eligible_only=False):
            if str(candidate.get("strategy_id") or "").strip().lower() == target:
                return candidate
        return None

    # ---- narrative -----------------------------------------------------
    def _phrase(self, findings: List[Dict[str, Any]]) -> bool:
        """Let the model word the findings. It may not change any of them.

        Every figure in a phrasing must already appear in the computed finding;
        anything else is discarded and the deterministic sentence stands alone.
        """
        if not findings or not self.llm_available:
            return False
        response = self.reason(
            system=self.system_prompt(self.NARRATIVE_ROLE),
            evidence={"findings": [{"id": f["id"], "computed": f["text"],
                                    "sample": f["sample"],
                                    "provisional": not f["actionable"]}
                                   for f in findings]},
            question=("Restate each finding as one clear sentence, using only the "
                      "figures given. Return {\"findings\": [{\"id\": ..., "
                      "\"sentence\": ...}]}."),
            schema={"type": "object", "properties": {"findings": {"type": "array"}},
                    "required": ["findings"]})
        if response is None or not response.ok or not isinstance(response.parsed, dict):
            return False
        by_id = {f["id"]: f for f in findings}
        applied = False
        for item in response.parsed.get("findings") or []:
            if not isinstance(item, dict):
                continue
            finding = by_id.get(item.get("id"))
            sentence = str(item.get("sentence") or "").strip()
            if not finding or not sentence:
                continue
            if not _numbers(sentence) <= _numbers(finding["text"]):
                self.log(f"discarded a phrasing for {finding['id']}: it introduced "
                         "a number the measurement did not produce")
                continue
            finding["narrative"] = sentence
            applied = True
        return applied

    # ---- artefact and row plumbing -------------------------------------
    def _safe_read(self, owner: Role, artefact: str) -> Any:
        try:
            return self.read_from(owner, artefact)
        except (PermissionError, OSError) as exc:      # noqa: BLE001
            self.log(f"could not read {owner.value}/{artefact}: {exc}")
            return None

    def _read_callout(self, symbol: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """The final callout. Risk publishes the last word on it; decision is
        the fallback when the risk layer has not republished it."""
        for owner, name in ((Role.RISK, "callout"), (Role.DECISION, "callout"),
                            (Role.DECISION, "decision")):
            callout = _extract_callout(self._safe_read(owner, name))
            if callout is None:
                continue
            found = str(callout.get("symbol") or "").upper()
            if found and found != symbol:
                self.log(f"ignored {owner.value}/{name}: it is for {found}, not {symbol}")
                continue
            return callout, f"{owner.value}/{name}"
        return None, ""

    def _read_predictions(self, symbol: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for role, artefact, ident in ((Role.ANALYST_A, "prediction_a", "A"),
                                      (Role.ANALYST_B, "prediction_b", "B"),
                                      (Role.ANALYST_C, "prediction_c", "C")):
            for raw in _extract_predictions(self._safe_read(role, artefact)):
                found = str(raw.get("symbol") or "").upper()
                if found and found != symbol:
                    continue
                out.append(_normalise_prediction(raw, ident))
        return out

    def _entry_id_for_callout(self, ctx: AgentContext, symbol: str,
                              callout_id: str) -> str:
        if not callout_id:
            return ""
        for row in ctx.storage.journal_entries(symbol=symbol, limit=200):
            if str(row.get("callout_id") or "") == callout_id:
                return str(row["entry_id"])
        return ""

    def _find_row(self, ctx: AgentContext, symbol: str,
                  entry_id: str) -> Optional[Dict[str, Any]]:
        rows = ctx.storage.journal_entries(symbol=symbol, limit=500)
        if entry_id:
            return next((r for r in rows if str(r.get("entry_id")) == entry_id), None)
        # Newest first: resolve the most recent open trade, then the most recent
        # unexamined NO TRADE, so both halves of the record stay current.
        for wanted in ("OPEN", "NOT_TAKEN"):
            for row in rows:
                if str(row.get("result") or "").upper() != wanted:
                    continue
                if wanted == "NOT_TAKEN" and str(row.get("what_happened_after") or ""):
                    continue
                return row
        return None

    def _bars_from(self, ctx: AgentContext, symbol: str, start: datetime,
                   minutes: int) -> List[Bar]:
        """Completed base bars in ``[start, start + minutes)``.

        Bars beyond ``ctx.as_of`` are excluded: a replay must resolve a trade
        with what was knowable then, not with the rest of the file.
        """
        try:
            series = ctx.frame(symbol).base
        except (KeyError, ValueError) as exc:          # noqa: BLE001
            self.log(f"no bar data for {symbol}: {exc}")
            return []
        if len(series) == 0:
            return []
        start_et = to_et(start)
        end_et = start_et + timedelta(minutes=max(minutes, 0))
        limit = to_et(ctx.as_of) if ctx.as_of is not None else None
        index = bisect.bisect_left([to_et(b.ts) for b in series.bars], start_et)
        out: List[Bar] = []
        for bar in series.bars[index:]:
            if to_et(bar.ts) >= end_et:
                break
            if not bar.complete:
                break
            if limit is not None and to_et(bar.end_ts) > limit:
                break
            out.append(bar)
        return out

    @staticmethod
    def _totals(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        results = [str(r.get("result") or "").upper() for r in rows]
        return {
            "entries": len(rows),
            "open": results.count("OPEN"),
            "not_taken": results.count("NOT_TAKEN"),
            "resolved": sum(1 for r in results if r in RESOLVED_RESULTS),
            "wins": results.count("WIN"),
            "losses": results.count("LOSS"),
        }


# --------------------------------------------------------------------------
# Module-level measurement helpers
# --------------------------------------------------------------------------

def _entry_time(row: Dict[str, Any]) -> Optional[datetime]:
    """Reconstruct the decision instant from the journal's date and ET stamp."""
    date_s = str(row.get("date_et") or "").strip()
    time_s = str(row.get("time_et") or "").strip().split(" ")[0] or "00:00:00"
    if not date_s:
        return None
    return _parse_et(f"{date_s}T{time_s}")


def _walk(bars: Sequence[Bar], *, entry: float, stop: Optional[float],
          targets: Sequence[float], sign: int) -> Dict[str, Any]:
    """Track excursions and find the exit, using the backtester's conventions.

    The stop is checked before the targets so that a bar containing both is read
    as the stop filling first - without tick data the order is unknowable, and
    the pessimistic reading is the only honest one. Gaps fill at the open.
    """
    mfe = mae = 0.0
    target = next((t for t in targets if t is not None), None)
    for i, bar in enumerate(bars):
        favourable = (bar.high - entry) if sign > 0 else (entry - bar.low)
        adverse = (entry - bar.low) if sign > 0 else (bar.high - entry)
        mfe = max(mfe, favourable)
        mae = max(mae, adverse)
        if stop is not None:
            hit = (bar.low <= stop) if sign > 0 else (bar.high >= stop)
            if hit:
                gapped = (bar.open <= stop) if sign > 0 else (bar.open >= stop)
                return {"mfe_points": mfe, "mae_points": mae,
                        "exit_price": bar.open if gapped else stop,
                        "exit_reason": "STOP", "exit_index": i,
                        "exit_ts": bar.end_ts}
        if target is not None:
            hit = (bar.high >= target) if sign > 0 else (bar.low <= target)
            if hit:
                gapped = (bar.open >= target) if sign > 0 else (bar.open <= target)
                return {"mfe_points": mfe, "mae_points": mae,
                        "exit_price": bar.open if gapped else target,
                        "exit_reason": "TARGET", "exit_index": i,
                        "exit_ts": bar.end_ts}
    if not bars:
        return {"mfe_points": 0.0, "mae_points": 0.0, "exit_price": None,
                "exit_reason": "", "exit_index": -1, "exit_ts": None}
    return {"mfe_points": mfe, "mae_points": mae, "exit_price": bars[-1].close,
            "exit_reason": "TIME", "exit_index": len(bars) - 1,
            "exit_ts": bars[-1].end_ts}


def _excursions(bars: Sequence[Bar], entry: float, sign: int) -> Tuple[float, float]:
    """Best and worst unrealised excursion over a known holding period."""
    mfe = mae = 0.0
    for bar in bars:
        mfe = max(mfe, (bar.high - entry) if sign > 0 else (entry - bar.low))
        mae = max(mae, (entry - bar.low) if sign > 0 else (bar.high - entry))
    return mfe, mae


def _after_window(bars: Sequence[Bar], exit_index: int, exit_price: float,
                  sign: int, minutes: int, risk_points: float) -> Dict[str, Any]:
    """What the market did after the exit - the half of a trade review that
    tells you whether the exit was early, late or right."""
    if exit_index < 0 or exit_index + 1 >= len(bars):
        return {"text": "no bars after the exit yet", "detail": {}}
    start = to_et(bars[exit_index].end_ts)
    window = [b for b in bars[exit_index + 1:]
              if to_et(b.ts) < start + timedelta(minutes=max(minutes, 0))]
    if not window:
        return {"text": "no bars after the exit yet", "detail": {}}
    further = max((b.high - exit_price) if sign > 0 else (exit_price - b.low)
                  for b in window)
    against = max((exit_price - b.low) if sign > 0 else (b.high - exit_price)
                  for b in window)
    close = window[-1].close
    drift = (close - exit_price) * sign
    detail = {"minutes": minutes, "bars": len(window),
              "continued_points": round(further, 4),
              "reversed_points": round(against, 4),
              "close": round(close, 4),
              "continued_r": round(further / risk_points, 4) if risk_points else None}
    text = (f"In the {minutes}m after the exit the market travelled "
            f"{max(further, 0.0):.2f} points further in the trade's direction and "
            f"{max(against, 0.0):.2f} points back against it, closing "
            f"{drift:+.2f} points from the exit (positive is the trade's direction).")
    if risk_points and further / risk_points >= 1.0:
        text += " The exit was early by a full R or more."
    return {"text": text, "detail": detail}


def _classify(realised_r: float, cost_r: float) -> str:
    """WIN / LOSS / BREAKEVEN / SCRATCH from the net R.

    A result inside the round-turn cost is a scratch, not a win: it paid the
    broker and nothing else.
    """
    band = max(cost_r, 0.02)
    if realised_r > band:
        return "WIN"
    if realised_r < -band:
        return "LOSS"
    return "BREAKEVEN" if abs(realised_r) <= 1e-9 else "SCRATCH"


def _attribute(view: Dict[str, Any], traded: Direction,
               realised_r: float) -> Dict[str, Any]:
    """Credit one analyst for one resolved trade.

    Backing the traded direction earns that trade's R; opposing it earns its
    negative (being right to disagree is worth something); standing aside earns
    nothing, because it risked nothing - but it is still marked correct when the
    trade did not pay.
    """
    direction = Direction.coerce(view.get("direction"))
    if direction is traded and direction.sign:
        stance, attributed, correct = "BACKED", realised_r, realised_r > 0
    elif direction.sign:
        stance, attributed, correct = "OPPOSED", -realised_r, realised_r < 0
    else:
        stance, attributed, correct = "STOOD_ASIDE", 0.0, realised_r <= 0
    return {
        "analyst_id": str(view.get("analyst_id") or ""),
        "direction": direction.value,
        "confidence": Confidence(view.get("confidence")),
        "stance": stance,
        "r": round(attributed, 4),
        "correct": bool(correct),
        "false_signal": bool(direction.sign and not correct),
        "directional": bool(direction.sign),
    }


def _finalise(attributions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(attributions)
    rs = [_f(a["r"]) for a in attributions]
    losses = [r for r in rs if r < 0]
    correct = sum(1 for a in attributions if a["correct"])
    return {
        "predictions": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "avg_r": round(_mean(rs), 4),
        "total_r": round(sum(rs), 4),
        "avg_loss_r": round(_mean(losses), 4),
        "worst_r": round(min(rs), 4) if rs else 0.0,
        "profit_factor": _round(_profit_factor(rs), 3),
        "false_signals": sum(1 for a in attributions if a["false_signal"]),
        "unnecessary_trades": sum(1 for a in attributions if a.get("unnecessary")),
        "worst_losing_streak": _worst_streak([bool(a["correct"]) for a in attributions]),
        "directional_calls": sum(1 for a in attributions if a["directional"]),
        "sample_sufficient": n >= MIN_AGENT_SAMPLE,
    }


def _slice_metrics(attributions: Sequence[Dict[str, Any]],
                   key: str) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for a in attributions:
        buckets.setdefault(str(a.get(key) or "UNKNOWN"), []).append(a)
    return {name: _finalise(items) for name, items in sorted(buckets.items())}


def _verdict(metrics: Dict[str, Any]) -> str:
    if not metrics["sample_sufficient"]:
        return f"INSUFFICIENT_SAMPLE ({metrics['predictions']}/{MIN_AGENT_SAMPLE})"
    avg_r, pf = metrics["avg_r"], metrics["profit_factor"]
    if avg_r >= 0.15 and (pf is None or pf >= 1.3):
        return "CONTRIBUTING"
    if avg_r <= -0.05:
        return "DETRACTING"
    return "NEUTRAL"


def _agent_notes(metrics: Dict[str, Any],
                 passed_on: Optional[Dict[str, int]]) -> List[str]:
    notes: List[str] = []
    if not metrics["sample_sufficient"]:
        notes.append(f"sample too small to judge: {metrics['predictions']} resolved "
                     f"prediction(s), {MIN_AGENT_SAMPLE} needed - nothing here is "
                     "evidence yet")
    if metrics["accuracy"] >= 0.6 and metrics["avg_r"] <= 0:
        notes.append(f"right {metrics['accuracy'] * 100:.0f}% of the time but "
                     f"contributing {metrics['avg_r']:+.3f}R per prediction: its "
                     "losers are bigger than its winners, so the hit rate flatters it")
    if metrics["predictions"] and metrics["directional_calls"] / metrics["predictions"] < 0.34:
        notes.append(f"only {metrics['directional_calls']} of {metrics['predictions']} "
                     "views were directional - it ranks where it does mostly by "
                     "abstaining, which costs nothing but earns nothing either")
    if metrics["worst_losing_streak"] >= 5:
        notes.append(f"worst losing streak {metrics['worst_losing_streak']} in a row")
    if metrics["unnecessary_trades"]:
        notes.append(f"{metrics['unnecessary_trades']} advocated trade(s) never "
                     "reached +1R before losing")
    if passed_on and passed_on.get("wrongly_passed"):
        notes.append(f"{passed_on['wrongly_passed']} setup(s) it called were declined "
                     "and would have paid")
    return notes


def _scorecard_warnings(analysts: Sequence[Dict[str, Any]],
                        stored: Sequence[Dict[str, Any]],
                        rows: Sequence[Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []
    if not rows:
        warnings.append("the journal is empty for this symbol")
    computed = {a["agent_id"]: a["overall"]["predictions"] for a in analysts}
    persisted: Dict[str, int] = {}
    for row in stored:
        persisted[str(row.get("agent_id"))] = (persisted.get(str(row.get("agent_id")), 0)
                                               + _i(row.get("predictions")))
    for agent, count in computed.items():
        kept = persisted.get(agent, 0)
        if count and kept != count:
            warnings.append(
                f"{agent}: {count} attribution(s) recomputed from the journal but "
                f"{kept} stored in agent_scores - the stored row was written by a "
                "different run or a resolution was replayed")
    if analysts and not any(a["overall"]["sample_sufficient"] for a in analysts):
        warnings.append(f"no analyst has reached {MIN_AGENT_SAMPLE} resolved "
                        "predictions - this scorecard describes, it does not judge")
    return warnings


# --------------------------------------------------------------------------
# Conditional findings
# --------------------------------------------------------------------------

def _news_bucket(text: str) -> str:
    upper = str(text or "").upper()
    for level in _NEWS_LEVELS:
        if level in upper:
            return f"{level} news risk"
    return "UNKNOWN"


def _conditional_findings(symbol: str,
                          resolved: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Where a strategy's results actually differ, and by how much.

    Slices are reported only when they hold a real sample and differ from the
    strategy's own baseline by a margin worth stating. The form is the one the
    specification asks for: performs well under X, poorly under Y.
    """
    findings: List[Dict[str, Any]] = []
    by_strategy: Dict[str, List[Dict[str, Any]]] = {}
    for row in resolved:
        by_strategy.setdefault(str(row.get("strategy") or "unattributed"), []).append(row)

    for strategy, rows in sorted(by_strategy.items()):
        if len(rows) < MIN_SLICE_SAMPLE:
            continue
        baseline = _mean([_f(r.get("realised_r")) for r in rows])
        label = _strategy_label(symbol, strategy, rows)
        for dimension, extract in (("session", lambda r: str(r.get("session") or "UNKNOWN")),
                                   ("regime", lambda r: str(r.get("market_regime") or "UNKNOWN")),
                                   ("volatility", lambda r: str(r.get("volatility_regime") or "UNKNOWN")),
                                   ("news", lambda r: _news_bucket(r.get("news_environment")))):
            buckets: Dict[str, List[float]] = {}
            for row in rows:
                buckets.setdefault(extract(row), []).append(_f(row.get("realised_r")))
            qualifying = {k: v for k, v in buckets.items() if len(v) >= MIN_SLICE_SAMPLE}
            if not qualifying:
                continue
            ranked = sorted(qualifying.items(), key=lambda kv: _mean(kv[1]), reverse=True)
            best_name, best = ranked[0]
            worst_name, worst = ranked[-1]
            if best_name != worst_name and _mean(best) - _mean(worst) >= MIN_SLICE_EDGE_R:
                findings.append(_finding(
                    strategy, dimension, len(best) + len(worst),
                    f"{label} performs well in {_humanise(best_name)} "
                    f"({len(best)} trades, {_mean(best):+.2f}R per trade) but poorly in "
                    f"{_humanise(worst_name)} ({len(worst)} trades, "
                    f"{_mean(worst):+.2f}R)."))
                continue
            for name, values in ranked:
                edge = _mean(values) - baseline
                if abs(edge) >= MIN_SLICE_EDGE_R:
                    verb = "better" if edge > 0 else "worse"
                    findings.append(_finding(
                        strategy, dimension, len(values),
                        f"{label} in {_humanise(name)}: {len(values)} trades at "
                        f"{_mean(values):+.2f}R per trade, {abs(edge):.2f}R {verb} "
                        f"than its {baseline:+.2f}R baseline."))
    return findings


def _finding(strategy: str, dimension: str, sample: int, text: str) -> Dict[str, Any]:
    actionable = sample >= MIN_LIVE_TRADES
    return {
        "id": f"{strategy or 'unattributed'}::{dimension}::{sample}",
        "strategy": strategy,
        "dimension": dimension,
        "sample": sample,
        "actionable": actionable,
        "text": text if actionable else f"{text} Provisional: sample below "
                                        f"{MIN_LIVE_TRADES} trades.",
    }


def _strategy_label(symbol: str, strategy: str,
                    rows: Sequence[Dict[str, Any]]) -> str:
    timeframes = [tf for row in rows for tf in _list(row.get("timeframes"))]
    primary = min((_i(t) for t in timeframes if _i(t)), default=0)
    prefix = f"{symbol} {primary}-minute " if primary else f"{symbol} "
    return f"{prefix}{strategy or 'unattributed setups'}"


def _humanise(name: str) -> str:
    return str(name).replace("_", " ").lower()


def _weight_reason(status: str, trades: int, live_r: float, backtest_r: float,
                   weight: float) -> str:
    if status == "INSUFFICIENT_LIVE_SAMPLE":
        return (f"{trades} live trade(s), below the {MIN_LIVE_TRADES} needed to "
                "adjust anything; weight held at 1.00 whichever way they went")
    if status == "DECAYED":
        return (f"live expectancy {live_r:+.3f}R against a backtested {backtest_r:+.3f}R "
                f"over {trades} trades - influence cut to {weight:.2f} and flagged "
                "for re-research")
    if status == "TRACKING_BACKTEST":
        return (f"live expectancy {live_r:+.3f}R is tracking the backtested "
                f"{backtest_r:+.3f}R over {trades} trades; weight left at "
                f"{weight:.2f} (never raised above 1.00)")
    if status == "NEGATIVE_LIVE_EXPECTANCY":
        return (f"no usable backtest row and {live_r:+.3f}R live over {trades} "
                f"trades - influence cut to {weight:.2f}")
    return "no measured reason to change this strategy's influence"


# --------------------------------------------------------------------------
# Row <-> entry conversion
# --------------------------------------------------------------------------

def _entry_from_row(row: Dict[str, Any]) -> JournalEntry:
    """Rebuild a :class:`JournalEntry` from its stored row."""
    thesis = row.get("thesis_correct")
    return JournalEntry(
        entry_id=str(row.get("entry_id") or ""),
        date_et=str(row.get("date_et") or ""),
        time_et=str(row.get("time_et") or ""),
        symbol=str(row.get("symbol") or ""),
        direction=Direction.coerce(row.get("direction")),
        entry=_fo(row.get("entry")),
        stop=_fo(row.get("stop")),
        targets=[_f(t) for t in _list(row.get("targets"))],
        strategy=str(row.get("strategy") or ""),
        strategy_group=str(row.get("strategy_group") or ""),
        timeframes=[_i(t) for t in _list(row.get("timeframes"))],
        indicators=[str(i) for i in _list(row.get("indicators"))],
        confluences=[str(c) for c in _list(row.get("confluences"))],
        market_regime=_regime_of(row.get("market_regime")),
        volatility_regime=_vol_of(row.get("volatility_regime")),
        session=str(row.get("session") or ""),
        news_environment=str(row.get("news_environment") or ""),
        analyst_predictions=[v for v in _list(row.get("analyst_predictions"))
                             if isinstance(v, dict)],
        final_decision=Decision.coerce(row.get("final_decision")),
        confidence=Confidence(row.get("confidence")),
        contracts=_i(row.get("contracts")),
        dollar_risk=_f(row.get("dollar_risk")),
        result=str(row.get("result") or "OPEN"),
        exit_price=_fo(row.get("exit_price")),
        exit_time_et=row.get("exit_time_et"),
        exit_reason=str(row.get("exit_reason") or ""),
        mfe_points=_f(row.get("mfe_points")),
        mae_points=_f(row.get("mae_points")),
        mfe_r=_f(row.get("mfe_r")),
        mae_r=_f(row.get("mae_r")),
        profit_loss=_f(row.get("profit_loss")),
        realised_r=_f(row.get("realised_r")),
        reward_risk_planned=_f(row.get("reward_risk_planned")),
        thesis_correct=None if thesis is None else bool(thesis),
        what_invalidated=str(row.get("what_invalidated") or ""),
        what_happened_after=str(row.get("what_happened_after") or ""),
        lessons=str(row.get("lessons") or ""),
        callout_id=str(row.get("callout_id") or ""),
    )


def _row_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    """A journal row reduced to what a reader of the artefact needs."""
    return {
        "entry_id": row.get("entry_id"),
        "date_et": row.get("date_et"),
        "time_et": row.get("time_et"),
        "symbol": row.get("symbol"),
        "decision": row.get("final_decision"),
        "direction": row.get("direction"),
        "strategy": row.get("strategy"),
        "regime": row.get("market_regime"),
        "volatility": row.get("volatility_regime"),
        "session": row.get("session"),
        "contracts": _i(row.get("contracts")),
        "dollar_risk": _f(row.get("dollar_risk")),
        "result": row.get("result"),
        "realised_r": _f(row.get("realised_r")),
        "mfe_r": _f(row.get("mfe_r")),
        "mae_r": _f(row.get("mae_r")),
        "thesis_correct": row.get("thesis_correct"),
        "what_happened_after": row.get("what_happened_after"),
    }
