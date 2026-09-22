"""Trend, momentum and continuation research - and the case against it.

This specialist owns the families that claim *a move in progress tends to
continue*: TREND, PULLBACK, MOMENTUM and MULTI_TIMEFRAME. The families are read
from :func:`families_for`, never written down here, because the split between
the three specialists is a property of the team, not of this file.

The stance this agent argues from, and the reason it is worth having a separate
seat for it:

**Continuation edges are real, and they are the easiest of all to fake.** Any
sample with net drift in it will make a long-biased rule look profitable, and
almost every equity-index sample has net drift in it. A trend rule tested on an
up-trending six months is not being tested; it is being congratulated. So every
finding this agent publishes carries a :meth:`_drift_attribution` block that
answers one question with a number - *how much of this R would a position of the
same direction, held for the same time, have earned from the sample's
unconditional drift alone?* A strategy whose edge is mostly that number is not
an edge, it is exposure, and it is withheld rather than published.

That screen runs **before** the debate, not in response to it. A specialist that
waits for a rival to find the hole in its own best finding has outsourced its
job. The ``withheld`` list in the ``findings`` artefact is the audit trail: the
candidates that cleared the robustness suite and were still not claimed, with
the numbers that disqualified them.

Cross-examination follows the same rule in the other direction. Every challenge
filed here carries a measurement taken by re-running the rival's strategy on
this agent's own frame - an adversarial split the owner did not choose, a
doubled-slippage re-run, a regime decomposition, a trade-level overlap against
this agent's own findings. Where a measurement was taken and did *not* support
an objection, that is recorded too, in ``measurements_taken``: "I looked and
found nothing" is a result, and a challenge list that only ever shows hits is
not evidence of rigour.
"""

from __future__ import annotations

import inspect
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..backtest.engine import BacktestEngine, Trade, run_portfolio
from ..backtest.metrics import Metrics, compute_metrics, slice_metrics
from ..backtest.robustness import (RobustnessReport, assess_robustness,
                                   deflated_expectancy, parameter_sensitivity)
from ..backtest.walkforward import (WalkForwardResult, robust_score,
                                    walk_forward)
from ..config import tf_label
from ..features import SymbolFrame
from ..strategies.base import Strategy
from ..strategies.combinator import generate_strategies
from ..team.agent import AgentResult
from ..team.board import Task
from ..team.roles import Role, families_for, opponents_of
from ..timeutil import et_stamp
from .base import DomainAgent
from .debate import (Challenge, ChallengeKind, Finding, Rebuttal, Verdict,
                     adversarial_retest, cost_stress, regime_concentration,
                     trade_overlap)
from .research import (MIN_TRADES_FOR_RANK, MIN_TRADES_PER_SLICE,
                       WF_MIN_TRAIN_FRACTION, StrategyResearchAgent)

__all__ = ["ResearchTrendAgent"]


# --------------------------------------------------------------------------
# Search budget. Deliberately smaller than the desk lead's: a specialist runs
# three times per debate round (research, challenge, rebut) and each rival
# finding costs several full passes to cross-examine, so the budget is spent on
# examining fewer candidates properly rather than more of them cheaply.
# --------------------------------------------------------------------------

#: Strategies that survive the leak-free screen into the walk-forward.
DEFAULT_WF_CANDIDATES = 12

#: How many of those earn the full robustness suite.
DEFAULT_FINALISTS = 3

#: Walk-forward folds. Fewer than the desk lead's six because the specialist
#: works on a windowed frame where six folds leave each fold too thin to mean
#: anything.
DEFAULT_FOLDS = 4

#: Rival findings cross-examined per opponent, best first.
DEFAULT_TARGETS_PER_RIVAL = 3

#: Extra bars prepended to a ``days``-limited window so the indicators are warm
#: when the evaluation window opens. Cold indicators depress early results,
#: which biases this agent's own findings downwards - the acceptable direction.
DEFAULT_WARMUP_BARS = 5_000


# --------------------------------------------------------------------------
# Thresholds. Each is the point at which a *measurement* becomes an argument;
# none of them is a tuning knob for making findings survive.
# --------------------------------------------------------------------------

#: Share of a finding's total R attributable to the sample's unconditional
#: drift, above which the "edge" is treated as directional exposure.
DRIFT_DOMINATED_SHARE = 0.60

#: Overlap at which two strategies are taking the same trades. Matches
#: ``find_redundancy``'s default so a challenge filed here and the pooler's own
#: de-duplication agree.
REDUNDANCY_THRESHOLD = 0.55

#: Fraction of baseline expectancy that must survive doubled slippage.
COST_RETENTION_FLOOR = 0.50

#: An adversarial split is only decisive when the losing half carries enough
#: trades to be a result rather than an accident.
MIN_TRADES_PER_SPLIT_HALF = 15

#: Primary adversarial split, and the confirming one. A fatal challenge is only
#: filed when both agree - one unlucky cut point is not an out-of-sample
#: failure.
PRIMARY_SPLIT = 0.5
CONFIRM_SPLIT = 0.65

#: Splits a rebuttal runs to answer an OUT_OF_SAMPLE_FAILURE. An edge that holds
#: across three different cut points was not fitted to one of them.
REBUTTAL_SPLITS = (0.40, 0.50, 0.60)

#: What the published ``trade_fingerprint`` indices mean. Stated in the artefact
#: because a fingerprint is only comparable between two specialists if both
#: index the same series.
FINGERPRINT_BASIS = ("absolute bar index into the symbol's full base series, "
                     "so fingerprints from different research windows are "
                     "directly comparable")


