"""Standardised data contracts shared by every agent.

Agents never exchange free-form prose. They exchange the structures in this
module, which are JSON-serialisable, schema-checked and rendered into a fixed
text block. That gives three things the system depends on:

* objective comparison of predictions that disagree,
* a complete audit trail - every callout traces back to the exact evidence,
* a stable target for the LLM agents' structured outputs.

Every structure carries a timestamp in Eastern Time.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .timeutil import et_stamp, now_et, to_et

__all__ = [
    "Direction", "Decision", "MarketRegime", "VolatilityRegime", "VolumeRegime",
    "NewsRisk", "Confidence", "SignalStrength",
    "Evidence", "HistoricalPerformance", "NewsContext", "NewsEvent", "NewsReaction",
    "AgentSignal", "AnalystPrediction", "RiskAssessment", "TradeCallout",
    "JournalEntry", "StrategyStats",
    "to_json", "from_json", "fmt_price", "fmt_prices",
]


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"

    @property
    def sign(self) -> int:
        return {Direction.LONG: 1, Direction.SHORT: -1, Direction.NEUTRAL: 0}[self]

    @classmethod
    def coerce(cls, value: Any) -> "Direction":
        if isinstance(value, cls):
            return value
        s = str(value or "").strip().upper()
        if s in ("LONG", "BUY", "L", "1", "UP", "BULLISH"):
            return cls.LONG
        if s in ("SHORT", "SELL", "S", "-1", "DOWN", "BEARISH"):
            return cls.SHORT
        return cls.NEUTRAL


class Decision(str, Enum):
    """The three legitimate outcomes of the final decision layer.

    ``NO_TRADE`` is a first-class result, not a failure to produce one.
    """

    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO TRADE"

    @classmethod
    def coerce(cls, value: Any) -> "Decision":
        if isinstance(value, cls):
            return value
        s = str(value or "").strip().upper().replace("_", " ")
        if s in ("LONG", "BUY"):
            return cls.LONG
        if s in ("SHORT", "SELL"):
            return cls.SHORT
        return cls.NO_TRADE

    @property
    def is_actionable(self) -> bool:
        return self in (Decision.LONG, Decision.SHORT)

    @property
    def as_direction(self) -> Direction:
        return {Decision.LONG: Direction.LONG,
                Decision.SHORT: Direction.SHORT,
                Decision.NO_TRADE: Direction.NEUTRAL}[self]


class MarketRegime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    VOLATILE_EXPANSION = "VOLATILE_EXPANSION"
    COMPRESSION = "COMPRESSION"
    UNKNOWN = "UNKNOWN"

    @property
    def is_trending(self) -> bool:
        return self in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN)


class VolatilityRegime(str, Enum):
    DEAD = "DEAD"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class VolumeRegime(str, Enum):
    THIN = "THIN"
    BELOW_AVERAGE = "BELOW_AVERAGE"
    AVERAGE = "AVERAGE"
    ABOVE_AVERAGE = "ABOVE_AVERAGE"
    SURGE = "SURGE"


class NewsRisk(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    BLACKOUT = "BLACKOUT"      # inside a scheduled high-impact event window

    @property
    def blocks_entry(self) -> bool:
        return self is NewsRisk.BLACKOUT


class SignalStrength(str, Enum):
    NONE = "NONE"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


def Confidence(value: float) -> float:
    """Clamp a confidence score into [0, 1]. Values outside the range are a bug
    somewhere upstream, so they are squashed rather than propagated."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return max(0.0, min(1.0, v))


# --------------------------------------------------------------------------
# Supporting structures
# --------------------------------------------------------------------------

def fmt_price(price: Optional[float], decimals: Optional[int] = None) -> str:
    """Render a price without losing ticks.

    ``%g`` silently truncates 21850.25 to "21850.2" at six significant digits,
    which is a whole tick of error on an index future - so prices are formatted
    with explicit decimals and trailing zeros trimmed instead.
    """
    if price is None:
        return "-"
    if decimals is not None:
        return f"{float(price):.{decimals}f}"
    s = f"{float(price):.5f}".rstrip("0").rstrip(".")
    return s or "0"


def fmt_prices(prices: Sequence[float], decimals: Optional[int] = None) -> str:
    return ", ".join(fmt_price(p, decimals) for p in prices) if prices else "-"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _ts() -> str:
    return to_et(now_et()).isoformat()


