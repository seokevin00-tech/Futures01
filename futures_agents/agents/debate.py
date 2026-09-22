"""The research debate: challenges, rebuttals and deterministic pooling.

Three specialists research different strategy families, then cross-examine each
other before anything reaches the live system. The point is not discussion. It
is that **an edge nobody tried to break is an edge nobody has tested**, and a
large combinatorial search guarantees that some findings are luck wearing a
good backtest.

Three rules make this a real process rather than roleplay:

1. **A challenge must carry a measurement.** ``Challenge.is_substantiated``
   is False without one, and unsubstantiated challenges are discarded by the
   pooler. "I doubt this" costs nothing and proves nothing.

2. **A rebuttal must also carry a measurement.** The challenged specialist may
   concede, or it may produce a counter-measurement - but it cannot simply
   assert that its finding is fine.

3. **Pooling is deterministic and symmetric.** The desk lead applies a fixed
   algorithm to the evidence. It cannot favour one specialist, and re-running
   the same debate produces the same ranking.

The adversarial re-test is the sharpest tool here: a challenger re-runs a
rival's strategy on a *different* walk-forward split than the one its owner
used. An edge that only survives the split its author chose was fitted to that
split.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..backtest.costs import CostModel, SlippageModel
from ..backtest.engine import BacktestEngine, Trade
from ..backtest.metrics import Metrics, compute_metrics, slice_metrics
from ..backtest.robustness import deflated_expectancy
from ..backtest.walkforward import robust_score
from ..features import SymbolFrame
from ..strategies.base import Strategy
from ..timeutil import et_stamp, now_et, to_et

__all__ = [
    "ChallengeKind", "Verdict", "Challenge", "Rebuttal", "Finding",
    "PooledRanking", "adversarial_retest", "trade_overlap", "find_redundancy",
    "pool_findings", "CHALLENGE_PENALTY", "render_debate_log",
    "cost_stress", "regime_concentration",
]


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

class ChallengeKind(str, Enum):
    """The measurable objections one specialist may raise against another's
    finding. Each names a specific way a backtest lies."""

    #: Re-run on a different walk-forward split and it degraded.
    OUT_OF_SAMPLE_FAILURE = "OUT_OF_SAMPLE_FAILURE"
    #: The edge does not survive deflation for the number of combinations searched.
    DATA_MINING = "DATA_MINING"
    #: Its trades are the same trades as another finding - not an independent edge.
    REDUNDANT = "REDUNDANT"
    #: The edge lives in one regime or session slice with a thin sample.
    REGIME_ARTEFACT = "REGIME_ARTEFACT"
    #: It dies when slippage is doubled.
    COST_FRAGILE = "COST_FRAGILE"
    #: Too few trades to distinguish from noise at all.
    SAMPLE_TOO_SMALL = "SAMPLE_TOO_SMALL"

    @property
    def is_fatal(self) -> bool:
        """Upheld, these disqualify a finding outright rather than penalising it."""
        return self in (ChallengeKind.OUT_OF_SAMPLE_FAILURE,
                        ChallengeKind.DATA_MINING,
                        ChallengeKind.SAMPLE_TOO_SMALL)


class Verdict(str, Enum):
    CONCEDED = "CONCEDED"        # the owner accepts the objection
    REBUTTED = "REBUTTED"        # the owner produced a counter-measurement
    PARTIAL = "PARTIAL"          # accepted in part - scope narrowed
    UNANSWERED = "UNANSWERED"    # no rebuttal was filed


#: Score multiplier applied to a finding for each challenge that stands.
#: Fatal kinds zero it; the rest are graded, because "worse than claimed" and
#: "not real" are different findings and collapsing them loses information.
CHALLENGE_PENALTY: Dict[ChallengeKind, float] = {
    ChallengeKind.OUT_OF_SAMPLE_FAILURE: 0.0,
    ChallengeKind.DATA_MINING: 0.0,
    ChallengeKind.SAMPLE_TOO_SMALL: 0.0,
    ChallengeKind.REGIME_ARTEFACT: 0.45,
    ChallengeKind.COST_FRAGILE: 0.35,
    ChallengeKind.REDUNDANT: 0.60,
}


@dataclass
class Finding:
    """One specialist's claim that a strategy has a real edge."""

    owner: str                       # role id of the specialist
    symbol: str
    strategy_id: str
    strategy_name: str = ""
    family: str = ""
    timeframe: Optional[int] = None
    metrics: Optional[Metrics] = None
    robustness_score: float = 0.0
    walk_forward_efficiency: float = 0.0
    trials_searched: int = 1
    live_eligible: bool = False
    #: (entry bar index, direction sign) per trade - the fingerprint used to
    #: detect that two findings are the same edge under different names.
    trade_fingerprint: List[Tuple[int, int]] = field(default_factory=list)
    r_series: List[float] = field(default_factory=list)
    claim: str = ""
    timestamp_et: str = field(default_factory=lambda: to_et(now_et()).isoformat())

    @property
    def base_score(self) -> float:
        return robust_score(self.metrics) if self.metrics else 0.0

    def to_dict(self) -> dict:
        return {
            "owner": self.owner, "symbol": self.symbol,
            "strategy_id": self.strategy_id, "strategy_name": self.strategy_name,
            "family": self.family, "timeframe": self.timeframe,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "robustness_score": round(self.robustness_score, 4),
            "walk_forward_efficiency": round(self.walk_forward_efficiency, 4),
            "trials_searched": self.trials_searched,
            "live_eligible": self.live_eligible,
            "base_score": round(self.base_score, 5),
            "trades": len(self.r_series), "claim": self.claim,
            "timestamp_et": self.timestamp_et,
            # The fingerprint and R series MUST survive publication. Redundancy
            # detection is defined on the fingerprint, so a Finding serialised
            # without it cannot be checked against anyone else's - and
            # find_redundancy then returns an empty list, which reads exactly
            # like "these edges are independent". Two specialists reporting the
            # identical edge would pass pooling as corroboration. The failure is
            # silent, which is why it is worth the extra bytes.
            "trade_fingerprint": [list(p) for p in self.trade_fingerprint],
            "r_series": [round(float(r), 6) for r in self.r_series],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Finding":
        """Rehydrate a published finding.

        Provided so every specialist and the desk lead parse artefacts the same
        way. Three agents each writing their own tolerant parser is how one of
        them quietly drops the fingerprint again.
        """
        metrics = None
        raw_metrics = data.get("metrics")
        if isinstance(raw_metrics, dict):
            metrics = Metrics()
            for key, value in raw_metrics.items():
                if hasattr(metrics, key):
                    setattr(metrics, key, value)
        fingerprint = [
            (int(p[0]), int(p[1]))
            for p in (data.get("trade_fingerprint") or [])
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        return cls(
            owner=str(data.get("owner", "")),
            symbol=str(data.get("symbol", "")),
            strategy_id=str(data.get("strategy_id", "")),
            strategy_name=str(data.get("strategy_name", "")),
            family=str(data.get("family", "")),
            timeframe=data.get("timeframe"),
            metrics=metrics,
            robustness_score=float(data.get("robustness_score") or 0.0),
            walk_forward_efficiency=float(data.get("walk_forward_efficiency") or 0.0),
            trials_searched=int(data.get("trials_searched") or 1),
            live_eligible=bool(data.get("live_eligible")),
            trade_fingerprint=fingerprint,
            r_series=[float(r) for r in (data.get("r_series") or [])],
            claim=str(data.get("claim", "")),
            timestamp_et=str(data.get("timestamp_et")
                             or to_et(now_et()).isoformat()),
        )


@dataclass
class Challenge:
    """A measured objection to another specialist's finding."""

    challenger: str
    target_owner: str
    target_strategy_id: str
    symbol: str
    kind: ChallengeKind
    claim: str
    #: The numbers behind the objection. Without these the challenge is noise.
    measurement: Dict[str, Any] = field(default_factory=dict)
    verdict: Verdict = Verdict.UNANSWERED
    timestamp_et: str = field(default_factory=lambda: to_et(now_et()).isoformat())

    @property
    def is_substantiated(self) -> bool:
        """A challenge without numbers is an opinion, and opinions do not move
        a ranking."""
        return bool(self.measurement) and self.challenger != self.target_owner

    @property
    def stands(self) -> bool:
        """Whether this challenge still counts against the finding."""
        if not self.is_substantiated:
            return False
        return self.verdict in (Verdict.CONCEDED, Verdict.PARTIAL, Verdict.UNANSWERED)

    @property
    def penalty(self) -> float:
        if not self.stands:
            return 1.0
        factor = CHALLENGE_PENALTY.get(self.kind, 0.5)
        # A partial concession is half a penalty: the finding is narrower than
        # claimed, not wrong.
        if self.verdict is Verdict.PARTIAL:
            factor = factor + (1.0 - factor) * 0.5
        return factor

    def to_dict(self) -> dict:
        return {
            "challenger": self.challenger, "target_owner": self.target_owner,
            "target_strategy_id": self.target_strategy_id, "symbol": self.symbol,
            "kind": self.kind.value, "claim": self.claim,
            "measurement": self.measurement, "verdict": self.verdict.value,
            "substantiated": self.is_substantiated, "stands": self.stands,
            "penalty": round(self.penalty, 3), "timestamp_et": self.timestamp_et,
        }

    def render(self) -> str:
        return (f"{self.challenger} -> {self.target_owner} "
                f"[{self.kind.value}] {self.target_strategy_id}: {self.claim} "
                f"({self.verdict.value})")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Challenge":
        """Rehydrate a published challenge.

        ``measurement`` is preserved exactly - including when it is empty,
        because an empty one is what makes ``is_substantiated`` False and gets
        the challenge discarded. Defaulting it to a placeholder would silently
        promote an unsubstantiated objection into a real one.
        """
        kind = data.get("kind")
        try:
            kind = ChallengeKind(kind)
        except ValueError:
            kind = ChallengeKind.REDUNDANT
        try:
            verdict = Verdict(data.get("verdict", Verdict.UNANSWERED.value))
        except ValueError:
            verdict = Verdict.UNANSWERED
        measurement = data.get("measurement")
        return cls(
            challenger=str(data.get("challenger", "")),
            target_owner=str(data.get("target_owner", "")),
            target_strategy_id=str(data.get("target_strategy_id", "")),
            symbol=str(data.get("symbol", "")),
            kind=kind, claim=str(data.get("claim", "")),
            measurement=dict(measurement) if isinstance(measurement, dict) else {},
            verdict=verdict,
            timestamp_et=str(data.get("timestamp_et") or to_et(now_et()).isoformat()),
        )


@dataclass
class Rebuttal:
    """The challenged specialist's answer, which must also be measured."""

    responder: str
    challenger: str
    target_strategy_id: str
    verdict: Verdict
    argument: str
    measurement: Dict[str, Any] = field(default_factory=dict)
    timestamp_et: str = field(default_factory=lambda: to_et(now_et()).isoformat())

    @property
    def is_substantiated(self) -> bool:
        """Only a measured rebuttal can overturn a measured challenge. An
        unmeasured REBUTTED verdict is downgraded to UNANSWERED by the pooler."""
        return bool(self.measurement)

    def to_dict(self) -> dict:
        return {
            "responder": self.responder, "challenger": self.challenger,
            "target_strategy_id": self.target_strategy_id,
            "verdict": self.verdict.value, "argument": self.argument,
            "measurement": self.measurement,
            "substantiated": self.is_substantiated,
            "timestamp_et": self.timestamp_et,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Rebuttal":
        """Rehydrate a published rebuttal, preserving an empty measurement."""
        try:
            verdict = Verdict(data.get("verdict", Verdict.UNANSWERED.value))
        except ValueError:
            verdict = Verdict.UNANSWERED
        measurement = data.get("measurement")
        return cls(
            responder=str(data.get("responder", "")),
            challenger=str(data.get("challenger", "")),
            target_strategy_id=str(data.get("target_strategy_id", "")),
            verdict=verdict, argument=str(data.get("argument", "")),
            measurement=dict(measurement) if isinstance(measurement, dict) else {},
            timestamp_et=str(data.get("timestamp_et") or to_et(now_et()).isoformat()),
        )


# --------------------------------------------------------------------------
# Measurement tools the specialists use to build challenges
# --------------------------------------------------------------------------

def adversarial_retest(frame: SymbolFrame, strategy: Strategy, *,
                       skip_fraction: float = 0.5,
                       cost_model: Optional[CostModel] = None) -> Dict[str, Any]:
    """Re-run a rival's strategy on a split its owner did not choose.

    The owner reports performance over a window it selected. This runs the same
    strategy over the complementary window. An edge that appears in one and
    vanishes in the other was fitted to the first.

    Returns both halves plus the ratio between them; a ratio well below 1 is
    the substantiation for an OUT_OF_SAMPLE_FAILURE challenge.
    """
    n = len(frame.base)
    if n < 200:
        return {"error": "insufficient bars for a split re-test", "bars": n}
    cut = int(n * skip_fraction)
    engine = BacktestEngine(frame, cost_model)

    first = compute_metrics(engine.run(strategy, start=0, end=cut).trades)
    second = compute_metrics(engine.run(strategy, start=cut, end=n).trades)

    ratio = 0.0
    if first.expectancy_r > 0:
        ratio = second.expectancy_r / first.expectancy_r
    elif second.expectancy_r > 0:
        ratio = 1.0

    return {
        "split_bar": cut,
        "first_half": {"trades": first.trades,
                       "expectancy_r": round(first.expectancy_r, 4),
                       "profit_factor": round(first.profit_factor, 3)},
        "second_half": {"trades": second.trades,
                        "expectancy_r": round(second.expectancy_r, 4),
                        "profit_factor": round(second.profit_factor, 3)},
        "consistency_ratio": round(ratio, 4),
        "both_halves_positive": first.expectancy_r > 0 and second.expectancy_r > 0,
    }


def cost_stress(frame: SymbolFrame, strategy: Strategy) -> Dict[str, Any]:
    """Re-run under doubled slippage. Substantiates a COST_FRAGILE challenge.

    A live account rarely gets better fills than the backtest assumed, so an
    edge that only exists at the optimistic assumption is not an edge.
    """
    base = compute_metrics(
        BacktestEngine(frame).run(strategy).trades)
    harsh_model = CostModel(frame.spec, slippage=SlippageModel(
        base_ticks=1.0, stop_order_extra_ticks=2.0, volatility_coefficient=1.2))
    harsh = compute_metrics(
        BacktestEngine(frame, harsh_model).run(strategy).trades)
    retained = (harsh.expectancy_r / base.expectancy_r
                if base.expectancy_r > 0 else 0.0)
    return {
        "baseline_expectancy_r": round(base.expectancy_r, 4),
        "stressed_expectancy_r": round(harsh.expectancy_r, 4),
        "retained_fraction": round(retained, 4),
        "survives": harsh.expectancy_r > 0,
    }


def regime_concentration(trades: Sequence[Trade]) -> Dict[str, Any]:
    """How much of the edge comes from a single regime slice.

    Substantiates a REGIME_ARTEFACT challenge: an edge whose entire profit sits
    in one thinly-sampled regime is a description of that period, not a rule.
    """
    if not trades:
        return {"error": "no trades"}
    total = sum(t.net_r for t in trades)
    by_regime = slice_metrics(trades, "regime", min_trades=1)
    contributions = {str(k): round(m.total_r, 4) for k, m in by_regime.items()}
    counts = {str(k): m.trades for k, m in by_regime.items()}
    if total <= 0 or not contributions:
        return {"total_r": round(total, 4), "by_regime": contributions,
                "counts": counts, "concentrated": False}
    top = max(contributions.items(), key=lambda kv: kv[1])
    share = top[1] / total if total else 0.0
    return {
        "total_r": round(total, 4),
        "by_regime": contributions, "counts": counts,
        "dominant_regime": top[0], "dominant_share": round(share, 4),
        "dominant_sample": counts.get(top[0], 0),
        # One regime carrying almost everything on a thin sample is the tell.
        "concentrated": share >= 0.80 and counts.get(top[0], 0) < 40,
    }


def trade_overlap(a: Sequence[Tuple[int, int]],
                  b: Sequence[Tuple[int, int]], *, tolerance: int = 3) -> float:
    """Jaccard overlap of two strategies' trade fingerprints, in [0, 1].

    A fingerprint is (entry bar index, direction). Two strategies that enter the
    same way within ``tolerance`` bars of each other are taking the same trade,
    whatever their rules are called - and pooling both would double-count one
    edge while looking like corroboration.
    """
    if not a or not b:
        return 0.0
    b_sorted = sorted(b)
    matched_b: Set[int] = set()
    shared = 0
    for idx, direction in a:
        for j, (bidx, bdir) in enumerate(b_sorted):
            if j in matched_b or bdir != direction:
                continue
            if abs(bidx - idx) <= tolerance:
                matched_b.add(j)
                shared += 1
                break
    union = len(a) + len(b) - shared
    return shared / union if union else 0.0


def find_redundancy(findings: Sequence[Finding], *, threshold: float = 0.55
                    ) -> List[Tuple[str, str, float]]:
    """Pairs of findings that are measurably the same edge.

    Returns ``(strategy_id_a, strategy_id_b, overlap)`` for every pair above
    ``threshold``, ordered by overlap descending.
    """
    out: List[Tuple[str, str, float]] = []
    for i, fa in enumerate(findings):
        for fb in findings[i + 1:]:
            if fa.symbol != fb.symbol:
                continue
            overlap = trade_overlap(fa.trade_fingerprint, fb.trade_fingerprint)
            if overlap >= threshold:
                out.append((fa.strategy_id, fb.strategy_id, round(overlap, 4)))
    out.sort(key=lambda t: -t[2])
    return out


# --------------------------------------------------------------------------
# Pooling
# --------------------------------------------------------------------------

@dataclass
class PooledRanking:
    """The reconciled outcome of one debate round."""

    symbol: str
    ranked: List[Dict[str, Any]] = field(default_factory=list)
    disqualified: List[Dict[str, Any]] = field(default_factory=list)
    redundant_clusters: List[Dict[str, Any]] = field(default_factory=list)
    by_specialist: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    challenges_filed: int = 0
    challenges_upheld: int = 0
    challenges_rebutted: int = 0
    challenges_discarded: int = 0
    timestamp_et: str = field(default_factory=lambda: to_et(now_et()).isoformat())

    @property
    def live_eligible(self) -> List[Dict[str, Any]]:
        return [r for r in self.ranked if r.get("live_eligible")]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "timestamp_et": self.timestamp_et,
            "ranked": self.ranked, "disqualified": self.disqualified,
            "redundant_clusters": self.redundant_clusters,
            "by_specialist": self.by_specialist,
            "debate": {
                "challenges_filed": self.challenges_filed,
                "upheld": self.challenges_upheld,
                "rebutted": self.challenges_rebutted,
                "discarded_unsubstantiated": self.challenges_discarded,
            },
            "live_eligible_count": len(self.live_eligible),
        }

    def summary(self) -> str:
        return (f"{self.symbol}: {len(self.ranked)} finding(s) pooled, "
                f"{len(self.disqualified)} disqualified, "
                f"{len(self.live_eligible)} live-eligible; "
                f"{self.challenges_filed} challenge(s) "
                f"({self.challenges_upheld} upheld, {self.challenges_rebutted} rebutted, "
                f"{self.challenges_discarded} discarded)")


def pool_findings(symbol: str, findings: Sequence[Finding],
                  challenges: Sequence[Challenge],
                  rebuttals: Sequence[Rebuttal] = (), *,
                  redundancy_threshold: float = 0.55) -> PooledRanking:
    """Reconcile three specialists' findings into one ranking.

    Deterministic and symmetric: the same inputs always produce the same
    ranking, and nothing about the algorithm can favour one specialist. The
    desk lead scores the debate; it does not take a side in it.

    Order of operations:

    1. Attach rebuttals to challenges. A REBUTTED verdict with no measurement
       behind it is downgraded to UNANSWERED - an unmeasured denial does not
       answer a measured objection.
    2. Discard unsubstantiated challenges entirely.
    3. Disqualify findings carrying a standing fatal challenge.
    4. Collapse redundant clusters, keeping the highest-scoring member, so one
       edge found by two specialists counts once.
    5. Score survivors: base robustness x each standing challenge's penalty.
    """
    result = PooledRanking(symbol=symbol)
    findings = [f for f in findings if f.symbol == symbol]
    challenges = [c for c in challenges if c.symbol == symbol]
    result.challenges_filed = len(challenges)

    # ---- 1 & 2: attach rebuttals, discard the unmeasured -----------------
    # Every challenge is COPIED before its verdict is resolved. Writing the
    # verdict back onto the caller's object would make this function mutate its
    # own evidence, so pooling the same debate twice would not give the same
    # answer - and a scoring function whose result depends on how many times it
    # has been called cannot be audited.
    by_target: Dict[str, List[Challenge]] = {}
    live_challenges: List[Challenge] = []
    for original in challenges:
        if not original.is_substantiated:
            result.challenges_discarded += 1
            continue
        ch = replace(original)
        answer = next((r for r in rebuttals
                       if r.target_strategy_id == ch.target_strategy_id
                       and r.challenger == ch.challenger), None)
        if answer is not None:
            if answer.verdict is Verdict.REBUTTED and not answer.is_substantiated:
                # A denial with no numbers behind it does not answer numbers.
                ch.verdict = Verdict.UNANSWERED
            else:
                ch.verdict = answer.verdict
        live_challenges.append(ch)
        by_target.setdefault(ch.target_strategy_id, []).append(ch)

    for ch in live_challenges:
        if ch.stands:
            result.challenges_upheld += 1
        else:
            result.challenges_rebutted += 1

    # ---- 3: fatal challenges disqualify ----------------------------------
    survivors: List[Finding] = []
    for f in findings:
        fatal = [c for c in by_target.get(f.strategy_id, ())
                 if c.stands and c.kind.is_fatal]
        if fatal:
            result.disqualified.append({
                "strategy_id": f.strategy_id, "owner": f.owner,
                "family": f.family,
                "disqualified_by": [c.to_dict() for c in fatal],
            })
        else:
            survivors.append(f)

    # ---- 4: collapse redundant clusters ----------------------------------
    # Redundancy is transitive: if A is the same edge as B and B the same as C,
    # then all three are one edge. Collapsing pairwise instead of by connected
    # component lets a finding be recorded as "kept" in one pair and "absorbed"
    # in the next, leaving the first pair pointing at a survivor that is no
    # longer in the ranking. Union-find keeps each component whole.
    duplicates = find_redundancy(survivors, threshold=redundancy_threshold)
    parent: Dict[str, str] = {f.strategy_id: f.strategy_id for f in survivors}

    def _root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]        # path compression
            x = parent[x]
        return x

    overlap_of: Dict[Tuple[str, str], float] = {}
    for sid_a, sid_b, overlap in duplicates:
        overlap_of[(sid_a, sid_b)] = overlap
        overlap_of[(sid_b, sid_a)] = overlap
        ra, rb = _root(sid_a), _root(sid_b)
        if ra != rb:
            parent[rb] = ra

    components: Dict[str, List[Finding]] = {}
    for f in survivors:
        components.setdefault(_root(f.strategy_id), []).append(f)

    absorbed: Set[str] = set()
    for members in components.values():
        if len(members) < 2:
            continue
        # Deterministic pick: best score, ties broken by id so the same inputs
        # always keep the same member.
        members.sort(key=lambda f: (-f.base_score, f.strategy_id))
        keep = members[0]
        for other in members[1:]:
            absorbed.add(other.strategy_id)
        result.redundant_clusters.append({
            "kept": keep.strategy_id, "kept_owner": keep.owner,
            "absorbed": [m.strategy_id for m in members[1:]],
            "absorbed_owners": sorted({m.owner for m in members[1:]}),
            "max_overlap": max(
                (overlap_of.get((a.strategy_id, b.strategy_id), 0.0)
                 for a in members for b in members if a is not b), default=0.0),
            "note": ("the same trades under different names - counted once, so "
                     "two specialists finding one edge does not read as "
                     "corroboration"),
        })

    # ---- 5: score what is left -------------------------------------------
    rows: List[Dict[str, Any]] = []
    for f in survivors:
        if f.strategy_id in absorbed:
            continue
        standing = [c for c in by_target.get(f.strategy_id, ()) if c.stands]
        penalty = 1.0
        for c in standing:
            penalty *= c.penalty
        final = f.base_score * penalty
        rows.append({
            **f.to_dict(),
            "standing_challenges": [c.to_dict() for c in standing],
            "penalty_applied": round(penalty, 4),
            "pooled_score": round(final, 5),
            # Surviving cross-examination is a necessary condition, not a
            # sufficient one: the finding still has to have been eligible.
            "live_eligible": bool(f.live_eligible and penalty > 0.0),
        })
    rows.sort(key=lambda r: (-r["pooled_score"], r["strategy_id"]))
    result.ranked = rows

    for owner in sorted({f.owner for f in findings}):
        owned = [r for r in rows if r["owner"] == owner]
        filed = [c for c in live_challenges if c.challenger == owner]
        against = [c for c in live_challenges if c.target_owner == owner]
        result.by_specialist[owner] = {
            "findings_submitted": sum(1 for f in findings if f.owner == owner),
            "findings_surviving": len(owned),
            "live_eligible": sum(1 for r in owned if r["live_eligible"]),
            "challenges_filed": len(filed),
            "challenges_upheld_for": sum(1 for c in filed if c.stands),
            "challenges_received": len(against),
            "challenges_survived": sum(1 for c in against if not c.stands),
            "best_pooled_score": max((r["pooled_score"] for r in owned), default=0.0),
        }
    return result


def render_debate_log(ranking: PooledRanking,
                      challenges: Sequence[Challenge],
                      rebuttals: Sequence[Rebuttal] = ()) -> str:
    """Human-readable transcript of one debate round."""
    lines = [f"RESEARCH DEBATE - {ranking.symbol}  [{et_stamp()}]", ""]
    lines.append(f"  {ranking.summary()}")
    lines.append("")
    if challenges:
        lines.append("  CHALLENGES")
        for c in challenges:
            mark = "UPHELD " if c.stands else ("DROPPED" if c.is_substantiated
                                               else "NO DATA")
            lines.append(f"    [{mark}] {c.render()}")
            if c.measurement:
                bits = ", ".join(f"{k}={v}" for k, v in list(c.measurement.items())[:4])
                lines.append(f"              {bits}")
    if rebuttals:
        lines.append("")
        lines.append("  REBUTTALS")
        for r in rebuttals:
            mark = "MEASURED" if r.is_substantiated else "ASSERTED"
            lines.append(f"    [{mark}] {r.responder} on {r.target_strategy_id}: "
                         f"{r.verdict.value} - {r.argument}")
    if ranking.redundant_clusters:
        lines.append("")
        lines.append("  REDUNDANT (same edge, counted once)")
        for c in ranking.redundant_clusters:
            absorbed = ", ".join(c["absorbed"])
            owners = ", ".join(c["absorbed_owners"])
            lines.append(f"    {c['kept']} ({c['kept_owner']}) absorbs "
                         f"{absorbed} ({owners}) "
                         f"- overlap up to {c['max_overlap']:.0%}")
    lines.append("")
    lines.append("  POOLED RANKING")
    for i, row in enumerate(ranking.ranked[:10], 1):
        flag = "eligible" if row["live_eligible"] else "not eligible"
        lines.append(f"    {i:2d}. {row['strategy_id']:24s} {row['owner']:20s} "
                     f"score {row['pooled_score']:+.4f} "
                     f"(x{row['penalty_applied']:.2f}) [{flag}]")
    if not ranking.ranked:
        lines.append("    (nothing survived cross-examination)")
    return "\n".join(lines)
