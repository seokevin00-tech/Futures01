"""Trade decision and confluence: LONG, SHORT or NO TRADE.

This is the one layer that looks at everything at once - the three independent
analyst predictions, the news environment, the measured strategy edge and what
is left of the account's risk budget. Four decisions shape the whole file:

* **Never a majority vote.** Each analyst is weighted by its *measured*
  accuracy in the *current* regime (``Storage.agent_accuracy``). Three
  analysts agreeing is weak evidence if all three have been wrong in these
  conditions, and one dissenter with a record can outweigh two without one.
  Below the sufficiency threshold the weights fall back to equal and the
  rationale says so out loud: an invented track record is worse than an
  admitted absence of one.

* **Missing evidence never becomes a guess.** Every input may be absent - the
  analysts may not have run, the research may have produced nothing eligible,
  the feature snapshot may still be warming up. Each absence subtracts
  evidence, and absent evidence resolves to NO TRADE. A ``decide`` task that
  concludes NO TRADE has succeeded; it has not failed.

* **This layer prices nothing of its own.** Entries, stops and targets come
  from the analysts that proposed them, snapped to the contract's tick and
  combined conservatively - widest stop, nearest first target - so the
  reward/risk reported here can only understate the edge, never flatter it.

* **Sizing belongs to the risk agent.** ``contracts``, ``dollar_risk``,
  ``account_risk_pct`` and ``risk_assessment`` are left at their defaults and a
  ``TradeProposal``-shaped dict is handed over in the payload, so the risk
  layer can size and veto without re-deriving any of this reasoning.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields as dataclass_fields
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import AccountConfig, ContractSpec, get_contract
from ..risk.manager import TradeProposal
from ..schema import (AnalystPrediction, Confidence, Decision, Direction,
                      Evidence, HistoricalPerformance, MarketRegime, NewsRisk,
                      TradeCallout, fmt_price)
from ..team.agent import AgentResult
from ..team.board import Task
from ..team.roles import Role
from ..timeutil import et_stamp
from .base import DomainAgent

__all__ = ["DecisionAgent"]


#: (seat, artefact it publishes, its letter, the evidence family it covers).
_ANALYSTS: Tuple[Tuple[Role, str, str, str], ...] = (
    (Role.ANALYST_A, "prediction_a", "A", "structure"),
    (Role.ANALYST_B, "prediction_b", "B", "statistics"),
    (Role.ANALYST_C, "prediction_c", "C", "macro"),
)

#: ``Evidence.kind`` -> the independent family it belongs to. Confluence is
#: counted in families, not in claims: three restatements of one observation
#: are one piece of evidence wearing three hats.
_EVIDENCE_FAMILY: Dict[str, str] = {
    "structure": "structure", "indicator": "structure", "price": "structure",
    "orderflow": "structure", "volume": "structure",
    "statistic": "statistics", "backtest": "statistics", "quant": "statistics",
    "probability": "statistics",
    "news": "macro", "macro": "macro", "event": "macro", "sentiment": "macro",
}

_SUFFICIENT_N = 20            # Storage.agent_accuracy's own sufficiency bar
_ACCURACY_PRIOR = 20.0        # pseudo-count shrinking accuracy toward a coin flip
_MIN_ANALYSTS_FOR_TRADE = 2   # one voice is an opinion, not a confluence
_MIN_CONVICTION = 0.30        # weighted directional conviction floor
_MAX_OPPOSING_SHARE = 0.50    # opposing mass this large is genuine disagreement
_MIN_FAMILIES_FOR_TRADE = 2   # structure + statistics, or statistics + macro, ...
_CONFIDENCE_CAP = 0.75        # house rule 5; lifted only for broad, deep evidence
_LLM_CONFIDENCE_LATITUDE = 0.15

#: Strategy groups whose logic assumes continuation, and those that assume a
#: fade. Used only to flag a regime mismatch on a row measured across ALL
#: regimes - a row measured *in* this regime needs no such inference.
_TREND_GROUPS = frozenset({"TREND", "MOMENTUM", "BREAKOUT", "PULLBACK",
                           "MULTI_TIMEFRAME", "OPENING_RANGE"})
_REVERSION_GROUPS = frozenset({"MEAN_REVERSION", "REVERSAL", "VWAP", "LIQUIDITY"})

_THIN_SESSIONS = frozenset({"ASIA", "LUNCH", "POST_CLOSE"})

_WORD = re.compile(r"[a-z0-9.]+")
#: Vocabulary shared by every trading rationale ever written. Excluded from the
#: restatement test so that two genuinely different arguments are not scored as
#: duplicates because both contain the word "level".
_GENERIC_WORDS = frozenset({
    "price", "prices", "market", "level", "levels", "above", "below", "trade",
    "trading", "setup", "with", "this", "that", "from", "into", "over", "under",
    "near", "move", "moves", "into", "target", "stop", "entry", "long", "short",
    "bias", "session", "current", "expect", "expected", "likely", "should",
})


# --------------------------------------------------------------------------
# Small numeric / parsing helpers
# --------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    """Best-effort float. Anything unparseable is absent, never zero - a stop
    silently defaulted to 0.0 would price a catastrophic trade."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _median(values: Sequence[Optional[float]]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def _weighted_mean(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    den = sum(w for _, w in pairs)
    if den <= 0:
        return None
    return sum(v * w for v, w in pairs) / den


def _tokens(text: str) -> frozenset:
    return frozenset(w for w in _WORD.findall(str(text).lower())
                     if len(w) > 3 and w not in _GENERIC_WORDS)