@dataclass
class Evidence:
    """One traceable piece of supporting data behind a conclusion.

    Every claim an agent makes must be attached to an Evidence record so the
    final callout can be traced to the data that produced it.
    """

    kind: str                      # "indicator" | "structure" | "statistic" | "news" | "backtest"
    name: str
    value: Any = None
    timeframe: Optional[int] = None
    detail: str = ""
    supports: Direction = Direction.NEUTRAL
    weight: float = 1.0            # relative importance, >= 0
    source: str = ""               # agent id, dataset name or URL

    def to_dict(self) -> dict:
        d = asdict(self)
        d["supports"] = self.supports.value
        return d

    def __str__(self) -> str:
        tf = f"[{self.timeframe}m]" if self.timeframe else ""
        val = "" if self.value is None else f"={self.value}"
        return f"{self.name}{tf}{val} ({self.supports.value}, w={self.weight:g})"


@dataclass
class HistoricalPerformance:
    """Backtested track record for the exact strategy/symbol/context in play.

    A live callout may not be issued without one of these attached - a setup
    with no measured history has no demonstrated edge, whatever it looks like.
    """

    strategy_id: str = ""
    symbol: str = ""
    timeframe: Optional[int] = None
    regime: Optional[str] = None
    session: Optional[str] = None
    trades: int = 0
    win_rate: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    max_drawdown_r: float = 0.0
    max_consecutive_losses: int = 0
    sharpe: float = 0.0
    sortino: float = 0.0
    out_of_sample_trades: int = 0
    out_of_sample_expectancy_r: float = 0.0
    walk_forward_efficiency: float = 0.0   # OOS expectancy / IS expectancy
    robustness_score: float = 0.0          # 0-1, from the anti-overfitting suite
    sample_is_sufficient: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_live_eligible(self) -> bool:
        """Minimum bar for a strategy to influence a live decision at all."""
        return (
            self.sample_is_sufficient
            and self.trades >= 30
            and self.out_of_sample_trades >= 10
            and self.expectancy_r > 0
            and self.out_of_sample_expectancy_r > 0
            and self.robustness_score >= 0.5
        )

    def summary(self) -> str:
        return (
            f"{self.trades} trades, {self.win_rate * 100:.1f}% win, "
            f"PF {self.profit_factor:.2f}, expectancy {self.expectancy_r:+.3f}R, "
            f"maxDD {self.max_drawdown_r:.2f}R, OOS {self.out_of_sample_expectancy_r:+.3f}R, "
            f"robustness {self.robustness_score:.2f}"
        )


@dataclass
class NewsEvent:
    """A scheduled or breaking market-moving event."""

    event_id: str = field(default_factory=lambda: _new_id("news"))
    timestamp_et: str = field(default_factory=_ts)
    title: str = ""
    category: str = ""             # CPI | NFP | FOMC | GDP | PMI | GEOPOLITICAL | EARNINGS | OTHER
    impact: str = "LOW"            # LOW | MEDIUM | HIGH
    scheduled: bool = True         # False => surprise development
    release_time_et: Optional[str] = None
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None
    surprise_direction: Optional[str] = None   # ABOVE | BELOW | INLINE
    affected_symbols: List[str] = field(default_factory=list)
    source: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NewsReaction:
    """Measured market reaction to a news event, per symbol and horizon.

    This is the row type of the news -> reaction database. It is what lets the
    system answer "when this report came in above expectations, how did MNQ
    actually behave over the next 1/5/15/30/60 minutes" from data rather than
    from assumption.
    """

    event_id: str = ""
    symbol: str = ""
    category: str = ""
    surprise_direction: Optional[str] = None
    release_time_et: str = ""
    price_at_release: float = 0.0
    move_1m: float = 0.0           # points
    move_5m: float = 0.0
    move_15m: float = 0.0
    move_30m: float = 0.0
    move_60m: float = 0.0
    range_60m: float = 0.0
    atr_normalised_60m: float = 0.0
    reverted: bool = False         # closed back through the release price within 60m
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def move_for(self, horizon_minutes: int) -> float:
        return {1: self.move_1m, 5: self.move_5m, 15: self.move_15m,
                30: self.move_30m, 60: self.move_60m}.get(horizon_minutes, 0.0)


