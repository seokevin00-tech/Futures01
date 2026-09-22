"""Strategy research and backtesting - the team's evidence factory.

Everything the decision layer is permitted to believe about a strategy is
measured here. The mandate is deliberately adversarial: this agent's job is not
to find winners, it is to *try very hard to disqualify* every candidate it
generates and report whatever is left.

Four design commitments shape the code:

**One pass, one symbol.** A symbol's strategy universe is generated for that
symbol alone and backtested in a single sweep through the bars, because
``run_portfolio`` shares one condition cache across every strategy on every
bar. Evaluating 4,000 strategies one at a time would recompute the same RSI
four thousand times per bar; the sweep computes it once. This is the single
most expensive operation in the whole system, so it is also the one place where
the cost is measured and reported rather than assumed.

**Every result is persisted, including the failures.** Rankings computed over
survivors are survivorship bias wearing a lab coat. A strategy that generated
zero trades, or lost money on four hundred, is written to the performance
database exactly like a winner - that is what makes the denominator honest and
what makes ``deflated_expectancy`` meaningful.

**Selection only ever uses data that existed at selection time.** The
walk-forward candidate screen runs on the first training window only, so the
pool handed to ``walk_forward`` was chosen without seeing a single
out-of-sample bar. ``walk_forward`` then re-selects per fold from training data
alone.

**Nothing is live-eligible on this agent's opinion.** The ``live_eligible``
flag written to storage is copied verbatim from ``RobustnessReport``, which
gates on sample size, deflated expectancy, walk-forward efficiency, parameter
sensitivity and probability of ruin. If nothing clears it, the artefact says so
in as many words. On synthetic data, nothing clearing is the expected outcome
and is reported as a finding, not smoothed over.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..backtest.costs import CostModel
from ..backtest.engine import BacktestResult, Trade, run_portfolio
from ..backtest.metrics import Metrics, compute_metrics, slice_metrics
from ..backtest.robustness import (BIAS_CHECKS, RobustnessReport,
                                   assess_robustness, parameter_sensitivity)
from ..backtest.walkforward import WalkForwardResult, robust_score, walk_forward
from ..config import tf_label
from ..features import SymbolFrame
from ..schema import Evidence, HistoricalPerformance
from ..strategies.base import Strategy
from ..strategies.combinator import generate_strategies
from ..team.agent import AgentResult
from ..team.board import Task
from ..team.roles import Role
from ..timeutil import et_stamp, trading_day
from .base import DomainAgent

__all__ = ["StrategyResearchAgent"]


#: Sample-size floor used for ranking. Below it ``robust_score`` collapses the
#: score, which is the point: a profit factor of 1.8 over 18 trades is noise.
MIN_TRADES_FOR_RANK = 30

#: A slice with a handful of trades in it is not a finding. Regime and session
#: breakdowns below this are computed but not persisted as decision inputs.
MIN_TRADES_PER_SLICE = 5

#: How many strategies survive the leak-free screen into the walk-forward.
DEFAULT_WF_CANDIDATES = 40

#: How many of those get the full robustness suite (Monte Carlo, sensitivity,
#: per-strategy walk-forward). Each one costs a handful of extra passes.
DEFAULT_FINALISTS = 5

#: Rows published per symbol in ``strategy_rankings``.
DEFAULT_TOP_N = 15

#: The walk-forward's first anchored training window, as a fraction of history.
#: Matches ``walkforward.anchored_windows``' default so the candidate screen and
#: fold 0 see exactly the same bars.
WF_MIN_TRAIN_FRACTION = 0.3


@dataclass
class _Sweep:
    """One portfolio pass and everything derived from it."""

    symbol: str
    strategies: List[Strategy]
    results: Dict[str, BacktestResult]
    metrics: Dict[str, Metrics] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)
    window: Dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    persist_seconds: float = 0.0

    @property
    def trades(self) -> List[Trade]:
        return [t for r in self.results.values() for t in r.trades]

    def ranked(self) -> List[str]:
        """Strategy ids ordered by durability, never by total profit."""
        return sorted(
            self.scores,
            key=lambda sid: (-self.scores[sid],
                             -self.metrics[sid].expectancy_r,
                             -self.metrics[sid].trades, sid))


class StrategyResearchAgent(DomainAgent):
    """Generates, backtests, walk-forwards and ranks one symbol's strategies."""

    def __init__(self, fs, bus, *, context=None, config=None, llm=None):
        super().__init__(Role.STRATEGY_RESEARCH, fs, bus,
                         context=context, config=config, llm=llm)

    # ==================================================================
    # Entry point
    # ==================================================================
    def handle(self, task: Task) -> AgentResult:
        if task.kind in ("research_strategies", "backtest"):
            return self._sweep_task(task, generate=(task.kind == "research_strategies"))
        if task.kind in ("walk_forward", "robustness"):
            return self._walk_forward_task(task)
        if task.kind == "optimise":
            return self._optimise_task(task)
        if task.kind == "rank_strategies":
            return self._rank_task(task)
        raise ValueError(f"{self.id} does not implement task kind {task.kind!r}")

    # ==================================================================
    # research_strategies / backtest
    # ==================================================================
    def _sweep_task(self, task: Task, *, generate: bool) -> AgentResult:
        """Generate (or reuse) a symbol's universe and backtest it in one pass."""
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        if len(frame.base) == 0:
            return AgentResult(
                ok=True, summary=f"{symbol}: no bars available - nothing to test",
                payload={"symbol": symbol, "bars": 0,
                         "note": "insufficient history; no strategies were tested"})

        max_strategies = self._int(task, "max_strategies",
                                   self._cfg("max_combinations", 4_000))
        days = self._int(task, "days", None)
        top_n = self._int(task, "top_n", DEFAULT_TOP_N)
        timeframes = self._timeframes(task, frame)

        strategies = self._universe(ctx, symbol, timeframes, max_strategies,
                                    generate=generate)
        if not strategies:
            return AgentResult(
                ok=True, summary=f"{symbol}: strategy universe is empty",
                payload={"symbol": symbol, "strategies": 0,
                         "note": "no combinations could be constructed"})

        sweep = self._run_sweep(frame, symbol, strategies, days)
        self._persist_sweep(sweep)

        ranked = sweep.ranked()
        tested = len(sweep.strategies)
        with_trades = sum(1 for m in sweep.metrics.values() if m.trades > 0)
        profitable = sum(1 for m in sweep.metrics.values()
                         if m.trades > 0 and m.expectancy_r > 0)
        qualified = sum(1 for m in sweep.metrics.values()
                        if m.trades >= MIN_TRADES_FOR_RANK and m.expectancy_r > 0)
        total_trades = sum(m.trades for m in sweep.metrics.values())

        section = self._rankings_section(sweep, ranked[:top_n], top_n=top_n)
        section["llm_commentary"] = self._commentary(symbol, section)
        section["source"] = "hybrid" if section["llm_commentary"] else "deterministic"

        rankings_path = self._publish_rankings(symbol, section)
        db_path = self._publish_performance_db(sweep)

        summary = (
            f"{symbol}: {tested} strategies tested in one pass over "
            f"{sweep.window['bars']:,} bars ({sweep.window['trading_days']} trading "
            f"days) in {sweep.seconds:.1f}s; {with_trades} produced trades "
            f"({total_trades:,} total), {profitable} had positive expectancy, "
            f"{qualified} cleared the {MIN_TRADES_FOR_RANK}-trade floor with a "
            f"positive edge. All {tested} results persisted, failures included.")
        self.log(summary)

        return AgentResult(
            ok=True, summary=summary,
            payload={
                "symbol": symbol,
                "strategies_tested": tested,
                "strategies_with_trades": with_trades,
                "strategies_positive_expectancy": profitable,
                "strategies_qualified": qualified,
                "total_trades": total_trades,
                "trials_searched": tested,
                "window": sweep.window,
                "timings_s": {"sweep": round(sweep.seconds, 2),
                              "persist": round(sweep.persist_seconds, 2)},
                "top": section["top"][:5],
                "live_eligible": section["live_eligible"],
                "live_eligibility_note": section["live_eligibility_note"],
            },
            artefacts=[rankings_path, db_path])

    # ==================================================================
    # walk_forward / robustness
    # ==================================================================
    def _walk_forward_task(self, task: Task) -> AgentResult:
        """Anchored walk-forward, then the full robustness suite on the finalists.

        The pool handed to ``walk_forward`` is screened on fold 0's training
        window only. Screening on the whole history first - the obvious thing to
        do - would leak every out-of-sample segment into the selection and make
        the resulting efficiency figure meaningless.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        n_bars = len(frame.base)
        if n_bars == 0:
            return AgentResult(
                ok=True, summary=f"{symbol}: no bars available - no walk-forward",
                payload={"symbol": symbol, "bars": 0})

        folds = self._int(task, "folds", self._cfg("walk_forward_folds", 6))
        top_k = self._int(task, "top_k", 5)
        n_candidates = self._int(task, "wf_candidates", DEFAULT_WF_CANDIDATES)
        n_finalists = self._int(task, "finalists", DEFAULT_FINALISTS)
        max_strategies = self._int(task, "max_strategies",
                                   self._cfg("max_combinations", 4_000))
        timeframes = self._timeframes(task, frame)

        strategies = self._universe(ctx, symbol, timeframes, max_strategies,
                                    generate=False)
        if not strategies:
            return AgentResult(
                ok=True, summary=f"{symbol}: no registered strategies to walk forward",
                payload={"symbol": symbol, "strategies": 0})

        trials = self._trials_searched(symbol, len(strategies))

        # --- 1. leak-free candidate screen on fold 0's training window ----
        screen_end = max(1, int(n_bars * WF_MIN_TRAIN_FRACTION))
        t0 = time.perf_counter()
        screen = run_portfolio(frame, strategies, start=0, end=screen_end)
        screen_scores: Dict[str, float] = {
            sid: robust_score(compute_metrics(r.trades),
                              min_trades=MIN_TRADES_FOR_RANK)
            for sid, r in screen.items()}
        chosen = set(sorted(screen_scores,
                            key=lambda sid: (-screen_scores[sid], sid))[:n_candidates])
        candidates = [s for s in strategies if s.strategy_id in chosen]
        screen_seconds = time.perf_counter() - t0
        self.log(f"{symbol}: screened {len(strategies)} -> {len(candidates)} "
                 f"candidates on bars 0..{screen_end} ({screen_seconds:.1f}s)")

        # --- 2. the universe walk-forward ---------------------------------
        t0 = time.perf_counter()
        wf = walk_forward(frame, candidates, folds=folds, top_k=top_k,
                          anchored=True,
                          progress=lambda k, total: self.log(
                              f"{symbol}: walk-forward fold {k + 1}/{total}"))
        wf_seconds = time.perf_counter() - t0
        self.log(f"{symbol}: {wf.summary()} ({wf_seconds:.1f}s)")

        # --- 3. finalists: selected in training, ordered by training score --
        finalists = self._finalists(wf, candidates, screen_scores, n_finalists)

        # --- 4. full-history trades for the finalists, in one pass ---------
        t0 = time.perf_counter()
        final_results = (run_portfolio(frame, finalists) if finalists else {})
        final_seconds = time.perf_counter() - t0

        reports: List[RobustnessReport] = []
        sensitivities: Dict[str, Dict[str, Any]] = {}
        own_wfs: Dict[str, WalkForwardResult] = {}
        risk_per_trade = self._risk_per_trade(ctx)
        t0 = time.perf_counter()
        for strategy in finalists:
            sid = strategy.strategy_id
            trades = final_results[sid].trades
            own = walk_forward(frame, [strategy], folds=folds, top_k=1, anchored=True)
            own_wfs[sid] = own
            sens = parameter_sensitivity(frame, strategy)
            sensitivities[sid] = sens
            report = assess_robustness(
                trades, strategy_id=sid, symbol=symbol,
                walk_forward_result=own, sensitivity=sens,
                trials_searched=trials,
                cost_r=self._cost_r(frame, trades),
                monte_carlo_runs=self._cfg("monte_carlo_runs", 2_000),
                account_risk_per_trade=risk_per_trade,
                starting_equity=self._account_cfg("starting_equity", 50_000.0),
                failure_drawdown=self._account_cfg("max_total_drawdown", 5_000.0))
            reports.append(report)
            self._persist_report(frame, strategy, trades, report, own)
            self.log(f"{symbol}: {report.summary()}")
        robustness_seconds = time.perf_counter() - t0

        eligible = [r.strategy_id for r in reports if r.live_eligible]
        note = self._eligibility_note(symbol, eligible, reports, wf)

        section = {
            "generated_et": et_stamp(),
            "symbol": symbol,
            "source": "deterministic",
            "bars": n_bars,
            "universe_size": len(strategies),
            "trials_searched": trials,
            "candidates_screened_to": len(candidates),
            "screen_window_bars": [0, screen_end],
            "screen_note": (
                "Candidates were ranked on fold 0's training window only, so no "
                "out-of-sample bar contributed to the pool selection."),
            "walk_forward": wf.to_dict(),
            "folds_selecting_nothing": sum(1 for f in wf.folds if not f.selected),
            "finalists": [
                {
                    "strategy": s.to_dict(),
                    # The score that put this strategy on the shortlist,
                    # measured on training bars only - recorded so the
                    # selection can be audited for leakage.
                    "training_screen_score": round(
                        screen_scores.get(s.strategy_id, 0.0), 6),
                    "selected_in_folds": [f.index for f in wf.folds
                                          if s.strategy_id in f.selected],
                    "robustness": reports[i].to_dict(),
                    "own_walk_forward": own_wfs[s.strategy_id].to_dict(),
                    "sensitivity": sensitivities[s.strategy_id],
                }
                for i, s in enumerate(finalists)],
            "live_eligible": eligible,
            "live_eligibility_note": note,
            "bias_catalogue": dict(BIAS_CHECKS),
            "timings_s": {
                "candidate_screen": round(screen_seconds, 2),
                "walk_forward": round(wf_seconds, 2),
                "finalist_backtest": round(final_seconds, 2),
                "robustness_suite": round(robustness_seconds, 2),
            },
        }
        section["llm_commentary"] = self._commentary(symbol, section,
                                                     kind="robustness")
        if section["llm_commentary"]:
            section["source"] = "hybrid"

        robustness_path = self._publish_robustness(symbol, section)
        # Rankings carry the eligibility flags, so they are refreshed from the
        # database now that the robustness suite has written its verdict.
        rankings_path = self._publish_rankings(
            symbol, self._rankings_from_storage(symbol, trials=trials,
                                                top_n=DEFAULT_TOP_N))

        oos = wf.combined_oos
        summary = (
            f"{symbol}: {folds}-fold anchored walk-forward over {n_bars:,} bars, "
            f"{len(candidates)} candidates screened from {len(strategies)}; "
            f"combined OOS {oos.trades} trades, expectancy {oos.expectancy_r:+.3f}R, "
            f"WF efficiency {wf.efficiency:.2f}, selection stability "
            f"{wf.selection_stability:.2f}, credible={'yes' if wf.is_credible else 'NO'}. "
            f"{len(reports)} finalists assessed against {trials} searched "
            f"combinations; {len(eligible)} live-eligible. {note}")
        self.log(summary)

        return AgentResult(
            ok=True, summary=summary,
            payload={
                "symbol": symbol,
                "folds": folds,
                "universe_size": len(strategies),
                "trials_searched": trials,
                "candidates": len(candidates),
                "combined_oos": oos.to_dict(),
                "walk_forward_efficiency": round(wf.efficiency, 4),
                "selection_stability": round(wf.selection_stability, 4),
                "is_credible": wf.is_credible,
                "finalists": [
                    {"strategy_id": r.strategy_id,
                     "score": round(r.score, 4),
                     "live_eligible": r.live_eligible,
                     "trades": r.metrics.trades if r.metrics else 0,
                     "expectancy_r": round(r.metrics.expectancy_r, 4) if r.metrics else 0.0,
                     "deflated_expectancy_r": round(r.deflated_expectancy_r, 5),
                     "own_wf_efficiency": round(own_wfs[r.strategy_id].efficiency, 4),
                     "oos_trades": own_wfs[r.strategy_id].combined_oos.trades,
                     "reasons": r.reasons}
                    for r in reports],
                "live_eligible": eligible,
                "live_eligibility_note": note,
                "timings_s": section["timings_s"],
            },
            artefacts=[robustness_path, rankings_path])

    # ==================================================================
    # optimise
    # ==================================================================
    def _optimise_task(self, task: Task) -> AgentResult:
        """Map the exit-parameter surface around the best strategies.

        This deliberately does **not** pick the best-performing perturbation.
        Selecting the peak of a parameter sweep is the textbook way to
        manufacture an edge that exists only in the sample. What it reports is
        the *shape* of the surface: a broad plateau is evidence, a single spike
        with collapse either side is a coincidence, and the strategy is flagged
        as such.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        limit = self._int(task, "limit", DEFAULT_FINALISTS)
        max_strategies = self._int(task, "max_strategies",
                                   self._cfg("max_combinations", 4_000))
        strategies = self._universe(ctx, symbol, self._timeframes(task, frame),
                                    max_strategies, generate=False)
        if not strategies:
            return AgentResult(ok=True, summary=f"{symbol}: nothing to optimise",
                               payload={"symbol": symbol, "strategies": 0})

        # Fetched generously and trimmed afterwards: the database query returns
        # regime and session slices alongside strategy rows, so a tight limit
        # can come back holding no strategy rows at all.
        wanted = self._requested_ids(task) or [
            row["strategy_id"]
            for row in self._stored_rows(symbol, max(limit * 20, 100))][:limit]
        by_id = {s.strategy_id: s for s in strategies}
        targets = [by_id[sid] for sid in wanted if sid in by_id][:limit]
        if not targets:
            return AgentResult(
                ok=True,
                summary=f"{symbol}: no measured strategies to optimise yet",
                payload={"symbol": symbol,
                         "note": "run research_strategies before optimise"})

        surfaces: List[Dict[str, Any]] = []
        t0 = time.perf_counter()
        for strategy in targets:
            sens = parameter_sensitivity(frame, strategy)
            rels = [v["relative"] for v in sens.get("variants", ())]
            plateau = bool(rels) and min(rels) >= 0.4
            surfaces.append({
                "strategy_id": strategy.strategy_id,
                "strategy": strategy.to_dict(),
                "baseline_expectancy_r": sens.get("baseline_expectancy_r", 0.0),
                "baseline_trades": sens.get("baseline_trades", 0),
                "worst_relative": sens.get("worst_relative", 0.0),
                "mean_relative": sens.get("mean_relative", 0.0),
                "variants": sens.get("variants", []),
                "surface": "plateau" if plateau else "spike",
                "verdict": ("baseline retained - the edge degrades gracefully"
                            if plateau else
                            "REJECT as parameter-fitted - the edge does not survive "
                            "a 25% move in the exit geometry"),
            })
        seconds = time.perf_counter() - t0

        plateaus = [s["strategy_id"] for s in surfaces if s["surface"] == "plateau"]
        section = self._robustness_section(symbol)
        section["parameter_surfaces"] = surfaces
        section["parameter_surfaces_note"] = (
            "No parameter set was selected from these sweeps. The surfaces are "
            "reported to disqualify spikes, not to tune peaks.")
        path = self._publish_robustness(symbol, section)

        summary = (f"{symbol}: mapped the exit-parameter surface for "
                   f"{len(surfaces)} strategies in {seconds:.1f}s; "
                   f"{len(plateaus)} sit on a plateau, "
                   f"{len(surfaces) - len(plateaus)} are parameter spikes and are "
                   f"rejected. No parameters were tuned.")
        self.log(summary)
        return AgentResult(ok=True, summary=summary,
                           payload={"symbol": symbol, "surfaces": surfaces,
                                    "plateaus": plateaus,
                                    "seconds": round(seconds, 2)},
                           artefacts=[path])

    # ==================================================================
    # rank_strategies
    # ==================================================================
    def _rank_task(self, task: Task) -> AgentResult:
        """Re-rank from the performance database without re-running backtests."""
        ctx = self.require_context()
        symbol = self._symbol(task)
        top_n = self._int(task, "top_n", DEFAULT_TOP_N)
        trials = self._trials_searched(symbol, len(ctx.registry.symbol(symbol)))
        section = self._rankings_from_storage(symbol, trials=trials, top_n=top_n)
        if not section["top"]:
            return AgentResult(
                ok=True,
                summary=f"{symbol}: performance database is empty - nothing to rank",
                payload={"symbol": symbol, "rows": 0,
                         "note": "run research_strategies first"})
        section["llm_commentary"] = self._commentary(symbol, section)
        section["source"] = "hybrid" if section["llm_commentary"] else "deterministic"
        path = self._publish_rankings(symbol, section)
        summary = (f"{symbol}: ranked {section['rows_in_database']} stored results "
                   f"by robust_score; {len(section['live_eligible'])} live-eligible. "
                   f"{section['live_eligibility_note']}")
        return AgentResult(ok=True, summary=summary,
                           payload={"symbol": symbol,
                                    "rows": section["rows_in_database"],
                                    "top": section["top"][:5],
                                    "live_eligible": section["live_eligible"]},
                           artefacts=[path])

    # ==================================================================
    # The sweep
    # ==================================================================
    def _run_sweep(self, frame: SymbolFrame, symbol: str,
                   strategies: Sequence[Strategy],
                   days: Optional[int]) -> _Sweep:
        start, window = self._window(frame, days)
        total = len(frame.base) - start
        self.log(f"{symbol}: sweeping {len(strategies)} strategies over "
                 f"{total:,} bars from {window['start_et']}")

        # run_portfolio calls back every 2,000 bars; log a tenth of those so a
        # 4,000-strategy sweep leaves a progress trail without flooding the log.
        def progress(done: int, span: int) -> None:
            if done and done % 20_000 == 0:
                self.log(f"{symbol}: sweep {done * 100 // max(1, span)}% "
                         f"({done:,}/{span:,} bars)")

        t0 = time.perf_counter()
        results = run_portfolio(frame, list(strategies), start=start,
                               progress=progress)
        seconds = time.perf_counter() - t0

        sweep = _Sweep(symbol=symbol, strategies=list(strategies), results=results,
                       window=window, seconds=seconds)
        for s in strategies:
            m = compute_metrics(results[s.strategy_id].trades)
            sweep.metrics[s.strategy_id] = m
            sweep.scores[s.strategy_id] = robust_score(m, min_trades=MIN_TRADES_FOR_RANK)
        return sweep

    def _persist_sweep(self, sweep: _Sweep) -> None:
        """Write every strategy's result - winners, losers and non-starters.

        The non-starters matter most. A performance database that only contains
        strategies which produced trades cannot tell the decision layer how many
        hypotheses were tried, and without that denominator no significance
        figure downstream means anything.
        """
        ctx = self.require_context()
        store = ctx.storage
        t0 = time.perf_counter()
        for strategy in sweep.strategies:
            sid = strategy.strategy_id
            metrics = sweep.metrics[sid]
            trades = sweep.results[sid].trades
            store.upsert_strategy_performance(
                strategy_id=sid, symbol=sweep.symbol,
                timeframe=strategy.primary_tf, metrics=metrics,
                scope="backtest", regime="ALL", session="ALL",
                robustness_score=max(0.0, sweep.scores[sid]),
                live_eligible=False,
                payload=self._row_payload(strategy, sweep, metrics, trades))
            if trades:
                self._persist_slices(sweep.symbol, strategy, trades)
        sweep.persist_seconds = time.perf_counter() - t0

    def _persist_slices(self, symbol: str, strategy: Strategy,
                        trades: Sequence[Trade], *,
                        robustness_score: float = 0.0,
                        live_eligible: bool = False) -> None:
        """Store per-regime and per-session breakdowns for one strategy.

        The decision layer's real question is never "is this strategy good" but
        "is this strategy good *in the regime I am looking at right now*", and
        that is a query, not a paragraph. ``live_eligible`` here is only ever
        the parent strategy's verdict copied down; a slice never earns
        eligibility on its own.
        """
        store = self.require_context().storage
        for regime, m in slice_metrics(trades, "regime",
                                       min_trades=MIN_TRADES_PER_SLICE).items():
            store.upsert_strategy_performance(
                strategy_id=strategy.strategy_id, symbol=symbol,
                timeframe=strategy.primary_tf, metrics=m, scope="backtest",
                regime=str(regime), session="ALL",
                robustness_score=robustness_score, live_eligible=live_eligible,
                payload={"slice_kind": "regime", "regime": str(regime),
                         "strategy_name": strategy.name, "group": strategy.group,
                         "trades": m.trades,
                         "note": "regime slice of the parent strategy; the "
                                 "eligibility flag is the parent's verdict"})
        for session, m in slice_metrics(trades, "session",
                                        min_trades=MIN_TRADES_PER_SLICE).items():
            store.upsert_strategy_performance(
                strategy_id=strategy.strategy_id, symbol=symbol,
                timeframe=strategy.primary_tf, metrics=m, scope="backtest",
                regime="ALL", session=str(session),
                robustness_score=robustness_score, live_eligible=live_eligible,
                payload={"slice_kind": "session", "session": str(session),
                         "strategy_name": strategy.name, "group": strategy.group,
                         "trades": m.trades,
                         "note": "session slice of the parent strategy; the "
                                 "eligibility flag is the parent's verdict"})

    def _persist_report(self, frame: SymbolFrame, strategy: Strategy,
                        trades: Sequence[Trade], report: RobustnessReport,
                        own_wf: WalkForwardResult) -> None:
        """Write a finalist's verdict. ``live_eligible`` comes only from here."""
        store = self.require_context().storage
        metrics = report.metrics or compute_metrics(trades)
        oos = own_wf.combined_oos
        store.upsert_strategy_performance(
            strategy_id=strategy.strategy_id, symbol=frame.symbol,
            timeframe=strategy.primary_tf, metrics=metrics,
            scope="backtest", regime="ALL", session="ALL",
            oos_trades=oos.trades, oos_expectancy_r=oos.expectancy_r,
            walk_forward_efficiency=own_wf.efficiency,
            robustness_score=report.score,
            live_eligible=report.live_eligible,
            payload={
                "strategy": strategy.to_dict(),
                "ranking_score": round(robust_score(metrics,
                                                    min_trades=MIN_TRADES_FOR_RANK), 6),
                "robustness_report_score": round(report.score, 4),
                "deflated_expectancy_r": round(report.deflated_expectancy_r, 6),
                "trials_searched": report.trials_searched,
                "reasons": report.reasons,
                "monte_carlo": report.monte_carlo.to_dict() if report.monte_carlo else None,
                "sensitivity": report.sensitivity,
                "biases": [b.to_dict() for b in report.biases],
                "scope_note": "full-history backtest; OOS columns are this "
                              "strategy's own anchored walk-forward",
            })
        if trades:
            self._persist_slices(frame.symbol, strategy, trades,
                                 robustness_score=report.score,
                                 live_eligible=report.live_eligible)

    # ==================================================================
    # Universe construction
    # ==================================================================
    def _universe(self, ctx, symbol: str, timeframes: Sequence[int],
                  max_strategies: int, *, generate: bool) -> List[Strategy]:
        """This symbol's strategies, generated once and registered once.

        Every symbol is its own universe: the generator is seeded per symbol and
        the registry refuses a strategy whose symbol does not match, so nothing
        measured on MNQ can leak into MES's rankings.
        """
        registered = ctx.registry.symbol(symbol).all()
        if registered and not generate:
            # Never truncated: the searched universe is what it is, and
            # trials_searched has to reflect all of it.
            return registered

        strategies = generate_strategies(symbol, timeframes, max_total=max_strategies)
        added = 0
        for s in strategies:
            if ctx.registry.get(s.strategy_id) is None:
                ctx.registry.add(s)
                added += 1
        self.log(f"{symbol}: generated {len(strategies)} strategies over "
                 f"timeframes {[tf_label(t) for t in timeframes]} ({added} new)")
        return strategies

    def _timeframes(self, task: Task, frame: SymbolFrame) -> List[int]:
        """Requested timeframes, narrowed to what the frame actually carries.

        A strategy bound to a timeframe the frame does not hold silently never
        fires, which looks identical to "no edge" in the results. Narrowing here
        and logging it keeps that failure visible.
        """
        requested = task.payload.get("timeframes") or self._cfg(
            "timeframes", frame.timeframes)
        wanted = [int(t) for t in requested]
        available = [t for t in wanted if t in frame.timeframes]
        if len(available) != len(wanted):
            missing = sorted(set(wanted) - set(available))
            self.log(f"{frame.symbol}: timeframes {missing} are not in the frame "
                     f"({[tf_label(t) for t in frame.timeframes]}); ignoring them")
        return available or list(frame.timeframes)

    @staticmethod
    def _window(frame: SymbolFrame, days: Optional[int]) -> Tuple[int, Dict[str, Any]]:
        """Start index for a sweep limited to the last ``days`` trading days.

        The frame keeps its full history either way - only the *evaluation*
        window is shortened. Truncating the data instead would also truncate
        every indicator's warm-up and quietly change the signals.
        """
        bars = frame.base.bars
        n = len(bars)
        start = 0
        seen: List[Any] = []
        if days and days > 0 and n:
            for i in range(n - 1, -1, -1):
                day = trading_day(bars[i].ts)
                if not seen or seen[-1] != day:
                    if len(seen) >= days:
                        start = i + 1
                        break
                    seen.append(day)
        covered = len(seen) if seen else len({trading_day(b.ts) for b in bars})
        return start, {
            "days_requested": days,
            "trading_days": covered,
            "bars": n - start,
            "bars_available": n,
            "start_index": start,
            "start_et": et_stamp(bars[start].ts) if n else None,
            "end_et": et_stamp(bars[-1].ts) if n else None,
            "note": ("full available history" if start == 0 else
                     f"reduced sweep: last {covered} trading days only"),
        }

    # ==================================================================
    # Reporting helpers
    # ==================================================================
    def _rankings_section(self, sweep: _Sweep, ids: Sequence[str], *,
                          top_n: int) -> Dict[str, Any]:
        """The per-symbol block published inside ``strategy_rankings``."""
        by_id = {s.strategy_id: s for s in sweep.strategies}
        frame = self.require_context().frame(sweep.symbol)
        all_trades = sweep.trades

        rows: List[Dict[str, Any]] = []
        for sid in ids:
            strategy = by_id[sid]
            metrics = sweep.metrics[sid]
            trades = sweep.results[sid].trades
            rows.append({
                "strategy_id": sid,
                "strategy": strategy.to_dict(),
                "ranking_score": round(sweep.scores[sid], 6),
                "live_eligible": False,
                "live_eligible_reason": "not yet walk-forwarded",
                "metrics": metrics.to_dict(),
                "historical_performance": self._historical(
                    strategy, metrics).to_dict(),
                "cost_r": self._cost_r(frame, trades),
                "slices": {
                    "regime": {str(k): v.to_dict() for k, v in slice_metrics(
                        trades, "regime", min_trades=MIN_TRADES_PER_SLICE).items()},
                    "session": {str(k): v.to_dict() for k, v in slice_metrics(
                        trades, "session", min_trades=MIN_TRADES_PER_SLICE).items()},
                },
                "evidence": [e.to_dict() for e in self._evidence(strategy, metrics)],
            })

        counts_by_group: Dict[str, int] = {}
        counts_by_tf: Dict[str, int] = {}
        for s in sweep.strategies:
            counts_by_group[s.group] = counts_by_group.get(s.group, 0) + 1
            key = tf_label(s.primary_tf)
            counts_by_tf[key] = counts_by_tf.get(key, 0) + 1

        return {
            "generated_et": et_stamp(),
            "symbol": sweep.symbol,
            "source": "deterministic",
            "scope": "backtest",
            "ranking_criterion": (
                "robust_score: expectancy in R, scaled by a sample-size penalty "
                "and the t-statistic, penalised for drawdown and consecutive "
                "losses. Total profit is deliberately not a term."),
            "window": sweep.window,
            "trials_searched": len(sweep.strategies),
            "rows_in_database": len(sweep.strategies),
            "universe": {"strategies": len(sweep.strategies),
                         "by_group": counts_by_group,
                         "by_timeframe": counts_by_tf},
            "survivorship": {
                "strategies_generated": len(sweep.strategies),
                "strategies_persisted": len(sweep.strategies),
                "strategies_with_trades": sum(1 for m in sweep.metrics.values()
                                              if m.trades > 0),
                "strategies_with_zero_trades": sum(1 for m in sweep.metrics.values()
                                                   if m.trades == 0),
                "note": "every generated combination is stored, including the "
                        "ones that never traded and the ones that lost money",
            },
            "universe_slices": {
                key: {str(k): v.to_dict() for k, v in slice_metrics(
                    all_trades, key, min_trades=MIN_TRADES_PER_SLICE).items()}
                for key in ("regime", "session", "timeframe")},
            "top": rows[:top_n],
            "live_eligible": [],
            "live_eligibility_note": (
                "NOTHING is live-eligible from a backtest sweep alone. Eligibility "
                "is written only by the walk_forward task, from "
                "RobustnessReport.live_eligible."),
            "timings_s": {"sweep": round(sweep.seconds, 2),
                          "persist": round(sweep.persist_seconds, 2)},
        }

    def _rankings_from_storage(self, symbol: str, *, trials: int,
                               top_n: int) -> Dict[str, Any]:
        """Rebuild a symbol's ranking block from the performance database.

        The sweep's own block is kept and updated rather than replaced. The
        database holds the verdicts (eligibility, out-of-sample columns,
        robustness score) while the published block holds the context the sweep
        measured - the window, the regime and session slices, the survivorship
        counts - and both matter to the decision layer.
        """
        previous = self._rankings_previous(symbol)
        prior = {r.get("strategy_id"): r for r in (previous.get("top") or [])}
        rows = self._stored_rows(symbol, max(top_n * 20, 500))
        eligible = [r["strategy_id"] for r in rows if r.get("live_eligible")]
        top: List[Dict[str, Any]] = []
        for row in rows[:top_n]:
            payload = self._row_json(row)
            entry = dict(prior.get(row["strategy_id"], {}))
            entry.update({
                "strategy_id": row["strategy_id"],
                "strategy": payload.get("strategy") or entry.get("strategy"),
                "ranking_score": payload.get("ranking_score",
                                             entry.get("ranking_score")),
                "robustness_score": row.get("robustness_score"),
                "live_eligible": bool(row.get("live_eligible")),
                "live_eligible_reason": (
                    "; ".join(payload.get("reasons", []))
                    or ("cleared the robustness suite" if row.get("live_eligible")
                        else "not yet walk-forwarded")),
                "deflated_expectancy_r": payload.get("deflated_expectancy_r"),
                "trials_searched": payload.get("trials_searched", trials),
                "metrics": {
                    "trades": row.get("trades"), "win_rate": row.get("win_rate"),
                    "profit_factor": row.get("profit_factor"),
                    "expectancy_r": row.get("expectancy_r"),
                    "max_drawdown_r": row.get("max_drawdown_r"),
                    "sharpe": row.get("sharpe"), "sortino": row.get("sortino"),
                },
                "out_of_sample": {
                    "trades": row.get("oos_trades"),
                    "expectancy_r": row.get("oos_expectancy_r"),
                    "walk_forward_efficiency": row.get("walk_forward_efficiency"),
                },
                "historical_performance": self._historical_from_row(row).to_dict(),
                "updated_et": row.get("updated_et"),
            })
            top.append(entry)

        section = dict(previous)
        section.update({
            "generated_et": et_stamp(),
            "symbol": symbol,
            "source": "deterministic",
            "scope": "backtest",
            "ranking_criterion": (
                "robustness_score then expectancy_r: the sweep stores "
                "robust_score, and RobustnessReport.score overwrites it for any "
                "strategy that has been through the full suite"),
            "trials_searched": trials,
            "rows_in_database": len(rows),
            "top": top,
            "live_eligible": eligible,
            "live_eligibility_note": self._storage_eligibility_note(symbol, eligible),
        })
        return section

    def _rankings_previous(self, symbol: str) -> Dict[str, Any]:
        doc = self._artefact("strategy_rankings")
        section = (doc.get("symbols", {}) or {}).get(symbol)
        return dict(section) if isinstance(section, dict) else {}

    def _historical_from_row(self, row: Dict[str, Any]) -> HistoricalPerformance:
        """The stored row as the schema object the decision layer consumes."""
        trades = int(row.get("trades") or 0)
        return HistoricalPerformance(
            strategy_id=row.get("strategy_id", ""), symbol=row.get("symbol", ""),
            timeframe=row.get("timeframe"),
            regime=None if row.get("regime") == "ALL" else row.get("regime"),
            session=None if row.get("session") == "ALL" else row.get("session"),
            trades=trades, win_rate=float(row.get("win_rate") or 0.0),
            profit_factor=float(row.get("profit_factor") or 0.0),
            expectancy_r=float(row.get("expectancy_r") or 0.0),
            max_drawdown_r=float(row.get("max_drawdown_r") or 0.0),
            sharpe=float(row.get("sharpe") or 0.0),
            sortino=float(row.get("sortino") or 0.0),
            out_of_sample_trades=int(row.get("oos_trades") or 0),
            out_of_sample_expectancy_r=float(row.get("oos_expectancy_r") or 0.0),
            walk_forward_efficiency=float(row.get("walk_forward_efficiency") or 0.0),
            robustness_score=float(row.get("robustness_score") or 0.0),
            sample_is_sufficient=trades >= MIN_TRADES_FOR_RANK)

    def _robustness_section(self, symbol: str) -> Dict[str, Any]:
        """The existing robustness block for a symbol, or a fresh empty one."""
        existing = self._artefact("robustness_report")
        section = (existing.get("symbols", {}) or {}).get(symbol)
        if isinstance(section, dict):
            return dict(section)
        return {"generated_et": et_stamp(), "symbol": symbol,
                "source": "deterministic",
                "live_eligible": [],
                "live_eligibility_note": (
                    "no walk-forward has been run for this symbol yet, so nothing "
                    "is live-eligible")}

    def _historical(self, strategy: Strategy, metrics: Metrics, *,
                    oos_trades: int = 0, oos_expectancy_r: float = 0.0,
                    wf_efficiency: float = 0.0,
                    robustness_score: float = 0.0) -> HistoricalPerformance:
        return HistoricalPerformance(
            strategy_id=strategy.strategy_id, symbol=strategy.symbol,
            timeframe=strategy.primary_tf,
            trades=metrics.trades, win_rate=metrics.win_rate,
            avg_win_r=metrics.avg_win_r, avg_loss_r=metrics.avg_loss_r,
            profit_factor=metrics.profit_factor, expectancy_r=metrics.expectancy_r,
            max_drawdown_r=metrics.max_drawdown_r,
            max_consecutive_losses=metrics.max_consecutive_losses,
            sharpe=metrics.sharpe, sortino=metrics.sortino,
            out_of_sample_trades=oos_trades,
            out_of_sample_expectancy_r=oos_expectancy_r,
            walk_forward_efficiency=wf_efficiency,
            robustness_score=robustness_score,
            sample_is_sufficient=metrics.trades >= MIN_TRADES_FOR_RANK)

    @staticmethod
    def _evidence(strategy: Strategy, metrics: Metrics) -> List[Evidence]:
        """One traceable record per claim the ranking makes."""
        return [
            Evidence(kind="backtest", name="sample_size", value=metrics.trades,
                     timeframe=strategy.primary_tf,
                     detail=(f"{metrics.trades} trades over "
                             f"{metrics.trading_days} trading days"),
                     weight=1.0, source=strategy.strategy_id),
            Evidence(kind="statistic", name="expectancy_r",
                     value=round(metrics.expectancy_r, 4),
                     timeframe=strategy.primary_tf,
                     detail=(f"{metrics.expectancy_r:+.3f}R per trade, "
                             f"t={metrics.t_statistic:.2f} over {metrics.trades} "
                             f"trades"),
                     weight=1.0, source=strategy.strategy_id),
            Evidence(kind="statistic", name="drawdown",
                     value=round(metrics.max_drawdown_r, 3),
                     timeframe=strategy.primary_tf,
                     detail=(f"max {metrics.max_drawdown_r:.2f}R, "
                             f"{metrics.max_consecutive_losses} consecutive losses"),
                     weight=0.8, source=strategy.strategy_id),
        ]

    @staticmethod
    def _eligibility_note(symbol: str, eligible: Sequence[str],
                          reports: Sequence[RobustnessReport],
                          wf: WalkForwardResult) -> str:
        if eligible:
            return (f"{len(eligible)} strategy/strategies cleared every gate "
                    f"(sample, deflated expectancy, walk-forward efficiency, "
                    f"parameter sensitivity, ruin) and are marked live-eligible.")
        if not reports:
            return (f"NOTHING is live-eligible for {symbol}: the walk-forward "
                    f"selected no strategy in any fold, so no candidate reached "
                    f"the robustness suite. Treat this symbol as having no "
                    f"demonstrated edge.")
        reasons: Dict[str, int] = {}
        for r in reports:
            for reason in r.reasons:
                key = reason.split(" - ")[0].split(",")[0]
                reasons[key] = reasons.get(key, 0) + 1
        ranked = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
        detail = "; ".join(f"{k} ({v} of {len(reports)})" for k, v in ranked)
        return (f"NOTHING is live-eligible for {symbol}. All {len(reports)} "
                f"finalists failed at least one gate: {detail}. Combined "
                f"out-of-sample expectancy was "
                f"{wf.combined_oos.expectancy_r:+.3f}R over "
                f"{wf.combined_oos.trades} trades. A strategy that fails out of "
                f"sample is a finding, not a failure - nothing here may inform a "
                f"live decision.")

    def _storage_eligibility_note(self, symbol: str,
                                  eligible: Sequence[str]) -> str:
        if eligible:
            return (f"{len(eligible)} strategy/strategies carry live_eligible=1, "
                    f"set from RobustnessReport.live_eligible.")
        return (f"NOTHING is live-eligible for {symbol}. Every stored row has "
                f"live_eligible=0 - either the walk-forward has not run yet, or "
                f"no candidate survived it. The decision layer must treat these "
                f"rankings as hypotheses, not as measured edges.")

    def _finalists(self, wf: WalkForwardResult, candidates: Sequence[Strategy],
                   screen_scores: Dict[str, float],
                   limit: int) -> List[Strategy]:
        """The strategies that earn the full robustness suite.

        Chosen from the ones the walk-forward selected *in training*, ordered by
        their training-window score. Both the membership and the ordering are
        therefore free of out-of-sample information; had they been ranked by
        out-of-sample results, the robustness assessment would be grading the
        exam it had already seen.
        """
        by_id = {s.strategy_id: s for s in candidates}
        picked: List[str] = []
        for fold in wf.folds:
            for sid in fold.selected:
                if sid not in picked and sid in by_id:
                    picked.append(sid)
        if not picked:
            picked = sorted(by_id, key=lambda sid: (-screen_scores.get(sid, 0.0), sid))
            self.log("walk-forward selected nothing in any fold; falling back to "
                     "the training-window screen so the failure is still measured")
        picked.sort(key=lambda sid: (-screen_scores.get(sid, 0.0), sid))
        return [by_id[sid] for sid in picked[:limit]]

    # ==================================================================
    # Storage / artefact plumbing
    # ==================================================================
    def _stored_rows(self, symbol: str, limit: int) -> List[Dict[str, Any]]:
        """Strategy-level rows only - regime and session slices are filtered out."""
        store = self.require_context().storage
        rows = store.top_strategies(symbol, limit=limit, live_eligible_only=False)
        return [r for r in rows
                if r.get("regime") == "ALL" and r.get("session") == "ALL"]

    @staticmethod
    def _row_json(row: Dict[str, Any]) -> Dict[str, Any]:
        try:
            value = json.loads(row.get("payload") or "null")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _row_payload(self, strategy: Strategy, sweep: _Sweep, metrics: Metrics,
                     trades: Sequence[Trade]) -> Dict[str, Any]:
        return {
            "strategy": strategy.to_dict(),
            "ranking_score": round(sweep.scores[strategy.strategy_id], 6),
            "trials_searched": len(sweep.strategies),
            "window": sweep.window,
            "exit_reasons": metrics.exit_reasons,
            "avg_mae_r": round(metrics.avg_mae_r, 4),
            "avg_mfe_r": round(metrics.avg_mfe_r, 4),
            "t_statistic": round(metrics.t_statistic, 4),
            "trades_per_day": round(metrics.trades_per_day, 3),
            "reasons": ([] if metrics.trades else ["no trades generated"]),
            "scope_note": "full-sample sweep; not yet walk-forwarded",
        }

    def _artefact(self, name: str) -> Dict[str, Any]:
        """This agent's own previously published artefact, or an empty shell."""
        try:
            existing = self.read_from(self.role, name)
        except (PermissionError, OSError):
            existing = None
        return existing if isinstance(existing, dict) else {}

    def _merge(self, name: str, symbol: str, section: Dict[str, Any],
               summary: str) -> str:
        """Merge one symbol's block into a multi-symbol artefact and publish it.

        Per-symbol independence is the whole point of the mandate, so the
        artefact is a map keyed by symbol rather than a single blended table -
        there is no such thing as a cross-symbol ranking here.
        """
        doc = self._artefact(name)
        symbols = doc.get("symbols")
        if not isinstance(symbols, dict):
            symbols = {}
        symbols[symbol] = section
        payload = {
            "artefact": name,
            "generated_et": et_stamp(),
            "note": ("Every symbol is an independent universe. Nothing measured "
                     "on one contract is claimed for another."),
            "symbols": symbols,
        }
        return self.publish(name, payload, summary)

    def _publish_rankings(self, symbol: str, section: Dict[str, Any]) -> str:
        eligible = section.get("live_eligible") or []
        return self._merge(
            "strategy_rankings", symbol, section,
            f"{symbol}: {len(section.get('top', []))} ranked strategies, "
            f"{len(eligible)} live-eligible")

    def _publish_robustness(self, symbol: str, section: Dict[str, Any]) -> str:
        eligible = section.get("live_eligible") or []
        return self._merge(
            "robustness_report", symbol, section,
            f"{symbol}: {len(section.get('finalists', []))} finalists assessed, "
            f"{len(eligible)} live-eligible")

    def _publish_performance_db(self, sweep: _Sweep) -> str:
        """The full per-strategy table, failures included.

        Deliberately compact and deliberately complete: this is the artefact
        that proves the rankings were computed over the whole generated
        universe rather than over whatever happened to work.
        """
        rows = [
            {"strategy_id": sid, "group": s.group, "primary_tf": s.primary_tf,
             "name": s.name,
             "trades": sweep.metrics[sid].trades,
             "win_rate": round(sweep.metrics[sid].win_rate, 4),
             "profit_factor": round(sweep.metrics[sid].profit_factor, 3),
             "expectancy_r": round(sweep.metrics[sid].expectancy_r, 4),
             "max_drawdown_r": round(sweep.metrics[sid].max_drawdown_r, 3),
             "t_statistic": round(sweep.metrics[sid].t_statistic, 3),
             "ranking_score": round(sweep.scores[sid], 6)}
            for sid, s in ((x.strategy_id, x) for x in sweep.strategies)]
        rows.sort(key=lambda r: (-r["ranking_score"], r["strategy_id"]))
        doc = self._artefact("performance_db")
        symbols = doc.get("symbols") if isinstance(doc.get("symbols"), dict) else {}
        symbols[sweep.symbol] = {
            "generated_et": et_stamp(),
            "window": sweep.window,
            "rows": rows,
            "note": "every generated combination, including those with zero "
                    "trades - rankings over survivors only would be biased",
        }
        return self.publish(
            "performance_db",
            {"artefact": "performance_db", "generated_et": et_stamp(),
             "symbols": symbols},
            f"{sweep.symbol}: {len(rows)} strategy results persisted")

    # ==================================================================
    # Small utilities
    # ==================================================================
    def _symbol(self, task: Task) -> str:
        symbol = task.payload.get("symbol")
        if not symbol:
            symbols = self._cfg("symbols", ())
            if not symbols:
                raise ValueError("no symbol in the task payload and none configured")
            symbol = symbols[0]
        return str(symbol).upper()

    @staticmethod
    def _requested_ids(task: Task) -> List[str]:
        ids = task.payload.get("strategy_ids") or []
        return [str(s) for s in ids]

    @staticmethod
    def _int(task: Task, key: str, default: Optional[int]) -> Optional[int]:
        raw = task.payload.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _cfg(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default

    def _account_cfg(self, name: str, default: float) -> float:
        account = getattr(self.config, "account", None) if self.config else None
        return float(getattr(account, name, default)) if account else float(default)

    def _risk_per_trade(self, ctx) -> Optional[float]:
        """Dollar risk per trade for the ruin simulation, from the risk layer.

        Asking the risk manager rather than assuming a number means the
        probability-of-ruin figure is computed at the size this account would
        actually trade, which is the only version of it worth reporting.
        """
        try:
            budget, _multiplier, _notes = ctx.risk.risk_budget()
        except Exception:                                   # noqa: BLE001
            budget = 0.0
        if budget and budget > 0:
            return float(budget)
        fallback = self._account_cfg("max_dollar_risk", 0.0)
        return float(fallback) if fallback > 0 else None

    @staticmethod
    def _cost_r(frame: SymbolFrame, trades: Sequence[Trade]) -> Optional[float]:
        """Round-turn cost as a fraction of one R, at this strategy's risk size.

        An edge smaller than its own transaction cost is not an edge, and on a
        micro contract with a tight stop the cost is a double-digit percentage
        of R - so it is measured per strategy rather than assumed away.
        """
        risks = [t.risk_points for t in trades if t.risk_points > 0]
        if not risks:
            return None
        return round(CostModel(frame.spec).cost_in_r(statistics.median(risks)), 5)

    def _trials_searched(self, symbol: str, fallback: int) -> int:
        """How many combinations were actually searched for this symbol.

        This is the number that deflates the t-statistic. Searching 4,000
        combinations buys roughly 4.1 t-units of apparent significance for free,
        so understating it here would quietly re-inflate every finalist's
        significance. It is read from the published sweep rather than guessed.
        """
        doc = self._artefact("strategy_rankings")
        section = (doc.get("symbols", {}) or {}).get(symbol)
        if isinstance(section, dict):
            trials = section.get("trials_searched")
            if isinstance(trials, int) and trials > 0:
                return max(trials, fallback)
        return max(1, fallback)

    # ==================================================================
    # Optional LLM commentary - narrative only, never a statistic
    # ==================================================================
    SYSTEM = """
You are the strategy research and backtesting member of the team. You read a
table of measured backtest and walk-forward statistics and comment on the
*research direction*: which strategy families look structurally robust, which
look curve-fitted, and what is worth testing next.

Hard limits on your output:

- You may not state, adjust, round or re-derive any number. Every statistic in
  the evidence was measured; your commentary references them, it does not
  produce them.
- You may not declare anything live-eligible. That flag is set by the
  robustness suite alone.
- "This universe shows no edge" is a complete and valuable answer. Synthetic or
  short samples usually produce exactly that, and saying so is correct.
- Quote the sample size whenever you mention a result.
""".strip()

    #: Structured shape for the commentary. Text only - no numeric fields, so
    #: there is nothing here the model could use to contradict a measurement.
    COMMENT_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string"},
            "robust_families": {"type": "array", "items": {"type": "string"}},
            "likely_curve_fit": {"type": "array", "items": {"type": "string"}},
            "research_next": {"type": "array", "items": {"type": "string"}},
            "what_would_change_my_mind": {"type": "string"},
        },
        "required": ["verdict", "robust_families", "likely_curve_fit",
                     "research_next", "what_would_change_my_mind"],
    }

    def _commentary(self, symbol: str, section: Dict[str, Any], *,
                    kind: str = "rankings") -> Optional[Dict[str, Any]]:
        """Ask the model to read the table. It may not change a single figure."""
        if not self.llm_available:
            return None
        evidence = {
            "symbol": symbol,
            "kind": kind,
            "window": section.get("window"),
            "universe": section.get("universe"),
            "survivorship": section.get("survivorship"),
            "trials_searched": section.get("trials_searched"),
            "live_eligible": section.get("live_eligible"),
            "live_eligibility_note": section.get("live_eligibility_note"),
            "walk_forward": section.get("walk_forward"),
            "top": [
                {k: v for k, v in row.items() if k != "evidence"}
                for row in (section.get("top") or [])[:10]],
            "finalists": section.get("finalists"),
            "universe_slices": section.get("universe_slices"),
        }
        response = self.reason(
            system=self.system_prompt(self.SYSTEM), evidence=evidence,
            question=(
                "Which strategy families in this table look structurally robust "
                "and which look curve-fitted? What should be researched next for "
                "this symbol? If the evidence supports no edge, say so plainly."),
            schema=self.COMMENT_SCHEMA)
        if response is None or not response.ok or response.parsed is None:
            return None
        return {"source": "llm", "model": response.model,
                "commentary": response.parsed,
                "note": "narrative only; every statistic above is measured"}