def _overlap(a: frozenset, b: frozenset) -> float:
    """Jaccard overlap, used to detect two analysts restating one observation."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _coerce_enum(enum_cls, value: Any, default):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().upper())
    except (ValueError, AttributeError):
        return default


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _find_payload(raw: Any, marker: str = "direction", depth: int = 3) -> Optional[dict]:
    """Locate the dict that actually carries a prediction.

    The analyst artefacts are written by another team member's agent. Accepting
    only one exact envelope shape would make this layer fail closed on a
    cosmetic difference - so the search is depth-limited and keys on the marker
    field rather than on the wrapper's name.
    """
    if isinstance(raw, list):
        for item in raw:
            found = _find_payload(item, marker, depth)
            if found is not None:
                return found
        return None
    if not isinstance(raw, dict):
        return None
    if marker in raw:
        return raw
    if depth <= 0:
        return None
    for value in raw.values():
        found = _find_payload(value, marker, depth - 1)
        if found is not None:
            return found
    return None


def _evidence_from(raw: Any) -> List[Evidence]:
    out: List[Evidence] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        tf = item.get("timeframe")
        out.append(Evidence(
            kind=str(item.get("kind") or ""),
            name=str(item.get("name") or ""),
            value=item.get("value"),
            timeframe=int(tf) if isinstance(tf, (int, float)) else None,
            detail=str(item.get("detail") or ""),
            supports=Direction.coerce(item.get("supports")),
            weight=_num(item.get("weight")) or 1.0,
            source=str(item.get("source") or ""),
        ))
    return out


def _hp_from_dict(raw: Any) -> Optional[HistoricalPerformance]:
    if not isinstance(raw, dict):
        return None
    allowed = {f.name for f in dataclass_fields(HistoricalPerformance)}
    kwargs = {k: v for k, v in raw.items() if k in allowed}
    try:
        return HistoricalPerformance(**kwargs)
    except (TypeError, ValueError):
        return None


def _prediction_from(raw: Any, letter: str) -> Optional[AnalystPrediction]:
    """Rebuild an :class:`AnalystPrediction` from a published artefact."""
    d = _find_payload(raw)
    if d is None:
        return None

    zone = d.get("entry_zone")
    entry_zone: Optional[Tuple[float, float]] = None
    if isinstance(zone, (list, tuple)) and len(zone) >= 2:
        lo, hi = _num(zone[0]), _num(zone[1])
        if lo is not None and hi is not None:
            entry_zone = (min(lo, hi), max(lo, hi))
    elif _num(zone) is not None:
        entry_zone = (_num(zone), _num(zone))
    if entry_zone is None and _num(d.get("entry")) is not None:
        entry_zone = (_num(d.get("entry")), _num(d.get("entry")))

    targets = [_num(t) for t in (d.get("targets") or [])] if isinstance(
        d.get("targets"), (list, tuple)) else []
    targets = [t for t in targets if t is not None]
    t1 = _num(d.get("target_1")) or (targets[0] if len(targets) > 0 else None)
    t2 = _num(d.get("target_2")) or (targets[1] if len(targets) > 1 else None)
    t3 = _num(d.get("target_3")) or (targets[2] if len(targets) > 2 else None)

    def _strlist(key: str) -> List[str]:
        v = d.get(key)
        return [str(x) for x in v] if isinstance(v, (list, tuple)) else []

    pred = AnalystPrediction(
        analyst_id=str(d.get("analyst_id") or letter),
        analyst_name=str(d.get("analyst_name") or ""),
        specialisation=str(d.get("specialisation") or ""),
        symbol=str(d.get("symbol") or ""),
        direction=Direction.coerce(d.get("direction")),
        entry_zone=entry_zone,
        stop=_num(d.get("stop")),
        target_1=t1, target_2=t2, target_3=t3,
        expected_reward_risk=_num(d.get("expected_reward_risk")),
        confidence=Confidence(d.get("confidence")),
        time_horizon=str(d.get("time_horizon") or ""),
        primary_reason=str(d.get("primary_reason") or ""),
        supporting_confluences=_strlist("supporting_confluences"),
        invalidation_conditions=_strlist("invalidation_conditions"),
        would_change_mind_if=str(d.get("would_change_mind_if") or ""),
        evidence=_evidence_from(d.get("evidence")),
        historical_performance=_hp_from_dict(d.get("historical_performance")),
        reasoning=str(d.get("reasoning") or ""),
        source=str(d.get("source") or "deterministic"),
    )
    if d.get("timestamp_et"):
        pred.timestamp_et = str(d["timestamp_et"])
    return pred


def _hp_from_row(row: Dict[str, Any], min_trades: int) -> HistoricalPerformance:
    """Turn a ``strategy_performance`` row into the schema's record.

    ``sample_is_sufficient`` is recomputed from the configured floor rather
    than trusted from the row, so that a strategy flagged eligible under a
    looser setting cannot smuggle itself into a live decision.
    """
    trades = int(row.get("trades") or 0)
    return HistoricalPerformance(
        strategy_id=str(row.get("strategy_id") or ""),
        symbol=str(row.get("symbol") or ""),
        timeframe=int(row["timeframe"]) if row.get("timeframe") else None,
        regime=str(row.get("regime") or ""),
        session=str(row.get("session") or ""),
        trades=trades,
        win_rate=_num(row.get("win_rate")) or 0.0,
        profit_factor=_num(row.get("profit_factor")) or 0.0,
        expectancy_r=_num(row.get("expectancy_r")) or 0.0,
        max_drawdown_r=_num(row.get("max_drawdown_r")) or 0.0,
        sharpe=_num(row.get("sharpe")) or 0.0,
        sortino=_num(row.get("sortino")) or 0.0,
        out_of_sample_trades=int(row.get("oos_trades") or 0),
        out_of_sample_expectancy_r=_num(row.get("oos_expectancy_r")) or 0.0,
        walk_forward_efficiency=_num(row.get("walk_forward_efficiency")) or 0.0,
        robustness_score=_num(row.get("robustness_score")) or 0.0,
        sample_is_sufficient=trades >= max(1, int(min_trades)),
    )


def _weight_for(record: Dict[str, Any]) -> Tuple[float, str, bool]:
    """How much one analyst's opinion is worth, from its measured record.

    Returns ``(weight, basis, measured)``. Below the sufficiency threshold the
    weight is exactly 1.0 - equal with every other analyst - and ``measured``
    is False so that every report can say plainly that no track record exists.
    Claiming a record from 3 scored predictions would be worse than claiming
    none.

    Above it, the accuracy is shrunk toward a coin flip before it is used
    (a 12-from-20 analyst is not a 60% analyst), then adjusted for realised R
    and for how often the seat has produced false signals.
    """
    n = int(record.get("predictions") or 0)
    if not record.get("sufficient") or n <= 0:
        return 1.0, (f"equal weight - {n} scored prediction(s), fewer than the "
                     f"{_SUFFICIENT_N} needed for a measured record"), False

    correct = float(record.get("correct") or 0.0)
    shrunk = (correct + 0.5 * _ACCURACY_PRIOR) / (n + _ACCURACY_PRIOR)
    weight = _clamp(1.0 + 4.0 * (shrunk - 0.5), 0.15, 2.0)
    basis = [f"{correct:.0f}/{n} correct in this regime "
             f"({record.get('accuracy', 0.0) * 100:.0f}%, shrunk to "
             f"{shrunk * 100:.0f}%)"]

    avg_r = float(record.get("avg_r") or 0.0)
    if avg_r < 0:
        weight *= 0.6
        basis.append(f"negative measured expectancy {avg_r:+.2f}R x0.60")
    elif avg_r >= 0.15:
        weight *= 1.15
        basis.append(f"measured expectancy {avg_r:+.2f}R x1.15")

    false_rate = float(record.get("false_signals") or 0.0) / n
    if false_rate > 0.35:
        weight *= 0.8
        basis.append(f"{false_rate:.0%} false signals x0.80")

    weight = _clamp(weight, 0.10, 2.50)
    return weight, "; ".join(basis), True


# --------------------------------------------------------------------------
# Internal working structures
# --------------------------------------------------------------------------

@dataclass
class _AnalystView:
    """One analyst seat: what it said, and what its record says it is worth."""

    role: Role
    letter: str
    artefact: str
    family: str
    prediction: Optional[AnalystPrediction] = None
    record: Dict[str, Any] = field(default_factory=dict)
    record_key: str = ""
    weight: float = 1.0
    weight_basis: str = ""
    measured: bool = False

    @property
    def present(self) -> bool:
        return self.prediction is not None

    @property
    def direction(self) -> Direction:
        return self.prediction.direction if self.prediction else Direction.NEUTRAL

    @property
    def confidence(self) -> float:
        return self.prediction.confidence if self.prediction else 0.0

    def families(self) -> List[str]:
        """Independent evidence families this view actually rests on."""
        if not self.prediction:
            return []
        fams = {_EVIDENCE_FAMILY.get((e.kind or "").strip().lower())
                for e in self.prediction.evidence}
        fams.discard(None)
        # No evidence records attached: fall back to the seat's mandate, and
        # label it as assumed rather than measured wherever it is reported.
        return sorted(fams) if fams else [self.family]

    def to_dict(self) -> dict:
        return {
            "analyst": self.letter,
            "agent_id": self.role.value,
            "present": self.present,
            "direction": self.direction.value,
            "confidence": round(self.confidence, 3),
            "weight": round(self.weight, 3),
            "weight_basis": self.weight_basis,
            "record_key": self.record_key,
            "track_record": {
                "predictions": self.record.get("predictions", 0),
                "correct": self.record.get("correct", 0),
                "accuracy": round(float(self.record.get("accuracy", 0.0)), 4),
                "avg_r": round(float(self.record.get("avg_r", 0.0)), 4),
                "false_signals": self.record.get("false_signals", 0),
                "sufficient": bool(self.record.get("sufficient", False)),
            },
            "evidence_families": self.families(),
            "prediction": self.prediction.to_dict() if self.prediction else None,
        }


@dataclass
class _Gate:
    """One pass/fail condition on the way to an executable callout.

    ``hard`` gates are matters of fact - no eligible strategy, a news blackout,
    an inadequate reward/risk. The reasoning model may argue about judgement;
    it may not argue a hard gate open.
    """

    name: str
    passed: bool
    detail: str
    hard: bool = True

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed,
                "hard": self.hard, "detail": self.detail}


@dataclass
class _Setup:
    """A priced setup assembled from the analysts who proposed this direction."""

    direction: Direction
    entry: float
    stop: float
    targets: List[float]
    risk_points: float
    stop_ticks: float
    reward_risk: float
    first_target_rr: float
    cost_per_contract: float
    risk_per_contract: float
    rr_after_costs: float
    entry_zone: Optional[Tuple[float, float]] = None
    contributors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction.value,
            "entry": self.entry, "stop": self.stop, "targets": list(self.targets),
            "entry_zone": list(self.entry_zone) if self.entry_zone else None,
            "risk_points": round(self.risk_points, 6),
            "stop_ticks": round(self.stop_ticks, 2),
            "reward_risk": round(self.reward_risk, 3),
            "first_target_rr": round(self.first_target_rr, 3),
            "cost_per_contract": round(self.cost_per_contract, 2),
            "risk_per_contract": round(self.risk_per_contract, 2),
            "reward_risk_after_costs_1_contract": round(self.rr_after_costs, 3),
            "contributors": list(self.contributors),
        }


@dataclass
class _Assessment:
    """Everything the deterministic pass established, in one auditable object."""

    symbol: str
    stamp: str
    spec: ContractSpec
    account: AccountConfig
    regime: MarketRegime = MarketRegime.UNKNOWN
    regime_name: str = "UNKNOWN"
    volatility: str = "NORMAL"
    volume: str = "AVERAGE"
    session: str = ""
    time_bucket: str = ""
    is_rth: bool = False
    price: Optional[float] = None
    atr: Optional[float] = None
    atr_median: Optional[float] = None
    alignment: Optional[float] = None
    views: List[_AnalystView] = field(default_factory=list)
    news_risk: NewsRisk = NewsRisk.NONE
    news_known: bool = False
    news_summary: str = ""
    news_bias: Direction = Direction.NEUTRAL
    minutes_to_news: Optional[float] = None
    strategy_rows: List[Dict[str, Any]] = field(default_factory=list)
    strategy: Optional[Dict[str, Any]] = None
    historical: Optional[HistoricalPerformance] = None
    strategy_name: str = ""
    strategy_group: str = ""
    strategy_timeframe: Optional[int] = None
    trading_mode: str = "UNKNOWN"
    mode_reasons: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    gates: List[_Gate] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    families: List[str] = field(default_factory=list)
    restatement: float = 0.0
    #: Weighted mass for the leading direction, against it, and abstaining.
    masses: Dict[str, float] = field(default_factory=dict)
    conviction: float = 0.0
    agreement_score: float = 0.0
    agreement_text: str = "no analyst predictions available"
    equal_weighted: bool = True
    lean: Decision = Decision.NO_TRADE
    setup: Optional[_Setup] = None
    confidence: float = 0.0
    soft_blocks: List[str] = field(default_factory=list)

    # ---- derived ------------------------------------------------------
    @property
    def present_views(self) -> List[_AnalystView]:
        return [v for v in self.views if v.present]

    @property
    def failed_hard_gates(self) -> List[_Gate]:
        return [g for g in self.gates if g.hard and not g.passed]

    @property
    def failed_gates(self) -> List[_Gate]:
        return [g for g in self.gates if not g.passed]

    def gate(self, name: str, passed: bool, detail: str, *, hard: bool = True) -> bool:
        self.gates.append(_Gate(name=name, passed=passed, detail=detail, hard=hard))
        return passed

    def add(self, kind: str, name: str, *, value: Any = None, detail: str = "",
            supports: Direction = Direction.NEUTRAL, weight: float = 1.0,
            timeframe: Optional[int] = None, source: str = "") -> Evidence:
        ev = Evidence(kind=kind, name=name, value=value, timeframe=timeframe,
                      detail=detail, supports=supports, weight=weight,
                      source=source or Role.DECISION.value)
        self.evidence.append(ev)
        return ev


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------

_ROLE_PROMPT = """
You are the trade decision and confluence layer. Everything below has already
been measured by the deterministic layer; your job is the one thing a language
model is genuinely better at than a formula: weighing conflicting structured
evidence and saying whether it adds up to a trade.

You are given, for one symbol: each analyst's independent prediction, each
analyst's measured accuracy in the current regime (with its sample size), the
news environment, the measured out-of-sample record of the candidate strategy,
the deterministic lean and the gate results.

Rules specific to this seat:

1. Never take a majority vote. Weigh each analyst by its measured record in
   this regime. Where the sample is too small to mean anything, say so and
   treat the analysts as equally (un)proven.