@dataclass
class NewsContext:
    """The news/macro environment as of one moment, consumed by every analyst."""

    timestamp_et: str = field(default_factory=_ts)
    risk: NewsRisk = NewsRisk.NONE
    headline_summary: str = ""
    macro_bias: Direction = Direction.NEUTRAL
    macro_bias_confidence: float = 0.0
    upcoming_events: List[NewsEvent] = field(default_factory=list)
    recent_events: List[NewsEvent] = field(default_factory=list)
    minutes_to_next_high_impact: Optional[float] = None
    historical_analogues: List[NewsReaction] = field(default_factory=list)
    cross_market: Dict[str, str] = field(default_factory=dict)   # DXY/yields/oil/overnight
    sentiment: str = "NEUTRAL"
    evidence: List[Evidence] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp_et": self.timestamp_et,
            "risk": self.risk.value,
            "headline_summary": self.headline_summary,
            "macro_bias": self.macro_bias.value,
            "macro_bias_confidence": self.macro_bias_confidence,
            "upcoming_events": [e.to_dict() for e in self.upcoming_events],
            "recent_events": [e.to_dict() for e in self.recent_events],
            "minutes_to_next_high_impact": self.minutes_to_next_high_impact,
            "historical_analogues": [r.to_dict() for r in self.historical_analogues],
            "cross_market": self.cross_market,
            "sentiment": self.sentiment,
            "evidence": [e.to_dict() for e in self.evidence],
            "sources": self.sources,
        }

    def blackout_active(self) -> bool:
        return self.risk.blocks_entry


# --------------------------------------------------------------------------
# The standard agent signal block (section 10 of the specification)
# --------------------------------------------------------------------------

@dataclass
class AgentSignal:
    """The universal message every agent publishes to shared state.

    The rendered form of this object is the fixed block quoted in the system
    specification: SYMBOL / TIMEFRAME / MARKET REGIME / DIRECTION / ENTRY /
    STOP / TARGETS / RISK-REWARD / CONFIDENCE / STRATEGY / CONFLUENCES /
    CONFLICTS / INVALIDATION / SUPPORTING DATA / HISTORICAL PERFORMANCE /
    NEWS CONTEXT / TIMESTAMP.
    """

    signal_id: str = field(default_factory=lambda: _new_id("sig"))
    agent_id: str = ""
    agent_role: str = ""
    symbol: str = ""
    timeframe: Optional[int] = None
    timeframes_considered: List[int] = field(default_factory=list)
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    volatility_regime: VolatilityRegime = VolatilityRegime.NORMAL
    volume_regime: VolumeRegime = VolumeRegime.AVERAGE
    direction: Direction = Direction.NEUTRAL
    entry: Optional[float] = None
    entry_zone: Optional[Tuple[float, float]] = None
    stop: Optional[float] = None
    targets: List[float] = field(default_factory=list)
    reward_risk: Optional[float] = None
    confidence: float = 0.0
    strategy: str = ""
    strategy_group: str = ""
    confluences: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    invalidation: str = ""
    supporting_data: List[Evidence] = field(default_factory=list)
    historical_performance: Optional[HistoricalPerformance] = None
    news_context_summary: str = ""
    time_horizon: str = ""
    primary_reason: str = ""
    reasoning: str = ""
    timestamp_et: str = field(default_factory=_ts)
    source: str = "deterministic"   # "deterministic" | "llm" | "hybrid"

    # ---- serialisation ----
    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "agent_id": self.agent_id,
            "agent_role": self.agent_role,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timeframes_considered": list(self.timeframes_considered),
            "market_regime": self.market_regime.value,
            "volatility_regime": self.volatility_regime.value,
            "volume_regime": self.volume_regime.value,
            "direction": self.direction.value,
            "entry": self.entry,
            "entry_zone": list(self.entry_zone) if self.entry_zone else None,
            "stop": self.stop,
            "targets": list(self.targets),
            "reward_risk": self.reward_risk,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "strategy_group": self.strategy_group,
            "confluences": list(self.confluences),
            "conflicts": list(self.conflicts),
            "invalidation": self.invalidation,
            "supporting_data": [e.to_dict() for e in self.supporting_data],
            "historical_performance": (
                self.historical_performance.to_dict() if self.historical_performance else None
            ),
            "news_context_summary": self.news_context_summary,
            "time_horizon": self.time_horizon,
            "primary_reason": self.primary_reason,
            "reasoning": self.reasoning,
            "timestamp_et": self.timestamp_et,
            "source": self.source,
        }

    def render_block(self) -> str:
        """The fixed-format text block used for the audit trail and LLM input."""
        tgt = fmt_prices(self.targets)
        zone = (f"{fmt_price(self.entry_zone[0])} - {fmt_price(self.entry_zone[1])}"
                if self.entry_zone else fmt_price(self.entry))
        hp = self.historical_performance.summary() if self.historical_performance else "none on record"
        rows = [
            ("SYMBOL", self.symbol),
            ("TIMEFRAME", ", ".join(f"{t}m" for t in self.timeframes_considered)
                or (f"{self.timeframe}m" if self.timeframe else "-")),
            ("MARKET REGIME", f"{self.market_regime.value} / vol {self.volatility_regime.value}"
                              f" / volume {self.volume_regime.value}"),
            ("DIRECTION", self.direction.value),
            ("ENTRY", zone),
            ("STOP", fmt_price(self.stop)),
            ("TARGETS", tgt),
            ("RISK/REWARD", f"{self.reward_risk:.2f}R" if self.reward_risk else "-"),
            ("CONFIDENCE", f"{self.confidence:.2f}"),
            ("STRATEGY", f"{self.strategy} [{self.strategy_group}]" if self.strategy_group
                         else (self.strategy or "-")),
            ("CONFLUENCES", "; ".join(self.confluences) or "-"),
            ("CONFLICTS", "; ".join(self.conflicts) or "none identified"),
            ("INVALIDATION", self.invalidation or "-"),
            ("SUPPORTING DATA", "; ".join(str(e) for e in self.supporting_data) or "-"),
            ("HISTORICAL PERFORMANCE", hp),
            ("NEWS CONTEXT", self.news_context_summary or "-"),
            ("TIMESTAMP", et_stamp(datetime.fromisoformat(self.timestamp_et))),
        ]
        width = max(len(k) for k, _ in rows)
        return "\n".join(f"{k:<{width}} : {v}" for k, v in rows)


