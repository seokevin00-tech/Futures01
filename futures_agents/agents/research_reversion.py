"""Research specialist: mean reversion, reversal and VWAP - the fade desk.

This agent researches one hypothesis about why price moves intraday: that it
overshoots a reference and comes back. Three families express it -
MEAN_REVERSION (stretch from a moving reference), REVERSAL (exhaustion at an
extreme) and VWAP (the session's volume-weighted anchor). The families are read
from ``families_for(self.role)`` and never written down here, because the split
between the three specialists is owned by ``team/roles.py``; hard-coding it here
is how two specialists end up claiming one family and the pooled ranking
double-counts an edge.

**Why this desk audits itself for cost before it publishes.** A fade is the
cheapest kind of edge to falsify. Price either returns to the reference inside a
defined window or it does not, and there is no narrative available either way -
which is exactly what makes these the most honest edges on the desk. That
honesty is bought at a price. A fade runs a tight target against a wide adverse
excursion; it is short the tail by construction, so its expectancy per trade is
small while its exposure to slippage is large. On a micro contract with a tight
stop the round turn is a double-digit percentage of one R, so +0.08R per trade
is not an edge, it is a transaction-cost sampling artefact wearing one.

So every candidate goes through ``cost_stress`` - the debate protocol's own
doubled-slippage test, the identical call a rival would use to challenge it -
*before* it is published. A fade that does not survive doubled slippage is not a
finding. Candidates that fail are kept in the artefact with their retained
fraction rather than dropped silently: on a synthetic or short sample the number
of fades that die at the cost bar is the most useful thing this desk measures,
and hiding it would leave the reader thinking the search found nothing rather
than that it found things and killed them.

**Cross-examination.** ``challenge`` reads both rivals' findings and files only
objections it can measure, on three angles this desk is placed to see:

* a continuation edge that is entirely one-sided over a sample that drifted is
  directional exposure, not skill - measured as the long/short expectancy split
  against the sample's own net move, plus an ``adversarial_retest`` on a split
  the owner did not choose;
* a breakout or liquidity edge tends to live in expansion and vanish elsewhere -
  measured with ``regime_concentration``;
* an edge that takes this desk's trades is one edge, not two - measured with
  ``trade_overlap`` against its own fingerprints.

``rebut`` answers what is filed back. Conceding a well-measured objection is the
job being done correctly: an unmeasured ``REBUTTED`` is downgraded to
``UNANSWERED`` by the pooler, so asserting is strictly worse than conceding, and
this desk expects to concede on cost fragility in particular.

Everything here is a measurement, so there is no LLM path: the model may not
originate a statistic, and a debate artefact is nothing but statistics. Every
figure published by this agent came from the backtester.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..backtest.engine import BacktestEngine, Trade
from ..backtest.metrics import Metrics
from ..backtest.robustness import deflated_expectancy
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
from .research import (DEFAULT_FINALISTS, DEFAULT_WF_CANDIDATES,
                       MIN_TRADES_FOR_RANK, StrategyResearchAgent)

__all__ = ["ResearchReversionAgent"]


#: Most candidates promoted to findings in one pass. Each one costs a
#: doubled-slippage re-run over the whole history before it may be published.
MAX_FINDINGS = 5

#: Rival findings cross-examined per opponent. Bounded because an
#: ``adversarial_retest`` is a full pass over the history per finding, and a
#: specialist that spends an hour to file twelve objections has not helped.
#: The ones examined are the first the owner listed, which is the owner's own
#: ranking order - so the findings most likely to reach the live system are the
#: ones that get cross-examined, and the choice is reproducible.
MAX_CHALLENGE_TARGETS = 3

#: Matches ``find_redundancy``'s default, so a REDUNDANT challenge this agent
#: files names exactly the cluster the pooler will independently collapse.
REDUNDANCY_THRESHOLD = 0.55

#: Trades needed on the losing side of a long/short split before the split is a
#: measurement rather than an empty bucket. Zero trades on one side is treated
#: separately: that is a structurally one-sided rule, not a thin sample.
ONE_SIDED_MIN_SAMPLE = 10

#: Where ``adversarial_retest`` cuts a rival's history. ``walk_forward``'s first
#: anchored training window ends at 0.30, so a 0.50 cut is a boundary no owner
#: selected on.
RETEST_SPLIT = 0.50

#: A surviving finding whose expectancy is more than halved by doubled slippage
#: is published, but a COST_FRAGILE challenge against it is conceded in part
#: rather than rebutted: it is real and it is smaller than the headline.
COST_RETENTION_PARTIAL = 0.50


class ResearchReversionAgent(StrategyResearchAgent):
    """Researches fade and exhaustion families, and cross-examines the rest."""

    def __init__(self, fs, bus, *, context=None, config=None, llm=None):
        # StrategyResearchAgent.__init__ binds the desk lead's role, so the
        # grandparent is called directly to bind this one. Everything else about
        # the base class - the universe sweep, persistence, walk-forward,
        # robustness suite and ranking - is inherited unchanged and reused.
        DomainAgent.__init__(self, Role.RESEARCH_REVERSION, fs, bus,
                             context=context, config=config, llm=llm)
        self.families: Tuple[str, ...] = families_for(self.role)
        self.opponents: Tuple[Role, ...] = opponents_of(self.role)
        #: Regenerated strategy universes, keyed by (symbol, families, budget).
        #: The combinator is seeded per symbol, so a rival's strategy object can
        #: be reproduced from its id without the rival having to hand it over.
        self._regenerated: Dict[Tuple[Any, ...], Dict[str, Strategy]] = {}

    # ==================================================================
    # Entry point
    # ==================================================================
    def handle(self, task: Task) -> AgentResult:
        if task.kind == "research_family":
            return self._research_family_task(task)
        if task.kind == "challenge":
            return self._challenge_task(task)
        if task.kind == "rebut":
            return self._rebut_task(task)
        # The desk lead's kinds (research_strategies, walk_forward, pool, ...)
        # are deliberately not claimed here even though they are implemented in
        # the base class: one family owner answering a desk-wide ranking request
        # would publish a ranking covering a third of the universe.
        raise ValueError(
            f"{self.id} does not implement task kind {task.kind!r}. It handles "
            f"research_family, challenge and rebut for the "
            f"{', '.join(self.families)} families; desk-wide research and "
            f"pooling belong to {Role.STRATEGY_RESEARCH.value}.")

    # ==================================================================
    # Universe: the one thing this specialist narrows
    # ==================================================================
    def _universe(self, ctx, symbol: str, timeframes: Sequence[int],
                  max_strategies: int, *, generate: bool) -> List[Strategy]:
        """This specialist's families only - the base class's sweep, unchanged.

        Overriding here rather than duplicating ``_sweep_task`` and
        ``_walk_forward_task`` is what makes the whole inherited pipeline
        family-scoped: both of them reach the strategy universe through this one
        method, so narrowing it narrows the sweep, the leak-free candidate
        screen, the walk-forward and ``trials_searched`` together, and they stay
        consistent with each other by construction.

        ``trials_searched`` is the reason this matters beyond tidiness. It is
        the divisor that deflates a finalist's t-statistic, and it has to count
        the combinations *this* search actually looked at. Sweeping all ten
        families and filtering to three afterwards would search 4,000 and report
        three families' worth of results, quietly re-inflating every finding's
        significance by the combinations it threw away.
        """
        registered = [s for s in ctx.registry.symbol(symbol).all()
                      if s.group in self.families]
        if registered and not generate:
            # Never truncated: the searched universe is what it is.
            return registered

        strategies = generate_strategies(symbol, timeframes,
                                         groups=self.families,
                                         max_total=max_strategies)
        added = 0
        for s in strategies:
            if ctx.registry.get(s.strategy_id) is None:
                ctx.registry.add(s)
                added += 1
        self.log(f"{symbol}: generated {len(strategies)} strategies in "
                 f"{'/'.join(self.families)} over timeframes "
                 f"{[tf_label(t) for t in timeframes]} ({added} new)")
        return strategies

    # ==================================================================
    # research_family
    # ==================================================================
    def _research_family_task(self, task: Task) -> AgentResult:
        """Sweep the fade families, walk-forward the best, publish findings.

        The order is deliberate. The sweep measures every combination and
        persists winners and losers alike, so the denominator stays honest. The
        walk-forward re-selects per fold from training data only. The cost bar
        runs last, on the candidates that survived both, because there is no
        point stressing a strategy that has not first shown an edge to stress.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        families = "/".join(self.families)
        if len(frame.base) == 0:
            return AgentResult(
                ok=True,
                summary=f"{symbol}: no bars available - no {families} research",
                payload={"symbol": symbol, "bars": 0, "families": list(self.families),
                         "findings": 0,
                         "note": "insufficient history; nothing was tested"})

        max_strategies = self._int(task, "max_strategies",
                                   self._cfg("max_combinations", 4_000))
        days = self._int(task, "days", None)
        timeframes = self._timeframes(task, frame)

        strategies = self._universe(ctx, symbol, timeframes, max_strategies,
                                    generate=True)
        if not strategies:
            return AgentResult(
                ok=True,
                summary=f"{symbol}: no {families} combinations could be built",
                payload={"symbol": symbol, "families": list(self.families),
                         "strategies": 0, "findings": 0})

        # ---- 1. the family sweep, via the inherited machinery -------------
        sweep = self._run_sweep(frame, symbol, strategies, days)
        self._persist_sweep(sweep)
        trials = len(sweep.strategies)

        # ---- 2. walk-forward the best, also inherited ---------------------
        wf_result = self._walk_forward_task(self._walk_forward_subtask(task, sweep))
        wf_payload = wf_result.payload if isinstance(wf_result.payload, dict) else {}
        wf_rows = {str(row.get("strategy_id")): row
                   for row in (wf_payload.get("finalists") or ())
                   if isinstance(row, dict)}
        eligible_ids = {str(s) for s in (wf_payload.get("live_eligible") or ())}

        # ---- 3. the cost bar, on this desk's own work ---------------------
        shortlist, rejected, qualified = self._shortlist(sweep)
        audit = [self._cost_audit(frame, sweep, sid) for sid in shortlist]
        survivors = [row for row in audit if row["survives"]]

        # ---- 4. findings -------------------------------------------------
        findings = [self._finding(sweep, row, wf_rows, eligible_ids, trials)
                    for row in survivors]
        section = self._findings_section(sweep, findings, audit, rejected,
                                         wf_payload, trials, qualified)
        path = self._merge(
            "findings", symbol, section,
            f"{symbol}: {len(findings)} {families} finding(s) past the cost bar")

        summary = self._research_summary(symbol, sweep, audit, findings,
                                         wf_payload, trials, qualified)
        self.log(summary)
        return AgentResult(
            ok=True, summary=summary,
            payload={
                "symbol": symbol,
                "families": list(self.families),
                "strategies_tested": trials,
                "trials_searched": trials,
                "window": sweep.window,
                "shortlisted": len(audit),
                "cost_bar_survivors": len(survivors),
                "cost_bar": section["cost_bar"]["aggregate"],
                "findings": [f.to_dict() for f in findings],
                "live_eligible": sorted(
                    f.strategy_id for f in findings if f.live_eligible),
                "walk_forward": {
                    "efficiency": wf_payload.get("walk_forward_efficiency"),
                    "is_credible": wf_payload.get("is_credible"),
                    "combined_oos_trades": (
                        wf_payload.get("combined_oos") or {}).get("trades"),
                    "live_eligibility_note": wf_payload.get("live_eligibility_note"),
                },
            },
            artefacts=[path] + list(wf_result.artefacts))

    def _walk_forward_subtask(self, task: Task, sweep) -> Task:
        """The walk-forward request, with its pool scaled to a family universe.

        ``folds`` is left alone - that is the strength of the test, and quietly
        weakening it to save time would be a lie told in units of seconds. What
        is scaled is the *pool*, which decides how many strategies are screened,
        not how hard each one is tested.

        The finalist count applies the desk lead's own ratio - it runs 5
        finalists from 40 candidates, one in eight - and takes the whole
        finalists that ratio pays for rather than rounding a fraction of one up.
        That matters because the robustness suite is by far the most expensive
        thing this agent does: each finalist gets its own six-fold walk-forward
        over the entire history, and the cost of that is driven by the bars
        traversed, not by the single strategy traversing them. Measured here, a
        finalist costs about as much as the whole candidate walk-forward does.

        The candidate count is a pragmatic compromise rather than a preserved
        ratio, and is worth saying plainly - the desk lead screens 40 from
        4,000, and one in a hundred of a 120-strategy family universe would be
        a single candidate. A floor of 8 keeps the per-fold selection with
        something to choose between; the cap keeps a full-size universe
        behaving exactly as the desk lead's would, at 40 candidates and 5
        finalists.

        Both knobs stay overridable from the payload, so a caller that wants a
        wider pool and has the minutes to spend can simply ask for it.
        """
        universe = len(sweep.strategies)
        candidates = max(8, min(DEFAULT_WF_CANDIDATES, universe // 8))
        finalists = max(1, min(DEFAULT_FINALISTS,
                               int(candidates * DEFAULT_FINALISTS
                                   / DEFAULT_WF_CANDIDATES)))
        payload = dict(task.payload)
        payload["symbol"] = sweep.symbol
        payload.setdefault("wf_candidates", candidates)
        payload.setdefault("finalists", finalists)
        return Task(task_id=f"{task.task_id}-wf", kind="walk_forward",
                    title=f"{sweep.symbol}: walk-forward the fade families",
                    payload=payload, assigned_to=self.role)

    # ---- the cost bar --------------------------------------------------
    def _shortlist(self, sweep) -> Tuple[List[str], List[Dict[str, Any]], int]:
        """Candidates worth stressing, why the near-misses were not, and how
        many cleared the bar in total.

        The gate is the repository's own sample floor plus a positive measured
        expectancy. Everything that fails is reported rather than forgotten,
        because "nothing cleared 30 trades" and "everything that cleared 30
        trades lost money" are different results and the reader needs to know
        which one happened.
        """
        shortlist: List[str] = []
        rejected: List[Dict[str, Any]] = []
        qualified = 0
        for sid in sweep.ranked():
            m = sweep.metrics[sid]
            if m.trades >= MIN_TRADES_FOR_RANK and m.expectancy_r > 0:
                qualified += 1
                if len(shortlist) < MAX_FINDINGS:
                    shortlist.append(sid)
                continue
            if len(rejected) < 10 and m.trades > 0:
                rejected.append({
                    "strategy_id": sid,
                    "family": sweep.results[sid].group,
                    "trades": m.trades,
                    "expectancy_r": round(m.expectancy_r, 4),
                    "reason": ("sample below the "
                               f"{MIN_TRADES_FOR_RANK}-trade floor"
                               if m.trades < MIN_TRADES_FOR_RANK
                               else "negative expectancy over the sweep window"),
                })
        return shortlist, rejected, qualified

    def _cost_audit(self, frame: SymbolFrame, sweep, sid: str) -> Dict[str, Any]:
        """Doubled slippage, run exactly as a rival would run it.

        ``cost_stress`` takes no window, so it measures the whole history while
        the sweep may have been limited to the last N trading days. That is
        deliberate and is reported rather than papered over: the point of
        pre-running it is that this is the *identical* call a challenger will
        make, so the number here is the number that will come back.

        It also means a candidate can fail for two quite different reasons, and
        they are separated here. ``baseline_positive`` False says the edge does
        not exist outside the sweep window at all; ``baseline_positive`` True
        with ``survives`` False says the edge is real and smaller than its own
        transaction costs. Only the second one is cost fragility.
        """
        strategy = {s.strategy_id: s for s in sweep.strategies}[sid]
        metrics = sweep.metrics[sid]
        stress = cost_stress(frame, strategy)
        baseline_positive = float(stress.get("baseline_expectancy_r", 0.0)) > 0
        survives = bool(stress.get("survives"))
        return {
            "strategy_id": sid,
            "strategy_name": strategy.name,
            "family": strategy.group,
            "timeframe": strategy.primary_tf,
            "sweep_window_trades": metrics.trades,
            "sweep_window_expectancy_r": round(metrics.expectancy_r, 4),
            "cost_r_per_round_turn": self._cost_r(frame, sweep.results[sid].trades),
            "cost_stress": stress,
            "cost_stress_window": (
                "full available history - cost_stress takes no window, so this "
                "is the same measurement a challenger would file"),
            "baseline_positive": baseline_positive,
            "survives": survives,
            "verdict": (
                "survives doubled slippage" if survives else
                ("edge is smaller than its own transaction costs - not a finding"
                 if baseline_positive else
                 "no edge outside the sweep window - the sweep window, not the "
                 "cost model, is what this failed")),
        }

    def _finding(self, sweep, row: Dict[str, Any], wf_rows: Dict[str, Dict[str, Any]],
                 eligible_ids: Iterable[str], trials: int) -> Finding:
        """One published claim, fingerprinted from the trades that produced it."""
        sid = row["strategy_id"]
        strategy = {s.strategy_id: s for s in sweep.strategies}[sid]
        metrics = sweep.metrics[sid]
        trades = sweep.results[sid].trades
        wf = wf_rows.get(sid) or {}
        return Finding(
            owner=self.id, symbol=sweep.symbol, strategy_id=sid,
            strategy_name=strategy.name, family=strategy.group,
            timeframe=strategy.primary_tf, metrics=metrics,
            # The walk-forward's verdict where this strategy reached it, the
            # sweep's ranking score where it did not - never the better of the
            # two, and the artefact records which was used.
            robustness_score=float(wf.get("score", sweep.scores[sid])),
            walk_forward_efficiency=float(wf.get("own_wf_efficiency", 0.0)),
            trials_searched=trials,
            live_eligible=sid in set(eligible_ids),
            trade_fingerprint=self._fingerprint(trades),
            r_series=[t.net_r for t in trades],
            claim=self._claim(strategy, metrics, row, wf, sweep, trials))

    @staticmethod
    def _fingerprint(trades: Sequence[Trade]) -> List[Tuple[int, int]]:
        """``(entry bar index, direction sign)`` per trade.

        ``entry_index`` is absolute in the base series whatever window the sweep
        ran over, so two specialists' fingerprints are directly comparable even
        when they swept different spans. Redundancy detection depends on this
        being the real trades: a synthesised or empty fingerprint would let one
        edge be pooled twice as two.
        """
        return [(int(t.entry_index), int(t.direction.sign)) for t in trades]

    def _claim(self, strategy: Strategy, metrics: Metrics, row: Dict[str, Any],
               wf: Dict[str, Any], sweep, trials: int) -> str:
        stress = row["cost_stress"]
        window = sweep.window
        parts = [
            f"{strategy.group} fade on {tf_label(strategy.primary_tf)}: "
            f"{metrics.expectancy_r:+.3f}R over {metrics.trades} trades "
            f"({window.get('trading_days')} trading days, "
            f"{window.get('bars', 0):,} bars), win rate "
            f"{metrics.win_rate * 100:.1f}%, PF {metrics.profit_factor:.2f}, "
            f"t={metrics.t_statistic:.2f} against {trials} combinations searched.",
            f"Held to the cost bar first: doubled slippage over the full history "
            f"leaves {stress['stressed_expectancy_r']:+.4f}R, "
            f"{stress['retained_fraction']:.0%} of the "
            f"{stress['baseline_expectancy_r']:+.4f}R baseline.",
        ]
        if wf:
            parts.append(
                f"Walk-forward: {wf.get('oos_trades', 0)} out-of-sample trades, "
                f"own efficiency {float(wf.get('own_wf_efficiency', 0.0)):.2f}, "
                f"deflated expectancy "
                f"{float(wf.get('deflated_expectancy_r', 0.0)):+.5f}R, "
                f"live_eligible={bool(wf.get('live_eligible'))}.")
        else:
            parts.append(
                "Not selected into the walk-forward finalists, so this claim "
                "rests on the full-sample sweep and the cost bar alone and is "
                "not live-eligible.")
        parts.append(
            "Falsifier: it reverts inside the defined window or it does not - "
            "re-run it on any split this desk did not choose, or at any "
            "slippage above the doubled model, and it should still be positive.")
        return " ".join(parts)

    def _findings_section(self, sweep, findings: Sequence[Finding],
                          audit: Sequence[Dict[str, Any]],
                          rejected: Sequence[Dict[str, Any]],
                          wf_payload: Dict[str, Any], trials: int,
                          qualified: int) -> Dict[str, Any]:
        retained = [float(r["cost_stress"].get("retained_fraction", 0.0))
                    for r in audit if r["baseline_positive"]]
        by_family: Dict[str, int] = {}
        for s in sweep.strategies:
            by_family[s.group] = by_family.get(s.group, 0) + 1
        killed_by_cost = [r["strategy_id"] for r in audit
                          if r["baseline_positive"] and not r["survives"]]
        killed_by_window = [r["strategy_id"] for r in audit
                            if not r["baseline_positive"]]
        finalist_ids = {str(r.get("strategy_id"))
                        for r in (wf_payload.get("finalists") or ())
                        if isinstance(r, dict)}
        return {
            "generated_et": et_stamp(),
            "symbol": sweep.symbol,
            "owner": self.id,
            "families": list(self.families),
            "source": "deterministic",
            "stance": (
                "Fade edges are cheap to falsify and expensive to hold: a tight "
                "target against a wide adverse excursion is short the tail, so "
                "cost is the first gate here, not the last."),
            "window": sweep.window,
            "trials_searched": trials,
            "universe": {"strategies": trials, "by_family": by_family},
            "survivorship": {
                "strategies_tested": trials,
                "strategies_with_trades": sum(1 for m in sweep.metrics.values()
                                              if m.trades > 0),
                "cleared_sample_floor_with_positive_expectancy": qualified,
                "cost_bar_budget": MAX_FINDINGS,
                "shortlisted_for_the_cost_bar": len(audit),
                "note": ("every combination was persisted to the performance "
                         "database, including those that never traded"),
            },
            "cost_bar": {
                "rule": ("cost_stress (doubled slippage) must leave a positive "
                         "expectancy. A fade that does not survive it is not "
                         "published as a finding."),
                "aggregate": {
                    "examined": len(audit),
                    "survived": len(findings),
                    "failed_on_cost": len(killed_by_cost),
                    "failed_outside_sweep_window": len(killed_by_window),
                    "median_retained_fraction": (
                        round(statistics.median(retained), 4) if retained else None),
                    "retained_fractions": [round(r, 4) for r in retained],
                    "note": ("retained_fraction is only meaningful where the "
                             "full-history baseline was positive; where it was "
                             "not, the candidate failed the window, not the "
                             "cost model"),
                },
                "audit": list(audit),
            },
            "near_misses": list(rejected),
            "findings": [
                {**f.to_dict(),
                 # Finding.to_dict() omits the fingerprint; redundancy detection
                 # across specialists needs it, so it is published explicitly.
                 "trade_fingerprint": [[i, d] for i, d in f.trade_fingerprint],
                 "cost_stress": next(r["cost_stress"] for r in audit
                                     if r["strategy_id"] == f.strategy_id),
                 "robustness_score_source": (
                     "RobustnessReport.score - this strategy was a walk-forward "
                     "finalist and went through the full suite"
                     if f.strategy_id in finalist_ids else
                     "robust_score over the sweep window - this strategy was "
                     "not selected as a walk-forward finalist, so it carries no "
                     "out-of-sample verdict and cannot be live-eligible")}
                for f in findings],
            "walk_forward": {
                "efficiency": wf_payload.get("walk_forward_efficiency"),
                "selection_stability": wf_payload.get("selection_stability"),
                "is_credible": wf_payload.get("is_credible"),
                "combined_oos": wf_payload.get("combined_oos"),
                "finalists": wf_payload.get("finalists"),
                "live_eligible": wf_payload.get("live_eligible"),
                "live_eligibility_note": wf_payload.get("live_eligibility_note"),
                "detail": ("the full robustness suite for these finalists is in "
                           "this agent's own robustness_report artefact"),
            },
            "live_eligible": sorted(f.strategy_id for f in findings
                                    if f.live_eligible),
            "note": (
                "A finding is a claim, not a verdict. Nothing here is live-"
                "eligible unless the robustness suite said so, and every claim "
                "is open to a measured challenge from the other two "
                "specialists."),
        }

    def _research_summary(self, symbol: str, sweep, audit: Sequence[Dict[str, Any]],
                          findings: Sequence[Finding], wf_payload: Dict[str, Any],
                          trials: int, qualified: int) -> str:
        families = "/".join(self.families)
        with_trades = sum(1 for m in sweep.metrics.values() if m.trades > 0)
        killed_cost = sum(1 for r in audit
                          if r["baseline_positive"] and not r["survives"])
        killed_window = sum(1 for r in audit if not r["baseline_positive"])
        retained = [float(r["cost_stress"].get("retained_fraction", 0.0))
                    for r in audit if r["baseline_positive"]]
        head = (f"{symbol} {families}: swept {trials} combinations over "
                f"{sweep.window.get('bars', 0):,} bars "
                f"({sweep.window.get('trading_days')} trading days) in "
                f"{sweep.seconds:.1f}s; {with_trades} produced trades, "
                f"{qualified} cleared the {MIN_TRADES_FOR_RANK}-trade floor "
                f"with positive expectancy.")
        if not audit:
            return (f"{head} Nothing reached the cost bar, so 0 findings are "
                    f"published - on this sample the fade families show no edge "
                    f"worth stressing, which is a result, not a failure. "
                    f"{(wf_payload.get('live_eligibility_note') or '')}").strip()
        cost = (f" Cost bar (doubled slippage, full history): {len(findings)} of "
                f"{len(audit)} survived")
        if retained:
            cost += (f", median retained fraction "
                     f"{statistics.median(retained):.0%}")
        cost += (f"; {killed_cost} died on cost, {killed_window} had no edge "
                 f"outside the sweep window.")
        tail = (f" {len(findings)} finding(s) published, "
                f"{sum(1 for f in findings if f.live_eligible)} live-eligible. "
                f"{(wf_payload.get('live_eligibility_note') or '')}")
        return (head + cost + tail).strip()

    # ==================================================================
    # challenge
    # ==================================================================
    def _challenge_task(self, task: Task) -> AgentResult:
        """Cross-examine both rivals, and file only what is measured.

        Every challenge here carries numbers. An unsubstantiated objection is
        discarded by the pooler and contributes nothing, so the failure mode
        this method guards against is not missing an objection - it is filing
        one this desk cannot back.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        mine = self._own_findings(symbol)

        filed: List[Challenge] = []
        examined: List[Dict[str, Any]] = []
        rivals: Dict[str, Any] = {}
        for opponent in self.opponents:
            doc = self._read_rival(opponent, "findings")
            if doc is None:
                rivals[opponent.value] = {
                    "readable": False,
                    "note": ("no findings artefact published yet - "
                             "self.read_from returned None; nothing to "
                             "cross-examine, and no objection is implied by "
                             "its absence"),
                }
                continue
            rows = [r for r in self._finding_rows(doc, symbol)
                    if str(r.get("owner") or opponent.value) != self.id]
            rivals[opponent.value] = {"readable": True,
                                      "findings_published": len(rows),
                                      "examined": min(len(rows),
                                                      MAX_CHALLENGE_TARGETS)}
            for row in rows[:MAX_CHALLENGE_TARGETS]:
                record, challenges = self._examine(frame, opponent, row, mine)
                examined.append(record)
                filed.extend(challenges)

        substantiated = [c for c in filed if c.is_substantiated]
        discarded = len(filed) - len(substantiated)
        section = {
            "generated_et": et_stamp(),
            "symbol": symbol,
            "challenger": self.id,
            "source": "deterministic",
            "opponents": rivals,
            "angles": {
                "directional_beta": (
                    "for continuation families: the long/short expectancy split "
                    "measured against the sample's own net move. An edge that is "
                    "entirely one-sided over a drifting sample is exposure, not "
                    "skill. Filed as REGIME_ARTEFACT - the nearest measurable "
                    "kind - because the edge is a property of the period."),
                "out_of_sample": (
                    f"adversarial_retest at a {RETEST_SPLIT:.0%} cut, a split no "
                    f"owner selected on: the anchored walk-forward's first "
                    f"training window ends at 30%."),
                "regime_concentration": (
                    "for breakout and liquidity families: expansion-regime edges "
                    "tend to hold their entire profit in one thinly-sampled "
                    "regime."),
                "redundancy": (
                    f"trade_overlap against this desk's own fingerprints at the "
                    f"{REDUNDANCY_THRESHOLD:.0%} threshold - an 'independent' "
                    f"edge that takes these trades is one edge, not two."),
            },
            "own_findings_compared": sorted(mine),
            "examined": examined,
            "challenges": [c.to_dict() for c in substantiated],
            "discarded_unsubstantiated": discarded,
            "note": ("Only measured objections are filed. Where a measurement "
                     "could not be taken the reason is recorded in 'examined' "
                     "and no challenge was raised."),
        }
        path = self._merge(
            "challenges", symbol, section,
            f"{symbol}: {len(substantiated)} measured challenge(s) filed")

        unreadable = [k for k, v in rivals.items() if not v.get("readable")]
        summary = self._challenge_summary(symbol, substantiated, examined,
                                          rivals, unreadable)
        self.log(summary)
        return AgentResult(
            ok=True, summary=summary,
            payload={"symbol": symbol, "challenger": self.id,
                     "challenges": [c.to_dict() for c in substantiated],
                     "filed": len(substantiated),
                     "examined": len(examined),
                     "opponents": rivals,
                     "opponents_without_findings": unreadable},
            artefacts=[path])

    def _challenge_summary(self, symbol: str, filed: Sequence[Challenge],
                           examined: Sequence[Dict[str, Any]],
                           rivals: Dict[str, Any], unreadable: List[str]) -> str:
        if not rivals or all(not v.get("readable") for v in rivals.values()):
            missing = ", ".join(unreadable) or "both rivals"
            return (f"{symbol}: no cross-examination possible - {missing} "
                    f"published no findings artefact (read_from returned None). "
                    f"0 challenges filed; this is a missing input, not a "
                    f"clean bill of health for their research.")
        by_kind: Dict[str, int] = {}
        for c in filed:
            by_kind[c.kind.value] = by_kind.get(c.kind.value, 0) + 1
        detail = ", ".join(f"{v}x {k}" for k, v in sorted(by_kind.items())) or "none"
        tail = (f" {', '.join(unreadable)} published no findings."
                if unreadable else "")
        return (f"{symbol}: examined {len(examined)} rival finding(s), filed "
                f"{len(filed)} measured challenge(s) ({detail}). Every challenge "
                f"carries a measurement; objections that could not be measured "
                f"were not filed.{tail}")

    # ---- one rival finding, examined -----------------------------------
    def _examine(self, frame: SymbolFrame, opponent: Role, row: Dict[str, Any],
                 mine: Dict[str, Dict[str, Any]]
                 ) -> Tuple[Dict[str, Any], List[Challenge]]:
        symbol = frame.symbol
        sid = str(row.get("strategy_id"))
        owner = str(row.get("owner") or opponent.value)
        family = str(row.get("family") or "").upper()
        lens = self._lens(family, opponent)
        strategy = self._resolve_strategy(symbol, sid, opponent, row)
        record: Dict[str, Any] = {
            "strategy_id": sid, "owner": owner, "family": family or "unstated",
            "lens": lens, "strategy_resolved": strategy is not None,
            "measurements": [], "not_measured": [],
        }
        if strategy is None:
            record["not_measured"].append(
                "the strategy object could not be resolved from the shared "
                "registry or regenerated from the seeded combinator, so nothing "
                "that needs a re-run could be measured")

        published = self._published_fingerprint(row)
        trades: Optional[List[Trade]] = None
        re_ran = False
        needs_trades = (lens == "expansion") or (published is None and mine)
        if needs_trades and strategy is not None:
            trades = list(BacktestEngine(frame).run(strategy).trades)
            re_ran = True
        reproduced = self._fingerprint(trades) if trades else None

        # A fingerprint this desk reproduced outranks one it was handed - an
        # overlap claim is only as good as the trades behind it, and these are
        # trades this agent watched the backtester take. Where the re-run comes
        # back empty the owner's own published fingerprint is still usable
        # evidence about the owner's own claim, but it is labelled unverified
        # rather than quietly promoted to a measurement.
        if reproduced:
            fingerprint: Optional[List[Tuple[int, int]]] = reproduced
            source = (f"reproduced by re-running the strategy over the full "
                      f"history ({len(reproduced)} trades)")
            if published and len(published) != len(reproduced):
                source += (f"; the owner published a {len(published)}-trade "
                           f"fingerprint, which does not agree")
        elif published:
            fingerprint = published
            source = ("published by the owner, NOT independently reproduced - a "
                      "re-run over the full history took no trades"
                      if re_ran else
                      "published by the owner, not independently reproduced")
        else:
            fingerprint, source = None, "unavailable"
        record["fingerprint_source"] = source

        out: List[Challenge] = []
        if lens == "continuation":
            out.extend(self._challenge_directional_beta(frame, row, owner, record))
            out.extend(self._challenge_split(frame, row, owner, strategy, record))
        elif lens == "expansion":
            out.extend(self._challenge_regime(frame, row, owner, trades, re_ran,
                                              record))
        else:
            record["not_measured"].append(
                f"family {family or 'unstated'} matches neither the "
                f"continuation nor the expansion lens; only redundancy was "
                f"tested")
        out.extend(self._challenge_overlap(frame, row, owner, fingerprint, mine,
                                           record))
        return record, out

    def _lens(self, family: str, opponent: Role) -> str:
        """Which cross-examination applies, from the finding's declared family.

        Keyed on the family rather than on who filed it, so a specialist
        publishing outside its usual shape still gets the right lens, with the
        owner's declared families as the fallback when the row does not say.
        """
        continuation = {"TREND", "PULLBACK", "MOMENTUM", "MULTI_TIMEFRAME"}
        expansion = {"LIQUIDITY", "OPENING_RANGE", "BREAKOUT"}
        candidates = [family] if family else list(families_for(opponent))
        if any(c in continuation for c in candidates):
            return "continuation"
        if any(c in expansion for c in candidates):
            return "expansion"
        return ""

    # ---- angle 1: directional beta --------------------------------------
    def _challenge_directional_beta(self, frame: SymbolFrame, row: Dict[str, Any],
                                    owner: str, record: Dict[str, Any]
                                    ) -> List[Challenge]:
        """Is the edge skill, or is it exposure to a sample that drifted?

        A continuation rule that makes all its money on one side, over a period
        that moved that way, has not demonstrated that it reads continuation. It
        has demonstrated that it was long a market that went up. The measurement
        is the long/short expectancy split set against the sample's own net
        move, which is what separates the two readings.
        """
        m = row.get("metrics")
        if not isinstance(m, dict):
            record["not_measured"].append(
                "directional beta: the finding publishes no metrics block, so "
                "the long/short split could not be read")
            return []
        longs, shorts = int(m.get("long_trades") or 0), int(m.get("short_trades") or 0)
        le = float(m.get("long_expectancy_r") or 0.0)
        se = float(m.get("short_expectancy_r") or 0.0)
        total = int(m.get("trades") or (longs + shorts))
        drift = self._sample_drift(frame)
        measurement = {
            "long_trades": longs, "short_trades": shorts,
            "long_expectancy_r": round(le, 4), "short_expectancy_r": round(se, 4),
            "total_trades": total, **drift,
        }
        record["measurements"].append({"directional_beta": measurement})

        if total < 2 * ONE_SIDED_MIN_SAMPLE:
            record["not_measured"].append(
                f"directional beta: {total} trades is too small a sample for a "
                f"long/short split to mean anything")
            return []
        positive_sides = [s for s, e in ((1, le), (-1, se)) if e > 0]
        if len(positive_sides) != 1:
            return []                       # both sides work, or neither: no case
        side = positive_sides[0]
        other_trades = shorts if side == 1 else longs
        other_exp = se if side == 1 else le
        if 0 < other_trades < ONE_SIDED_MIN_SAMPLE:
            record["not_measured"].append(
                f"directional beta: only {other_trades} trades on the losing "
                f"side - one-sidedness is not measurable at that sample")
            return []
        if drift["sample_drift_sign"] != side:
            return []                       # one-sided against the drift is not beta

        measurement.update({
            "profitable_side": "LONG" if side == 1 else "SHORT",
            "unprofitable_side_trades": other_trades,
            "unprofitable_side_expectancy_r": round(other_exp, 4),
            "structurally_one_sided": other_trades == 0,
            "aligned_with_sample_drift": True,
        })
        shape = ("takes no trades at all on the other side"
                 if other_trades == 0 else
                 f"loses {abs(other_exp):.3f}R per trade over {other_trades} "
                 f"trades on the other side")
        return [Challenge(
            challenger=self.id, target_owner=owner,
            target_strategy_id=str(row.get("strategy_id")), symbol=frame.symbol,
            kind=ChallengeKind.REGIME_ARTEFACT,
            claim=(
                f"Directional beta, not an edge: this finding earns "
                f"{max(le, se):+.3f}R per trade on the "
                f"{measurement['profitable_side']} side and {shape}, over a "
                f"sample whose own net move was "
                f"{drift['sample_net_move_points']:+.1f} points "
                f"({drift['sample_net_move_pct']:+.2f}%) in that same direction. "
                f"A rule that only works in the direction the sample happened to "
                f"travel is exposure to the period, not a reading of "
                f"continuation. Filed as REGIME_ARTEFACT because the edge is a "
                f"property of the sample rather than of the rule; it would be "
                f"answered by showing a positive expectancy on the other side, "
                f"or the same edge over a sample that drifted the other way."),
            measurement=measurement)]

    def _sample_drift(self, frame: SymbolFrame) -> Dict[str, Any]:
        """The sample's own net move - the thing a one-sided edge may be riding."""
        bars = frame.base.bars
        if len(bars) < 2:
            return {"sample_net_move_points": 0.0, "sample_net_move_pct": 0.0,
                    "sample_drift_sign": 0, "bars_in_sample": len(bars)}
        first, last = float(bars[0].close), float(bars[-1].close)
        move = last - first
        return {
            "sample_net_move_points": round(move, 2),
            "sample_net_move_pct": round(100.0 * move / first, 3) if first else 0.0,
            "sample_drift_sign": 1 if move > 0 else (-1 if move < 0 else 0),
            "sample_first_close": round(first, 2),
            "sample_last_close": round(last, 2),
            "bars_in_sample": len(bars),
        }

    # ---- angle 1b: the split they did not pick --------------------------
    def _challenge_split(self, frame: SymbolFrame, row: Dict[str, Any], owner: str,
                         strategy: Optional[Strategy], record: Dict[str, Any]
                         ) -> List[Challenge]:
        """Re-run on a boundary the owner did not select on.

        OUT_OF_SAMPLE_FAILURE is fatal - it zeroes the finding outright - so the
        bar for filing it is correspondingly high: the edge has to be present in
        the half the owner would have trained on and *gone* in the other, on a
        sample large enough for the disappearance to be a measurement.
        """
        if strategy is None:
            return []
        retest = adversarial_retest(frame, strategy, skip_fraction=RETEST_SPLIT)
        record["measurements"].append({"adversarial_retest": retest})
        if "error" in retest:
            record["not_measured"].append(
                f"adversarial retest: {retest['error']}")
            return []
        first, second = retest["first_half"], retest["second_half"]
        if first["expectancy_r"] <= 0:
            return []                       # never worked in the first half either
        if second["expectancy_r"] > 0:
            return []                       # holds on both sides of the cut
        if second["trades"] < ONE_SIDED_MIN_SAMPLE:
            record["not_measured"].append(
                f"adversarial retest: only {second['trades']} trades in the "
                f"second half - too few to call the edge gone rather than absent")
            return []
        measurement = dict(retest)
        measurement["owner_anchor_fraction"] = 0.30
        measurement["split_fraction"] = RETEST_SPLIT
        return [Challenge(
            challenger=self.id, target_owner=owner,
            target_strategy_id=str(row.get("strategy_id")), symbol=frame.symbol,
            kind=ChallengeKind.OUT_OF_SAMPLE_FAILURE,
            claim=(
                f"Re-run at a {RETEST_SPLIT:.0%} cut - a boundary no owner "
                f"selected on, the anchored walk-forward trains to 30% - this "
                f"edge is {first['expectancy_r']:+.3f}R over {first['trades']} "
                f"trades in the first half and {second['expectancy_r']:+.3f}R "
                f"over {second['trades']} in the second, a consistency ratio of "
                f"{retest['consistency_ratio']:.2f}. An edge present only on the "
                f"side of the cut its author chose was fitted to that side. It "
                f"would be answered by a positive expectancy in the second half "
                f"at any split, this one included."),
            measurement=measurement)]

    # ---- angle 2: regime concentration -----------------------------------
    def _challenge_regime(self, frame: SymbolFrame, row: Dict[str, Any], owner: str,
                          trades: Optional[Sequence[Trade]], re_ran: bool,
                          record: Dict[str, Any]) -> List[Challenge]:
        """Does the whole profit sit in one thinly-sampled regime?

        "Could not re-run it" and "re-ran it and it never trades" are different
        answers and are reported as such. Collapsing them would let a finding
        whose strategy takes no trades at all on the full history read as a
        finding this desk merely failed to reach.
        """
        if re_ran and not trades:
            record["not_measured"].append(
                f"regime concentration: the strategy was re-run over all "
                f"{len(frame.base):,} bars and took no trades at all, so there "
                f"is no per-regime split to measure. Worth noting against a "
                f"finding claiming an edge, but not filed as a challenge: the "
                f"owner may have measured it on a window this run did not use.")
            return []
        if not trades:
            record["not_measured"].append(
                "regime concentration: the strategy could not be re-run, so the "
                "per-regime split could not be measured")
            return []
        conc = regime_concentration(trades)
        record["measurements"].append({"regime_concentration": conc})
        if "error" in conc or not conc.get("concentrated"):
            return []
        return [Challenge(
            challenger=self.id, target_owner=owner,
            target_strategy_id=str(row.get("strategy_id")), symbol=frame.symbol,
            kind=ChallengeKind.REGIME_ARTEFACT,
            claim=(
                f"The edge is one regime, not a rule: "
                f"{conc['dominant_share']:.0%} of this strategy's "
                f"{conc['total_r']:+.2f}R comes from the "
                f"{conc['dominant_regime']} regime on "
                f"{conc['dominant_sample']} trades, out of "
                f"{sum(conc.get('counts', {}).values())} in total. A breakout "
                f"rule that holds its entire profit in expansion is a "
                f"description of when expansion happened in this sample. It "
                f"would be answered by a positive expectancy outside the "
                f"dominant regime on a sample worth quoting."),
            measurement=conc)]

    # ---- angle 3: redundancy --------------------------------------------
    def _challenge_overlap(self, frame: SymbolFrame, row: Dict[str, Any], owner: str,
                           fingerprint: Optional[Sequence[Tuple[int, int]]],
                           mine: Dict[str, Dict[str, Any]],
                           record: Dict[str, Any]) -> List[Challenge]:
        """Does their independent edge take this desk's trades?"""
        if not mine:
            record["not_measured"].append(
                "redundancy: this desk has published no findings for this "
                "symbol, so there is nothing to overlap against")
            return []
        if not fingerprint:
            record["not_measured"].append(
                f"redundancy: no usable trade fingerprint "
                f"({record.get('fingerprint_source', 'unavailable')}), so "
                f"overlap could not be measured")
            return []
        best_id, best_overlap, best_len = "", 0.0, 0
        for sid, mine_row in sorted(mine.items()):
            ours = self._row_fingerprint(mine_row)
            if not ours:
                continue
            overlap = trade_overlap(list(fingerprint), ours)
            if overlap > best_overlap:
                best_id, best_overlap, best_len = sid, overlap, len(ours)
        measurement = {
            "overlap": round(best_overlap, 4),
            "threshold": REDUNDANCY_THRESHOLD,
            "their_trades": len(fingerprint),
            "nearest_own_finding": best_id,
            "own_trades": best_len,
            "tolerance_bars": 3,
            "their_fingerprint_source": record.get("fingerprint_source",
                                                   "unavailable"),
        }
        record["measurements"].append({"trade_overlap": measurement})
        if not best_id or best_overlap < REDUNDANCY_THRESHOLD:
            return []
        return [Challenge(
            challenger=self.id, target_owner=owner,
            target_strategy_id=str(row.get("strategy_id")), symbol=frame.symbol,
            kind=ChallengeKind.REDUNDANT,
            claim=(
                f"Not an independent edge: {best_overlap:.0%} of this "
                f"strategy's {len(fingerprint)} entries match this desk's "
                f"{best_id} ({best_len} trades) in direction and within three "
                f"bars. Whatever the two rule sets are called, they are taking "
                f"the same trades, and pooling both would read as corroboration "
                f"while double-counting one edge. It would be answered by the "
                f"non-overlapping subset carrying an edge of its own."),
            measurement=measurement)]

    # ==================================================================
    # rebut
    # ==================================================================
    def _rebut_task(self, task: Task) -> AgentResult:
        """Answer every challenge filed against this desk, with numbers or not at all.

        Three verdicts are available and only one of them is cheap. CONCEDED
        costs the finding and is the right answer whenever the objection
        reproduces. PARTIAL narrows the claim. REBUTTED requires a
        counter-measurement that actually contradicts theirs - without one the
        pooler downgrades it to UNANSWERED, so an assertion is strictly worse
        than a concession and there is no reason to reach for one.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        mine = self._own_findings(symbol)

        incoming: List[Dict[str, Any]] = []
        rivals: Dict[str, Any] = {}
        for opponent in self.opponents:
            doc = self._read_rival(opponent, "challenges")
            if doc is None:
                rivals[opponent.value] = {
                    "readable": False,
                    "note": ("no challenges artefact published yet - "
                             "self.read_from returned None"),
                }
                continue
            rows = self._challenge_rows(doc, symbol)
            against_me = [r for r in rows if str(r.get("target_owner")) == self.id]
            rivals[opponent.value] = {"readable": True,
                                      "challenges_published": len(rows),
                                      "against_this_desk": len(against_me)}
            incoming.extend(against_me)

        rebuttals = [self._answer(frame, row, mine) for row in incoming]
        counts: Dict[str, int] = {}
        for r in rebuttals:
            counts[r.verdict.value] = counts.get(r.verdict.value, 0) + 1

        section = {
            "generated_et": et_stamp(),
            "symbol": symbol,
            "responder": self.id,
            "source": "deterministic",
            "opponents": rivals,
            "challenges_received": len(incoming),
            "verdicts": counts,
            "rule": (
                "A REBUTTED verdict is filed only where an independent re-run "
                "contradicts the challenger's measurement. Where it reproduces "
                "theirs the verdict is CONCEDED; where the edge survives but "
                "smaller than claimed it is PARTIAL; where no measurement was "
                "possible it is UNANSWERED and says why. Conceding a "
                "well-measured objection is this protocol working."),
            "own_findings": sorted(mine),
            "rebuttals": [r.to_dict() for r in rebuttals],
        }
        path = self._merge(
            "rebuttals", symbol, section,
            f"{symbol}: {len(rebuttals)} rebuttal(s) filed")

        unreadable = [k for k, v in rivals.items() if not v.get("readable")]
        summary = self._rebut_summary(symbol, rebuttals, counts, rivals, unreadable)
        self.log(summary)
        return AgentResult(
            ok=True, summary=summary,
            payload={"symbol": symbol, "responder": self.id,
                     "challenges_received": len(incoming),
                     "verdicts": counts,
                     "rebuttals": [r.to_dict() for r in rebuttals],
                     "opponents": rivals,
                     "opponents_without_challenges": unreadable},
            artefacts=[path])

    def _rebut_summary(self, symbol: str, rebuttals: Sequence[Rebuttal],
                       counts: Dict[str, int], rivals: Dict[str, Any],
                       unreadable: List[str]) -> str:
        if not rivals or all(not v.get("readable") for v in rivals.values()):
            missing = ", ".join(unreadable) or "both rivals"
            return (f"{symbol}: nothing to answer - {missing} published no "
                    f"challenges artefact (read_from returned None). 0 "
                    f"rebuttals filed.")
        if not rebuttals:
            return (f"{symbol}: rivals published challenges but none were "
                    f"aimed at {self.id}; 0 rebuttals filed.")
        detail = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        measured = sum(1 for r in rebuttals if r.is_substantiated)
        return (f"{symbol}: answered {len(rebuttals)} challenge(s) - {detail}. "
                f"{measured} carry a counter-measurement. Conceded objections "
                f"are conceded on the numbers, not argued around.")

    # ---- one challenge, answered ----------------------------------------
    def _answer(self, frame: SymbolFrame, row: Dict[str, Any],
                mine: Dict[str, Dict[str, Any]]) -> Rebuttal:
        challenger = str(row.get("challenger") or "unknown")
        sid = str(row.get("target_strategy_id"))
        finding = mine.get(sid)
        if finding is None:
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.CONCEDED,
                argument=(f"{self.id} publishes no finding for {sid} on "
                          f"{frame.symbol}, so there is nothing here to defend "
                          f"and the objection is accepted as moot."),
                measurement={"own_published_findings": sorted(mine),
                             "target_present": False})
        try:
            kind = ChallengeKind(str(row.get("kind")))
        except ValueError:
            return self._unanswered(
                challenger, sid,
                f"the challenge kind {row.get('kind')!r} is not one this "
                f"protocol defines, so no matching re-measurement exists")
        handlers = {
            ChallengeKind.COST_FRAGILE: self._rebut_cost,
            ChallengeKind.OUT_OF_SAMPLE_FAILURE: self._rebut_out_of_sample,
            ChallengeKind.SAMPLE_TOO_SMALL: self._rebut_sample,
            ChallengeKind.DATA_MINING: self._rebut_data_mining,
            ChallengeKind.REGIME_ARTEFACT: self._rebut_regime,
            ChallengeKind.REDUNDANT: self._rebut_redundant,
        }
        return handlers[kind](frame, row, finding, challenger, sid)

    def _unanswered(self, challenger: str, sid: str, why: str) -> Rebuttal:
        """No measurement was possible, and saying so beats asserting."""
        return Rebuttal(
            responder=self.id, challenger=challenger, target_strategy_id=sid,
            verdict=Verdict.UNANSWERED,
            argument=(f"No counter-measurement was possible: {why}. The "
                      f"challenge therefore stands unanswered rather than being "
                      f"denied - an unmeasured denial answers nothing."),
            measurement={})

    def _rebut_cost(self, frame: SymbolFrame, row: Dict[str, Any],
                    finding: Dict[str, Any], challenger: str, sid: str) -> Rebuttal:
        """Cost fragility - the objection this desk expects and usually concedes."""
        strategy = self._resolve_strategy(frame.symbol, sid, self.role, finding)
        if strategy is None:
            return self._unanswered(
                challenger, sid,
                "the strategy object could not be resolved to re-run the "
                "doubled-slippage test")
        stress = cost_stress(frame, strategy)
        prior = finding.get("cost_stress") or {}
        theirs = row.get("measurement") if isinstance(row.get("measurement"), dict) else {}
        measurement = {
            "independent_rerun": stress,
            "pre_publication_cost_stress": prior,
            "challenger_measurement": theirs,
            "reproduces_challenger": (
                bool(theirs.get("survives")) == bool(stress.get("survives"))
                if "survives" in theirs else None),
        }
        retained = float(stress.get("retained_fraction") or 0.0)
        if not stress.get("survives"):
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.CONCEDED,
                argument=(
                    f"Conceded. Re-running cost_stress independently gives "
                    f"{stress.get('stressed_expectancy_r', 0.0):+.4f}R under "
                    f"doubled slippage against a "
                    f"{stress.get('baseline_expectancy_r', 0.0):+.4f}R baseline, "
                    f"which reproduces the objection. A fade runs a tight target "
                    f"against a wide adverse excursion, so it is the first thing "
                    f"the cost model kills, and an edge that only exists at "
                    f"optimistic fills is not an edge. The finding is withdrawn."),
                measurement=measurement)
        if retained < COST_RETENTION_PARTIAL:
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.PARTIAL,
                argument=(
                    f"Accepted in part. The edge does survive doubled slippage - "
                    f"{stress.get('stressed_expectancy_r', 0.0):+.4f}R, still "
                    f"positive - but it retains only {retained:.0%} of its "
                    f"{stress.get('baseline_expectancy_r', 0.0):+.4f}R baseline, "
                    f"so the claim is narrowed to that smaller figure. Anything "
                    f"downstream should size it on the stressed number, not the "
                    f"headline."),
                measurement=measurement)
        return Rebuttal(
            responder=self.id, challenger=challenger, target_strategy_id=sid,
            verdict=Verdict.REBUTTED,
            argument=(
                f"An independent re-run does not reproduce the objection: under "
                f"doubled slippage this holds "
                f"{stress.get('stressed_expectancy_r', 0.0):+.4f}R, "
                f"{retained:.0%} of its "
                f"{stress.get('baseline_expectancy_r', 0.0):+.4f}R baseline. "
                f"The same test was run before publication and is recorded in "
                f"the findings artefact, so this is a repeat measurement rather "
                f"than one produced to answer the challenge."),
            measurement=measurement)

    def _rebut_out_of_sample(self, frame: SymbolFrame, row: Dict[str, Any],
                             finding: Dict[str, Any], challenger: str,
                             sid: str) -> Rebuttal:
        """Re-run their split and a second one. Two failures is a concession."""
        strategy = self._resolve_strategy(frame.symbol, sid, self.role, finding)
        if strategy is None:
            return self._unanswered(
                challenger, sid,
                "the strategy object could not be resolved to re-run the split")
        theirs = row.get("measurement") if isinstance(row.get("measurement"), dict) else {}
        their_fraction = self._their_split(theirs, len(frame.base))
        at_theirs = adversarial_retest(frame, strategy, skip_fraction=their_fraction)
        second_fraction = 0.35 if abs(their_fraction - 0.35) > 0.05 else 0.65
        at_second = adversarial_retest(frame, strategy, skip_fraction=second_fraction)
        measurement = {
            f"split_{their_fraction:.2f}": at_theirs,
            f"split_{second_fraction:.2f}": at_second,
            "challenger_measurement": theirs,
        }
        # A re-test that could not run is not a re-test that failed. Reading a
        # missing result as "the edge is absent" would concede the finding on
        # the strength of a measurement nobody took.
        errors = [r["error"] for r in (at_theirs, at_second) if "error" in r]
        if errors:
            return self._unanswered(
                challenger, sid,
                f"the split re-test could not be run ({'; '.join(errors)})")
        holds_theirs = bool(at_theirs.get("both_halves_positive"))
        holds_second = bool(at_second.get("both_halves_positive"))
        if not holds_theirs and not holds_second:
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.CONCEDED,
                argument=(
                    f"Conceded. The edge fails at the challenger's "
                    f"{their_fraction:.0%} cut and independently at a "
                    f"{second_fraction:.0%} cut this desk chose, so it is not a "
                    f"property of where the line was drawn. An edge that does "
                    f"not survive a split its author did not pick was fitted to "
                    f"the one they did."),
                measurement=measurement)
        if holds_theirs != holds_second:
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.PARTIAL,
                argument=(
                    f"Accepted in part. The edge holds on both halves at one cut "
                    f"({their_fraction:.0%}: {holds_theirs}; "
                    f"{second_fraction:.0%}: {holds_second}) and not the other, "
                    f"so it is split-sensitive and the claim is narrowed "
                    f"accordingly rather than defended whole."),
                measurement=measurement)
        return Rebuttal(
            responder=self.id, challenger=challenger, target_strategy_id=sid,
            verdict=Verdict.REBUTTED,
            argument=(
                f"Both halves are positive at the challenger's "
                f"{their_fraction:.0%} cut and at an independent "
                f"{second_fraction:.0%} cut, so the re-test does not reproduce "
                f"the failure claimed."),
            measurement=measurement)

    @staticmethod
    def _their_split(measurement: Dict[str, Any], bars: int) -> float:
        """The fraction the challenger cut at, recovered from their numbers."""
        fraction = measurement.get("split_fraction")
        if isinstance(fraction, (int, float)) and 0.05 < float(fraction) < 0.95:
            return float(fraction)
        split_bar = measurement.get("split_bar")
        if isinstance(split_bar, (int, float)) and bars:
            candidate = float(split_bar) / float(bars)
            if 0.05 < candidate < 0.95:
                return candidate
        return RETEST_SPLIT

    def _rebut_sample(self, frame: SymbolFrame, row: Dict[str, Any],
                      finding: Dict[str, Any], challenger: str, sid: str) -> Rebuttal:
        """Sample size is a fact about the artefact, not a matter of opinion."""
        m = finding.get("metrics") if isinstance(finding.get("metrics"), dict) else {}
        trades = int(m.get("trades") or 0)
        measurement = {
            "trades": trades, "floor": MIN_TRADES_FOR_RANK,
            "t_statistic": m.get("t_statistic"),
            "trades_per_day": m.get("trades_per_day"),
            "window": (finding.get("metrics") or {}).get("trading_days"),
        }
        if trades < MIN_TRADES_FOR_RANK:
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.CONCEDED,
                argument=(f"Conceded on the count: {trades} trades is below the "
                          f"{MIN_TRADES_FOR_RANK}-trade floor this repository "
                          f"uses for ranking, and no statistic computed on it "
                          f"separates the edge from noise."),
                measurement=measurement)
        return Rebuttal(
            responder=self.id, challenger=challenger, target_strategy_id=sid,
            verdict=Verdict.REBUTTED,
            argument=(f"The sample is {trades} trades, at or above the "
                      f"{MIN_TRADES_FOR_RANK}-trade floor, with a t-statistic of "
                      f"{float(m.get('t_statistic') or 0.0):.2f}. That is not a "
                      f"large sample, but it is not the thin one the challenge "
                      f"describes."),
            measurement=measurement)

    def _rebut_data_mining(self, frame: SymbolFrame, row: Dict[str, Any],
                           finding: Dict[str, Any], challenger: str,
                           sid: str) -> Rebuttal:
        """Recompute the deflation against this desk's true search count."""
        m = finding.get("metrics") if isinstance(finding.get("metrics"), dict) else {}
        trials = int(finding.get("trials_searched") or 1)
        metrics = Metrics(trades=int(m.get("trades") or 0),
                          std_r=float(m.get("std_r") or 0.0),
                          t_statistic=float(m.get("t_statistic") or 0.0))
        deflated = deflated_expectancy(metrics, trials)
        measurement = {
            "trials_searched": trials, "trades": metrics.trades,
            "t_statistic": round(metrics.t_statistic, 4),
            "std_r": round(metrics.std_r, 4),
            "deflated_expectancy_r": round(deflated, 6),
            "raw_expectancy_r": m.get("expectancy_r"),
        }
        if deflated <= 0:
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.CONCEDED,
                argument=(f"Conceded. Deflated for the {trials} combinations "
                          f"this desk actually searched, the expectancy is "
                          f"{deflated:+.6f}R - the observed t-statistic of "
                          f"{metrics.t_statistic:.2f} is inside what searching "
                          f"{trials} combinations buys for free."),
                measurement=measurement)
        return Rebuttal(
            responder=self.id, challenger=challenger, target_strategy_id=sid,
            verdict=Verdict.REBUTTED,
            argument=(f"Deflated against the true search count of {trials} "
                      f"combinations - the family universe, not a subset - the "
                      f"expectancy is still {deflated:+.6f}R per trade over "
                      f"{metrics.trades} trades."),
            measurement=measurement)

    def _rebut_regime(self, frame: SymbolFrame, row: Dict[str, Any],
                      finding: Dict[str, Any], challenger: str, sid: str) -> Rebuttal:
        """Answer the objection actually raised - concentration or one-sidedness."""
        theirs = row.get("measurement") if isinstance(row.get("measurement"), dict) else {}
        if "profitable_side" in theirs:
            return self._rebut_one_sided(frame, theirs, finding, challenger, sid)
        strategy = self._resolve_strategy(frame.symbol, sid, self.role, finding)
        if strategy is None:
            return self._unanswered(
                challenger, sid,
                "the strategy object could not be resolved to recompute the "
                "per-regime split")
        trades = list(BacktestEngine(frame).run(strategy).trades)
        conc = regime_concentration(trades)
        measurement = {"regime_concentration": conc, "challenger_measurement": theirs}
        if conc.get("concentrated"):
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.CONCEDED,
                argument=(f"Conceded. Recomputed independently, "
                          f"{conc['dominant_share']:.0%} of the profit sits in "
                          f"the {conc['dominant_regime']} regime on "
                          f"{conc['dominant_sample']} trades. That is a "
                          f"description of a slice of this sample, not a rule."),
                measurement=measurement)
        if "error" in conc:
            return self._unanswered(challenger, sid,
                                    f"regime split unavailable: {conc['error']}")
        return Rebuttal(
            responder=self.id, challenger=challenger, target_strategy_id=sid,
            verdict=Verdict.REBUTTED,
            argument=(f"Recomputed, the profit is not concentrated: the largest "
                      f"regime contributes "
                      f"{float(conc.get('dominant_share') or 0.0):.0%} of "
                      f"{conc.get('total_r')}R across "
                      f"{conc.get('counts')} trades by regime, below the 80% on "
                      f"a thin sample that the concentration test flags."),
            measurement=measurement)

    def _rebut_one_sided(self, frame: SymbolFrame, theirs: Dict[str, Any],
                         finding: Dict[str, Any], challenger: str,
                         sid: str) -> Rebuttal:
        """A directional-beta objection is answered on the long/short split."""
        m = finding.get("metrics") if isinstance(finding.get("metrics"), dict) else {}
        le = float(m.get("long_expectancy_r") or 0.0)
        se = float(m.get("short_expectancy_r") or 0.0)
        longs, shorts = int(m.get("long_trades") or 0), int(m.get("short_trades") or 0)
        measurement = {
            "long_trades": longs, "short_trades": shorts,
            "long_expectancy_r": round(le, 4), "short_expectancy_r": round(se, 4),
            "both_sides_positive": le > 0 and se > 0,
            "challenger_measurement": theirs,
            **self._sample_drift(frame),
        }
        if le > 0 and se > 0 and min(longs, shorts) >= ONE_SIDED_MIN_SAMPLE:
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.REBUTTED,
                argument=(f"This finding is not one-sided: {le:+.3f}R over "
                          f"{longs} long trades and {se:+.3f}R over {shorts} "
                          f"short trades, both positive. It cannot be exposure "
                          f"to the sample's drift, which only ran one way."),
                measurement=measurement)
        return Rebuttal(
            responder=self.id, challenger=challenger, target_strategy_id=sid,
            verdict=Verdict.CONCEDED,
            argument=(f"Conceded. The split is {le:+.3f}R over {longs} long and "
                      f"{se:+.3f}R over {shorts} short trades, so the edge does "
                      f"rest on one side, and this sample drifted "
                      f"{measurement['sample_net_move_points']:+.1f} points. "
                      f"One-sided over a drifting sample cannot be separated "
                      f"from exposure to it on this data."),
            measurement=measurement)

    def _rebut_redundant(self, frame: SymbolFrame, row: Dict[str, Any],
                         finding: Dict[str, Any], challenger: str,
                         sid: str) -> Rebuttal:
        """Recompute the overlap against the counterpart they named."""
        theirs = row.get("measurement") if isinstance(row.get("measurement"), dict) else {}
        ours = self._row_fingerprint(finding)
        if not ours:
            return self._unanswered(
                challenger, sid,
                "this desk's own trade fingerprint is missing from its findings "
                "artefact, so the overlap could not be recomputed")
        counterpart = str(theirs.get("nearest_own_finding")
                          or theirs.get("counterpart_strategy_id") or "")
        other = self._rival_fingerprint(challenger, counterpart, frame.symbol)
        if not other:
            return self._unanswered(
                challenger, sid,
                f"the counterpart fingerprint ({counterpart or 'unnamed'}) is "
                f"not published in the challenger's findings artefact, so the "
                f"overlap claim could not be independently recomputed")
        overlap = trade_overlap(ours, other)
        measurement = {
            "recomputed_overlap": round(overlap, 4),
            "threshold": REDUNDANCY_THRESHOLD,
            "own_trades": len(ours), "counterpart_trades": len(other),
            "counterpart_strategy_id": counterpart,
            "challenger_measurement": theirs,
        }
        if overlap >= REDUNDANCY_THRESHOLD:
            return Rebuttal(
                responder=self.id, challenger=challenger, target_strategy_id=sid,
                verdict=Verdict.CONCEDED,
                argument=(f"Conceded. Recomputed independently the overlap is "
                          f"{overlap:.0%} against {counterpart}, above the "
                          f"{REDUNDANCY_THRESHOLD:.0%} threshold. These are the "
                          f"same trades under two names and should be counted "
                          f"once."),
                measurement=measurement)
        return Rebuttal(
            responder=self.id, challenger=challenger, target_strategy_id=sid,
            verdict=Verdict.REBUTTED,
            argument=(f"Recomputed, the overlap is {overlap:.0%} against "
                      f"{counterpart} - {len(ours)} trades here against "
                      f"{len(other)} there - below the "
                      f"{REDUNDANCY_THRESHOLD:.0%} threshold at which two rule "
                      f"sets are the same edge."),
            measurement=measurement)

    # ==================================================================
    # Reading the other desks
    # ==================================================================
    def _read_rival(self, owner: Role, artefact: str) -> Optional[Any]:
        """A rival's artefact, or None - which is a normal state, not an error.

        The three specialists are written and run in parallel, so a rival may
        simply not have published yet. That is reported as a missing input; it
        is never reported as the rival having nothing to say, and it never
        raises.
        """
        try:
            doc = self.read_from(owner, artefact)
        except (PermissionError, OSError) as exc:            # noqa: BLE001
            self.log(f"could not read {owner.value}/{artefact}: "
                     f"{type(exc).__name__}: {exc}")
            return None
        if doc is None:
            self.log(f"{owner.value} has published no {artefact} artefact yet")
        return doc

    #: Container keys whose contents are explicitly *not* claims. All three
    #: specialists publish the candidates they declined alongside the ones they
    #: stand behind - which is good practice, and exactly why a challenge must
    #: never be filed against one. Objecting to a finding its owner already
    #: withheld is noise that the pooler discards and the scorecard remembers.
    NON_CLAIM_CONTAINERS: Tuple[str, ...] = (
        "withheld", "near_misses", "rejected", "discarded", "excluded",
        "not_published", "disqualified", "audit")

    #: Row-level fields that mark a row as a candidate that was not claimed.
    #: None of these appear in ``Finding.to_dict()``.
    NON_CLAIM_FIELDS: Tuple[str, ...] = (
        "withheld_because", "withheld_reason", "reason", "is_slice",
        "disqualified_by")

    @staticmethod
    def _rows_matching(doc: Any, symbol: str, required: str,
                       markers: Sequence[str],
                       deny_fields: Sequence[str] = (),
                       skip_containers: Sequence[str] = ()
                       ) -> List[Dict[str, Any]]:
        """Every row of one shape in an artefact, wherever the owner nested it.

        The three specialists are written in parallel by three different people
        and only the debate protocol's field names are actually agreed between
        them. A recursive walk keyed on those names finds the rows whatever
        document shape the owner chose, instead of guessing at a layout nobody
        signed up to and reporting "no findings" when the guess is wrong.

        Rows come back in walk order and are *not* de-duplicated here, because
        what makes two rows the same differs by row type: one finding per
        strategy, but a challenger may legitimately file two different kinds of
        objection against one strategy, and collapsing on the strategy id would
        silently discard the second.

        ``markers`` has to be a field only the row type in question carries.
        Matching on something generic like ``metrics`` pulls in every nested
        robustness block and every candidate the owner listed as withheld,
        and a challenge filed against a claim nobody made is worse than no
        challenge at all. ``deny_fields`` and ``skip_containers`` exclude the
        not-claimed rows from the other direction.
        """
        out: List[Dict[str, Any]] = []
        target = symbol.upper()
        skip = {s.lower() for s in skip_containers}

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                key = node.get(required)
                if (isinstance(key, str) and key
                        and any(m in node for m in markers)
                        and not any(d in node for d in deny_fields)):
                    if str(node.get("symbol") or target).upper() == target:
                        out.append(node)
                    return          # a matched row's sub-dicts are its own detail
                for name, value in node.items():
                    if str(name).lower() in skip:
                        continue
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(doc)
        return out

    @staticmethod
    def _unique_by(rows: Sequence[Dict[str, Any]],
                   key: Any) -> List[Dict[str, Any]]:
        """First row wins, order preserved - so a re-read is reproducible."""
        seen: Dict[Any, Dict[str, Any]] = {}
        for row in rows:
            seen.setdefault(key(row), row)
        return list(seen.values())

    def _finding_rows(self, doc: Any, symbol: str) -> List[Dict[str, Any]]:
        """Rows their owner actually stands behind, in any document shape.

        ``owner``, ``claim`` and ``base_score`` are carried by
        ``Finding.to_dict()`` and by nothing else in these artefacts, which is
        what keeps a nested ``RobustnessReport`` or a withheld candidate from
        being read as a claim.
        """
        rows = self._rows_matching(
            doc, symbol, "strategy_id", ("owner", "claim", "base_score"),
            deny_fields=self.NON_CLAIM_FIELDS,
            skip_containers=self.NON_CLAIM_CONTAINERS)
        return self._unique_by(rows, lambda r: str(r.get("strategy_id")))

    def _challenge_rows(self, doc: Any, symbol: str) -> List[Dict[str, Any]]:
        """Challenges only - ``Rebuttal.to_dict()`` also carries
        ``target_strategy_id``, so an owner that publishes both in one document
        would otherwise have its answers read as fresh objections. ``kind`` and
        ``target_owner`` belong to a Challenge; ``responder`` and ``argument``
        belong to a Rebuttal and disqualify the row.
        """
        rows = self._rows_matching(
            doc, symbol, "target_strategy_id", ("kind", "target_owner"),
            deny_fields=("responder", "argument"),
            skip_containers=("discarded", "rejected", "unsubstantiated",
                             "rebuttals"))
        # One objection per (challenger, target, kind): the same challenge
        # echoed in a summary block and again in the detail block is one
        # challenge, but two kinds against one finding are two.
        return self._unique_by(
            rows, lambda r: (str(r.get("challenger")),
                             str(r.get("target_strategy_id")),
                             str(r.get("kind"))))

    def _own_findings(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """This desk's own published findings, keyed by strategy id."""
        doc = self._artefact("findings")
        section = (doc.get("symbols", {}) or {}).get(symbol)
        rows = (section or {}).get("findings") or []
        return {str(r["strategy_id"]): r for r in rows
                if isinstance(r, dict) and r.get("strategy_id")}

    def _rival_fingerprint(self, owner_id: str, strategy_id: str,
                           symbol: str) -> List[Tuple[int, int]]:
        """A named rival finding's fingerprint, if its owner published one."""
        if not strategy_id:
            return []
        for role in self.opponents:
            if role.value != owner_id:
                continue
            doc = self._read_rival(role, "findings")
            if doc is None:
                return []
            for row in self._finding_rows(doc, symbol):
                if str(row.get("strategy_id")) == strategy_id:
                    return self._row_fingerprint(row)
        return []

    @staticmethod
    def _row_fingerprint(row: Dict[str, Any]) -> List[Tuple[int, int]]:
        """``trade_fingerprint`` out of a JSON row, as pairs.

        ``Finding.to_dict()`` does not carry the fingerprint, so a rival that
        published only ``to_dict()`` output has none to read. That is handled by
        returning nothing and re-running the strategy instead - never by
        fabricating one, which would make redundancy detection agree with itself
        about trades that were never taken.
        """
        raw = row.get("trade_fingerprint")
        if not isinstance(raw, list):
            return []
        out: List[Tuple[int, int]] = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    out.append((int(item[0]), int(item[1])))
                except (TypeError, ValueError):
                    continue
        return out

    def _published_fingerprint(self, row: Dict[str, Any]
                               ) -> Optional[List[Tuple[int, int]]]:
        fingerprint = self._row_fingerprint(row)
        return fingerprint or None

    def _resolve_strategy(self, symbol: str, strategy_id: str, owner: Role,
                          row: Optional[Dict[str, Any]] = None
                          ) -> Optional[Strategy]:
        """The Strategy object behind an id, from the registry or regenerated.

        The shared registry is the normal path - all three specialists hold one
        ``AgentContext``. The fallback matters when they do not: the combinator
        is seeded per symbol and strategy ids are content hashes, so an owner's
        universe can be reproduced from its declared families and search count
        without the owner having to serialise anything. When neither works the
        answer is None and the caller records that a measurement was impossible,
        because the alternative - challenging on a strategy that could not be
        re-run - is exactly the unsubstantiated objection the pooler discards.
        """
        ctx = self.require_context()
        found = ctx.registry.get(strategy_id)
        if found is not None and found.symbol.upper() == symbol.upper():
            return found

        # The owner's *whole* family tuple is tried first, because that is what
        # a specialist passes as ``groups``, and the combinator divides one
        # budget across the groups it is given: regenerating a single declared
        # family at the same budget produces a different set, not a subset of
        # the same one. The single family is tried second for an owner that is
        # not one of the three specialists.
        hinted = int((row or {}).get("trials_searched") or 0)
        budget = max(120, min(int(self._cfg("max_combinations", 4_000)), hinted))
        declared = str((row or {}).get("family") or "").upper()
        attempts: List[Tuple[str, ...]] = []
        for candidate in (families_for(owner) or self.families, (declared,)):
            if candidate and all(candidate) and candidate not in attempts:
                attempts.append(tuple(candidate))

        for families in attempts:
            key = (symbol.upper(), families, budget)
            if key not in self._regenerated:
                frame = ctx.frame(symbol)
                self._regenerated[key] = {
                    s.strategy_id: s
                    for s in generate_strategies(symbol, list(frame.timeframes),
                                                 groups=families,
                                                 max_total=budget)}
                self.log(f"{symbol}: regenerated {len(self._regenerated[key])} "
                         f"{'/'.join(families)} strategies at budget {budget} to "
                         f"resolve ids not in the shared registry")
            hit = self._regenerated[key].get(strategy_id)
            if hit is not None:
                return hit
        return None