2. Judge whether the agreeing evidence is genuinely independent - market
   structure, statistics and macro are three sources; three analysts quoting
   the same moving average is one.
3. You may not invent or adjust any price, level, statistic, sample size or
   historical figure. Every number is supplied; reason only over those.
4. A failed hard gate is final. If any hard gate failed, the decision is
   NO TRADE and your task is to explain why, not to argue around it.
5. NO TRADE is a complete and successful answer. Prefer it whenever the
   evidence conflicts, the sample is thin or the edge is not worth the risk.
""".strip()

_LLM_QUESTION = (
    "Weigh this evidence and return the decision. State the conflicts you "
    "found and the strongest reason to avoid the trade even if you conclude "
    "it is worth taking. Quote sample sizes with any statistic you cite."
)

_LLM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "confidence", "rationale", "key_conflicts",
                 "reason_to_avoid"],
    "properties": {
        "decision": {"type": "string", "enum": ["LONG", "SHORT", "NO TRADE"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "rationale": {"type": "string",
                      "description": "The reasoning chain, tracing to the "
                                     "supplied evidence."},
        "key_conflicts": {"type": "array", "items": {"type": "string"}},
        "reason_to_avoid": {"type": "string",
                            "description": "The strongest argument against "
                                           "taking this trade."},
    },
}


class DecisionAgent(DomainAgent):
    """Weighs the team's evidence by measured accuracy and concludes."""

    def __init__(self, fs, bus, *, context=None, config=None, llm=None):
        super().__init__(Role.DECISION, fs, bus,
                         context=context, config=config, llm=llm)

    # ------------------------------------------------------------------
    def handle(self, task: Task) -> AgentResult:
        if task.kind == "decide":
            return self._decide(task)
        if task.kind == "review_setup":
            return self._review_setup(task)
        raise ValueError(f"decision agent cannot handle task kind {task.kind!r}")

    # ==================================================================
    # Input gathering
    # ==================================================================
    def _symbol_for(self, task: Task) -> str:
        ctx = self.require_context()
        raw = task.payload.get("symbol") or task.payload.get("symbols")
        if isinstance(raw, (list, tuple)) and raw:
            raw = raw[0]
        symbol = str(raw or (ctx.symbols[0] if ctx.symbols else "")).upper().strip()
        if not symbol:
            raise ValueError("decide task carries no symbol and the context has none")
        get_contract(symbol)          # raises a helpful error for an unknown one
        return symbol

    def _gather_views(self, symbol: str, regime: str) -> List[_AnalystView]:
        """Read each analyst's artefact and attach its measured track record."""
        ctx = self.require_context()
        views: List[_AnalystView] = []
        for role, artefact, letter, family in _ANALYSTS:
            view = _AnalystView(role=role, letter=letter, artefact=artefact,
                                family=family)
            try:
                raw = self.read_from(role, artefact)
            except PermissionError:                      # pragma: no cover
                raw = None
            view.prediction = _prediction_from(raw, letter)
            if view.prediction is not None and view.prediction.symbol and \
                    view.prediction.symbol.upper() != symbol:
                # A prediction for a different symbol is not evidence here.
                self.log(f"ignoring {artefact}: it is for "
                         f"{view.prediction.symbol}, not {symbol}")
                view.prediction = None
            view.record, view.record_key = self._track_record(view, symbol, regime)
            view.weight, view.weight_basis, view.measured = _weight_for(view.record)
            views.append(view)
        return views

    def _track_record(self, view: _AnalystView, symbol: str,
                      regime: str) -> Tuple[Dict[str, Any], str]:
        """This seat's measured accuracy, in this regime, from the journal.

        The journal agent owns the id under which it scores each analyst, so a
        few plausible spellings are tried and the first with any scored
        predictions wins. A record that exists under a key nobody queries is
        indistinguishable from no record at all, and silently weighting an
        analyst at "no history" when it has one is exactly the failure this
        layer exists to avoid.
        """
        ctx = self.require_context()
        pred = view.prediction
        candidates = [view.role.value, view.letter, f"analyst_{view.letter.lower()}"]
        if pred is not None:
            for extra in (pred.analyst_id, pred.analyst_name):
                if extra:
                    candidates.append(str(extra))
        seen: List[str] = []
        for key in candidates:
            if key in seen:
                continue
            seen.append(key)
            record = ctx.storage.agent_accuracy(key, symbol, regime)
            if record.get("predictions"):
                return record, key
        return ctx.storage.agent_accuracy(view.role.value, symbol, regime), view.role.value

    def _read_news(self, assessment: _Assessment) -> None:
        """Attach the news environment. Absent news is unknown, not benign."""
        ctx = self.require_context()
        raw = self.read_from(Role.NEWS_MACRO, "news_context")
        payload = _find_payload(raw, "risk", depth=3)
        if payload is None and ctx.news is not None:
            payload = ctx.news.to_dict()
        if payload is None:
            assessment.news_known = False
            assessment.news_risk = NewsRisk.NONE
            assessment.news_summary = "no news context published"
            return
        assessment.news_known = True
        assessment.news_risk = _coerce_enum(NewsRisk, payload.get("risk"), NewsRisk.NONE)
        assessment.news_bias = Direction.coerce(payload.get("macro_bias"))
        assessment.minutes_to_news = _num(payload.get("minutes_to_next_high_impact"))
        upcoming = payload.get("upcoming_events")
        head = str(payload.get("headline_summary") or "").strip()
        bits = [f"risk {assessment.news_risk.value}"]
        if assessment.minutes_to_news is not None:
            bits.append(f"next high-impact event in "
                        f"{assessment.minutes_to_news:.0f} min")
        if isinstance(upcoming, list) and upcoming:
            titles = [str(e.get("title") or e.get("category") or "")
                      for e in upcoming[:2] if isinstance(e, dict)]
            if any(titles):
                bits.append("upcoming: " + "; ".join(t for t in titles if t))
        if head:
            bits.append(head[:220])
        assessment.news_summary = " | ".join(bits)

    def _load_strategies(self, assessment: _Assessment) -> None:
        ctx = self.require_context()
        regime = assessment.regime_name if assessment.regime_name != "UNKNOWN" else None
        rows = ctx.top_strategies(assessment.symbol, regime=regime) or []
        assessment.strategy_rows = [dict(r) for r in rows]

    # ==================================================================
    # Weighted confluence
    # ==================================================================
    def _score(self, assessment: _Assessment) -> None:
        """Accuracy-weighted directional conviction - never a head count."""
        mass = net = neutral_mass = 0.0
        by_direction = {Direction.LONG: 0.0, Direction.SHORT: 0.0}

        for view in assessment.present_views:
            conf = view.confidence
            if view.direction is Direction.NEUTRAL:
                # An abstention is a vote against risking capital. It is worth
                # at least half a unit even when reported weakly, so that a
                # "no setup here" does not vanish from the arithmetic.
                effective = max(conf, 0.5)
                neutral_mass += view.weight * effective
                mass += view.weight * effective
                continue
            effective = max(conf, 0.05)
            contribution = view.weight * effective
            by_direction[view.direction] += contribution
            mass += contribution
            net += contribution * view.direction.sign

        assessment.conviction = net / mass if mass > 0 else 0.0
        lead_dir = (Direction.LONG
                    if by_direction[Direction.LONG] >= by_direction[Direction.SHORT]
                    else Direction.SHORT)
        lead = by_direction[lead_dir]
        opposing = by_direction[Direction.LONG if lead_dir is Direction.SHORT
                                else Direction.SHORT]

        # -1 (opposed) .. +1 (unanimous), the scale the risk manager reads.
        # Opposition is penalised harder than abstention because a countervailing
        # view is evidence against, where an abstention is only absence of
        # evidence for.
        if mass > 0:
            agreement = (lead - 1.5 * opposing - 0.5 * neutral_mass) / mass
        else:
            agreement = 0.0
        assessment.masses = {"lead": round(lead, 4), "opposing": round(opposing, 4),
                             "abstaining": round(neutral_mass, 4),
                             "total": round(mass, 4)}
        present = len(assessment.present_views)
        if agreement > 0:
            # One analyst agreeing with itself is not unanimity.
            agreement *= present / float(len(_ANALYSTS))
        assessment.agreement_score = round(_clamp(agreement, -1.0, 1.0), 4)
        assessment.equal_weighted = not any(v.measured for v in assessment.present_views)
        assessment.agreement_text = self._agreement_text(assessment, lead, opposing,
                                                         neutral_mass)
        if abs(assessment.conviction) < 1e-9:
            assessment.lean = Decision.NO_TRADE
        else:
            assessment.lean = (Decision.LONG if assessment.conviction > 0
                               else Decision.SHORT)

    @staticmethod
    def _agreement_text(assessment: _Assessment, lead: float, opposing: float,
                        neutral_mass: float) -> str:
        """The readable agreement string that goes on the callout."""
        parts: List[str] = []
        for view in assessment.views:
            if not view.present:
                parts.append(f"{view.letter} absent")
                continue
            rec = view.record
            if view.measured:
                record = (f"{rec['accuracy'] * 100:.0f}% over "
                          f"{rec['predictions']} in {assessment.regime_name}, "
                          f"{rec['avg_r']:+.2f}R avg")
            else:
                record = (f"no measured record ({rec.get('predictions', 0)} of "
                          f"{_SUFFICIENT_N} scored predictions)")
            parts.append(f"{view.letter} {view.direction.value} "
                         f"(conf {view.confidence:.2f}, weight {view.weight:.2f}; "
                         f"{record})")
        head = " / ".join(parts)
        basis = ("equal weights - no analyst has a sufficient measured record "
                 "in this regime" if assessment.equal_weighted
                 else "weights from measured accuracy in this regime")
        shape = (f"weighted agreement {assessment.agreement_score:+.2f}, "
                 f"conviction {assessment.conviction:+.2f} "
                 f"[for {lead:.2f} / against {opposing:.2f} / "
                 f"abstaining {neutral_mass:.2f}]")
        return f"{head} -- {shape}; {basis}"

    # ==================================================================
    # Pricing the setup from the analysts who proposed it
    # ==================================================================
    def _build_setup(self, assessment: _Assessment,
                     direction: Direction) -> Tuple[Optional[_Setup], List[str]]:
        """Assemble entry, stop and targets from the agreeing analysts only.

        Combined conservatively on purpose: the *widest* stop (the level that
        gives the trade the most room to be wrong is also the one that costs
        the most to be wrong at, so it cannot flatter the size) and the
        *nearest* first target (the least reward that can be claimed). Any
        reward/risk this produces is therefore a floor, not a hope.
        """
        spec = assessment.spec
        problems: List[str] = []
        supporters = [v for v in assessment.present_views if v.direction is direction]
        if not supporters:
            return None, ["no analyst proposed this direction"]

        entries = [(0.5 * (v.prediction.entry_zone[0] + v.prediction.entry_zone[1]),
                    v.weight * max(v.confidence, 0.05))
                   for v in supporters if v.prediction.entry_zone]
        if not entries:
            problems.append("no supporting analyst supplied an entry zone, and "
                            "this layer does not invent levels")
            return None, problems
        entry = spec.round_to_tick(_weighted_mean(entries))

        sign = direction.sign
        stops = [v.prediction.stop for v in supporters
                 if v.prediction.stop is not None
                 and (v.prediction.stop - entry) * sign < 0]
        if not stops:
            problems.append("no supporting analyst supplied a stop on the "
                            "correct side of the combined entry")
            return None, problems
        stop = spec.round_to_tick(min(stops) if direction is Direction.LONG
                                  else max(stops))
        widest, tightest = max(abs(s - entry) for s in stops), min(
            abs(s - entry) for s in stops)
        if tightest > 0 and widest / tightest >= 2.0 and len(stops) > 1:
            problems.append(
                f"supporting analysts disagree about where the trade is wrong "
                f"by {widest / tightest:.1f}x "
                f"({fmt_price(tightest)} vs {fmt_price(widest)} points of risk)")

        candidates: List[float] = []
        per_view_last: List[float] = []
        for v in supporters:
            valid = [spec.round_to_tick(t) for t in v.prediction.targets
                     if t is not None and (t - entry) * sign > 0]
            candidates.extend(valid)
            if valid:
                per_view_last.append(max(valid, key=lambda t: abs(t - entry)))
        candidates = sorted(set(candidates), key=lambda t: abs(t - entry))
        if not candidates:
            problems.append("no supporting analyst supplied a target beyond "
                            "the entry")
            return None, problems

        first = candidates[0]
        final = _median(per_view_last) or candidates[-1]
        final = spec.round_to_tick(final)
        if abs(final - entry) < abs(first - entry):
            final = candidates[-1]
        middle = [t for t in candidates
                  if abs(first - entry) < abs(t - entry) < abs(final - entry)]
        targets: List[float] = [first]
        mid = _median(middle)
        if mid is not None:
            targets.append(spec.round_to_tick(mid))
        if abs(final - entry) > abs(targets[-1] - entry):
            targets.append(final)
        targets = sorted(set(targets), key=lambda t: abs(t - entry))

        risk_points = abs(entry - stop)
        if risk_points <= 0:
            problems.append("the combined stop rounds onto the entry - risk "
                            "would be undefined")
            return None, problems

        risk_per_contract = risk_points * spec.point_value
        cost = spec.round_turn_cost
        first_reward = abs(targets[0] - entry) * spec.point_value
        lows = [v.prediction.entry_zone[0] for v in supporters if v.prediction.entry_zone]
        highs = [v.prediction.entry_zone[1] for v in supporters if v.prediction.entry_zone]
        zone = (spec.round_to_tick(min(lows)), spec.round_to_tick(max(highs)))
        setup = _Setup(
            entry_zone=zone,
            direction=direction, entry=entry, stop=stop, targets=targets,
            risk_points=risk_points,
            stop_ticks=spec.ticks_between(entry, stop),
            reward_risk=abs(targets[-1] - entry) / risk_points,
            first_target_rr=abs(targets[0] - entry) / risk_points,
            cost_per_contract=cost,
            risk_per_contract=risk_per_contract,
            rr_after_costs=((first_reward - cost) / risk_per_contract
                            if risk_per_contract > 0 else 0.0),
            contributors=[v.letter for v in supporters],
        )
        return setup, problems

    # ==================================================================
    # Evidence and gates
    # ==================================================================
    def _evaluate(self, assessment: _Assessment) -> None:
        """Record every dimension as Evidence, then apply the gates."""
        ctx = self.require_context()
        acct = assessment.account
        symbol = assessment.symbol
        lean_dir = assessment.lean.as_direction

        # ---- 1. is there any evidence at all ---------------------------
        present = assessment.present_views
        missing = [v.letter for v in assessment.views if not v.present]
        assessment.add(
            "statistic", "analyst_coverage", value=f"{len(present)}/3",
            detail=("all three analysts reported"
                    if not missing else
                    f"missing predictions from analyst(s) {', '.join(missing)} - "
                    "less evidence, not a reason to extrapolate"),
            supports=Direction.NEUTRAL,
            weight=1.0 if not missing else 1.5)
        assessment.gate(
            "analyst_evidence", len(present) >= _MIN_ANALYSTS_FOR_TRADE,
            f"{len(present)} of 3 analysts reported; {_MIN_ANALYSTS_FOR_TRADE} "
            "independent views are the minimum for an executable callout")
        if missing:
            assessment.conflicts.append(
                f"analyst {', '.join(missing)} did not report")

        # ---- 2. agreement / disagreement -------------------------------
        assessment.add(
            "statistic", "analyst_agreement", value=assessment.agreement_score,
            detail=assessment.agreement_text, supports=lean_dir,
            weight=abs(assessment.agreement_score) or 0.1)

        # ---- 3. how the analysts were weighted -------------------------
        weighting = "; ".join(f"{v.letter}: {v.weight_basis}" for v in assessment.views)
        assessment.add(
            "statistic", "analyst_weighting",
            value="equal" if assessment.equal_weighted else "measured",
            detail=weighting, supports=Direction.NEUTRAL, weight=1.0)

        # ---- 4. strategy edge ------------------------------------------
        self._strategy_evidence(assessment, lean_dir)

        # ---- 5. independence of the agreeing evidence ------------------
        self._independence_evidence(assessment, lean_dir)

        # ---- 6. reward against risk, after costs -----------------------
        self._reward_evidence(assessment)

        # ---- 7. session and time of day --------------------------------
        self._session_evidence(assessment)

        # ---- 8. news ----------------------------------------------------
        blackout = assessment.news_risk.blocks_entry
        assessment.add(
            "news", "news_risk", value=assessment.news_risk.value,
            detail=assessment.news_summary,
            supports=(assessment.news_bias if assessment.news_known
                      else Direction.NEUTRAL),
            weight=1.5 if assessment.news_risk in (NewsRisk.HIGH, NewsRisk.BLACKOUT) else 0.8)
        assessment.gate("news_blackout", not blackout,
                        "inside a scheduled high-impact event window"
                        if blackout else
                        f"news risk {assessment.news_risk.value}"
                        + ("" if assessment.news_known
                           else " (no news context published - treated as unknown)"))
        if assessment.news_risk is NewsRisk.HIGH:
            assessment.conflicts.append("high news risk into the setup")
        if (assessment.news_known and assessment.news_bias is not Direction.NEUTRAL
                and lean_dir is not Direction.NEUTRAL
                and assessment.news_bias is not lean_dir):
            assessment.conflicts.append(
                f"macro bias is {assessment.news_bias.value} against a "
                f"{lean_dir.value} setup")

        # ---- 9. trending vs ranging ------------------------------------
        self._regime_evidence(assessment, lean_dir)

        # ---- 10. volatility --------------------------------------------
        self._volatility_evidence(assessment)

        # ---- 11. liquidity ---------------------------------------------
        thin = (assessment.volume in ("THIN", "BELOW_AVERAGE")
                or assessment.session in _THIN_SESSIONS)
        assessment.add(
            "indicator", "liquidity", value=assessment.volume,
            detail=(f"volume regime {assessment.volume} in the "
                    f"{assessment.session or 'unknown'} session"
                    f"{' (outside regular trading hours)' if not assessment.is_rth else ''}"
                    + ("; fills and stop behaviour are less reliable here"
                       if thin else "")),
            supports=Direction.NEUTRAL, weight=1.2 if thin else 0.6)
        if assessment.volume == "THIN":
            assessment.conflicts.append(
                f"thin volume in the {assessment.session} session")

        # ---- 12. the account's own limits ------------------------------
        self._account_evidence(assessment)

        # ---- 13. conflicting signals, collected ------------------------
        if assessment.conflicts:
            assessment.add(
                "statistic", "conflicting_signals", value=len(assessment.conflicts),
                detail="; ".join(assessment.conflicts),
                supports=Direction.NEUTRAL, weight=float(len(assessment.conflicts)))

    def _strategy_evidence(self, assessment: _Assessment, lean: Direction) -> None:
        """Does this setup match a strategy with a demonstrated OOS edge?"""
        ctx = self.require_context()
        rows = assessment.strategy_rows
        acct = assessment.account
        chosen: Optional[Dict[str, Any]] = None
        hp: Optional[HistoricalPerformance] = None

        for row in rows:
            if row.get("_not_yet_eligible") or not row.get("live_eligible"):
                continue
            candidate = _hp_from_row(row, acct.min_backtest_trades)
            if not candidate.is_live_eligible:
                assessment.notes.append(
                    f"strategy {row.get('strategy_id')} is flagged live-eligible in "
                    f"the database but fails the recomputed bar "
                    f"({candidate.summary()}) - not used")
                continue
            strategy = ctx.registry.get(str(row.get("strategy_id") or ""))
            if (strategy is not None and lean is not Direction.NEUTRAL
                    and lean not in strategy.allowed_directions):
                continue
            chosen, hp = row, candidate
            if strategy is not None:
                assessment.strategy_name = strategy.name
                assessment.strategy_group = strategy.group
            break

        assessment.strategy = chosen
        assessment.historical = hp
        if chosen is not None:
            assessment.strategy_timeframe = (int(chosen["timeframe"])
                                             if chosen.get("timeframe") else None)
            assessment.strategy_name = assessment.strategy_name or str(
                chosen.get("strategy_id") or "")
            assessment.add(
                "backtest", "strategy_edge", value=str(chosen.get("strategy_id")),
                timeframe=assessment.strategy_timeframe,
                detail=(f"{assessment.strategy_name or chosen.get('strategy_id')} "
                        f"[{chosen.get('regime')}/{chosen.get('session')}]: "
                        f"{hp.summary()}"),
                supports=lean, weight=1.0 + min(1.0, hp.robustness_score),
                source="strategy_performance")
        else:
            eligible_seen = len([r for r in rows if r.get("live_eligible")
                                 and not r.get("_not_yet_eligible")])
            detail = ("no strategy performance rows exist for this symbol and "
                      "regime at all" if not rows else
                      f"{len(rows)} strategy row(s) available, "
                      f"{eligible_seen} flagged live-eligible, none of which "
                      "clears the out-of-sample bar for this direction")
            assessment.add("backtest", "strategy_edge", value=None, detail=detail,
                           supports=Direction.NEUTRAL, weight=2.0,
                           source="strategy_performance")
        assessment.gate(
            "strategy_edge", chosen is not None,
            "matched to a strategy with a measured out-of-sample edge"
            if chosen is not None else
            "no strategy with a demonstrated out-of-sample edge covers this "
            "setup - an unmeasured edge is not an edge")

    def _independence_evidence(self, assessment: _Assessment,
                               lean: Direction) -> None:
        """Are the agreeing views independent, or one observation restated?"""
        supporters = [v for v in assessment.present_views if v.direction is lean
                      and lean is not Direction.NEUTRAL]
        families: List[str] = sorted({f for v in supporters for f in v.families()})
        assessment.families = families

        worst = 0.0
        pair = ""
        for i, a in enumerate(supporters):
            for b in supporters[i + 1:]:
                text_a = _tokens(" ".join(
                    [a.prediction.primary_reason, *a.prediction.supporting_confluences]))
                text_b = _tokens(" ".join(
                    [b.prediction.primary_reason, *b.prediction.supporting_confluences]))
                score = _overlap(text_a, text_b)
                if score > worst:
                    worst, pair = score, f"{a.letter}/{b.letter}"
        assessment.restatement = round(worst, 3)

        detail = (f"agreeing evidence spans {len(families)} independent "
                  f"family/families ({', '.join(families) or 'none'})")
        if worst >= 0.5:
            detail += (f"; analysts {pair} restate substantially the same "
                       f"observation (reason overlap {worst:.0%})")
            assessment.conflicts.append(
                f"analysts {pair} are not independent evidence "
                f"({worst:.0%} overlap in their stated reasons)")
        if any(not v.prediction.evidence for v in supporters):
            detail += ("; family attributed from the analyst's mandate where no "
                       "evidence records were attached")
        assessment.add("statistic", "evidence_independence", value=len(families),
                       detail=detail, supports=lean if len(families) >= 2 else
                       Direction.NEUTRAL, weight=float(len(families) or 1))
        assessment.gate(
            "independent_confluence",
            len(families) >= _MIN_FAMILIES_FOR_TRADE and worst < 0.75,
            f"{len(families)} independent evidence family/families supporting "
            f"the lean (need {_MIN_FAMILIES_FOR_TRADE}); peak restatement "
            f"overlap {worst:.0%}", hard=False)

    def _reward_evidence(self, assessment: _Assessment) -> None:
        setup = assessment.setup
        acct = assessment.account
        spec = assessment.spec
        if setup is None:
            assessment.add(
                "statistic", "reward_vs_risk", value=None,
                detail="no priced setup could be assembled from the analyst "
                       "levels, so reward and risk are unquantified",
                supports=Direction.NEUTRAL, weight=2.0)
            assessment.gate("priced_setup", False,
                            "; ".join(assessment.soft_blocks) or
                            "no priced setup available")
            assessment.gate("reward_after_costs", False,
                            "reward/risk cannot be assessed without a setup")
            assessment.gate("stop_distance", False,
                            "stop distance cannot be assessed without a setup")
            return

        assessment.gate("priced_setup", True,
                        f"entry {fmt_price(setup.entry)}, stop "
                        f"{fmt_price(setup.stop)}, targets "
                        f"{', '.join(fmt_price(t) for t in setup.targets)} "
                        f"(from analyst(s) {', '.join(setup.contributors)})")
        assessment.add(
            "statistic", "reward_vs_risk", value=round(setup.rr_after_costs, 3),
            detail=(f"{setup.risk_points:g} points of risk "
                    f"(${setup.risk_per_contract:,.2f}/contract); first target "
                    f"{setup.first_target_rr:.2f}R gross, "
                    f"{setup.rr_after_costs:.2f}R after ${setup.cost_per_contract:,.2f} "
                    f"round-turn costs; final target {setup.reward_risk:.2f}R"),
            supports=setup.direction if setup.rr_after_costs >= 1.0 else Direction.NEUTRAL,
            weight=1.5)
        assessment.gate(
            "stop_distance", setup.stop_ticks >= spec.min_stop_ticks,
            f"stop is {setup.stop_ticks:.0f} ticks against {assessment.symbol}'s "
            f"{spec.min_stop_ticks}-tick noise floor")
        adequate = (setup.rr_after_costs >= 1.0
                    and setup.reward_risk >= acct.min_reward_risk)
        assessment.gate(
            "reward_after_costs", adequate,
            f"first target returns {setup.rr_after_costs:.2f}R after costs "
            f"(floor 1.00R) and the final target {setup.reward_risk:.2f}R "
            f"(floor {acct.min_reward_risk:.2f}R)")
        if not adequate:
            assessment.conflicts.append(
                f"reward/risk after costs ({setup.rr_after_costs:.2f}R) does not "
                "justify the risk")
        if assessment.price is not None and assessment.atr:
            away = abs(setup.entry - assessment.price) / assessment.atr
            if away > 1.5:
                assessment.notes.append(
                    f"entry sits {away:.1f} ATR from the last price "
                    f"({fmt_price(assessment.price)}) - this is a pending level, "
                    "not a live fill")

    def _session_evidence(self, assessment: _Assessment) -> None:
        """Is this session and time of day historically favourable here?"""
        ctx = self.require_context()
        session = assessment.session or "UNKNOWN"
        row = None
        if assessment.strategy:
            sid = str(assessment.strategy.get("strategy_id") or "")
            row = (ctx.storage.strategy_performance(
                sid, regime=assessment.regime_name, session=session)
                or ctx.storage.strategy_performance(sid, regime="ALL", session=session))
        live = ctx.storage.journal_stats(symbol=assessment.symbol, session=session)

        if row and (row.get("trades") or 0) >= 20:
            detail = (f"{assessment.strategy_name or row.get('strategy_id')} in "
                      f"{session}: {row['trades']} trades, "
                      f"{(row.get('win_rate') or 0) * 100:.0f}% win, expectancy "
                      f"{(row.get('expectancy_r') or 0):+.3f}R")
            supports = (assessment.lean.as_direction
                        if (row.get("expectancy_r") or 0) > 0 else Direction.NEUTRAL)
            weight = 1.2
        elif live["trades"] >= 10:
            detail = (f"live journal for {assessment.symbol} in {session}: "
                      f"{live['trades']} resolved trades, "
                      f"{live['win_rate'] * 100:.0f}% win, {live['avg_r']:+.2f}R average")
            supports = (assessment.lean.as_direction if live["avg_r"] > 0
                        else Direction.NEUTRAL)
            weight = 1.0
        else:
            detail = (f"no measured record for the {session} session "
                      f"({live['trades']} resolved live trades, "
                      f"{(row or {}).get('trades', 0)} backtested) - this session "
                      "is neither support nor objection, it is unknown")
            supports = Direction.NEUTRAL
            weight = 0.8
        assessment.add("statistic", "session_history", value=session, detail=detail,
                       supports=supports, weight=weight, source="storage")

    def _regime_evidence(self, assessment: _Assessment, lean: Direction) -> None:
        regime = assessment.regime
        trending = regime.is_trending
        detail = (f"regime {regime.value}"
                  + (f", multi-timeframe alignment {assessment.alignment:+.2f}"
                     if assessment.alignment is not None else ""))
        counter_trend = (
            (regime is MarketRegime.TREND_UP and lean is Direction.SHORT)
            or (regime is MarketRegime.TREND_DOWN and lean is Direction.LONG))
        if counter_trend:
            detail += f"; a {lean.value} here is counter-trend"
            assessment.conflicts.append(
                f"{lean.value} against a {regime.value} regime")
        group = (assessment.strategy_group or "").upper()
        row_regime = str((assessment.strategy or {}).get("regime") or "").upper()
        if group and row_regime in ("", "ALL"):
            # The row was measured across all regimes, so the fit has to be
            # inferred from the strategy's own logic rather than its record.
            if group in _TREND_GROUPS and not trending:
                assessment.conflicts.append(
                    f"{group} strategy in a {regime.value} regime, and its record "
                    "is not measured per regime")
            elif group in _REVERSION_GROUPS and trending:
                assessment.conflicts.append(
                    f"{group} strategy fading a {regime.value} regime, and its "
                    "record is not measured per regime")
        assessment.add("indicator", "regime_fit", value=regime.value, detail=detail,
                       supports=Direction.NEUTRAL if counter_trend else lean,
                       weight=1.0)

    def _volatility_evidence(self, assessment: _Assessment) -> None:
        acct = assessment.account
        ratio = (assessment.atr / assessment.atr_median
                 if assessment.atr and assessment.atr_median else None)
        within = True
        if ratio is not None:
            within = (acct.min_atr_multiple_of_median <= ratio
                      <= acct.max_atr_multiple_of_median)
        dead_or_wild = assessment.volatility in ("DEAD", "EXTREME")
        detail = (f"volatility regime {assessment.volatility}"
                  + (f", ATR {assessment.atr:.2f} = {ratio:.2f}x its median "
                     f"(band {acct.min_atr_multiple_of_median:.2f}-"
                     f"{acct.max_atr_multiple_of_median:.2f}x)" if ratio else ""))
        assessment.add("indicator", "volatility_suitability",
                       value=round(ratio, 3) if ratio else assessment.volatility,
                       detail=detail, supports=Direction.NEUTRAL,
                       weight=1.5 if (dead_or_wild or not within) else 0.8)
        assessment.gate(
            "volatility_band", within and not dead_or_wild,
            detail + ("" if within and not dead_or_wild else
                      " - outside the band where stop distance and slippage are "
                      "reliable"))
        if assessment.volatility == "HIGH":
            assessment.conflicts.append("elevated volatility widens slippage")

    def _account_evidence(self, assessment: _Assessment) -> None:
        """The account's own state can end the decision before evidence does."""
        ctx = self.require_context()
        acct = assessment.account
        mode, reasons = ctx.risk.mode(ctx.now())
        state = ctx.account
        assessment.trading_mode = mode.value
        assessment.mode_reasons = list(reasons)
        budget = state.remaining_daily_loss_budget
        buffer_left = state.usable_buffer
        detail = (f"mode {mode.value}; equity ${state.equity:,.2f}; usable risk "
                  f"buffer ${buffer_left:,.2f}; remaining daily loss budget "
                  f"${budget:,.2f}"
                  + (f"; {'; '.join(reasons)}" if reasons else ""))
        assessment.add("statistic", "account_limits", value=mode.value, detail=detail,
                       supports=Direction.NEUTRAL,
                       weight=2.0 if not mode.can_trade else 0.8, source="risk_manager")
        assessment.gate("account_permits_trading", mode.can_trade,
                        detail if not mode.can_trade else
                        f"account is in {mode.value} mode")
        assessment.gate(
            "risk_budget_available", budget >= acct.min_dollar_risk,
            f"${budget:,.2f} left in today's loss budget against a "
            f"${acct.min_dollar_risk:,.0f} minimum meaningful risk")
        if reasons:
            assessment.conflicts.extend(reasons)

    # ==================================================================
    # Confidence and conclusion
    # ==================================================================
    def _confidence(self, assessment: _Assessment) -> float:
        """Confidence in the *directional* case, if one exists.

        Honest calibration per house rule 5: the ceiling is only lifted when
        several independent kinds of evidence agree over a large measured
        sample.
        """
        if assessment.setup is None or assessment.lean is Decision.NO_TRADE:
            return 0.0
        hp = assessment.historical
        score = 0.50 + 0.35 * abs(assessment.conviction)

        if hp is not None:
            score += min(0.10, 0.06 * hp.robustness_score
                         + 0.20 * max(0.0, hp.out_of_sample_expectancy_r))
        families = len(assessment.families)
        score += 0.06 if families >= 3 else (0.02 if families == 2 else -0.08)
        if assessment.restatement >= 0.5:
            score -= 0.05
        score -= 0.06 * (len(_ANALYSTS) - len(assessment.present_views))
        if assessment.news_risk is NewsRisk.HIGH:
            score -= 0.08
        elif assessment.news_risk is NewsRisk.MODERATE:
            score -= 0.03
        if not assessment.news_known:
            score -= 0.04
        if assessment.equal_weighted:
            # Not knowing which analyst to trust is itself a reason for caution.
            score -= 0.05
        score -= min(0.12, 0.03 * len(assessment.conflicts))
        if assessment.volatility == "HIGH":
            score -= 0.04
        if assessment.volume in ("THIN", "BELOW_AVERAGE"):
            score -= 0.05
        if assessment.setup.rr_after_costs < 1.3:
            score -= 0.04

        deep = (families >= 3 and hp is not None and hp.trades >= 100
                and hp.out_of_sample_trades >= 30
                and len(assessment.present_views) == len(_ANALYSTS))
        return Confidence(min(score, 1.0 if deep else _CONFIDENCE_CAP))

    @staticmethod
    def _standaside_confidence(assessment: _Assessment) -> float:
        """How sure the layer is that standing aside is the right call.

        A NO TRADE carries its own confidence: several failed gates and a pile
        of conflicts make abstaining an easy call, while a marginal miss on one
        soft threshold does not.
        """
        score = 0.55 + 0.08 * len(assessment.failed_hard_gates) \
            + 0.03 * len(assessment.conflicts)
        if not assessment.present_views:
            score = max(score, 0.85)        # no evidence at all is unambiguous
        return Confidence(min(0.95, score))

    def _finalise(self, assessment: _Assessment) -> None:
        """Soft gates, confidence and the deterministic conclusion."""
        acct = assessment.account
        masses = assessment.masses
        lead = masses.get("lead", 0.0)
        opposing = masses.get("opposing", 0.0)

        assessment.gate(
            "directional_conviction",
            abs(assessment.conviction) >= _MIN_CONVICTION,
            f"weighted conviction {assessment.conviction:+.2f} against a "
            f"{_MIN_CONVICTION:.2f} floor", hard=False)
        material_disagreement = opposing > _MAX_OPPOSING_SHARE * lead if lead > 0 else True
        assessment.gate(
            "analysts_not_opposed", not material_disagreement,
            f"opposing weighted mass {opposing:.2f} against {lead:.2f} for the "
            "lean - mixed evidence rarely justifies risking capital"
            if material_disagreement else
            f"opposing weighted mass {opposing:.2f} of {lead:.2f} for the lean",
            hard=False)
        if material_disagreement and opposing > 0:
            assessment.conflicts.append(
                "analysts genuinely disagree on direction")

        assessment.confidence = self._confidence(assessment)
        assessment.gate(
            "confidence_floor", assessment.confidence >= acct.min_confidence,
            f"confidence {assessment.confidence:.2f} against the "
            f"{acct.min_confidence:.2f} floor", hard=False)

        if assessment.failed_gates:
            assessment.lean = Decision.NO_TRADE

    # ==================================================================
    # The reasoning model, on top of the measured evidence
    # ==================================================================
    def _llm_evidence(self, assessment: _Assessment) -> Dict[str, Any]:
        hp = assessment.historical
        return {
            "as_of_et": assessment.stamp,
            "symbol": assessment.symbol,
            "market": {
                "price": assessment.price,
                "regime": assessment.regime.value,
                "volatility_regime": assessment.volatility,
                "volume_regime": assessment.volume,
                "session": assessment.session,
                "time_bucket": assessment.time_bucket,
                "is_rth": assessment.is_rth,
                "atr": assessment.atr,
                "atr_median": assessment.atr_median,
                "multi_timeframe_alignment": assessment.alignment,
            },
            "analysts": [v.to_dict() for v in assessment.views],
            "analyst_weighting_basis": (
                "equal weights - no analyst has >= 20 scored predictions in this "
                "regime" if assessment.equal_weighted else
                "weights derived from measured accuracy in this regime"),
            "weighted_conviction": round(assessment.conviction, 4),
            "weighted_agreement": assessment.agreement_score,
            "evidence_families_supporting_lean": assessment.families,
            "peak_reason_overlap_between_supporters": assessment.restatement,
            "news": {
                "known": assessment.news_known,
                "risk": assessment.news_risk.value,
                "macro_bias": assessment.news_bias.value,
                "minutes_to_next_high_impact": assessment.minutes_to_news,
                "summary": assessment.news_summary,
            },
            "strategy": {
                "selected": assessment.strategy,
                "historical_performance": hp.to_dict() if hp else None,
                "candidates_considered": len(assessment.strategy_rows),
            },
            "priced_setup": assessment.setup.to_dict() if assessment.setup else None,
            "account": {
                "trading_mode": assessment.trading_mode,
                "mode_reasons": assessment.mode_reasons,
                "equity": self.require_context().account.equity,
                "remaining_daily_loss_budget":
                    self.require_context().account.remaining_daily_loss_budget,
                "usable_risk_buffer": self.require_context().account.usable_buffer,
            },
            "deterministic_lean": assessment.lean.value,
            "deterministic_confidence": round(assessment.confidence, 3),
            "conflicts": assessment.conflicts,
            "notes": assessment.notes,
            "gates": [g.to_dict() for g in assessment.gates],
            "failed_hard_gates": [g.name for g in assessment.failed_hard_gates],
            "evidence_chain": [e.to_dict() for e in assessment.evidence],
        }

    def _apply_llm(self, assessment: _Assessment
                   ) -> Tuple[Decision, float, str, Optional[Dict[str, Any]]]:
        """Let the model weigh the same evidence. It cannot open a hard gate."""
        deterministic = (assessment.lean,
                         assessment.confidence if assessment.lean.is_actionable
                         else self._standaside_confidence(assessment),
                         "deterministic", None)
        if not self.llm_available:
            return deterministic

        response = self.reason(
            system=self.system_prompt(_ROLE_PROMPT),
            evidence=self._llm_evidence(assessment),
            question=_LLM_QUESTION,
            schema=_LLM_SCHEMA,
        )
        if response is None or not response.ok or not isinstance(response.parsed, dict):
            reason = (response.error if response is not None
                      else "no client") or "no structured output"
            assessment.notes.append(f"model review unavailable ({reason}) - "
                                    "deterministic result stands")
            return deterministic

        parsed: Dict[str, Any] = response.parsed
        model_decision = Decision.coerce(parsed.get("decision"))
        model_confidence = Confidence(parsed.get("confidence"))
        for conflict in parsed.get("key_conflicts") or []:
            text = str(conflict).strip()
            if text:
                assessment.conflicts.append(f"{text} [model]")

        hard_failed = [g.name for g in assessment.failed_hard_gates]
        if model_decision.is_actionable:
            if hard_failed:
                assessment.notes.append(
                    f"the model argued for {model_decision.value}; hard gates "
                    f"({', '.join(hard_failed)}) failed and are not negotiable")
                return Decision.NO_TRADE, self._standaside_confidence(assessment), \
                    "hybrid", parsed
            if assessment.setup is None:
                assessment.notes.append(
                    f"the model argued for {model_decision.value} but no priced "
                    "setup exists and this layer does not originate levels")
                return Decision.NO_TRADE, self._standaside_confidence(assessment), \
                    "hybrid", parsed
            if assessment.setup.direction is not model_decision.as_direction:
                assessment.notes.append(
                    f"the model argued for {model_decision.value} against a priced "
                    f"{assessment.setup.direction.value} setup; the opposite "
                    "direction has no levels from any analyst, so the call is "
                    "NO TRADE rather than an invented one")
                return Decision.NO_TRADE, self._standaside_confidence(assessment), \
                    "hybrid", parsed
            if assessment.lean is Decision.NO_TRADE:
                assessment.notes.append(
                    "the model overrode the deterministic abstention; every hard "
                    "gate had passed, so the soft thresholds were judgement calls")
            confidence = _clamp(model_confidence,
                                max(0.0, assessment.confidence - _LLM_CONFIDENCE_LATITUDE),
                                min(1.0, assessment.confidence + _LLM_CONFIDENCE_LATITUDE)) \
                if assessment.confidence > 0 else model_confidence
            if confidence < assessment.account.min_confidence:
                assessment.notes.append(
                    f"model confidence {confidence:.2f} sits below the "
                    f"{assessment.account.min_confidence:.2f} floor")
                return Decision.NO_TRADE, self._standaside_confidence(assessment), \
                    "hybrid", parsed
            return model_decision, confidence, "hybrid", parsed

        if assessment.lean.is_actionable:
            assessment.notes.append(
                "the model rejected the deterministic lean and called NO TRADE")
        stand_aside = self._standaside_confidence(assessment)
        confidence = max(model_confidence, stand_aside) if model_confidence else stand_aside
        return Decision.NO_TRADE, confidence, "hybrid", parsed

    # ==================================================================
    # Assembly
    # ==================================================================
    def _assess(self, symbol: str, stamp: str) -> _Assessment:
        """The deterministic pass. Always runs, always produces a usable result."""
        ctx = self.require_context()
        spec = get_contract(symbol)
        account_cfg = getattr(ctx.config, "account", None) or AccountConfig()
        assessment = _Assessment(symbol=symbol, stamp=stamp, spec=spec,
                                 account=account_cfg)

        snap = ctx.snapshot(symbol)
        if snap is not None:
            assessment.regime_name = snap.regime.regime
            assessment.regime = _coerce_enum(MarketRegime, snap.regime.regime,
                                             MarketRegime.UNKNOWN)
            assessment.volatility = snap.regime.volatility
            assessment.volume = snap.regime.volume
            assessment.session = snap.session
            assessment.time_bucket = snap.time_bucket
            assessment.is_rth = snap.is_rth
            assessment.price = snap.price
            assessment.atr = snap.regime.atr
            assessment.atr_median = snap.regime.atr_median
            assessment.alignment = round(snap.alignment(), 3)
        assessment.gate(
            "market_snapshot", snap is not None,
            (f"snapshot at {fmt_price(assessment.price)}, {assessment.regime_name}, "
             f"{assessment.session} session" if snap is not None else
             "no feature snapshot for this symbol - insufficient history to "
             "assess anything"))

        assessment.views = self._gather_views(symbol, assessment.regime_name)
        self._read_news(assessment)
        self._load_strategies(assessment)
        self._score(assessment)

        if assessment.lean.is_actionable:
            setup, problems = self._build_setup(assessment, assessment.lean.as_direction)
            assessment.setup = setup
            if setup is None:
                assessment.soft_blocks = problems
            else:
                assessment.conflicts.extend(problems)
        else:
            assessment.soft_blocks = ["no directional lean to price"]

        self._evaluate(assessment)
        self._finalise(assessment)
        return assessment

    def _proposal(self, assessment: _Assessment,
                  confidence: float) -> Optional[TradeProposal]:
        setup = assessment.setup
        if setup is None:
            return None
        return TradeProposal(
            symbol=assessment.symbol,
            direction=setup.direction,
            entry=setup.entry,
            stop=setup.stop,
            targets=list(setup.targets),
            confidence=confidence,
            strategy_id=str((assessment.strategy or {}).get("strategy_id") or ""),
            strategy_name=assessment.strategy_name,
            timeframe=int(assessment.strategy_timeframe or 0),
            regime=assessment.regime.value,
            volatility=assessment.volatility,
            session=assessment.session,
            news_risk=assessment.news_risk,
            minutes_to_high_impact=assessment.minutes_to_news,
            historical=assessment.historical,
            atr=assessment.atr,
            atr_median=assessment.atr_median,
            analyst_agreement=assessment.agreement_score,
        )

    @staticmethod
    def _proposal_dict(proposal: Optional[TradeProposal]) -> Optional[Dict[str, Any]]:
        """Exactly the fields ``TradeProposal(**d)`` takes - nothing else.

        Derived figures live beside it under ``setup``; adding them here would
        make the dict un-constructable, which is the one thing it is for.
        """
        if proposal is None:
            return None
        return {f.name: _jsonable(getattr(proposal, f.name))
                for f in dataclass_fields(TradeProposal)}

    def _build_callout(self, assessment: _Assessment, decision: Decision,
                       confidence: float, source: str,
                       model: Optional[Dict[str, Any]]) -> TradeCallout:
        ctx = self.require_context()
        state = ctx.account
        hp = assessment.historical
        setup = assessment.setup if decision.is_actionable else None

        timeframe = (f"{assessment.strategy_timeframe}m"
                     if assessment.strategy_timeframe else "")
        if not timeframe:
            horizons = [v.prediction.time_horizon for v in assessment.present_views
                        if v.prediction.time_horizon]
            timeframe = "; ".join(sorted(set(horizons))) or "-"

        supporters = [v for v in assessment.present_views
                      if setup is not None and v.direction is setup.direction]
        reasons = [v.prediction.primary_reason for v in supporters
                   if v.prediction.primary_reason]
        if hp is not None and assessment.strategy:
            reasons.append(f"strategy {assessment.strategy_name or assessment.strategy.get('strategy_id')}: "
                           f"{hp.summary()}")
        invalidations: List[str] = []
        for v in supporters:
            invalidations.extend(v.prediction.invalidation_conditions)
        invalidation_text = ""
        if setup is not None:
            invalidation_text = f"stop {fmt_price(setup.stop)}"
            extra = [i for i in dict.fromkeys(invalidations) if i][:3]
            if extra:
                invalidation_text += "; " + "; ".join(extra)

        avoid = list(dict.fromkeys(assessment.conflicts))
        for gate in assessment.failed_gates:
            avoid.append(f"gate {gate.name}: {gate.detail}")
        if model and str(model.get("reason_to_avoid") or "").strip():
            avoid.append(f"{str(model['reason_to_avoid']).strip()} [model]")

        callout = TradeCallout(
            timestamp_et=ctx.now().isoformat(),
            symbol=assessment.symbol,
            decision=decision,
            entry=setup.entry if setup else None,
            entry_zone=setup.entry_zone if setup else None,
            stop_loss=setup.stop if setup else None,
            targets=list(setup.targets) if setup else [],
            expected_reward_risk=round(setup.reward_risk, 3) if setup else None,
            # contracts, dollar_risk, account_risk_pct and risk_assessment are
            # deliberately left at their defaults: the risk agent sizes this
            # proposal and may veto it outright.
            strategy=assessment.strategy_name or str(
                (assessment.strategy or {}).get("strategy_id") or ""),
            strategy_group=assessment.strategy_group,
            timeframe=timeframe,
            market_regime=assessment.regime,
            news_risk=assessment.news_risk,
            historical_win_rate=hp.win_rate if hp else 0.0,
            historical_expectancy_r=hp.expectancy_r if hp else 0.0,
            max_historical_drawdown_r=hp.max_drawdown_r if hp else 0.0,
            analyst_agreement=assessment.agreement_text,
            confidence=confidence,
            trade_invalidation=invalidation_text,
            reason_for_entry="; ".join(dict.fromkeys(reasons))[:1200],
            reason_to_avoid="; ".join(avoid)[:1500],
            remaining_drawdown_buffer=state.usable_buffer,
            remaining_daily_loss_budget=state.remaining_daily_loss_budget,
            account_equity=state.equity,
            evidence_chain=list(assessment.evidence),
            analyst_predictions=[v.prediction for v in assessment.present_views],
            decision_rationale=self._rationale(assessment, decision, confidence,
                                               source, model),
        )
        return callout

    def _rationale(self, assessment: _Assessment, decision: Decision,
                   confidence: float, source: str,
                   model: Optional[Dict[str, Any]]) -> str:
        """The traceable chain: what was read, how it was weighed, what decided it."""
        a = assessment
        hp = a.historical
        lines = [f"[{a.stamp}] {a.symbol} - {decision.value} "
                 f"(confidence {confidence:.2f}, {source})"]

        read = [f"analyst {v.letter}: "
                + (f"{v.direction.value} @ conf {v.confidence:.2f}" if v.present
                   else "no prediction published")
                for v in a.views]
        read.append("news_context: " + (a.news_summary if a.news_known
                                        else "not published"))
        read.append(f"strategy rankings: {len(a.strategy_rows)} row(s) for "
                    f"{a.regime_name}")
        lines.append("1. Evidence read - " + "; ".join(read) + ".")

        weighting = "; ".join(f"{v.letter} weight {v.weight:.2f} ({v.weight_basis})"
                              for v in a.views)
        lines.append(
            "2. Analyst weighting (never a head count) - " + weighting
            + (". No analyst has the ~20 scored predictions needed for a measured "
               "record in this regime, so weights fall back to equal and no track "
               "record is claimed." if a.equal_weighted else
               ". Weights come from measured accuracy in this regime, so an "
               "analyst agreeing with the others adds nothing if it has been "
               "wrong here."))

        lines.append(
            f"3. Confluence - {a.agreement_text}. Independent evidence families "
            f"behind the lean: {', '.join(a.families) or 'none'}"
            + (f"; peak reason overlap between supporters "
               f"{a.restatement:.0%}." if a.restatement else "."))

        if hp is not None and a.strategy:
            lines.append(
                f"4. Strategy edge - {a.strategy_name or a.strategy.get('strategy_id')} "
                f"[{a.strategy.get('regime')}/{a.strategy.get('session')}]: "
                f"{hp.summary()}.")
        else:
            lines.append("4. Strategy edge - none: no strategy with a demonstrated "
                         "out-of-sample edge covers this setup.")

        if a.setup is not None:
            s = a.setup
            lines.append(
                f"5. Reward against risk - entry {fmt_price(s.entry)}, stop "
                f"{fmt_price(s.stop)} ({s.stop_ticks:.0f} ticks, "
                f"${s.risk_per_contract:,.2f}/contract), targets "
                f"{', '.join(fmt_price(t) for t in s.targets)}; first target "
                f"{s.rr_after_costs:.2f}R after ${s.cost_per_contract:,.2f} costs, "
                f"final {s.reward_risk:.2f}R. Levels come from analyst(s) "
                f"{', '.join(s.contributors)} - this layer prices nothing itself, "
                "and combines the widest stop with the nearest first target so the "
                "figure cannot flatter the trade.")
        else:
            lines.append("5. Reward against risk - unpriced: "
                         + ("; ".join(a.soft_blocks) or "no setup"))

        lines.append(
            f"6. Context - {a.regime.value} regime, volatility {a.volatility}, "
            f"volume {a.volume}, {a.session or 'unknown'} session"
            f"{'' if a.is_rth else ' (outside RTH)'}; news risk "
            f"{a.news_risk.value}"
            + ("" if a.news_known else " (unknown - nothing published)")
            + f"; account {a.trading_mode}, "
            f"${self.require_context().account.remaining_daily_loss_budget:,.2f} of "
            "today's loss budget left.")

        passed = [g.name for g in a.gates if g.passed]
        failed = [f"{g.name} ({'hard' if g.hard else 'soft'}): {g.detail}"
                  for g in a.failed_gates]
        lines.append(f"7. Gates - passed: {', '.join(passed) or 'none'}. "
                     f"Failed: {'; '.join(failed) or 'none'}.")

        if a.conflicts:
            lines.append("8. Conflicting signals - " + "; ".join(
                dict.fromkeys(a.conflicts)) + ".")
        if a.notes:
            lines.append("9. Notes - " + "; ".join(a.notes) + ".")
        if model:
            lines.append("10. Model review - " + str(model.get("rationale") or "")[:1500])

        if decision.is_actionable:
            lines.append(
                f"Conclusion: {decision.value}. Sizing, dollar risk and the effect "
                "on the remaining drawdown buffer are the risk agent's to set, and "
                "its veto stands over this call.")
        else:
            lead = (failed[0] if failed else "the evidence does not justify risking capital")
            lines.append(
                f"Conclusion: NO TRADE - {lead}. A missed opportunity costs "
                "nothing; a low-quality trade costs capital.")
        return "\n".join(lines)

    # ==================================================================
    # Task: decide
    # ==================================================================
    def _decide(self, task: Task) -> AgentResult:
        """LONG, SHORT or NO TRADE for one symbol.

        A NO TRADE conclusion is a completed task, not a failed one: this
        method returns ``ok=True`` whichever of the three it reaches, and only
        raises when it genuinely cannot do the work (no context, unknown
        symbol).
        """
        ctx = self.require_context()
        symbol = self._symbol_for(task)
        stamp = et_stamp(ctx.now())
        self.log(f"[{stamp}] deciding {symbol}")

        assessment = self._assess(symbol, stamp)
        decision, confidence, source, model = self._apply_llm(assessment)
        callout = self._build_callout(assessment, decision, confidence, source, model)
        ctx.storage.record_callout(callout)

        proposal = self._proposal(assessment, confidence) if decision.is_actionable else None
        headline = self._headline(assessment, decision, callout)
        summary = (f"[{stamp}] {symbol} {decision.value} "
                   f"(confidence {confidence:.2f}) - {headline}")

        payload: Dict[str, Any] = {
            "timestamp_et": stamp,
            "symbol": symbol,
            "decision": decision.value,
            "actionable": decision.is_actionable,
            "confidence": round(confidence, 4),
            "source": source,
            "callout_id": callout.callout_id,
            "headline": headline,
            "analyst_agreement": assessment.agreement_text,
            "agreement_score": assessment.agreement_score,
            "weighted_conviction": round(assessment.conviction, 4),
            "equal_weighted": assessment.equal_weighted,
            "analysts": [v.to_dict() for v in assessment.views],
            "market": {
                "price": assessment.price, "regime": assessment.regime.value,
                "volatility": assessment.volatility, "volume": assessment.volume,
                "session": assessment.session, "is_rth": assessment.is_rth,
                "atr": assessment.atr, "atr_median": assessment.atr_median,
            },
            "news_risk": assessment.news_risk.value,
            "news_known": assessment.news_known,
            "strategy": assessment.strategy,
            "historical_performance": (assessment.historical.to_dict()
                                       if assessment.historical else None),
            "setup": assessment.setup.to_dict() if assessment.setup else None,
            "trade_proposal": self._proposal_dict(proposal),
            "trade_proposal_shape": (
                "keyword arguments for futures_agents.risk.manager.TradeProposal; "
                "coerce direction with Direction.coerce, news_risk with "
                "NewsRisk(...) and historical with HistoricalPerformance(**d)"),
            "gates": [g.to_dict() for g in assessment.gates],
            "failed_hard_gates": [g.name for g in assessment.failed_hard_gates],
            "conflicts": list(dict.fromkeys(assessment.conflicts)),
            "notes": assessment.notes,
            "evidence_chain": [e.to_dict() for e in assessment.evidence],
            "model_review": model,
            "decision_rationale": callout.decision_rationale,
            "callout": callout.to_dict(),
        }

        artefacts = [
            self.publish("decision", payload, summary),
            self.publish("callout", callout.to_dict(), summary),
        ]
        self.log(callout.decision_rationale)
        return AgentResult(ok=True, summary=summary, payload=payload,
                           artefacts=artefacts)

    @staticmethod
    def _headline(assessment: _Assessment, decision: Decision,
                  callout: TradeCallout) -> str:
        if decision.is_actionable and assessment.setup is not None:
            s = assessment.setup
            return (f"entry {fmt_price(s.entry)}, stop {fmt_price(s.stop)}, "
                    f"targets {', '.join(fmt_price(t) for t in s.targets)}, "
                    f"{s.rr_after_costs:.2f}R after costs; awaiting risk sizing")
        failed = assessment.failed_gates
        if failed:
            return failed[0].detail
        if not assessment.present_views:
            return "no analyst predictions published"
        return "the evidence does not justify risking capital"

    # ==================================================================
    # Task: review_setup
    # ==================================================================
    def _review_setup(self, task: Task) -> AgentResult:
        """Re-test a setup that already exists against the current evidence.

        Publishes ``decision`` in review mode and deliberately does *not*
        republish ``callout``: a review is an opinion about an existing call,
        and overwriting the live callout with it would lose the thing being
        reviewed.
        """
        ctx = self.require_context()
        symbol = self._symbol_for(task)
        stamp = et_stamp(ctx.now())
        assessment = self._assess(symbol, stamp)
        reviewed, origin = self._setup_under_review(task, symbol)
        verdict, reasons = self._verdict(assessment, reviewed)

        summary = (f"[{stamp}] {symbol} setup review: {verdict} "
                   f"({origin}) - {reasons[0] if reasons else 'no change'}")
        payload = {
            "timestamp_et": stamp,
            "symbol": symbol,
            "mode": "review",
            "verdict": verdict,
            "reviewed_setup": reviewed,
            "reviewed_from": origin,
            "reasons": reasons,
            "current_lean": assessment.lean.value,
            "current_confidence": round(assessment.confidence, 4),
            "analyst_agreement": assessment.agreement_text,
            "agreement_score": assessment.agreement_score,
            "gates": [g.to_dict() for g in assessment.gates],
            "failed_hard_gates": [g.name for g in assessment.failed_hard_gates],
            "conflicts": list(dict.fromkeys(assessment.conflicts)),
            "evidence_chain": [e.to_dict() for e in assessment.evidence],
            "setup": assessment.setup.to_dict() if assessment.setup else None,
        }
        artefacts = [self.publish("decision", payload, summary)]
        self.log(summary)
        return AgentResult(ok=True, summary=summary, payload=payload,
                           artefacts=artefacts)

    def _setup_under_review(self, task: Task,
                            symbol: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """The setup being reviewed: from the task, our last callout, or storage."""
        for key in ("setup", "callout", "proposal", "trade_proposal", "decision"):
            found = _find_payload(task.payload.get(key), "direction", depth=3)
            if found is not None:
                return found, f"task payload ({key})"
        own = self.workspace.read_json("out/callout.json")
        found = _find_payload(own, "decision", depth=2)
        if found is not None and str(found.get("symbol") or "").upper() == symbol:
            return found, "our own last published callout"
        recent = self.require_context().storage.recent_callouts(symbol=symbol, limit=1)
        if recent:
            return recent[0].get("payload") or None, "the callout database"
        return None, "nothing on record"

    def _verdict(self, assessment: _Assessment,
                 reviewed: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
        if not reviewed:
            return "NO SETUP ON RECORD", [
                f"no prior setup could be found for {assessment.symbol}"]

        direction = Direction.coerce(reviewed.get("direction")
                                     or reviewed.get("decision"))
        reasons: List[str] = []
        if direction is Direction.NEUTRAL:
            return "NOT A TRADE", ["the setup under review is itself a NO TRADE"]

        stop = _num(reviewed.get("stop") or reviewed.get("stop_loss"))
        price = assessment.price
        if stop is not None and price is not None:
            if (direction is Direction.LONG and price <= stop) or \
                    (direction is Direction.SHORT and price >= stop):
                reasons.append(f"price {fmt_price(price)} has traded through the "
                               f"stop at {fmt_price(stop)}")
                return "INVALIDATED", reasons

        hard_failed = assessment.failed_hard_gates
        if hard_failed:
            reasons.extend(f"{g.name}: {g.detail}" for g in hard_failed)
            return "INVALIDATED", reasons

        if assessment.lean.is_actionable and assessment.lean.as_direction is not direction:
            reasons.append(
                f"the current weighted evidence leans {assessment.lean.value} "
                f"against a {direction.value} setup ({assessment.agreement_text})")
            return "INVALIDATED", reasons

        soft_failed = [g for g in assessment.failed_gates if not g.hard]
        if soft_failed:
            reasons.extend(f"{g.name}: {g.detail}" for g in soft_failed)
            return "DOWNGRADED", reasons

        reasons.append(f"the evidence still supports {direction.value}: "
                       f"{assessment.agreement_text}")
        if assessment.conflicts:
            reasons.append("open conflicts: " + "; ".join(
                dict.fromkeys(assessment.conflicts)))
        return "CONFIRMED", reasons