@dataclass
class AnalystPrediction:
    """A live prediction from one of the three independent analysts.

    Analysts are not required to agree. Disagreement is retained and passed to
    the decision layer as information in its own right.
    """

    analyst_id: str = ""                   # "A" | "B" | "C"
    analyst_name: str = ""
    specialisation: str = ""
    symbol: str = ""
    direction: Direction = Direction.NEUTRAL
    entry_zone: Optional[Tuple[float, float]] = None
    stop: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    target_3: Optional[float] = None
    expected_reward_risk: Optional[float] = None
    confidence: float = 0.0
    time_horizon: str = ""
    primary_reason: str = ""
    supporting_confluences: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
    would_change_mind_if: str = ""
    evidence: List[Evidence] = field(default_factory=list)
    historical_performance: Optional[HistoricalPerformance] = None
    reasoning: str = ""
    timestamp_et: str = field(default_factory=_ts)
    source: str = "deterministic"

    @property
    def targets(self) -> List[float]:
        return [t for t in (self.target_1, self.target_2, self.target_3) if t is not None]

    def to_dict(self) -> dict:
        return {
            "analyst_id": self.analyst_id,
            "analyst_name": self.analyst_name,
            "specialisation": self.specialisation,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "entry_zone": list(self.entry_zone) if self.entry_zone else None,
            "stop": self.stop,
            "target_1": self.target_1,
            "target_2": self.target_2,
            "target_3": self.target_3,
            "expected_reward_risk": self.expected_reward_risk,
            "confidence": self.confidence,
            "time_horizon": self.time_horizon,
            "primary_reason": self.primary_reason,
            "supporting_confluences": list(self.supporting_confluences),
            "invalidation_conditions": list(self.invalidation_conditions),
            "would_change_mind_if": self.would_change_mind_if,
            "evidence": [e.to_dict() for e in self.evidence],
            "historical_performance": (
                self.historical_performance.to_dict() if self.historical_performance else None),
            "reasoning": self.reasoning,
            "timestamp_et": self.timestamp_et,
            "source": self.source,
        }

    def render(self) -> str:
        z = (f"{fmt_price(self.entry_zone[0])} - {fmt_price(self.entry_zone[1])}"
             if self.entry_zone else "-")
        rr = f"{self.expected_reward_risk:.2f}R" if self.expected_reward_risk else "-"
        rows = [
            ("Symbol", self.symbol),
            ("Direction", self.direction.value),
            ("Entry Zone", z),
            ("Stop", fmt_price(self.stop)),
            ("Target 1", fmt_price(self.target_1)),
            ("Target 2", fmt_price(self.target_2)),
            ("Target 3", fmt_price(self.target_3)),
            ("Expected Risk/Reward", rr),
            ("Confidence", f"{self.confidence:.2f}"),
            ("Time Horizon", self.time_horizon or "-"),
            ("Primary Reason", self.primary_reason or "-"),
            ("Supporting Confluences", "; ".join(self.supporting_confluences) or "-"),
            ("Invalidation Conditions", "; ".join(self.invalidation_conditions) or "-"),
            ("Would change mind if", self.would_change_mind_if or "-"),
        ]
        width = max(len(k) for k, _ in rows)
        return "\n".join(f"{k + ':':<{width + 1}} {v}" for k, v in rows)


