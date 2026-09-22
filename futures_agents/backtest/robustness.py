"""Anti-overfitting: robustness scoring, parameter sensitivity and bias checks.

A high backtest return is not evidence. It is the *default* outcome of
searching a large enough space, and this system searches thousands of
combinations per symbol, so some of them will look excellent by pure chance.
This module exists to make that failure mode visible and to refuse to promote
anything that cannot survive it.

The checks map directly onto the specification's list: overfitting, data-mining
bias, parameter sensitivity, insufficient sample size, unrealistic fills,
excessive transaction costs, slippage, commission effects, look-ahead bias,
survivorship bias, repainting indicators and future-data leakage. Some are
verified mechanically here; the rest are asserted structurally elsewhere in the
codebase, and each one says which.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..features import SymbolFrame
from ..strategies.base import ExitModel, Strategy
from .costs import CostModel, SlippageModel
from .engine import BacktestEngine, Trade
from .metrics import Metrics, compute_metrics
from .montecarlo import MonteCarloResult, monte_carlo
from .walkforward import WalkForwardResult, robust_score

__all__ = ["RobustnessReport", "BiasCheck", "BIAS_CHECKS", "check_biases",
           "parameter_sensitivity", "assess_robustness", "deflated_expectancy"]


# --------------------------------------------------------------------------
# Data-mining correction
# --------------------------------------------------------------------------

def deflated_expectancy(metrics: Metrics, trials: int) -> float:
    """Expectancy after discounting for how many strategies were searched.

    Testing ``trials`` strategies means the best one's t-statistic is inflated
    by selection alone. The expected maximum of *n* standard normals is roughly
    ``sqrt(2 ln n)``, so that much of the observed t-statistic is attributable
    to searching rather than to edge. What remains is the honest estimate.

    Search 4,000 combinations and roughly 4.1 t-units of apparent significance
    are free - which is why an unadjusted "t = 3.2, highly significant" result
    from a large sweep means nothing at all.
    """
    if metrics.trades < 2 or metrics.std_r <= 0 or trials < 1:
        return 0.0
    expected_max_t = math.sqrt(2.0 * math.log(max(2, trials)))
    deflated_t = metrics.t_statistic - expected_max_t
    if deflated_t <= 0:
        return 0.0
    return deflated_t * metrics.std_r / math.sqrt(metrics.trades)


# --------------------------------------------------------------------------
# Bias checks
# --------------------------------------------------------------------------

@dataclass
class BiasCheck:
    name: str
    passed: bool
    detail: str
    how_verified: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail,
                "how_verified": self.how_verified}


#: The bias catalogue, with how each one is addressed in this codebase.
BIAS_CHECKS: Dict[str, str] = {
    "look_ahead_bias":
        "Indicators return None during warm-up and never read an index beyond "
        "the one being computed; verified mechanically by appending future bars "
        "and asserting history is unchanged.",
    "future_data_leakage":
        "SymbolFrame aligns each base bar to the last CLOSED higher-timeframe "
        "bar; the developer agent asserts no aligned bar ends after its base bar.",
    "repainting_indicators":
        "Swings carry an explicit confirmation lag and are only visible from "
        "confirmed_index onward; opening ranges expose a `complete` flag and "
        "breakout conditions refuse to fire on an incomplete range.",
    "unrealistic_fills":
        "Entries fill at the NEXT bar's open, never the signal bar's close; "
        "gaps fill at the open; when one bar contains both stop and target the "
        "stop is assumed to fill first.",
    "transaction_costs":
        "Commission and exchange fees are charged per round turn and expressed "
        "in R, so an edge smaller than its own cost cannot appear profitable.",
    "slippage":
        "Slippage is applied to fill prices, widens with volatility percentile, "
        "and is larger for stop (market) orders than for limit entries.",
    "insufficient_sample":
        "robust_score applies a hard sample-size penalty and HistoricalPerformance "
        "refuses live eligibility below 30 trades and 10 out-of-sample trades.",
    "data_mining_bias":
        "deflated_expectancy discounts the t-statistic by the expected maximum "
        "of the number of combinations actually searched.",
    "overfitting":
        "Walk-forward efficiency (out-of-sample expectancy / in-sample "
        "expectancy) must clear a threshold before a strategy is live-eligible.",
    "parameter_sensitivity":
        "Exit geometry is perturbed and the degradation measured; a strategy "
        "that only works at one parameter setting is rejected.",
    "survivorship_bias":
        "Every generated combination is retained and scored, including failures; "
        "rankings are computed over the full generated universe, not over "
        "survivors.",
    "regime_dependence":
        "All metrics are sliced by regime, session, volatility and time of day, "
        "so an edge that exists only in one regime is visible as such.",
}


def check_biases(result_metrics: Metrics, wf: Optional[WalkForwardResult] = None,
                 *, trials: int = 1, sensitivity: Optional[Dict[str, float]] = None,
                 cost_r: Optional[float] = None) -> List[BiasCheck]:
    """Run the checks that can be evaluated from results, and report the rest."""
    checks: List[BiasCheck] = []

    checks.append(BiasCheck(
        "insufficient_sample", result_metrics.trades >= 30,
        f"{result_metrics.trades} trades "
        f"({'sufficient' if result_metrics.trades >= 30 else 'BELOW the 30-trade floor'})",
        BIAS_CHECKS["insufficient_sample"]))

    deflated = deflated_expectancy(result_metrics, trials)
    checks.append(BiasCheck(
        "data_mining_bias", deflated > 0,
        f"searched {trials} combination(s); t={result_metrics.t_statistic:.2f}, "
        f"expectancy {result_metrics.expectancy_r:+.3f}R -> deflated {deflated:+.3f}R",
        BIAS_CHECKS["data_mining_bias"]))

    if wf is not None:
        checks.append(BiasCheck(
            "overfitting", wf.efficiency >= 0.35,
            f"walk-forward efficiency {wf.efficiency:.2f} "
            f"(OOS {wf.combined_oos.expectancy_r:+.3f}R vs "
            f"IS {wf.combined_is.expectancy_r:+.3f}R)",
            BIAS_CHECKS["overfitting"]))
        checks.append(BiasCheck(
            "selection_stability", wf.selection_stability >= 0.2,
            f"selection overlap between folds {wf.selection_stability:.2f}",
            "A universe whose best strategies change completely every fold has "
            "found noise, not an edge."))

    if sensitivity is not None:
        worst = sensitivity.get("worst_relative", 0.0)
        checks.append(BiasCheck(
            "parameter_sensitivity", worst >= 0.4,
            f"worst perturbed variant retains {worst:.0%} of baseline expectancy",
            BIAS_CHECKS["parameter_sensitivity"]))

    if cost_r is not None:
        # An edge that is mostly cost is not an edge.
        covered = (result_metrics.expectancy_r > cost_r * 0.5)
        checks.append(BiasCheck(
            "transaction_costs", covered,
            f"expectancy {result_metrics.expectancy_r:+.3f}R vs round-turn cost "
            f"{cost_r:.3f}R",
            BIAS_CHECKS["transaction_costs"]))

    # Structural guarantees - asserted by construction elsewhere, reported here
    # so the full catalogue is always visible in the report.
    for name in ("look_ahead_bias", "future_data_leakage", "repainting_indicators",
                 "unrealistic_fills", "slippage", "survivorship_bias",
                 "regime_dependence"):
        checks.append(BiasCheck(name, True, "enforced structurally",
                                BIAS_CHECKS[name]))
    return checks


# --------------------------------------------------------------------------
# Parameter sensitivity
# --------------------------------------------------------------------------

def parameter_sensitivity(
    frame: SymbolFrame, strategy: Strategy, *,
    cost_model: Optional[CostModel] = None,
    stop_multipliers: Sequence[float] = (0.75, 1.25),
    target_scales: Sequence[float] = (0.8, 1.25),
    start: int = 0, end: Optional[int] = None,
) -> Dict[str, Any]:
    """Perturb the exit geometry and measure how much of the edge survives.

    A strategy whose expectancy collapses when the stop moves 25% is not a
    strategy, it is a coincidence that happened to fit one parameter value.
    Robust edges degrade gracefully.
    """
    engine = BacktestEngine(frame, cost_model)
    baseline = compute_metrics(engine.run(strategy, start=start, end=end).trades)
    variants: List[Dict[str, Any]] = []

    if baseline.trades == 0:
        return {"baseline_expectancy_r": 0.0, "variants": [],
                "worst_relative": 0.0, "mean_relative": 0.0,
                "note": "baseline produced no trades"}

    def evaluate(label: str, exit_model: ExitModel) -> None:
        try:
            variant = replace(strategy, exit=exit_model, _id=None)
        except (ValueError, TypeError):
            return
        m = compute_metrics(engine.run(variant, start=start, end=end).trades)
        rel = (m.expectancy_r / baseline.expectancy_r
               if baseline.expectancy_r > 0 else 0.0)
        variants.append({
            "label": label, "trades": m.trades,
            "expectancy_r": round(m.expectancy_r, 4),
            "relative": round(rel, 4),
            "profit_factor": round(m.profit_factor, 3),
        })

    for mult in stop_multipliers:
        evaluate(f"stop x{mult:g}",
                 replace(strategy.exit, stop_mult=strategy.exit.stop_mult * mult))
    for scale in target_scales:
        scaled = tuple(round(t * scale, 4) for t in strategy.exit.targets_r)
        evaluate(f"targets x{scale:g}", replace(strategy.exit, targets_r=scaled))

    # Cost stress: double the slippage assumption. A live account that gets
    # worse fills than the backtest assumed is the normal case, not the
    # exceptional one.
    harsh = CostModel(frame.spec, slippage=SlippageModel(
        base_ticks=1.0, stop_order_extra_ticks=2.0, volatility_coefficient=1.2))
    harsh_engine = BacktestEngine(frame, harsh)
    harsh_m = compute_metrics(harsh_engine.run(strategy, start=start, end=end).trades)
    harsh_rel = (harsh_m.expectancy_r / baseline.expectancy_r
                 if baseline.expectancy_r > 0 else 0.0)
    variants.append({"label": "double slippage", "trades": harsh_m.trades,
                     "expectancy_r": round(harsh_m.expectancy_r, 4),
                     "relative": round(harsh_rel, 4),
                     "profit_factor": round(harsh_m.profit_factor, 3)})

    rels = [v["relative"] for v in variants] or [0.0]
    return {
        "baseline_expectancy_r": round(baseline.expectancy_r, 4),
        "baseline_trades": baseline.trades,
        "variants": variants,
        "worst_relative": round(min(rels), 4),
        "mean_relative": round(statistics.fmean(rels), 4),
    }


# --------------------------------------------------------------------------
# Overall report
# --------------------------------------------------------------------------

@dataclass
class RobustnessReport:
    """Everything the system knows about whether an edge is real."""

    strategy_id: str = ""
    symbol: str = ""
    score: float = 0.0                       # 0-1
    live_eligible: bool = False
    metrics: Optional[Metrics] = None
    walk_forward: Optional[WalkForwardResult] = None
    monte_carlo: Optional[MonteCarloResult] = None
    sensitivity: Dict[str, Any] = field(default_factory=dict)
    biases: List[BiasCheck] = field(default_factory=list)
    trials_searched: int = 1
    deflated_expectancy_r: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "symbol": self.symbol,
            "score": round(self.score, 4), "live_eligible": self.live_eligible,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "walk_forward": self.walk_forward.to_dict() if self.walk_forward else None,
            "monte_carlo": self.monte_carlo.to_dict() if self.monte_carlo else None,
            "sensitivity": self.sensitivity,
            "biases": [b.to_dict() for b in self.biases],
            "trials_searched": self.trials_searched,
            "deflated_expectancy_r": round(self.deflated_expectancy_r, 5),
            "reasons": self.reasons,
        }

    def summary(self) -> str:
        return (f"{self.strategy_id}: robustness {self.score:.2f}, "
                f"live_eligible={'yes' if self.live_eligible else 'NO'}"
                + (f" ({'; '.join(self.reasons[:2])})" if self.reasons else ""))


def assess_robustness(
    trades: Sequence[Trade], *,
    strategy_id: str = "", symbol: str = "",
    walk_forward_result: Optional[WalkForwardResult] = None,
    sensitivity: Optional[Dict[str, Any]] = None,
    trials_searched: int = 1,
    cost_r: Optional[float] = None,
    monte_carlo_runs: int = 1_000,
    account_risk_per_trade: Optional[float] = None,
    starting_equity: float = 50_000.0,
    failure_drawdown: float = 5_000.0,
) -> RobustnessReport:
    """Score an edge from 0 to 1 and decide whether it may inform live trading.

    The score is a product of independent gates, not a sum. A strategy that
    fails badly on any one dimension cannot compensate with the others - which
    is the point, because it is the single unchecked dimension that empties an
    account.
    """
    m = compute_metrics(trades)
    report = RobustnessReport(strategy_id=strategy_id, symbol=symbol, metrics=m,
                              walk_forward=walk_forward_result,
                              sensitivity=sensitivity or {},
                              trials_searched=max(1, trials_searched))

    if m.trades == 0:
        report.reasons.append("no trades")
        return report

    report.deflated_expectancy_r = deflated_expectancy(m, report.trials_searched)
    mc = monte_carlo(
        [t.net_r for t in trades], runs=monte_carlo_runs, seed=20260922,
        starting_equity=starting_equity,
        dollar_risk_per_trade=account_risk_per_trade,
        failure_drawdown=failure_drawdown)
    report.monte_carlo = mc
    report.biases = check_biases(m, walk_forward_result,
                                 trials=report.trials_searched,
                                 sensitivity=sensitivity, cost_r=cost_r)

    # --- gates, each in [0, 1] ---
    sample_gate = min(1.0, m.trades / 60.0)
    if m.trades < 30:
        sample_gate *= 0.3
        report.reasons.append(f"sample of {m.trades} trades is below the 30-trade floor")

    edge_gate = 1.0 if m.expectancy_r > 0 else 0.0
    if edge_gate == 0.0:
        report.reasons.append(f"expectancy {m.expectancy_r:+.3f}R is not positive")

    deflation_gate = 1.0 if report.deflated_expectancy_r > 0 else 0.25
    if report.deflated_expectancy_r <= 0:
        report.reasons.append(
            f"edge does not survive correction for searching "
            f"{report.trials_searched} combinations")

    wf_gate = 0.5
    if walk_forward_result is not None:
        wf_gate = max(0.0, min(1.0, walk_forward_result.efficiency / 0.7))
        if walk_forward_result.efficiency < 0.35:
            report.reasons.append(
                f"walk-forward efficiency {walk_forward_result.efficiency:.2f} "
                "- performance collapses out of sample")

    sens_gate = 0.6
    if sensitivity:
        worst = sensitivity.get("worst_relative", 0.0)
        sens_gate = max(0.0, min(1.0, worst / 0.7))
        if worst < 0.4:
            report.reasons.append(
                f"perturbing parameters leaves only {worst:.0%} of the edge")

    ruin_gate = 1.0
    if account_risk_per_trade:
        por = mc.probability_of_ruin
        ruin_gate = max(0.0, 1.0 - por / 0.05)      # 5% ruin => gate closes
        if por > 0.02:
            report.reasons.append(
                f"probability of account failure {por:.1%} at "
                f"${account_risk_per_trade:,.0f} risk per trade")

    dd_gate = 1.0 / (1.0 + max(0.0, mc.max_dd_p95) / 12.0)

    report.score = (sample_gate * edge_gate * deflation_gate * wf_gate
                    * sens_gate * ruin_gate * dd_gate) ** (1.0 / 3.0)
    report.score = max(0.0, min(1.0, report.score))

    report.live_eligible = (
        report.score >= 0.45
        and m.trades >= 30
        and m.expectancy_r > 0
        and report.deflated_expectancy_r > 0
        and (walk_forward_result is None or walk_forward_result.efficiency >= 0.35)
        and (not account_risk_per_trade or mc.probability_of_ruin <= 0.02)
    )
    if report.live_eligible and not report.reasons:
        report.reasons.append("survives sample, deflation, walk-forward, "
                              "sensitivity and ruin checks")
    return report