class ResearchTrendAgent(StrategyResearchAgent):
    """Researches directional continuation, and argues about it with evidence."""

    def __init__(self, fs, bus, *, context=None, config=None, llm=None):
        # Deliberately skips ``StrategyResearchAgent.__init__``, which binds the
        # desk lead's role. Everything else about that class - the sweep, the
        # persistence, the walk-forward plumbing - is inherited unchanged.
        DomainAgent.__init__(self, Role.RESEARCH_TREND, fs, bus,
                             context=context, config=config, llm=llm)

    # ------------------------------------------------------------------
    @property
    def families(self) -> Tuple[str, ...]:
        """The strategy families this seat owns, from the team's declaration."""
        return families_for(self.role)

    def handle(self, task: Task) -> AgentResult:
        if task.kind == "research_family":
            return self._research_family(task)
        if task.kind == "challenge":
            return self._challenge(task)
        if task.kind == "rebut":
            return self._rebut(task)
        raise ValueError(f"{self.id} does not implement task kind {task.kind!r}")

    # ==================================================================
    # research_family
    # ==================================================================
    def _research_family(self, task: Task) -> AgentResult:
        """Sweep this seat's families, walk-forward the best, publish findings.

        The order matters. Selection happens on the front of the research window
        only, so the walk-forward's out-of-sample segments contributed nothing to
        choosing what was validated on them. The drift screen then runs on what
        survives, before anything is claimed.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        if len(frame.base) == 0:
            return AgentResult(
                ok=True, summary=f"{symbol}: no bars available - nothing to test",
                payload={"symbol": symbol, "bars": 0, "families": list(self.families),
                         "note": "insufficient history; no strategies were tested"})

        days = self._int(task, "days", None)
        warmup = self._int(task, "warmup_bars", DEFAULT_WARMUP_BARS)
        folds = self._int(task, "folds", DEFAULT_FOLDS)
        n_candidates = self._int(task, "wf_candidates", DEFAULT_WF_CANDIDATES)
        n_finalists = self._int(task, "finalists", DEFAULT_FINALISTS)
        max_strategies = self._int(task, "max_strategies",
                                   self._cfg("max_combinations", 4_000))
        timeframes = self._timeframes(task, frame)

        research, window = self._research_frame(frame, days, warmup)
        strategies = self._family_universe(ctx, symbol, timeframes, max_strategies)
        if not strategies:
            return AgentResult(
                ok=True,
                summary=(f"{symbol}: no strategies could be constructed for "
                         f"{'/'.join(self.families)}"),
                payload={"symbol": symbol, "families": list(self.families),
                         "strategies": 0})

        # --- 1. one sweep over the research window ------------------------
        sweep = self._run_sweep(research, symbol, strategies, None)
        self._persist_sweep(sweep)
        trials = len(strategies)

        # --- 2. leak-free candidate screen --------------------------------
        screen_scores, candidates, screen_end = self._screen(
            research, strategies, n_candidates)

        # --- 3. walk-forward the survivors --------------------------------
        t0 = time.perf_counter()
        wf = walk_forward(research, candidates, folds=folds,
                          top_k=max(1, n_finalists), anchored=True,
                          progress=lambda k, total: self.log(
                              f"{symbol}: {'/'.join(self.families)} fold "
                              f"{k + 1}/{total}"))
        wf_seconds = time.perf_counter() - t0
        self.log(f"{symbol}: {wf.summary()} ({wf_seconds:.1f}s)")

        finalists = self._finalists(wf, candidates, screen_scores, n_finalists)

        # --- 4. robustness, then this seat's own drift screen --------------
        findings, withheld, assessed = self._assess_finalists(
            research, window, sweep, finalists, wf, trials, folds)

        section = self._findings_section(
            symbol=symbol, frame=research, window=window, sweep=sweep, wf=wf,
            trials=trials,
            screen_end=screen_end, candidates=candidates, finalists=finalists,
            findings=findings, withheld=withheld, assessed=assessed,
            wf_seconds=wf_seconds)
        section["llm_commentary"] = self._family_commentary(symbol, section)
        section["source"] = "hybrid" if section["llm_commentary"] else "deterministic"

        path = self._publish_sectioned("findings", symbol, section, "findings",
                                       f"{symbol}: {len(findings)} finding(s) "
                                       f"from {'/'.join(self.families)}, "
                                       f"{len(withheld)} withheld")

        summary = self._research_summary(symbol, sweep, wf, trials, findings,
                                         withheld, section)
        self.log(summary)
        return AgentResult(
            ok=True, summary=summary,
            payload={
                "symbol": symbol,
                "families": list(self.families),
                "strategies_tested": trials,
                "trials_searched": trials,
                "research_window": window,
                "sample_drift": section["sample_drift"],
                "walk_forward_efficiency": round(wf.efficiency, 4),
                "combined_oos": wf.combined_oos.to_dict(),
                "findings": [f["strategy_id"] for f in section["findings"]],
                "withheld": [w["strategy_id"] for w in withheld],
                "live_eligible": section["live_eligible"],
                "live_eligibility_note": section["live_eligibility_note"],
            },
            artefacts=[path])

    # ------------------------------------------------------------------
    def _screen(self, frame: SymbolFrame, strategies: Sequence[Strategy],
                limit: int) -> Tuple[Dict[str, float], List[Strategy], int]:
        """Rank candidates on the front of the window only.

        Screening on the whole window - the obvious thing to do - would leak
        every out-of-sample segment into the choice of what gets validated on
        them, and the walk-forward efficiency that came back would be measuring
        nothing.
        """
        n = len(frame.base)
        end = max(1, int(n * WF_MIN_TRAIN_FRACTION))
        t0 = time.perf_counter()
        results = run_portfolio(frame, list(strategies), start=0, end=end)
        scores = {sid: robust_score(compute_metrics(r.trades),
                                    min_trades=MIN_TRADES_FOR_RANK)
                  for sid, r in results.items()}
        chosen = set(sorted(scores, key=lambda s: (-scores[s], s))[:limit])
        candidates = [s for s in strategies if s.strategy_id in chosen]
        self.log(f"{frame.symbol}: screened {len(strategies)} -> "
                 f"{len(candidates)} candidates on bars 0..{end} "
                 f"({time.perf_counter() - t0:.1f}s)")
        return scores, candidates, end

    def _assess_finalists(self, frame: SymbolFrame, window: Dict[str, Any],
                          sweep, finalists: Sequence[Strategy],
                          wf: WalkForwardResult, trials: int, folds: int
                          ) -> Tuple[List[Tuple[Finding, Strategy, Dict[str, Any]]],
                                     List[Dict[str, Any]], int]:
        """Full robustness suite, then this agent's own drift screen.

        Returns the findings it is prepared to claim, the candidates it measured
        and deliberately did not claim, and how many were assessed.
        """
        ctx = self.require_context()
        offset = int(window.get("offset_in_base_series") or 0)
        kept: List[Tuple[Finding, Strategy, Dict[str, Any]]] = []
        withheld: List[Dict[str, Any]] = []
        risk_per_trade = self._risk_per_trade(ctx)

        for strategy in finalists:
            sid = strategy.strategy_id
            trades = sweep.results[sid].trades
            own = walk_forward(frame, [strategy], folds=folds, top_k=1,
                               anchored=True)
            sens = parameter_sensitivity(frame, strategy)
            report = assess_robustness(
                trades, strategy_id=sid, symbol=frame.symbol,
                walk_forward_result=own, sensitivity=sens,
                trials_searched=trials, cost_r=self._cost_r(frame, trades),
                monte_carlo_runs=self._cfg("monte_carlo_runs", 2_000),
                account_risk_per_trade=risk_per_trade,
                starting_equity=self._account_cfg("starting_equity", 50_000.0),
                failure_drawdown=self._account_cfg("max_total_drawdown", 5_000.0))
            self._persist_report(frame, strategy, trades, report, own)
            self.log(f"{frame.symbol}: {report.summary()}")

            metrics = report.metrics or compute_metrics(trades)
            drift = self._drift_attribution(frame, trades, metrics)
            concentration = regime_concentration(trades)
            verdict = self._own_screen(metrics, drift, own)
            evidence = {
                "drift_attribution": drift,
                "regime_concentration": concentration,
                "cost_in_r": self._cost_r(frame, trades),
                "own_walk_forward": own.to_dict(),
                "parameter_surface": {
                    "worst_relative": sens.get("worst_relative", 0.0),
                    "mean_relative": sens.get("mean_relative", 0.0),
                    "shape": ("plateau" if sens.get("worst_relative", 0.0) >= 0.4
                              else "spike"),
                },
                "robustness_reasons": report.reasons,
                "deflated_expectancy_r": round(report.deflated_expectancy_r, 6),
                "regime_slices": {
                    str(k): v.to_dict() for k, v in slice_metrics(
                        trades, "regime", min_trades=MIN_TRADES_PER_SLICE).items()},
                "selected_in_folds": [f.index for f in wf.folds
                                      if sid in f.selected],
            }

            if verdict["claimed"]:
                finding = Finding(
                    owner=self.id, symbol=frame.symbol, strategy_id=sid,
                    strategy_name=strategy.name, family=strategy.group,
                    timeframe=strategy.primary_tf, metrics=metrics,
                    robustness_score=report.score,
                    walk_forward_efficiency=own.efficiency,
                    trials_searched=trials,
                    # Copied verbatim from the robustness suite. This agent may
                    # decline to claim a finding at all, but it does not get to
                    # promote one.
                    live_eligible=report.live_eligible,
                    trade_fingerprint=[(t.entry_index + offset, t.direction.sign)
                                       for t in trades],
                    r_series=[t.net_r for t in trades],
                    claim=self._claim_text(strategy, metrics, own, drift, report))
                kept.append((finding, strategy, evidence))
            else:
                withheld.append({
                    "strategy_id": sid, "strategy_name": strategy.name,
                    "family": strategy.group,
                    "metrics": metrics.to_dict(),
                    "robustness_score": round(report.score, 4),
                    "live_eligible_per_robustness_suite": report.live_eligible,
                    "withheld_because": verdict["reasons"],
                    "drift_attribution": drift,
                    "note": ("measured, assessed and deliberately not claimed - "
                             "recorded so the screen that rejected it is "
                             "auditable rather than invisible"),
                })
        return kept, withheld, len(finalists)

    @staticmethod
    def _own_screen(metrics: Metrics, drift: Dict[str, Any],
                    own: WalkForwardResult) -> Dict[str, Any]:
        """This seat's pre-debate screen on its own candidates.

        Four ways a continuation candidate fails before anyone else looks at it:
        it never traded, the sample is too thin to distinguish from noise, it
        has no out-of-sample evidence at all, or its expectancy is the sample's
        drift wearing a rule.

        The sample floor is deliberately the same one this agent uses when it
        files ``SAMPLE_TOO_SMALL`` against a rival. Publishing a nine-trade
        finding while challenging a rival's nine-trade finding is not a research
        stance, it is a double standard, and the scorecard would show it.
        """
        reasons: List[str] = []
        if metrics.trades == 0:
            reasons.append("no trades generated")
        elif metrics.trades < MIN_TRADES_FOR_RANK:
            reasons.append(f"{metrics.trades} trades is below the "
                           f"{MIN_TRADES_FOR_RANK}-trade floor this desk applies "
                           f"to its rivals - nothing at that sample size is "
                           f"distinguishable from noise")
        elif metrics.expectancy_r <= 0:
            reasons.append(f"expectancy {metrics.expectancy_r:+.4f}R is not "
                           f"positive - there is no edge here to claim")
        if own.combined_oos.trades == 0:
            reasons.append("its own anchored walk-forward produced no "
                           "out-of-sample trades, so there is no out-of-sample "
                           "evidence to claim an edge from")
        share = drift.get("drift_share_of_total_r")
        net = drift.get("expectancy_net_of_drift_r")
        if (share is not None and share >= DRIFT_DOMINATED_SHARE
                and net is not None and net <= 0):
            reasons.append(
                f"{share * 100:.0f}% of total R is attributable to the sample's "
                f"drift and expectancy net of drift is {net:+.4f}R - this is "
                f"directional exposure to a drifting sample, not a continuation "
                f"edge")
        return {"claimed": not reasons, "reasons": reasons}

    def _claim_text(self, strategy: Strategy, metrics: Metrics,
                    own: WalkForwardResult, drift: Dict[str, Any],
                    report: RobustnessReport) -> str:
        share = drift.get("drift_share_of_total_r")
        if drift.get("fights_drift"):
            drift_bit = (f"the sample's drift works against this rule "
                         f"({drift.get('drift_attributed_r'):+.2f}R of the "
                         f"{drift.get('total_r'):+.2f}R total), so whatever this "
                         f"is, it is not directional exposure to the sample")
        elif share is None:
            drift_bit = ("total R is not positive, so there is no profit to "
                         "attribute to drift")
        else:
            drift_bit = (f"{share * 100:.0f}% of total R is attributable to the "
                         f"sample's drift; {drift.get('expectancy_net_of_drift_r'):+.4f}R "
                         f"per trade survives net of it")
        return (f"{strategy.group} on {tf_label(strategy.primary_tf)}: "
                f"{metrics.expectancy_r:+.4f}R over {metrics.trades} trades "
                f"(t={metrics.t_statistic:.2f}, PF {metrics.profit_factor:.2f}), "
                f"walk-forward efficiency {own.efficiency:.2f} over "
                f"{own.combined_oos.trades} out-of-sample trades, deflated "
                f"expectancy {report.deflated_expectancy_r:+.4f}R against "
                f"{report.trials_searched} searched combinations. {drift_bit}.")

    # ==================================================================
    # The measurement this seat brings to the debate
    # ==================================================================
    @staticmethod
    def _drift_attribution(frame: SymbolFrame, trades: Sequence[Trade],
                           metrics: Optional[Metrics] = None) -> Dict[str, Any]:
        """How much of an edge is just exposure to the sample's drift.

        A trend rule tested on a drifting sample is graded on a curve nobody
        declared. The benchmark here is deliberately crude and deliberately
        unarguable: take the window's *unconditional* drift per bar, and credit
        each trade with what a position of its own direction, held for its own
        number of bars, at its own risk size, would have earned from that drift
        alone. Sum it, and compare to what the strategy actually made.

        A continuation rule that beats its own drift benchmark has found
        something. One that does not has found the sample.
        """
        bars = frame.base.bars
        n = len(bars)
        if n < 2:
            return {"error": "window too short to measure drift", "bars": n}
        m = metrics or compute_metrics(trades)
        drift_per_bar = (bars[-1].close - bars[0].close) / float(n - 1)
        base = {
            "window_bars": n,
            "drift_points_total": round(bars[-1].close - bars[0].close, 4),
            "drift_points_per_bar": round(drift_per_bar, 8),
            "benchmark": ("per trade: direction x drift_points_per_bar x bars "
                          "held / risk_points - the R a position of the same "
                          "direction and duration earns from the sample's "
                          "unconditional drift alone"),
        }
        if not trades:
            base.update({"trades": 0, "error": "no trades to attribute"})
            return base

        attributed = 0.0
        measurable = 0
        for t in trades:
            if t.risk_points <= 0:
                continue
            held = max(1, int(t.exit_index or t.entry_index) - int(t.entry_index))
            attributed += t.direction.sign * drift_per_bar * held / t.risk_points
            measurable += 1
        total_r = sum(t.net_r for t in trades)
        # A negative share is a real and informative reading: the sample's drift
        # worked against this rule and it made money anyway. Collapsing that to
        # None would throw away the most interesting case this desk can find.
        share = (attributed / total_r) if total_r > 0 else None
        net_of_drift = ((total_r - attributed) / len(trades)) if trades else None

        base.update({
            "trades": len(trades),
            "trades_attributed": measurable,
            "total_r": round(total_r, 4),
            "expectancy_r": round(m.expectancy_r, 4),
            "drift_attributed_r": round(attributed, 4),
            "drift_share_of_total_r": None if share is None else round(share, 4),
            "expectancy_net_of_drift_r": (None if net_of_drift is None
                                          else round(net_of_drift, 4)),
            "long_trades": m.long_trades, "short_trades": m.short_trades,
            "long_expectancy_r": round(m.long_expectancy_r, 4),
            "short_expectancy_r": round(m.short_expectancy_r, 4),
            "direction_balance": (round((m.long_trades - m.short_trades)
                                        / float(m.trades), 4) if m.trades else None),
            # A rule that made money against the drift is the interesting case:
            # whatever it found, it is not the sample's slope.
            "fights_drift": attributed < 0 and total_r > 0,
            "drift_dominated": bool(share is not None
                                    and share >= DRIFT_DOMINATED_SHARE),
            "share_note": ("drift_share_of_total_r is null only when total R is "
                           "not positive, so there is no profit to attribute. A "
                           "negative share means the drift cost this rule money "
                           "and the edge is not directional exposure."),
        })
        return base

    # ==================================================================
    # challenge
    # ==================================================================
    def _challenge(self, task: Task) -> AgentResult:
        """Cross-examine both rivals, with a measurement behind every objection.

        Every number here is taken by re-running the rival's strategy on this
        agent's own frame. Nothing is inferred from what the rival published
        about itself - an objection built on the other side's arithmetic is not
        an independent check of it.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        if len(frame.base) == 0:
            return AgentResult(
                ok=True, summary=f"{symbol}: no bars - nothing can be measured",
                payload={"symbol": symbol, "challenges": 0})

        research, window = self._research_frame(
            frame, self._int(task, "days", self._recorded_days(symbol)),
            self._int(task, "warmup_bars", DEFAULT_WARMUP_BARS))
        offset = int(window.get("offset_in_base_series") or 0)
        per_rival = self._int(task, "targets_per_rival", DEFAULT_TARGETS_PER_RIVAL)
        mine = self._own_findings(symbol)
        self.log(f"{symbol}: cross-examining on {window['bars']:,} bars "
                 f"(offset {window['offset_in_base_series']:,}) - the same "
                 f"window these findings were measured on")

        challenges: List[Challenge] = []
        measurements: List[Dict[str, Any]] = []
        notes: List[str] = []
        unreadable: List[str] = []

        for opponent in opponents_of(self.role):
            rows, note = self._rival_findings(opponent, symbol)
            if note:
                notes.append(note)
            if not rows:
                unreadable.append(opponent.value)
                continue
            for row in rows[:per_rival]:
                filed, taken = self._examine(research, offset, opponent, row, mine)
                challenges.extend(filed)
                measurements.extend(taken)

        section = {
            "generated_et": et_stamp(),
            "symbol": symbol,
            "source": "deterministic",
            "challenger": self.id,
            "families": list(self.families),
            "opponents": [r.value for r in opponents_of(self.role)],
            "research_window": window,
            "challenges": [c.to_dict() for c in challenges],
            "measurements_taken": measurements,
            "rivals_without_readable_findings": unreadable,
            "notes": notes,
            "method": (
                "Every measurement was taken by re-running the rival's strategy "
                "on this agent's frame. A rival strategy that is not in the "
                "shared registry cannot be re-run, so no challenge is filed "
                "against it at all - an unmeasured objection is discarded by "
                "the pooler and contributes nothing."),
            "by_kind": self._count_kinds(challenges),
        }
        path = self._publish_sectioned(
            "challenges", symbol, section, "challenges",
            f"{symbol}: {len(challenges)} measured challenge(s) against "
            f"{len(set(c.target_owner for c in challenges))} rival(s)")

        summary = self._challenge_summary(symbol, challenges, measurements,
                                          unreadable, notes)
        self.log(summary)
        return AgentResult(
            ok=True, summary=summary,
            payload={"symbol": symbol,
                     "challenges_filed": len(challenges),
                     "measurements_taken": len(measurements),
                     "by_kind": section["by_kind"],
                     "rivals_without_readable_findings": unreadable,
                     "notes": notes},
            artefacts=[path])

    def _examine(self, frame: SymbolFrame, offset: int, opponent: Role,
                 row: Dict[str, Any], mine: List[Dict[str, Any]]
                 ) -> Tuple[List[Challenge], List[Dict[str, Any]]]:
        """Re-run one rival finding and file whatever the numbers support."""
        ctx = self.require_context()
        sid = str(row.get("strategy_id") or "")
        family = str(row.get("family") or
                     (row.get("strategy") or {}).get("group") or "")
        strategy = ctx.registry.get(sid) if sid else None
        if strategy is None:
            return [], [{
                "target_owner": opponent.value, "target_strategy_id": sid,
                "family": family, "measured": False,
                "reason": ("strategy is not in the shared registry, so it could "
                           "not be re-run; no challenge filed rather than an "
                           "unsubstantiated one"),
            }]

        symbol = frame.symbol
        engine = BacktestEngine(frame)
        trades = engine.run(strategy).trades
        metrics = compute_metrics(trades)
        fingerprint = [(t.entry_index + offset, t.direction.sign) for t in trades]
        cost_r = self._cost_r(frame, trades)

        filed: List[Challenge] = []
        taken: List[Dict[str, Any]] = []

        def file(kind: ChallengeKind, claim: str, measurement: Dict[str, Any]) -> None:
            filed.append(Challenge(
                challenger=self.id, target_owner=opponent.value,
                target_strategy_id=sid, symbol=symbol, kind=kind, claim=claim,
                measurement=measurement))

        def record(name: str, result: Dict[str, Any], supported: bool,
                   verdict: str) -> None:
            taken.append({"target_owner": opponent.value,
                          "target_strategy_id": sid, "family": family,
                          "measurement": name, "measured": True,
                          "supported_a_challenge": supported,
                          "finding": verdict, "result": result})

        # ---- 1. sample size, re-counted rather than taken on trust -------
        sample = {"trades_on_my_window": metrics.trades,
                  "floor": MIN_TRADES_FOR_RANK,
                  "window_bars": len(frame.base),
                  "expectancy_r": round(metrics.expectancy_r, 4)}
        if metrics.trades < MIN_TRADES_FOR_RANK:
            file(ChallengeKind.SAMPLE_TOO_SMALL,
                 f"Re-run on my research window this rule produced "
                 f"{metrics.trades} trades against a {MIN_TRADES_FOR_RANK}-trade "
                 f"floor. At that sample nothing distinguishes it from noise, "
                 f"whatever the profit factor says.", sample)
            record("sample_count", sample, True,
                   f"{metrics.trades} trades - below the floor")
            # Nothing below is worth measuring on a sample this thin.
            return filed, taken
        record("sample_count", sample, False,
               f"{metrics.trades} trades - clears the floor")

        # ---- 2. the split its owner did not choose -----------------------
        primary = adversarial_retest(frame, strategy, skip_fraction=PRIMARY_SPLIT)
        if "error" not in primary:
            decisive = self._split_is_decisive(primary)
            if decisive:
                confirm = adversarial_retest(frame, strategy,
                                             skip_fraction=CONFIRM_SPLIT)
                both = decisive and self._split_is_decisive(confirm)
                measurement = {"primary_split": primary,
                               "confirming_split": confirm,
                               "splits_tried": [PRIMARY_SPLIT, CONFIRM_SPLIT],
                               "agreed": both}
                if both:
                    file(ChallengeKind.OUT_OF_SAMPLE_FAILURE,
                         f"On a {PRIMARY_SPLIT:.0%}/{1 - PRIMARY_SPLIT:.0%} split "
                         f"this rule earns "
                         f"{primary['first_half']['expectancy_r']:+.3f}R in the "
                         f"first segment and "
                         f"{primary['second_half']['expectancy_r']:+.3f}R in the "
                         f"second (consistency "
                         f"{primary['consistency_ratio']:.2f}), and the same "
                         f"collapse repeats at a "
                         f"{CONFIRM_SPLIT:.0%} cut. Two independent cut points "
                         f"agree: the edge lives in one segment.", measurement)
                    record("adversarial_retest", measurement, True,
                           "collapsed on two independent splits")
                else:
                    record("adversarial_retest", measurement, False,
                           "collapsed on one split but not the confirming one - "
                           "not filed")
            else:
                record("adversarial_retest", primary, False,
                       "holds across the split its owner did not choose")
        else:
            record("adversarial_retest", primary, False,
                   "window too short to split")

        # ---- 3. cost. Fades run tight targets; that is testable ----------
        stress = cost_stress(frame, strategy)
        stress["round_turn_cost_in_r"] = cost_r
        stress["cost_note"] = (
            "round_turn_cost_in_r is the transaction cost as a fraction of one "
            "R at this strategy's median stop distance. An edge smaller than "
            "its own cost is not an edge.")
        retained = float(stress.get("retained_fraction") or 0.0)
        fade = family in frozenset(families_for(Role.RESEARCH_REVERSION))
        if not stress.get("survives") or retained < COST_RETENTION_FLOOR:
            structural = (" A fade runs a tight target by construction, so the "
                          "round-turn cost is a large fraction of the R it is "
                          "trying to capture - this is structural, not bad luck."
                          if fade else "")
            file(ChallengeKind.COST_FRAGILE,
                 f"Under doubled slippage expectancy goes from "
                 f"{stress['baseline_expectancy_r']:+.4f}R to "
                 f"{stress['stressed_expectancy_r']:+.4f}R, retaining "
                 f"{retained:.0%}"
                 + (f"; round-turn cost is {cost_r:.3f}R at this rule's median "
                    f"stop" if cost_r is not None else "")
                 + f".{structural}", stress)
            record("cost_stress", stress, True,
                   f"retains {retained:.0%} under doubled slippage")
        else:
            record("cost_stress", stress, False,
                   f"retains {retained:.0%} under doubled slippage - survives, "
                   f"so no challenge is filed"
                   + (" despite being a fade family" if fade else ""))

        # ---- 4. is the profit a period rather than a rule? ---------------
        concentration = regime_concentration(trades)
        concentration["drift_attribution"] = self._drift_attribution(
            frame, trades, metrics)
        if concentration.get("concentrated"):
            file(ChallengeKind.REGIME_ARTEFACT,
                 f"{concentration['dominant_share']:.0%} of this rule's total R "
                 f"comes from the {concentration['dominant_regime']} regime on "
                 f"{concentration['dominant_sample']} trades. That is a "
                 f"description of a period, not a rule that can be relied on in "
                 f"the next one.", concentration)
            record("regime_concentration", concentration, True,
                   f"{concentration['dominant_share']:.0%} in "
                   f"{concentration['dominant_regime']}")
        else:
            record("regime_concentration", concentration, False,
                   "profit is not concentrated in one thin regime")

        # ---- 5. is their edge my edge? -----------------------------------
        overlap_result = self._overlap_against_mine(fingerprint, mine)
        if overlap_result["best_overlap"] >= REDUNDANCY_THRESHOLD:
            file(ChallengeKind.REDUNDANT,
                 f"This rule's entries overlap mine "
                 f"({overlap_result['best_match']}) by "
                 f"{overlap_result['best_overlap']:.0%} at trade level. Whatever "
                 f"the two rule sets are called, they are taking the same "
                 f"trades, and pooling both would read as corroboration when it "
                 f"is one edge counted twice.", overlap_result)
            record("trade_overlap", overlap_result, True,
                   f"{overlap_result['best_overlap']:.0%} overlap with "
                   f"{overlap_result['best_match']}")
        else:
            record("trade_overlap", overlap_result, False,
                   f"best overlap {overlap_result['best_overlap']:.0%} - "
                   f"independent of my findings")
        return filed, taken

    @staticmethod
    def _split_is_decisive(split: Dict[str, Any]) -> bool:
        """Whether an adversarial split genuinely failed rather than wobbled."""
        if not split or "error" in split:
            return False
        first = split.get("first_half") or {}
        second = split.get("second_half") or {}
        if first.get("trades", 0) < MIN_TRADES_PER_SPLIT_HALF:
            return False
        if second.get("trades", 0) < MIN_TRADES_PER_SPLIT_HALF:
            return False
        # The claim is "it worked where it was looked at and not elsewhere".
        return (first.get("expectancy_r", 0.0) > 0
                and second.get("expectancy_r", 0.0) <= 0)

    def _overlap_against_mine(self, fingerprint: Sequence[Tuple[int, int]],
                              mine: List[Dict[str, Any]]) -> Dict[str, Any]:
        best = 0.0
        best_id = ""
        pairs: List[Dict[str, Any]] = []
        for row in mine:
            own = self._fingerprint_of(row)
            if not own:
                continue
            value = trade_overlap(fingerprint, own)
            pairs.append({"my_strategy_id": row.get("strategy_id"),
                          "overlap": round(value, 4),
                          "my_trades": len(own)})
            if value > best:
                best, best_id = value, str(row.get("strategy_id") or "")
        pairs.sort(key=lambda p: -p["overlap"])
        return {
            "best_overlap": round(best, 4),
            "best_match": best_id or "none",
            # Named explicitly so the responder can re-run it and recompute the
            # overlap itself instead of trusting a fingerprint it did not take.
            "challenger_strategy_id": best_id,
            "threshold": REDUNDANCY_THRESHOLD,
            "their_trades": len(fingerprint),
            "compared_against": len(pairs),
            "pairs": pairs[:5],
            "fingerprint_basis": FINGERPRINT_BASIS,
            "note": ("their fingerprint was taken by re-running their strategy "
                     "on my frame, so both sides index the same bars"),
        }

    # ==================================================================
    # rebut
    # ==================================================================
    def _rebut(self, task: Task) -> AgentResult:
        """Answer every challenge filed against this seat's findings.

        Conceding a well-measured challenge is the correct outcome, not a loss.
        An unmeasured ``REBUTTED`` is downgraded to ``UNANSWERED`` by the pooler,
        so asserting a finding is fine is strictly worse than admitting it is
        not - and this agent does not assert.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        if len(frame.base) == 0:
            return AgentResult(
                ok=True, summary=f"{symbol}: no bars - nothing to answer with",
                payload={"symbol": symbol, "rebuttals": 0})

        research, window = self._research_frame(
            frame, self._int(task, "days", self._recorded_days(symbol)),
            self._int(task, "warmup_bars", DEFAULT_WARMUP_BARS))
        offset = int(window.get("offset_in_base_series") or 0)
        mine = {str(r.get("strategy_id")): r for r in self._own_findings(symbol)}
        self.log(f"{symbol}: answering on {window['bars']:,} bars "
                 f"(offset {window['offset_in_base_series']:,})")

        rebuttals: List[Rebuttal] = []
        notes: List[str] = []
        incoming = 0
        for opponent in opponents_of(self.role):
            rows, note = self._rival_challenges(opponent, symbol)
            if note:
                notes.append(note)
            for row in rows:
                if str(row.get("target_owner") or "") != self.id:
                    continue
                incoming += 1
                rebuttals.append(
                    self._answer(research, offset, row, mine, notes))

        counts = {v.value: sum(1 for r in rebuttals if r.verdict is v)
                  for v in Verdict}
        section = {
            "generated_et": et_stamp(),
            "symbol": symbol,
            "source": "deterministic",
            "responder": self.id,
            "families": list(self.families),
            "research_window": window,
            "challenges_received": incoming,
            "rebuttals": [r.to_dict() for r in rebuttals],
            "by_verdict": {k: v for k, v in counts.items() if v},
            "unmeasured_rebuttals": sum(1 for r in rebuttals
                                        if r.verdict is Verdict.REBUTTED
                                        and not r.is_substantiated),
            "notes": notes,
            "policy": (
                "A REBUTTED verdict is only returned where a counter-measurement "
                "exists. Where the challenger's measurement stands, the verdict "
                "is CONCEDED - the finding was withdrawn, which is the correct "
                "outcome of a cross-examination that found something."),
        }
        if section["by_verdict"]:
            headline = (f"{symbol}: answered {incoming} challenge(s) - "
                        + ", ".join(f"{v} {k.lower()}"
                                    for k, v in section["by_verdict"].items()))
        else:
            headline = f"{symbol}: no challenges to answer"
        path = self._publish_sectioned("rebuttals", symbol, section,
                                       "rebuttals", headline)

        summary = self._rebut_summary(symbol, incoming, section, notes)
        self.log(summary)
        return AgentResult(
            ok=True, summary=summary,
            payload={"symbol": symbol, "challenges_received": incoming,
                     "rebuttals_filed": len(rebuttals),
                     "by_verdict": section["by_verdict"],
                     "unmeasured_rebuttals": section["unmeasured_rebuttals"],
                     "notes": notes},
            artefacts=[path])

    def _answer(self, frame: SymbolFrame, offset: int, row: Dict[str, Any],
                mine: Dict[str, Dict[str, Any]], notes: List[str]) -> Rebuttal:
        """Produce one measured answer, or concede."""
        ctx = self.require_context()
        challenger = str(row.get("challenger") or "unknown")
        sid = str(row.get("target_strategy_id") or "")
        try:
            kind = ChallengeKind(str(row.get("kind")))
        except ValueError:
            kind = None
        strategy = ctx.registry.get(sid)

        def rebuttal(verdict: Verdict, argument: str,
                     measurement: Optional[Dict[str, Any]] = None) -> Rebuttal:
            return Rebuttal(responder=self.id, challenger=challenger,
                            target_strategy_id=sid, verdict=verdict,
                            argument=argument, measurement=measurement or {})

        if strategy is None or kind is None:
            return rebuttal(
                Verdict.CONCEDED,
                (f"Cannot re-measure {sid or 'this finding'}"
                 f"{' - it is not in the shared registry' if strategy is None else ''}"
                 f"{' - the challenge kind was not recognised' if kind is None else ''}"
                 f". With no counter-measurement available the challenge stands; "
                 f"an unmeasured denial would be downgraded to UNANSWERED anyway."))

        engine = BacktestEngine(frame)
        trades = engine.run(strategy).trades
        metrics = compute_metrics(trades)

        if kind is ChallengeKind.SAMPLE_TOO_SMALL:
            measurement = {"trades_on_my_window": metrics.trades,
                           "floor": MIN_TRADES_FOR_RANK,
                           "window_bars": len(frame.base)}
            if metrics.trades < MIN_TRADES_FOR_RANK:
                return rebuttal(Verdict.CONCEDED,
                                f"Confirmed: {metrics.trades} trades against a "
                                f"{MIN_TRADES_FOR_RANK}-trade floor. The sample "
                                f"cannot carry the claim and the finding is "
                                f"withdrawn.", measurement)
            return rebuttal(Verdict.REBUTTED,
                            f"Re-counted on my research window: {metrics.trades} "
                            f"trades, above the {MIN_TRADES_FOR_RANK}-trade "
                            f"floor. The challenger measured a different window.",
                            measurement)

        if kind is ChallengeKind.OUT_OF_SAMPLE_FAILURE:
            splits = {f"split_{int(f * 100)}": adversarial_retest(
                frame, strategy, skip_fraction=f) for f in REBUTTAL_SPLITS}
            usable = [s for s in splits.values() if "error" not in s]
            held = [s for s in usable if s.get("both_halves_positive")]
            measurement = {
                "splits": splits,
                "splits_tried": list(REBUTTAL_SPLITS),
                "splits_holding": len(held), "splits_usable": len(usable),
                "note": ("an edge that survives three different cut points was "
                         "not fitted to any one of them"),
            }
            if usable and len(held) == len(usable):
                return rebuttal(Verdict.REBUTTED,
                                f"The finding holds at all {len(usable)} cut "
                                f"points tried ({', '.join(str(f) for f in REBUTTAL_SPLITS)}), "
                                f"with both halves positive in each. A single "
                                f"unfavourable cut point straddles a regime "
                                f"change; three agreeing ones do not.",
                                measurement)
            if held:
                return rebuttal(Verdict.PARTIAL,
                                f"The finding holds at {len(held)} of "
                                f"{len(usable)} cut points. The claim is "
                                f"narrowed accordingly: it is not stable across "
                                f"every split, and should be treated as "
                                f"conditional rather than general.", measurement)
            return rebuttal(Verdict.CONCEDED,
                            f"Confirmed across {len(usable)} independent cut "
                            f"points: no split leaves both halves positive. The "
                            f"finding is withdrawn.", measurement)

        if kind is ChallengeKind.COST_FRAGILE:
            stress = cost_stress(frame, strategy)
            stress["round_turn_cost_in_r"] = self._cost_r(frame, trades)
            retained = float(stress.get("retained_fraction") or 0.0)
            if stress.get("survives") and retained >= COST_RETENTION_FLOOR:
                return rebuttal(Verdict.REBUTTED,
                                f"Re-run under doubled slippage the edge retains "
                                f"{retained:.0%} of baseline expectancy "
                                f"({stress['stressed_expectancy_r']:+.4f}R) and "
                                f"stays positive. Continuation entries run wide "
                                f"targets, so cost is a smaller fraction of R "
                                f"here than the challenge assumes.", stress)
            return rebuttal(Verdict.CONCEDED,
                            f"Confirmed: doubled slippage leaves "
                            f"{stress['stressed_expectancy_r']:+.4f}R "
                            f"({retained:.0%} retained). An edge that only "
                            f"exists at optimistic fills is not one.", stress)

        if kind is ChallengeKind.REGIME_ARTEFACT:
            concentration = regime_concentration(trades)
            drift = self._drift_attribution(frame, trades, metrics)
            measurement = {"regime_concentration": concentration,
                           "drift_attribution": drift}
            if concentration.get("concentrated") or drift.get("drift_dominated"):
                return rebuttal(
                    Verdict.CONCEDED,
                    "Confirmed. "
                    + (f"{concentration.get('dominant_share', 0):.0%} of total R "
                       f"sits in {concentration.get('dominant_regime')} on "
                       f"{concentration.get('dominant_sample')} trades. "
                       if concentration.get("concentrated") else "")
                    + (f"{(drift.get('drift_share_of_total_r') or 0):.0%} of "
                       f"total R is attributable to the sample's drift. "
                       if drift.get("drift_dominated") else "")
                    + "This is a period, not a rule, and the finding is "
                      "withdrawn.", measurement)
            spread = {k: v for k, v in (concentration.get("counts") or {}).items()
                      if v >= MIN_TRADES_PER_SLICE}
            return rebuttal(
                Verdict.REBUTTED,
                f"The profit is not concentrated: the dominant regime carries "
                f"{(concentration.get('dominant_share') or 0):.0%} of total R "
                f"across {len(spread)} regime(s) with a usable sample, and "
                f"{(drift.get('drift_share_of_total_r') or 0):.0%} of total R is "
                f"drift-attributable, below the "
                f"{DRIFT_DOMINATED_SHARE:.0%} bar this desk applies to its own "
                f"findings.", measurement)

        if kind is ChallengeKind.REDUNDANT:
            measured_by = "re-ran the challenger's strategy on my frame"
            theirs = self._rerun_fingerprint(frame, offset,
                                             row.get("measurement") or {})
            if not theirs:
                measured_by = "the fingerprint published in the challenge"
                theirs = self._fingerprint_of(row.get("measurement") or {})
            ours = [(t.entry_index + offset, t.direction.sign) for t in trades]
            if not theirs:
                return rebuttal(
                    Verdict.CONCEDED,
                    "The challenge carries no comparable fingerprint for the "
                    "other side, so the overlap cannot be recomputed here. With "
                    "no counter-measurement the challenge stands.",
                    {"my_trades": len(ours), "their_fingerprint": "unavailable"})
            value = trade_overlap(ours, theirs)
            measurement = {"recomputed_overlap": round(value, 4),
                           "threshold": REDUNDANCY_THRESHOLD,
                           "my_trades": len(ours), "their_trades": len(theirs),
                           "their_side_measured_by": measured_by,
                           "fingerprint_basis": FINGERPRINT_BASIS}
            if value >= REDUNDANCY_THRESHOLD:
                return rebuttal(Verdict.CONCEDED,
                                f"Confirmed at {value:.0%} trade-level overlap. "
                                f"It is the same edge and should be counted "
                                f"once.", measurement)
            return rebuttal(Verdict.REBUTTED,
                            f"Recomputed overlap is {value:.0%}, below the "
                            f"{REDUNDANCY_THRESHOLD:.0%} threshold. The two rule "
                            f"sets are not taking the same trades.", measurement)

        if kind is ChallengeKind.DATA_MINING:
            row_finding = mine.get(sid) or {}
            section = self._section_of("findings", frame.symbol)
            trials = int(row_finding.get("trials_searched")
                         or section.get("trials_searched") or 1)
            deflated = deflated_expectancy(metrics, trials)
            measurement = {"trials_searched": trials,
                           "t_statistic": round(metrics.t_statistic, 4),
                           "deflated_expectancy_r": round(deflated, 6),
                           "trades": metrics.trades}
            if deflated <= 0:
                return rebuttal(Verdict.CONCEDED,
                                f"Confirmed: against {trials} searched "
                                f"combinations the deflated expectancy is "
                                f"{deflated:+.5f}R. Nothing survives the search "
                                f"correction and the finding is withdrawn.",
                                measurement)
            return rebuttal(Verdict.REBUTTED,
                            f"Deflated against the true trial count of {trials}, "
                            f"expectancy is still {deflated:+.5f}R over "
                            f"{metrics.trades} trades.", measurement)

        return rebuttal(Verdict.CONCEDED,
                        f"No measurement procedure is defined here for "
                        f"{kind.value}; the challenge stands unanswered rather "
                        f"than being denied without evidence.")

    # ==================================================================
    # Universe and window
    # ==================================================================
    def _family_universe(self, ctx, symbol: str, timeframes: Sequence[int],
                         max_strategies: int) -> List[Strategy]:
        """This seat's families only, generated once and registered once.

        Prefers the inherited ``_universe`` when it exposes a ``groups=``
        filter. Where it does not, the family slice is generated with the same
        ``generate_strategies(groups=...)`` call ``_universe`` uses internally -
        which spends the whole search budget on this seat's families instead of
        a tenth of it, and leaves the other specialists' families alone.
        """
        families = frozenset(self.families)
        if self._universe_accepts_groups():
            found = super()._universe(ctx, symbol, timeframes, max_strategies,
                                      generate=False, groups=tuple(self.families))
            mine = [s for s in found if s.group in families]
            if mine:
                return mine

        registered = [s for s in ctx.registry.symbol(symbol).all()
                      if s.group in families]
        if len(registered) >= max_strategies:
            return registered

        generated = generate_strategies(symbol, list(timeframes),
                                        groups=tuple(self.families),
                                        max_total=max_strategies)
        added = 0
        for s in generated:
            if ctx.registry.get(s.strategy_id) is None:
                ctx.registry.add(s)
                added += 1
        mine = {s.strategy_id: s for s in registered}
        mine.update({s.strategy_id: s for s in generated if s.group in families})
        out = sorted(mine.values(), key=lambda s: (s.group, s.primary_tf, s.name))
        self.log(f"{symbol}: {len(out)} strategies across "
                 f"{'/'.join(self.families)} over timeframes "
                 f"{[tf_label(t) for t in timeframes]} ({added} newly registered)")
        return out

    @staticmethod
    def _universe_accepts_groups() -> bool:
        """Whether the inherited ``_universe`` can filter by family yet."""
        try:
            params = inspect.signature(StrategyResearchAgent._universe).parameters
        except (TypeError, ValueError):                     # pragma: no cover
            return False
        return "groups" in params

    def _research_frame(self, frame: SymbolFrame, days: Optional[int],
                        warmup: Optional[int]) -> Tuple[SymbolFrame, Dict[str, Any]]:
        """The frame the sweep, the screen and the walk-forward all run on.

        One frame for all three stages, deliberately. Sweeping a short window
        and then validating on the full history would mean the strategies were
        chosen on bars the walk-forward later calls out-of-sample, which inflates
        walk-forward efficiency for free. Where ``days`` shortens the window, a
        warm-up margin is prepended so the indicators are not cold when the
        window opens; those bars are evaluated too, which depresses early
        results rather than flattering them.
        """
        n = len(frame.base)
        full = {
            "days_requested": days, "bars": n, "bars_available": n,
            "offset_in_base_series": 0, "warmup_bars": 0,
            "start_et": et_stamp(frame.base.bars[0].ts) if n else None,
            "end_et": et_stamp(frame.base.bars[-1].ts) if n else None,
            "note": "full available history",
        }
        if not days or days <= 0:
            return frame, full

        start, window = self._window(frame, days)
        if start <= 0:
            full["days_requested"] = days
            full["trading_days"] = window.get("trading_days")
            return frame, full

        margin = max(0, int(warmup if warmup is not None else DEFAULT_WARMUP_BARS))
        begin = max(0, start - margin)
        t0 = time.perf_counter()
        sub = SymbolFrame(frame.base[begin:], frame.timeframes, frame.spec)
        bars = sub.base.bars
        self.log(f"{frame.symbol}: research window {len(bars):,} bars "
                 f"({window.get('trading_days')} trading days + "
                 f"{start - begin:,} warm-up bars) built in "
                 f"{time.perf_counter() - t0:.1f}s")
        return sub, {
            "days_requested": days,
            "trading_days": window.get("trading_days"),
            "bars": len(bars), "bars_available": n,
            "offset_in_base_series": begin,
            "warmup_bars": start - begin,
            "start_et": et_stamp(bars[0].ts), "end_et": et_stamp(bars[-1].ts),
            "note": (f"last {window.get('trading_days')} trading days plus "
                     f"{start - begin:,} warm-up bars. Sweep, candidate screen "
                     f"and walk-forward all run on exactly these bars, so "
                     f"nothing was selected on data the walk-forward then "
                     f"reported as out-of-sample."),
        }

    def _recorded_days(self, symbol: str) -> Optional[int]:
        """The window this seat used when it published its findings.

        A challenge measured on a different window than the findings it is
        compared against is not measuring the same thing, so the window is
        recovered rather than re-guessed.
        """
        section = self._section_of("findings", symbol)
        window = section.get("research_window") if isinstance(section, dict) else None
        if isinstance(window, dict):
            value = window.get("days_requested")
            if isinstance(value, int) and value > 0:
                return value
        return None

    # ==================================================================
    # Reporting
    # ==================================================================
    def _findings_section(self, *, symbol: str, frame: SymbolFrame,
                          window: Dict[str, Any], sweep,
                          wf: WalkForwardResult, trials: int, screen_end: int,
                          candidates: Sequence[Strategy],
                          finalists: Sequence[Strategy],
                          findings: Sequence[Tuple[Finding, Strategy, Dict[str, Any]]],
                          withheld: Sequence[Dict[str, Any]], assessed: int,
                          wf_seconds: float) -> Dict[str, Any]:
        eligible = [f.strategy_id for f, _s, _e in findings if f.live_eligible]
        by_family: Dict[str, int] = {}
        for s in sweep.strategies:
            by_family[s.group] = by_family.get(s.group, 0) + 1
        return {
            "generated_et": et_stamp(),
            "symbol": symbol,
            "owner": self.id,
            "source": "deterministic",
            "families": list(self.families),
            "stance": (
                "Continuation edges are real and are the easiest to fake: a "
                "drifting sample makes almost any long-biased rule look good. "
                "Every finding below is reported with the share of its R that "
                "the sample's unconditional drift accounts for, and candidates "
                "whose edge is drift are withheld rather than published."),
            "research_window": window,
            # Universe-wide: across every trade these families took on this
            # window, how much of the total R is the sample's slope rather than
            # the rules. It is the headline number this seat exists to report.
            "sample_drift": self._drift_attribution(frame, sweep.trades),
            "universe": {"strategies": trials, "by_family": by_family,
                         "by_timeframe": self._by_timeframe(sweep.strategies)},
            "trials_searched": trials,
            "trials_note": (
                f"{trials} combinations were generated and swept across "
                f"{'/'.join(self.families)} for {symbol}, and every one was "
                f"persisted - including those that never traded. This is the "
                f"number that deflates the t-statistic; understating it would "
                f"re-inflate every finding's significance."),
            "survivorship": {
                "strategies_swept": trials,
                "with_trades": sum(1 for m in sweep.metrics.values() if m.trades > 0),
                "zero_trades": sum(1 for m in sweep.metrics.values() if m.trades == 0),
                "positive_expectancy": sum(1 for m in sweep.metrics.values()
                                           if m.trades > 0 and m.expectancy_r > 0),
                "cleared_trade_floor": sum(
                    1 for m in sweep.metrics.values()
                    if m.trades >= MIN_TRADES_FOR_RANK and m.expectancy_r > 0),
            },
            "selection": {
                "screen_window_bars": [0, screen_end],
                "candidates": len(candidates),
                "finalists": [s.strategy_id for s in finalists],
                "note": ("candidates were ranked on the front of the research "
                         "window only, so no out-of-sample bar contributed to "
                         "the pool selection"),
            },
            "walk_forward": wf.to_dict(),
            "findings": [self._finding_payload(f, s, e) for f, s, e in findings],
            "withheld": list(withheld),
            "withheld_note": (
                "These candidates were measured and assessed exactly like the "
                "published findings, and were not claimed. They are listed so "
                "the screen that rejected them can be audited."),
            "finalists_assessed": assessed,
            "live_eligible": eligible,
            "live_eligibility_note": self._eligibility_note_for(
                symbol, eligible, findings, withheld, wf),
            "fingerprint_basis": FINGERPRINT_BASIS,
            "timings_s": {"sweep": round(sweep.seconds, 2),
                          "persist": round(sweep.persist_seconds, 2),
                          "walk_forward": round(wf_seconds, 2)},
        }

    def _finding_payload(self, finding: Finding, strategy: Strategy,
                         evidence: Dict[str, Any]) -> Dict[str, Any]:
        """A finding as published.

        ``Finding.to_dict`` deliberately omits the trade fingerprint and the R
        series - they are bulky - but redundancy detection is defined on the
        fingerprint, so a findings artefact without it cannot be cross-examined
        by anyone. Both are added back here.
        """
        payload = finding.to_dict()
        payload.update({
            "strategy": strategy.to_dict(),
            "trade_fingerprint": [[int(i), int(d)]
                                  for i, d in finding.trade_fingerprint],
            "fingerprint_basis": FINGERPRINT_BASIS,
            "r_series": [round(float(r), 6) for r in finding.r_series],
            "evidence": evidence,
            "live_eligible_source": (
                "copied verbatim from RobustnessReport.live_eligible; this agent "
                "can decline to claim a finding but never promotes one"),
        })
        return payload

    @staticmethod
    def _by_timeframe(strategies: Sequence[Strategy]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for s in strategies:
            key = tf_label(s.primary_tf)
            out[key] = out.get(key, 0) + 1
        return out

    def _eligibility_note_for(self, symbol: str, eligible: Sequence[str],
                              findings: Sequence[Any],
                              withheld: Sequence[Any],
                              wf: WalkForwardResult) -> str:
        if eligible:
            return (f"{len(eligible)} finding(s) carry live_eligible=1, set only "
                    f"from RobustnessReport.live_eligible. Cross-examination has "
                    f"not run yet - surviving it is a further condition, not a "
                    f"consequence of this flag.")
        if not findings and not withheld:
            return (f"NOTHING is live-eligible for {symbol} in "
                    f"{'/'.join(self.families)}: no candidate reached the "
                    f"robustness suite. Combined out-of-sample expectancy across "
                    f"the walk-forward was {wf.combined_oos.expectancy_r:+.3f}R "
                    f"over {wf.combined_oos.trades} trades. Treat these families "
                    f"as having no demonstrated edge on this sample.")
        return (f"NOTHING is live-eligible for {symbol} in "
                f"{'/'.join(self.families)}. {len(findings)} finding(s) are "
                f"claimed as hypotheses and {len(withheld)} candidate(s) were "
                f"withheld outright. Combined out-of-sample expectancy was "
                f"{wf.combined_oos.expectancy_r:+.3f}R over "
                f"{wf.combined_oos.trades} trades at walk-forward efficiency "
                f"{wf.efficiency:.2f}. A continuation family that fails out of "
                f"sample is a finding, not a failure.")

    def _research_summary(self, symbol: str, sweep, wf: WalkForwardResult,
                          trials: int, findings: Sequence[Any],
                          withheld: Sequence[Any],
                          section: Dict[str, Any]) -> str:
        drift = section.get("sample_drift") or {}
        traded = sum(1 for m in sweep.metrics.values() if m.trades > 0)
        return (
            f"{symbol}: swept {trials} {'/'.join(self.families)} combinations "
            f"over {sweep.window['bars']:,} bars in {sweep.seconds:.1f}s "
            f"({traded} produced trades); walk-forward OOS "
            f"{wf.combined_oos.expectancy_r:+.3f}R over "
            f"{wf.combined_oos.trades} trades, efficiency {wf.efficiency:.2f}, "
            f"credible={'yes' if wf.is_credible else 'NO'}. The sample drifts "
            f"{drift.get('drift_points_total', 0):+.0f} points, which is why "
            f"every finding carries a drift attribution. "
            f"{len(findings)} finding(s) published, {len(withheld)} withheld, "
            f"{len(section['live_eligible'])} live-eligible. "
            f"{section['live_eligibility_note']}")

    def _challenge_summary(self, symbol: str, challenges: Sequence[Challenge],
                           measurements: Sequence[Dict[str, Any]],
                           unreadable: Sequence[str],
                           notes: Sequence[str]) -> str:
        if not measurements:
            reason = ("; ".join(notes) if notes
                      else "no rival findings were readable")
            return (f"{symbol}: filed 0 challenges - {reason}. Nothing was "
                    f"asserted without a measurement.")
        by_kind = self._count_kinds(challenges)
        detail = (", ".join(f"{v} {k}" for k, v in sorted(by_kind.items()))
                  or "none upheld by the numbers")
        tail = (f" {len(unreadable)} rival(s) had no readable findings: "
                f"{', '.join(unreadable)}." if unreadable else "")
        return (f"{symbol}: took {len(measurements)} measurement(s) against "
                f"rival findings and filed {len(challenges)} substantiated "
                f"challenge(s) ({detail}). Measurements that did not support an "
                f"objection are recorded rather than dropped.{tail}")

    def _rebut_summary(self, symbol: str, incoming: int,
                       section: Dict[str, Any], notes: Sequence[str]) -> str:
        if not incoming:
            reason = ("; ".join(notes) if notes
                      else "no rival has filed a challenge against this seat")
            return f"{symbol}: no challenges to answer - {reason}."
        verdicts = ", ".join(f"{v} {k.lower()}"
                             for k, v in section["by_verdict"].items())
        return (f"{symbol}: answered {incoming} challenge(s) - {verdicts}. Every "
                f"REBUTTED verdict carries a counter-measurement "
                f"({section['unmeasured_rebuttals']} unmeasured); the rest were "
                f"conceded, which is the correct outcome when the challenger's "
                f"numbers stand.")

    @staticmethod
    def _count_kinds(challenges: Sequence[Challenge]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for c in challenges:
            out[c.kind.value] = out.get(c.kind.value, 0) + 1
        return out

    # ==================================================================
    # Artefact plumbing
    # ==================================================================
    def _publish_sectioned(self, name: str, symbol: str,
                           section: Dict[str, Any], flat_key: str,
                           summary: str) -> str:
        """Merge one symbol's block into this seat's artefact and publish it.

        Keyed by symbol like the desk lead's artefacts, because every symbol is
        an independent universe. A flat top-level list is published alongside so
        a rival reading this file does not have to guess the nesting - the three
        specialists are written in parallel and cannot agree a shape between
        themselves at runtime.
        """
        doc = self._artefact(name)
        symbols = doc.get("symbols")
        if not isinstance(symbols, dict):
            symbols = {}
        symbols[symbol] = section
        flat: List[Any] = []
        for block in symbols.values():
            if isinstance(block, dict):
                flat.extend(block.get(flat_key) or [])
        return self.publish(name, {
            "artefact": name,
            "owner": self.id,
            "families": list(self.families),
            "generated_et": et_stamp(),
            "note": ("Every symbol is an independent universe. The flat "
                     f"'{flat_key}' list is the same content as "
                     f"symbols.<SYMBOL>.{flat_key}, concatenated for readers "
                     "that do not want to walk the map."),
            "symbols": symbols,
            flat_key: flat,
        }, summary)

    def _section_of(self, name: str, symbol: str) -> Dict[str, Any]:
        doc = self._artefact(name)
        section = (doc.get("symbols", {}) or {}).get(symbol)
        return dict(section) if isinstance(section, dict) else {}

    def _own_findings(self, symbol: str) -> List[Dict[str, Any]]:
        """This seat's own published findings for a symbol."""
        section = self._section_of("findings", symbol)
        rows = section.get("findings")
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def _rival_findings(self, owner: Role, symbol: str
                        ) -> Tuple[List[Dict[str, Any]], str]:
        """A rival's findings, or an honest note about why there are none."""
        doc, note = self._read_rival(owner, "findings")
        if doc is None:
            return [], note
        rows = [r for r in self._harvest(doc, ("owner", "strategy_id"))
                if str(r.get("symbol") or symbol) == symbol]
        if not rows:
            return [], (f"{owner.value}: published a findings artefact with no "
                        f"finding for {symbol}")
        rows.sort(key=lambda r: -float(r.get("base_score") or
                                       r.get("robustness_score") or 0.0))
        return rows, ""

    def _rival_challenges(self, owner: Role, symbol: str
                          ) -> Tuple[List[Dict[str, Any]], str]:
        doc, note = self._read_rival(owner, "challenges")
        if doc is None:
            return [], note
        rows = [r for r in self._harvest(doc, ("challenger", "target_strategy_id",
                                               "kind"))
                if str(r.get("symbol") or symbol) == symbol]
        return rows, ""

    def _read_rival(self, owner: Role, artefact: str
                    ) -> Tuple[Optional[Any], str]:
        """Read a rival's artefact, tolerating its absence.

        The three specialists are built in parallel and dispatched in any order,
        so a rival that has not published - or does not exist yet - is an
        expected state, not an error. It is reported, not raised.
        """
        try:
            doc = self.read_from(owner, artefact)
        except PermissionError as exc:                      # pragma: no cover
            return None, f"{owner.value}: {artefact} not readable - {exc}"
        except OSError as exc:
            return None, f"{owner.value}: {artefact} could not be read - {exc}"
        if doc is None:
            return None, (f"{owner.value}: has not published '{artefact}' yet - "
                          f"nothing from this rival could be examined")
        return doc, ""

    @staticmethod
    def _harvest(doc: Any, required: Sequence[str], *, depth: int = 0
                 ) -> List[Dict[str, Any]]:
        """Every dict anywhere in a document that carries all ``required`` keys.

        Shape-tolerant on purpose. The three specialists agree on the *names* of
        their artefacts and on the debate dataclasses inside them, but nothing
        forces them to agree on the nesting, and a reader that only understands
        one layout silently reports "no rival findings" when the rival in fact
        published plenty.
        """
        if depth > 8:
            return []
        out: List[Dict[str, Any]] = []
        if isinstance(doc, dict):
            if all(k in doc for k in required):
                out.append(doc)
            else:
                for value in doc.values():
                    out.extend(ResearchTrendAgent._harvest(value, required,
                                                           depth=depth + 1))
        elif isinstance(doc, (list, tuple)):
            for value in doc:
                out.extend(ResearchTrendAgent._harvest(value, required,
                                                       depth=depth + 1))
        return out

    def _rerun_fingerprint(self, frame: SymbolFrame, offset: int,
                           measurement: Dict[str, Any]) -> List[Tuple[int, int]]:
        """Fingerprint the challenger's own strategy by re-running it here.

        A redundancy claim is a claim about two sets of trades, so the answer to
        it should be two sets of trades measured the same way. Where the
        challenge names the strategy it compared against, it is re-run on this
        agent's frame; only if it names none is the published fingerprint used,
        and the rebuttal says which of the two it was.
        """
        registry = self.require_context().registry
        for key in ("challenger_strategy_id", "their_strategy_id",
                    "my_strategy_id", "best_match", "strategy_id"):
            sid = measurement.get(key)
            if not sid or not isinstance(sid, str):
                continue
            strategy = registry.get(sid)
            if strategy is None:
                continue
            trades = BacktestEngine(frame).run(strategy).trades
            return [(t.entry_index + offset, t.direction.sign) for t in trades]
        return []

    @staticmethod
    def _fingerprint_of(row: Dict[str, Any]) -> List[Tuple[int, int]]:
        """A ``trade_fingerprint`` from a published dict, in any sane encoding."""
        raw = row.get("trade_fingerprint")
        if not isinstance(raw, (list, tuple)):
            return []
        out: List[Tuple[int, int]] = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    out.append((int(item[0]), int(item[1])))
                except (TypeError, ValueError):
                    continue
        return out

    # ==================================================================
    # Optional LLM commentary - narrative only, never a statistic
    # ==================================================================
    SYSTEM = """
You are the trend, momentum and continuation research specialist on the team.
You have swept your own strategy families, measured them, and measured how much
of each result is attributable to the sample's unconditional drift.

Your job in this commentary is to judge the *research direction*, not to grade
the arithmetic.

Hard limits on your output:

- You may not state, adjust, round or re-derive any number. Every statistic in
  the evidence was measured; you reference them, you do not produce them.
- You may not declare anything live-eligible. That flag comes from the
  robustness suite alone.
- A continuation result on a drifting sample is presumed to be exposure until
  the drift attribution says otherwise. Say so when it applies.
- "These families show no edge on this sample" is a complete and correct
  answer, and on synthetic or short data it is the expected one.
- Quote the sample size whenever you mention a result.
""".strip()

    COMMENT_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string"},
            "edges_that_are_probably_exposure": {"type": "array",
                                                 "items": {"type": "string"}},
            "edges_worth_defending": {"type": "array", "items": {"type": "string"}},
            "where_a_rival_should_attack_me": {"type": "array",
                                               "items": {"type": "string"}},
            "what_would_change_my_mind": {"type": "string"},
        },
        "required": ["verdict", "edges_that_are_probably_exposure",
                     "edges_worth_defending", "where_a_rival_should_attack_me",
                     "what_would_change_my_mind"],
    }

    def _family_commentary(self, symbol: str,
                           section: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.llm_available:
            return None
        evidence = {
            "symbol": symbol,
            "families": section.get("families"),
            "research_window": section.get("research_window"),
            "sample_drift": section.get("sample_drift"),
            "universe": section.get("universe"),
            "survivorship": section.get("survivorship"),
            "trials_searched": section.get("trials_searched"),
            "walk_forward": section.get("walk_forward"),
            "live_eligibility_note": section.get("live_eligibility_note"),
            "findings": [{k: v for k, v in row.items()
                          if k not in ("trade_fingerprint", "r_series")}
                         for row in (section.get("findings") or [])],
            "withheld": section.get("withheld"),
        }
        response = self.reason(
            system=self.system_prompt(self.SYSTEM), evidence=evidence,
            question=(
                "Which of these continuation results are most likely to be "
                "directional exposure to the sample's drift rather than an edge, "
                "and which are worth defending under cross-examination? Where "
                "should a rival specialist attack my findings first? If the "
                "evidence supports no edge in these families, say so plainly."),
            schema=self.COMMENT_SCHEMA)
        if response is None or not response.ok or response.parsed is None:
            return None
        return {"source": "llm", "model": response.model,
                "commentary": response.parsed,
                "note": "narrative only; every statistic above is measured"}