@dataclass
class RiskAssessment:
    """Output of the independent risk layer. It can veto any trade."""

    approved: bool = False
    contracts: int = 0
    dollar_risk: float = 0.0
    account_risk_pct: float = 0.0
    stop_distance_points: float = 0.0
    stop_distance_ticks: float = 0.0
    expected_cost: float = 0.0             # commissions + fees + slippage, all-in
    reward_risk_after_costs: float = 0.0
    remaining_daily_loss_budget: float = 0.0
    remaining_drawdown_buffer: float = 0.0
    buffer_consumed_if_stopped_pct: float = 0.0
    risk_multiplier_applied: float = 1.0
    vetoes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    timestamp_et: str = field(default_factory=_ts)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def veto_reason(self) -> str:
        return "; ".join(self.vetoes)


@dataclass
class TradeCallout:
    """The live BUY / SELL / NO TRADE callout.

    Field-for-field the format required by the specification, including the
    explicit statement of dollars at risk and the effect on the remaining
    drawdown buffer.
    """

    callout_id: str = field(default_factory=lambda: _new_id("call"))
    timestamp_et: str = field(default_factory=_ts)
    symbol: str = ""
    decision: Decision = Decision.NO_TRADE
    entry: Optional[float] = None
    entry_zone: Optional[Tuple[float, float]] = None
    stop_loss: Optional[float] = None
    targets: List[float] = field(default_factory=list)
    expected_reward_risk: Optional[float] = None
    contracts: int = 0
    dollar_risk: float = 0.0
    account_risk_pct: float = 0.0
    strategy: str = ""
    strategy_group: str = ""
    timeframe: str = ""
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    news_risk: NewsRisk = NewsRisk.NONE
    historical_win_rate: float = 0.0
    historical_expectancy_r: float = 0.0
    max_historical_drawdown_r: float = 0.0
    analyst_agreement: str = ""
    confidence: float = 0.0
    trade_invalidation: str = ""
    reason_for_entry: str = ""
    reason_to_avoid: str = ""
    remaining_drawdown_buffer: float = 0.0
    buffer_consumed_if_stopped_pct: float = 0.0
    remaining_daily_loss_budget: float = 0.0
    account_equity: float = 0.0
    evidence_chain: List[Evidence] = field(default_factory=list)
    analyst_predictions: List[AnalystPrediction] = field(default_factory=list)
    risk_assessment: Optional[RiskAssessment] = None
    decision_rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "callout_id": self.callout_id,
            "timestamp_et": self.timestamp_et,
            "symbol": self.symbol,
            "decision": self.decision.value,
            "entry": self.entry,
            "entry_zone": list(self.entry_zone) if self.entry_zone else None,
            "stop_loss": self.stop_loss,
            "targets": list(self.targets),
            "expected_reward_risk": self.expected_reward_risk,
            "contracts": self.contracts,
            "dollar_risk": self.dollar_risk,
            "account_risk_pct": self.account_risk_pct,
            "strategy": self.strategy,
            "strategy_group": self.strategy_group,
            "timeframe": self.timeframe,
            "market_regime": self.market_regime.value,
            "news_risk": self.news_risk.value,
            "historical_win_rate": self.historical_win_rate,
            "historical_expectancy_r": self.historical_expectancy_r,
            "max_historical_drawdown_r": self.max_historical_drawdown_r,
            "analyst_agreement": self.analyst_agreement,
            "confidence": self.confidence,
            "trade_invalidation": self.trade_invalidation,
            "reason_for_entry": self.reason_for_entry,
            "reason_to_avoid": self.reason_to_avoid,
            "remaining_drawdown_buffer": self.remaining_drawdown_buffer,
            "buffer_consumed_if_stopped_pct": self.buffer_consumed_if_stopped_pct,
            "remaining_daily_loss_budget": self.remaining_daily_loss_budget,
            "account_equity": self.account_equity,
            "evidence_chain": [e.to_dict() for e in self.evidence_chain],
            "analyst_predictions": [p.to_dict() for p in self.analyst_predictions],
            "risk_assessment": self.risk_assessment.to_dict() if self.risk_assessment else None,
            "decision_rationale": self.decision_rationale,
        }

    @property
    def is_actionable(self) -> bool:
        return self.decision.is_actionable and self.contracts > 0


@dataclass
class StrategyStats:
    """Rolling live performance of one strategy, updated from the journal."""

    strategy_id: str = ""
    symbol: str = ""
    timeframe: Optional[int] = None
    live_trades: int = 0
    live_wins: int = 0
    live_expectancy_r: float = 0.0
    live_profit_factor: float = 0.0
    live_max_drawdown_r: float = 0.0
    current_consecutive_losses: int = 0
    influence_weight: float = 1.0     # scaled down when live results decay
    flagged_for_research: bool = False
    last_updated_et: str = field(default_factory=_ts)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JournalEntry:
    """One row of the automated trading journal - the learning substrate.

    Contains everything needed to re-derive why the decision was made and what
    actually happened, which is what makes the continuous-learning loop
    auditable rather than a black box.
    """

    entry_id: str = field(default_factory=lambda: _new_id("jrn"))
    date_et: str = ""
    time_et: str = ""
    symbol: str = ""
    direction: Direction = Direction.NEUTRAL
    entry: Optional[float] = None
    stop: Optional[float] = None
    targets: List[float] = field(default_factory=list)
    strategy: str = ""
    strategy_group: str = ""
    timeframes: List[int] = field(default_factory=list)
    indicators: List[str] = field(default_factory=list)
    confluences: List[str] = field(default_factory=list)
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    volatility_regime: VolatilityRegime = VolatilityRegime.NORMAL
    session: str = ""
    news_environment: str = ""
    analyst_predictions: List[Dict[str, Any]] = field(default_factory=list)
    final_decision: Decision = Decision.NO_TRADE
    confidence: float = 0.0
    contracts: int = 0
    dollar_risk: float = 0.0
    # ---- outcome (filled in when the trade resolves) ----
    result: str = "OPEN"               # WIN | LOSS | BREAKEVEN | SCRATCH | OPEN | NOT_TAKEN
    exit_price: Optional[float] = None
    exit_time_et: Optional[str] = None
    exit_reason: str = ""              # TARGET | STOP | TIME | INVALIDATION | MANUAL
    mfe_points: float = 0.0
    mae_points: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    profit_loss: float = 0.0
    realised_r: float = 0.0
    reward_risk_planned: float = 0.0
    thesis_correct: Optional[bool] = None
    what_invalidated: str = ""
    what_happened_after: str = ""
    lessons: str = ""
    callout_id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["market_regime"] = self.market_regime.value
        d["volatility_regime"] = self.volatility_regime.value
        d["final_decision"] = self.final_decision.value
        return d


# --------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------

def _default(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, datetime):
        return to_et(obj).isoformat()
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    return str(obj)


def to_json(obj: Any, indent: Optional[int] = None) -> str:
    """Serialise any schema object (or container of them) to JSON.

    ``sort_keys`` is on so that identical content produces an identical string -
    which keeps prompt-cache prefixes stable for the LLM agents.
    """
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    return json.dumps(obj, indent=indent, default=_default, sort_keys=True)


def from_json(text: str) -> Any:
    return json.loads(text)
