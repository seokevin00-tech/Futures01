"""Research specialist - liquidity, opening range and breakout.

This agent owns the LIQUIDITY, OPENING_RANGE and BREAKOUT families, and it
holds a specific view about where intraday edge comes from: **structure is
temporal before it is technical**. The opening drive, the initial balance, the
lunch lull and the close are not decorations on an indicator - they are the
mechanism. Liquidity pools where participants agree to stop being active, and
breaks matter or fail depending on whether anyone is there to continue them. So
every claim this agent makes is conditioned on time of day and session, and it
is this agent that insists a rival's claim be sliced that way too.

The trap that comes with that view is the whole reason the code below is shaped
the way it is. Slicing by session multiplies hypotheses: five sessions and
thirteen half-hour buckets turn one backtest into twenty-seven chances to find
something. A specialist who conditions on time and then deflates as though it
had tested one hypothesis is manufacturing edge, not finding it. Three rules
follow, and they bind this agent's own findings harder than its challenges:

**The deflation is paid for the search that was actually run.**
``trials_searched`` reported here is the family universe multiplied by the
temporal slices examined, not the universe alone. It is the harshest of the
three specialists' deflations by construction, and that is correct - this one
searched the most places.

**No finding rests on a thin slice.** A slice below ``MIN_SLICE_TRADES`` is
computed, reported with its sample size, and explicitly not claimed. If a
strategy's entire profit sits in one bucket that thin, the finding is withheld
and the reason is published instead.

**Every quoted statistic carries its n.** A session breakdown without sample
sizes is the most persuasive way to lie with a backtest, because the slice that
looks best is almost always the slice with the fewest trades in it.

Against rivals, the sharp angle is the same one: an edge whose profit comes
entirely from one 30-minute bucket, on a thin sample, is a coincidence with
good manners. Nobody else on the desk is looking for that. The other angles -
an adversarial re-test on a split they did not pick, regime concentration, and
trade overlap against this agent's own book - are filed only when the
measurement exists, because an unsubstantiated challenge is discarded by the
pooler and contributes nothing.
"""

from __future__ import annotations

import inspect
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..backtest.engine import Trade, run_portfolio
from ..backtest.metrics import Metrics, compute_metrics, slice_metrics
from ..backtest.robustness import (RobustnessReport, assess_robustness,
                                   deflated_expectancy, parameter_sensitivity)
from ..backtest.walkforward import WalkForwardResult, robust_score, walk_forward
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
from .research import (MIN_TRADES_FOR_RANK, WF_MIN_TRAIN_FRACTION,
                       StrategyResearchAgent)

__all__ = ["ResearchLiquidityAgent"]


#: A temporal slice below this is not evidence. It is reported with its sample
#: size and never claimed. Thirty is the same floor the desk lead uses to rank a
#: whole strategy, and a subset has no business clearing a lower bar than the
#: set it came from.
MIN_SLICE_TRADES = 30

#: Share of a strategy's total R that one slice must carry before "the edge is
#: that slice" is a fair description rather than a rhetorical one.
CONCENTRATION_SHARE = 0.70

#: Below this ratio between the two halves of an adversarial re-test, the edge
#: did not survive a split its owner did not choose.
OOS_FAILURE_RATIO = 0.5

#: Fingerprint overlap at which two findings are one edge under two names.
#: Matches ``pool_findings``' default so a challenge filed here predicts what
#: the desk lead's de-duplication will do.
REDUNDANCY_THRESHOLD = 0.55

#: Trades each side needs inside the common bar range before an overlap figure
#: means anything. Two strategies with four trades each can overlap perfectly
#: by chance.
MIN_OVERLAP_SAMPLE = 10

#: How many strategies survive the leak-free screen into the walk-forward.
#: Lower than the desk lead's, because a specialist sweeps one third of the
#: template space and a wide net over a narrow universe buys nothing.
DEFAULT_WF_CANDIDATES = 24

#: How many of those earn the full robustness suite. Each finalist costs an
#: anchored walk-forward of its own over the whole history plus a Monte Carlo,
#: which is the most expensive thing this agent does - three keeps the canonical
#: research_family call inside a 900-second budget on a shared box. It is a
#: compute budget, not a filter: raising it cannot make an edge appear, it only
#: hands the same suite to more candidates.
DEFAULT_FINALISTS = 3

#: The temporal axes this agent conditions on - and therefore the axes it must
#: pay for in its own deflation.
TEMPORAL_AXES: Tuple[str, ...] = ("session", "time_bucket")


# --------------------------------------------------------------------------
# Temporal measurement - the specialist's own tooling
# --------------------------------------------------------------------------

def _fingerprint(trades: Sequence[Trade]) -> List[Tuple[int, int]]:
    """``(entry bar index, direction sign)`` per trade, in bar order.

    This is what makes redundancy detection possible: two strategies entering
    the same way within a few bars of each other are taking the same trade,
    whatever their rules are called. Bar indices are absolute within the frame,
    so fingerprints from specialists who swept different windows still line up
    on the bars they share.
    """
    return sorted((int(t.entry_index), int(t.direction.sign)) for t in trades)


def _bucket_of(trade: Trade, key: str) -> str:
    """The trade's label on one slice axis. ``session``, ``time_bucket`` and
    ``regime`` are all plain attributes of a :class:`Trade`."""
    return str(getattr(trade, key, ""))


def _slice_profile(trades: Sequence[Trade], key: str) -> Dict[str, Any]:
    """Per-bucket contribution along one axis, every sample size included.

    Two deliberate refusals here. Shares are computed only when the strategy
    made money overall - against a negative total, "this bucket is 80% of the
    profit" is arithmetic rather than evidence. And buckets are split into
    reportable and thin at :data:`MIN_SLICE_TRADES` rather than being ranked
    together, because sorting slices by expectancy puts the smallest sample on
    top essentially every time.
    """
    if not trades:
        return {"axis": key, "buckets": {}, "bucket_count": 0, "total_r": 0.0,
                "reportable_buckets": [], "thin_buckets": [],
                "single_bucket_artefact": False,
                "note": "no trades to slice"}

    by_bucket = slice_metrics(trades, key, min_trades=1)
    total = sum(t.net_r for t in trades)
    rows: Dict[str, Dict[str, Any]] = {
        str(name): {
            "trades": m.trades,
            "expectancy_r": round(m.expectancy_r, 4),
            "total_r": round(m.total_r, 4),
            "win_rate": round(m.win_rate, 4),
            "profit_factor": round(m.profit_factor, 3),
            "share_of_total_r": (round(m.total_r / total, 4) if total > 0 else None),
            "reportable": m.trades >= MIN_SLICE_TRADES,
        }
        for name, m in by_bucket.items()}

    out: Dict[str, Any] = {
        "axis": key,
        "buckets": rows,
        "bucket_count": len(rows),
        "total_r": round(total, 4),
        "trades": len(trades),
        "reportable_buckets": sorted(k for k, v in rows.items() if v["reportable"]),
        "thin_buckets": sorted(k for k, v in rows.items() if not v["reportable"]),
        "min_slice_trades": MIN_SLICE_TRADES,
        "note": (f"buckets with fewer than {MIN_SLICE_TRADES} trades are "
                 "reported but never claimed"),
    }

    if total <= 0 or not rows:
        out["single_bucket_artefact"] = False
        out["share_note"] = ("total R is not positive, so contribution shares "
                             "are not meaningful and none were computed")
        return out

    dominant = max(rows, key=lambda k: rows[k]["total_r"])
    top = rows[dominant]
    rest = [t for t in trades if _bucket_of(t, key) != dominant]
    ex = compute_metrics(rest)
    out.update({
        "dominant_bucket": dominant,
        "dominant_share_of_total_r": top["share_of_total_r"],
        "dominant_sample": top["trades"],
        "ex_dominant_trades": ex.trades,
        "ex_dominant_expectancy_r": round(ex.expectancy_r, 4),
        "ex_dominant_total_r": round(ex.total_r, 4),
        # The three-part test: one bucket carries the edge, that bucket is too
        # thin to be evidence, and removing it leaves nothing behind. Any one of
        # the three on its own is unremarkable; together they are a coincidence.
        "single_bucket_artefact": bool(
            (top["share_of_total_r"] or 0.0) >= CONCENTRATION_SHARE
            and top["trades"] < MIN_SLICE_TRADES
            and ex.expectancy_r <= 0),
    })
    return out


def _share_phrase(share: Optional[float]) -> str:
    """Describe one bucket's contribution share readably.

    A share above 1.0 is arithmetically correct and not a bug: when the other
    buckets lose money, the winning bucket contributes more R than the strategy
    netted overall. Printing it as "124% of total R" invites a reader to assume
    a defect, so the case is named instead.
    """
    if share is None:
        return "an unmeasured share of"
    if share > 1.0:
        return (f"more than all of ({share:.2f}x) the net R of - the other "
                f"buckets lose money, so this one carries")
    return f"{share:.0%} of"


def _temporal_verdict(session: Dict[str, Any],
                      bucket: Dict[str, Any]) -> Tuple[bool, str]:
    """Whether a temporal profile disqualifies a claim, and why in one line."""
    for profile in (session, bucket):
        if profile.get("single_bucket_artefact"):
            return False, (
                f"{_share_phrase(profile['dominant_share_of_total_r'])} total R "
                f"comes from the single {profile['axis']} bucket "
                f"'{profile['dominant_bucket']}' on {profile['dominant_sample']} "
                f"trades (below the {MIN_SLICE_TRADES}-trade floor), and "
                f"excluding it the remaining {profile['ex_dominant_trades']} trades "
                f"average {profile['ex_dominant_expectancy_r']:+.3f}R - the edge "
                "is that bucket, and that bucket is too thin to be evidence")
    return True, "no single temporal bucket carries the edge on a thin sample"


def _best_reportable(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The strongest slice that actually clears the sample floor, or None."""
    reportable = [(name, profile["buckets"][name])
                  for name in profile.get("reportable_buckets", ())]
    positive = [(n, r) for n, r in reportable if r["expectancy_r"] > 0]
    if not positive:
        return None
    name, row = max(positive, key=lambda kv: (kv[1]["expectancy_r"], kv[0]))
    return {"axis": profile["axis"], "bucket": name, **row}


def _overlap_measure(mine: Sequence[Tuple[int, int]],
                     theirs: Sequence[Tuple[int, int]]) -> Dict[str, Any]:
    """Fingerprint overlap, raw and restricted to the bars both actually cover.

    The raw Jaccard is diluted whenever two specialists swept different
    windows - trades the other side never had the chance to take count against
    the union. The common-window figure removes that artefact, so a REDUNDANT
    challenge is not defeated simply by the rival having swept more history.
    """
    raw = trade_overlap(list(mine), list(theirs))
    out: Dict[str, Any] = {
        "raw_overlap": round(raw, 4),
        "my_trades": len(mine), "their_trades": len(theirs),
        "threshold": REDUNDANCY_THRESHOLD,
    }
    if not mine or not theirs:
        out.update({"common_window_overlap": 0.0, "comparable": False,
                    "note": "one side published no fingerprint"})
        return out

    low = max(min(i for i, _ in mine), min(i for i, _ in theirs))
    high = min(max(i for i, _ in mine), max(i for i, _ in theirs))
    if high <= low:
        out.update({"common_window_overlap": 0.0, "comparable": False,
                    "note": "the two sweep windows do not overlap"})
        return out

    mine_in = [p for p in mine if low <= p[0] <= high]
    theirs_in = [p for p in theirs if low <= p[0] <= high]
    common = trade_overlap(mine_in, theirs_in)
    out.update({
        "common_bar_range": [low, high],
        "my_trades_in_common_window": len(mine_in),
        "their_trades_in_common_window": len(theirs_in),
        "common_window_overlap": round(common, 4),
        "comparable": (len(mine_in) >= MIN_OVERLAP_SAMPLE
                       and len(theirs_in) >= MIN_OVERLAP_SAMPLE),
        "effective_overlap": round(max(raw, common), 4),
    })
    return out


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------

class ResearchLiquidityAgent(StrategyResearchAgent):
    """Liquidity, opening-range and breakout research, conditioned on session."""

    def __init__(self, fs, bus, *, context=None, config=None, llm=None):
        # Deliberately skipping StrategyResearchAgent.__init__: it pins the
        # role to STRATEGY_RESEARCH, which would give this agent the desk
        # lead's workspace and its task kinds. Everything else on that class -
        # the sweep, persistence, walk-forward and ranking machinery - is
        # inherited and reused unchanged.
        DomainAgent.__init__(self, Role.RESEARCH_LIQUIDITY, fs, bus,
                             context=context, config=config, llm=llm)

    @property
    def families(self) -> Tuple[str, ...]:
        """The strategy families this seat owns, from the roles table.

        Read, never hard-coded: the split between the three specialists is the
        thing that stops a pooled ranking double-counting an edge, and a copy
        of it in this file would be a second source of truth able to drift.
        """
        return families_for(self.role)

    # ==================================================================
    # Entry point
    # ==================================================================
    def handle(self, task: Task) -> AgentResult:
        if task.kind == "research_family":
            return self._research_family(task)
        if task.kind == "challenge":
            return self._challenge_task(task)
        if task.kind == "rebut":
            return self._rebut_task(task)
        raise ValueError(
            f"{self.id} does not implement task kind {task.kind!r}. "
            f"This seat handles research_family, challenge and rebut; the desk "
            f"lead ({Role.STRATEGY_RESEARCH.value}) owns research_strategies, "
            f"backtest, walk_forward, optimise, rank_strategies, robustness "
            f"and pool.")

    # ==================================================================
    # research_family
    # ==================================================================
    def _research_family(self, task: Task) -> AgentResult:
        """Sweep my families, walk-forward the best, publish measured findings."""
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)
        families = self.families

        if len(frame.base) == 0:
            return self._empty(symbol, "no bars available - nothing to research")
        if not families:
            return self._empty(symbol, f"{self.id} owns no families in the roles table")

        max_strategies = self._int(task, "max_strategies",
                                   self._cfg("max_combinations", 4_000))
        days = self._int(task, "days", None)
        folds = self._int(task, "folds", self._cfg("walk_forward_folds", 6))
        n_candidates = self._int(task, "wf_candidates", DEFAULT_WF_CANDIDATES)
        n_finalists = self._int(task, "finalists", DEFAULT_FINALISTS)
        timeframes = self._timeframes(task, frame)

        universe = self._family_universe(ctx, symbol, timeframes, max_strategies)
        if not universe:
            return self._empty(
                symbol, f"no strategies could be constructed for {list(families)}")

        # ---- 1. one pass over the requested window ------------------------
        sweep = self._run_sweep(frame, symbol, universe, days)
        self._persist_sweep(sweep)
        window_start = int(sweep.window.get("start_index") or 0)

        # ---- 2. the family's whole trade population, sliced temporally -----
        # Done before any strategy is singled out, so the picture of when this
        # family trades at all is not conditioned on which member won.
        family_trades = sweep.trades
        family_session = _slice_profile(family_trades, "session")
        family_bucket = _slice_profile(family_trades, "time_bucket")

        # ---- 3. what the search actually cost in hypotheses ---------------
        trials, trials_note = self._deflation_trials(
            len(universe), (family_session, family_bucket))

        # ---- 4. leak-free candidate screen on fold 0's training window ----
        screen_end = max(1, int(len(frame.base) * WF_MIN_TRAIN_FRACTION))
        t0 = time.perf_counter()
        screen = run_portfolio(frame, universe, start=0, end=screen_end)
        screen_scores = {sid: robust_score(compute_metrics(r.trades),
                                           min_trades=MIN_TRADES_FOR_RANK)
                         for sid, r in screen.items()}
        chosen = set(sorted(screen_scores,
                            key=lambda sid: (-screen_scores[sid], sid))[:n_candidates])
        candidates = [s for s in universe if s.strategy_id in chosen]
        screen_seconds = time.perf_counter() - t0
        self.log(f"{symbol}: screened {len(universe)} -> {len(candidates)} on "
                 f"bars 0..{screen_end} ({screen_seconds:.1f}s)")

        # ---- 5. walk-forward the survivors --------------------------------
        t0 = time.perf_counter()
        wf = walk_forward(frame, candidates, folds=folds, top_k=5, anchored=True,
                          progress=lambda k, n: self.log(
                              f"{symbol}: walk-forward fold {k + 1}/{n}"))
        wf_seconds = time.perf_counter() - t0
        self.log(f"{symbol}: {wf.summary()} ({wf_seconds:.1f}s)")

        finalists = self._finalists(wf, candidates, screen_scores, n_finalists)

        # ---- 6. robustness, then findings ---------------------------------
        findings, withheld, reports = self._assess_finalists(
            ctx, frame, symbol, sweep, finalists, wf, folds, trials, window_start)

        section = self._findings_section(
            symbol, sweep, universe, candidates, wf, findings, withheld,
            reports, trials, trials_note, family_session, family_bucket,
            window_start, timings={"screen": round(screen_seconds, 2),
                                   "sweep": round(sweep.seconds, 2),
                                   "persist": round(sweep.persist_seconds, 2),
                                   "walk_forward": round(wf_seconds, 2)})
        section["llm_commentary"] = self._commentary(symbol, section,
                                                     kind="family_findings")
        if section["llm_commentary"]:
            section["source"] = "hybrid"

        path = self.publish(
            "findings", section,
            f"{symbol}: {len(findings)} finding(s) from "
            f"{'/'.join(families)} over {len(universe)} strategies")

        summary = (
            f"{symbol} [{'/'.join(families)}]: {len(universe)} strategies swept over "
            f"{sweep.window['bars']:,} bars ({sweep.window['trading_days']} trading "
            f"days), {len(family_trades):,} trades; {len(candidates)} screened into a "
            f"{folds}-fold walk-forward (efficiency {wf.efficiency:.2f}, combined OOS "
            f"{wf.combined_oos.trades} trades at {wf.combined_oos.expectancy_r:+.3f}R). "
            f"{len(findings)} finding(s) published, {len(withheld)} withheld on the "
            f"temporal-slice floor. Deflated against {trials:,} hypotheses "
            f"({len(universe)} strategies x {trials // max(1, len(universe))} temporal "
            f"slices) - this seat conditions on time of day, so it pays for it.")
        self.log(summary)

        return AgentResult(
            ok=True, summary=summary,
            payload={
                "symbol": symbol,
                "families": list(families),
                "strategies_searched": len(universe),
                "trials_searched": trials,
                "trades": len(family_trades),
                "window": sweep.window,
                "walk_forward_efficiency": round(wf.efficiency, 4),
                "combined_oos": wf.combined_oos.to_dict(),
                "findings": [f.to_dict() for f in findings],
                "withheld": withheld,
                "session_profile": family_session,
                "time_bucket_profile": family_bucket,
                "live_eligible": [f.strategy_id for f in findings if f.live_eligible],
            },
            artefacts=[path])

    # ---- research_family helpers --------------------------------------
    def _family_universe(self, ctx, symbol: str, timeframes: Sequence[int],
                         max_strategies: int) -> List[Strategy]:
        """This symbol's strategies in **my** families only.

        The desk lead's ``_universe`` is expected to grow a ``groups=`` filter;
        if it has one it is used, so this agent tracks that file rather than
        forking it. Until then the same generator is called directly with the
        family filter, and the result is registered the same way, so the shared
        registry ends up identical either way. The final filter is belt and
        braces: sweeping a family another specialist owns would put the same
        edge into a pooled ranking twice.
        """
        families = set(self.families)
        universe: List[Strategy]
        try:
            supports_groups = "groups" in inspect.signature(
                StrategyResearchAgent._universe).parameters
        except (TypeError, ValueError):                    # pragma: no cover
            supports_groups = False

        if supports_groups:
            universe = list(self._universe(ctx, symbol, timeframes, max_strategies,
                                           generate=True, groups=tuple(self.families)))
        else:
            universe = list(generate_strategies(symbol, list(timeframes),
                                                groups=list(self.families),
                                                max_total=max_strategies))
            added = 0
            for s in universe:
                if ctx.registry.get(s.strategy_id) is None:
                    ctx.registry.add(s)
                    added += 1
            self.log(f"{symbol}: generated {len(universe)} strategies in "
                     f"{sorted(families)} ({added} new to the registry)")

        # Anything already registered in my families belongs in the search too,
        # and is never truncated away: trials_searched has to reflect every
        # hypothesis that was actually tested.
        known = {s.strategy_id for s in universe}
        for s in ctx.registry.symbol(symbol).all():
            if s.group in families and s.strategy_id not in known:
                universe.append(s)
                known.add(s.strategy_id)
        return [s for s in universe if s.group in families]

    def _deflation_trials(self, universe_size: int,
                          profiles: Sequence[Dict[str, Any]]) -> Tuple[int, str]:
        """How many hypotheses this seat actually searched.

        A specialist that conditions on time of day did not test ``n``
        strategies, it tested ``n`` strategies in each of the temporal buckets
        it was willing to look at - and the expected maximum of a search grows
        with every one of them. Reporting the universe size alone would deflate
        against a search smaller than the one that was run, which is the exact
        arithmetic that turns a session breakdown into a discovery.

        This produces the harshest ``trials_searched`` of the three specialists
        by construction. That is the correct ordering: this one looked in the
        most places.
        """
        axes = [p for p in profiles if p.get("bucket_count")]
        slices = sum(int(p["bucket_count"]) for p in axes)
        multiplier = 1 + slices
        trials = max(1, universe_size) * multiplier
        detail = ", ".join(f"{p['axis']}={p['bucket_count']}" for p in axes) or "none"
        note = (
            f"{universe_size} strategies x (1 unconditional + {slices} temporal "
            f"slices: {detail}) = {trials:,} hypotheses. Conditioning on time of "
            "day multiplies the search, so the deflation is charged for the "
            "slices as well as the strategies.")
        return trials, note

    def _assess_finalists(self, ctx, frame: SymbolFrame, symbol: str, sweep,
                          finalists: Sequence[Strategy], wf: WalkForwardResult,
                          folds: int, trials: int, window_start: int
                          ) -> Tuple[List[Finding], List[Dict[str, Any]],
                                     List[Dict[str, Any]]]:
        """Robustness-test each finalist and turn the survivors into findings."""
        findings: List[Finding] = []
        withheld: List[Dict[str, Any]] = []
        reports: List[Dict[str, Any]] = []
        risk_per_trade = self._risk_per_trade(ctx)

        for strategy in finalists:
            sid = strategy.strategy_id
            metrics: Metrics = sweep.metrics[sid]
            trades: List[Trade] = list(sweep.results[sid].trades)

            own_wf = walk_forward(frame, [strategy], folds=folds, top_k=1,
                                  anchored=True)
            # Scoped to the window the finding is actually measured on.
            # Perturbing the exits and then measuring the result over a
            # different window would answer a different question than the one
            # the claim makes - and on a reduced sweep it is also the single
            # most expensive call here.
            sens = parameter_sensitivity(frame, strategy, start=window_start)
            report: RobustnessReport = assess_robustness(
                trades, strategy_id=sid, symbol=symbol,
                walk_forward_result=own_wf, sensitivity=sens,
                trials_searched=trials, cost_r=self._cost_r(frame, trades),
                monte_carlo_runs=self._cfg("monte_carlo_runs", 2_000),
                account_risk_per_trade=risk_per_trade,
                starting_equity=self._account_cfg("starting_equity", 50_000.0),
                failure_drawdown=self._account_cfg("max_total_drawdown", 5_000.0))
            self._persist_report(frame, strategy, trades, report, own_wf)
            self.log(f"{symbol}: {report.summary()}")

            session = _slice_profile(trades, "session")
            bucket = _slice_profile(trades, "time_bucket")
            regime = regime_concentration(trades) if trades else {"error": "no trades"}
            ok, temporal_reason = _temporal_verdict(session, bucket)

            reports.append({
                "strategy_id": sid, "group": strategy.group,
                "robustness": report.to_dict(),
                "own_walk_forward": own_wf.to_dict(),
                "sensitivity": sens,
                "session_profile": session,
                "time_bucket_profile": bucket,
                "regime_concentration": regime,
                "temporal_verdict": temporal_reason,
            })

            reason = self._publication_block(metrics, ok, temporal_reason)
            if reason is not None:
                withheld.append({
                    "strategy_id": sid, "group": strategy.group,
                    "trades": metrics.trades,
                    "expectancy_r": round(metrics.expectancy_r, 4),
                    "reason": reason,
                    "session_profile": session,
                    "time_bucket_profile": bucket,
                })
                self.log(f"{symbol}: withholding {sid} - {reason}")
                continue

            findings.append(Finding(
                owner=self.id, symbol=symbol, strategy_id=sid,
                strategy_name=strategy.name, family=strategy.group,
                timeframe=strategy.primary_tf, metrics=metrics,
                robustness_score=report.score,
                walk_forward_efficiency=own_wf.efficiency,
                trials_searched=trials,
                live_eligible=report.live_eligible,
                trade_fingerprint=_fingerprint(trades),
                r_series=[round(t.net_r, 6) for t in trades],
                claim=self._claim(strategy, metrics, report, own_wf, session,
                                  bucket, sweep.window, trials)))
        return findings, withheld, reports

    @staticmethod
    def _publication_block(metrics: Metrics, temporal_ok: bool,
                           temporal_reason: str) -> Optional[str]:
        """Why this finalist must not be published as a finding, or None."""
        if metrics.trades < MIN_TRADES_FOR_RANK:
            return (f"{metrics.trades} trades is below the "
                    f"{MIN_TRADES_FOR_RANK}-trade floor - not distinguishable "
                    "from noise at any slice")
        if metrics.expectancy_r <= 0:
            return (f"expectancy {metrics.expectancy_r:+.4f}R over "
                    f"{metrics.trades} trades is not an edge to claim")
        if not temporal_ok:
            return temporal_reason
        return None

    def _claim(self, strategy: Strategy, metrics: Metrics,
               report: RobustnessReport, own_wf: WalkForwardResult,
               session: Dict[str, Any], bucket: Dict[str, Any],
               window: Dict[str, Any], trials: int) -> str:
        """One sentence per claim, and a sample size on every number in it."""
        oos = own_wf.combined_oos
        best_session = _best_reportable(session)
        best_bucket = _best_reportable(bucket)
        parts = [
            f"{strategy.group} {strategy.primary_tf}m: {metrics.expectancy_r:+.3f}R "
            f"over {metrics.trades} trades "
            f"(profit factor {metrics.profit_factor:.2f}, t={metrics.t_statistic:.2f}) "
            f"across {window.get('trading_days')} trading days",
            f"deflated {report.deflated_expectancy_r:+.4f}R against {trials:,} "
            f"searched hypotheses",
            f"own walk-forward: {oos.expectancy_r:+.3f}R over {oos.trades} "
            f"out-of-sample trades, efficiency {own_wf.efficiency:.2f}",
        ]
        if best_session:
            parts.append(f"strongest reportable session '{best_session['bucket']}' "
                         f"{best_session['expectancy_r']:+.3f}R over "
                         f"{best_session['trades']} trades")
        else:
            parts.append(f"no session slice clears {MIN_SLICE_TRADES} trades with a "
                         "positive edge, so the claim is unconditional only")
        if best_bucket:
            parts.append(f"strongest reportable 30-minute bucket "
                         f"'{best_bucket['bucket']}' {best_bucket['expectancy_r']:+.3f}R "
                         f"over {best_bucket['trades']} trades")
        if session.get("dominant_bucket"):
            parts.append(
                f"time concentration: '{session['dominant_bucket']}' holds "
                f"{session['dominant_share_of_total_r']:.0%} of total R on "
                f"{session['dominant_sample']} trades; excluding it the remaining "
                f"{session['ex_dominant_trades']} trades average "
                f"{session['ex_dominant_expectancy_r']:+.3f}R")
        parts.append("live-eligible" if report.live_eligible
                     else "NOT live-eligible: " + "; ".join(report.reasons[:2]))
        return ". ".join(parts) + "."

    def _findings_section(self, symbol: str, sweep, universe: Sequence[Strategy],
                          candidates: Sequence[Strategy], wf: WalkForwardResult,
                          findings: Sequence[Finding],
                          withheld: Sequence[Dict[str, Any]],
                          reports: Sequence[Dict[str, Any]], trials: int,
                          trials_note: str, session: Dict[str, Any],
                          bucket: Dict[str, Any], window_start: int,
                          timings: Dict[str, float]) -> Dict[str, Any]:
        """The published ``findings`` artefact.

        ``trade_fingerprint`` is written out explicitly alongside
        ``Finding.to_dict()``, which does not carry it. Without the fingerprint
        in the JSON, no rival can measure trade overlap and the desk lead's
        redundancy pass has nothing to work with - two specialists finding the
        same edge would read as corroboration.
        """
        return {
            "generated_et": et_stamp(),
            "owner": self.id,
            "symbol": symbol,
            "source": "deterministic",
            "families": list(self.families),
            "stance": (
                "Intraday structure is temporal before it is technical: the "
                "opening drive, the initial balance, the lunch lull and the "
                "close. Every claim here is conditioned on session and time of "
                "day, and the deflation is charged for that conditioning."),
            "window": sweep.window,
            "window_start_index": window_start,
            "universe_size": len(universe),
            "candidates_screened_to": len(candidates),
            "screen_note": ("candidates were ranked on fold 0's training window "
                            "only, so no out-of-sample bar contributed to the "
                            "pool selection"),
            "selection_integrity": self._selection_integrity(
                window_start, int(sweep.window.get("bars_available") or 0)),
            "trials_searched": trials,
            "trials_note": trials_note,
            "walk_forward": wf.to_dict(),
            "family_session_profile": session,
            "family_time_bucket_profile": bucket,
            "finalist_detail": list(reports),
            "findings": [
                {**f.to_dict(),
                 # Published explicitly: Finding.to_dict() omits both.
                 "trade_fingerprint": [list(p) for p in f.trade_fingerprint],
                 "r_series": f.r_series}
                for f in findings],
            "withheld": list(withheld),
            "withheld_note": (
                f"a finding resting on a slice below {MIN_SLICE_TRADES} trades is "
                "not published. Slicing by session multiplies hypotheses, so the "
                "specialist that slices hardest needs the harshest floor."),
            "live_eligible": [f.strategy_id for f in findings if f.live_eligible],
            "live_eligibility_note": self._eligibility_summary(findings, withheld),
        }

    @staticmethod
    def _selection_integrity(window_start: int, bars_available: int) -> Dict[str, Any]:
        """How the screen window and the measurement window relate.

        Worth stating because the answer changes with ``days`` and the
        consequence is not obvious. The candidate screen always runs on fold 0's
        training window of the *whole* frame; the findings are measured on the
        requested window. When ``days`` pushes the measurement window past the
        screen, the two are disjoint and selection is fully out of sample - a
        candidate can then legitimately produce zero trades in the window it is
        reported on, which looks like a defect and is not one. When the whole
        history is swept they overlap, which is the desk lead's usual situation
        and the weaker guarantee.
        """
        screen_end = max(1, int(bars_available * WF_MIN_TRAIN_FRACTION))
        disjoint = window_start >= screen_end
        return {
            "screen_window_bars": [0, screen_end],
            "finding_window_bars": [window_start, bars_available],
            "disjoint": disjoint,
            "note": (
                "the candidate screen and the window these findings are measured "
                "on share no bars, so selection is fully out of sample; a "
                "candidate producing zero trades here is a real result, not a bug"
                if disjoint else
                "the candidate screen window lies inside the measured window, so "
                "selection and measurement share bars - the weaker guarantee, and "
                "the reason trials_searched has to carry the whole search"),
        }

    @staticmethod
    def _eligibility_summary(findings: Sequence[Finding],
                             withheld: Sequence[Dict[str, Any]]) -> str:
        eligible = [f.strategy_id for f in findings if f.live_eligible]
        if eligible:
            return (f"{len(eligible)} finding(s) cleared full robustness: "
                    f"{', '.join(eligible)}. Eligibility here is copied from "
                    "RobustnessReport and is still subject to cross-examination.")
        return (f"Nothing cleared live eligibility. {len(findings)} finding(s) "
                f"published for cross-examination and {len(withheld)} withheld on "
                "the temporal-slice floor. On synthetic or short samples this is "
                "the expected outcome and is reported as a result, not smoothed "
                "over.")

    # ==================================================================
    # challenge
    # ==================================================================
    def _challenge_task(self, task: Task) -> AgentResult:
        """Cross-examine both rivals - with a measurement behind every objection."""
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)

        mine, _ = self._read_findings(self.role, symbol)
        my_prints = {row["strategy_id"]: self._prints(row)
                     for row in mine if row.get("strategy_id")}

        challenges: List[Challenge] = []
        examined: List[Dict[str, Any]] = []
        unavailable: List[str] = []

        for opponent in opponents_of(self.role):
            rows, note = self._read_findings(opponent, symbol)
            if note:
                unavailable.append(note)
                self.log(note)
                continue
            for row in rows:
                record = self._examine(ctx, frame, symbol, opponent, row,
                                       my_prints, challenges)
                examined.append(record)

        section = {
            "generated_et": et_stamp(),
            "owner": self.id,
            "symbol": symbol,
            "source": "deterministic",
            "opponents": [r.value for r in opponents_of(self.role)],
            "angles": {
                "time_of_day": (
                    "slice their trades by session and time_bucket. An edge whose "
                    "profit comes entirely from one 30-minute bucket, on a thin "
                    "sample, is a coincidence with good manners - and nobody else "
                    "on this desk is looking for it"),
                "adversarial_retest": ("re-run on a split its owner did not pick; "
                                       "an edge fitted to one split vanishes in the "
                                       "other"),
                "regime_concentration": ("trend and reversion findings alike: how "
                                         "much of the edge is one regime"),
                "trade_overlap": ("breakouts and momentum entries fire on the same "
                                  "bars often enough that one edge can be counted "
                                  "twice"),
            },
            "findings_examined": len(examined),
            "examinations": examined,
            "challenges": [c.to_dict() for c in challenges],
            "rivals_unavailable": unavailable,
            "discipline_note": (
                "A challenge is filed only where the measurement crosses the "
                "threshold stated in it. Unsubstantiated objections are discarded "
                "by the pooler and contribute nothing, so none are filed."),
        }
        path = self.publish("challenges", section,
                            f"{symbol}: {len(challenges)} measured challenge(s) "
                            f"over {len(examined)} rival finding(s)")

        by_kind: Dict[str, int] = {}
        for c in challenges:
            by_kind[c.kind.value] = by_kind.get(c.kind.value, 0) + 1
        if unavailable and not examined:
            summary = (f"{symbol}: no rival findings to cross-examine - "
                       + "; ".join(unavailable)
                       + ". Nothing was filed; an unmeasured objection is worth "
                         "less than silence.")
        else:
            summary = (
                f"{symbol}: examined {len(examined)} rival finding(s) from "
                f"{len(opponents_of(self.role))} specialist(s) and filed "
                f"{len(challenges)} measured challenge(s)"
                + (f" ({', '.join(f'{k}x{v}' for k, v in sorted(by_kind.items()))})"
                   if by_kind else "")
                + (f". {len(unavailable)} rival(s) unavailable: "
                   + "; ".join(unavailable) if unavailable else "."))
        self.log(summary)

        return AgentResult(
            ok=True, summary=summary,
            payload={"symbol": symbol,
                     "findings_examined": len(examined),
                     "challenges_filed": len(challenges),
                     "by_kind": by_kind,
                     "rivals_unavailable": unavailable,
                     "challenges": [c.to_dict() for c in challenges]},
            artefacts=[path])

    def _examine(self, ctx, frame: SymbolFrame, symbol: str, opponent: Role,
                 row: Dict[str, Any], my_prints: Dict[str, List[Tuple[int, int]]],
                 out: List[Challenge]) -> Dict[str, Any]:
        """Measure one rival finding four ways, and file what the numbers support."""
        sid = str(row.get("strategy_id") or "")
        owner = str(row.get("owner") or opponent.value)
        record: Dict[str, Any] = {"target_owner": owner, "strategy_id": sid,
                                  "family": row.get("family"), "measurements": {}}

        def file(kind: ChallengeKind, claim: str, measurement: Dict[str, Any]) -> None:
            # A specialist cannot challenge itself; is_substantiated would be
            # False and the pooler would discard it anyway.
            if owner == self.id:
                return
            out.append(Challenge(challenger=self.id, target_owner=owner,
                                 target_strategy_id=sid, symbol=symbol,
                                 kind=kind, claim=claim, measurement=measurement))
            record.setdefault("filed", []).append(kind.value)

        # ---- redundancy needs only their published fingerprint ------------
        theirs = self._prints(row)
        if theirs and my_prints:
            best_id, best = "", {}
            for my_id, mine in my_prints.items():
                m = _overlap_measure(mine, theirs)
                if m.get("effective_overlap", m["raw_overlap"]) > \
                        best.get("effective_overlap", best.get("raw_overlap", -1.0)):
                    best_id, best = my_id, m
            if best:
                best["compared_against"] = best_id
                best["compared_against_owner"] = self.id
                # Carried so the rival can recompute the overlap rather than
                # having to take it on trust. A challenge whose measurement
                # cannot be reproduced invites an unmeasured denial, and an
                # unmeasured denial helps nobody.
                best["fingerprint"] = [list(p) for p in my_prints[best_id]]
                record["measurements"]["trade_overlap"] = {
                    k: v for k, v in best.items() if k != "fingerprint"}
                overlap = best.get("effective_overlap", best["raw_overlap"])
                if overlap >= REDUNDANCY_THRESHOLD and best.get("comparable"):
                    file(ChallengeKind.REDUNDANT,
                         f"{sid} takes the same trades as my {best_id}: "
                         f"{overlap:.0%} fingerprint overlap over the "
                         f"{best['my_trades_in_common_window']} / "
                         f"{best['their_trades_in_common_window']} trades in the "
                         f"bars we both swept. Breakout and momentum entries fire "
                         f"on the same bars; pooling both would count one edge "
                         f"twice and read as corroboration.", best)
        elif not theirs:
            record["measurements"]["trade_overlap"] = {
                "error": "the rival's finding carries no trade_fingerprint, so "
                         "overlap cannot be measured. Finding.to_dict() omits it - "
                         "it has to be written into the artefact explicitly."}
        else:
            record["measurements"]["trade_overlap"] = {
                "error": "this seat published no findings of its own to compare "
                         "against, so redundancy could not be measured from here"}

        # ---- everything else needs the strategy object --------------------
        strategy = ctx.registry.get(sid)
        if strategy is None:
            record["measurements"]["retest"] = {
                "error": f"{sid} is not in the shared strategy registry, so it "
                         "cannot be re-run. No challenge is filed on an "
                         "unmeasured suspicion."}
            return record

        trades = list(run_portfolio(frame, [strategy])[sid].trades)
        metrics = compute_metrics(trades)
        record["remeasured"] = {"trades": metrics.trades,
                                "expectancy_r": round(metrics.expectancy_r, 4),
                                "scope": "full frame history, re-run by the "
                                         "challenger rather than taken on trust"}

        # ---- 1. sample floor ---------------------------------------------
        if 0 < metrics.trades < MIN_TRADES_FOR_RANK:
            file(ChallengeKind.SAMPLE_TOO_SMALL,
                 f"{sid} produced {metrics.trades} trades over the full frame - "
                 f"below the {MIN_TRADES_FOR_RANK}-trade floor. At that sample no "
                 f"slice of it distinguishes from noise, and the session breakdown "
                 f"its owner may be quoting is smaller still.",
                 {"remeasured_trades": metrics.trades,
                  "floor": MIN_TRADES_FOR_RANK,
                  "expectancy_r": round(metrics.expectancy_r, 4)})

        # ---- 2. time-of-day artefact - the distinctive angle --------------
        for axis in TEMPORAL_AXES:
            profile = _slice_profile(trades, axis)
            record["measurements"][f"{axis}_profile"] = profile
            if profile.get("single_bucket_artefact"):
                file(ChallengeKind.REGIME_ARTEFACT,
                     f"{sid}'s edge is one {axis} bucket. "
                     f"'{profile['dominant_bucket']}' carries "
                     f"{_share_phrase(profile['dominant_share_of_total_r'])} "
                     f"total R on {profile['dominant_sample']} trades - below the "
                     f"{MIN_SLICE_TRADES}-trade floor - and excluding it the "
                     f"remaining {profile['ex_dominant_trades']} trades average "
                     f"{profile['ex_dominant_expectancy_r']:+.3f}R. That is a "
                     f"description of one part of the day, not a rule.",
                     {"axis": axis,
                      "dominant_bucket": profile["dominant_bucket"],
                      "dominant_share_of_total_r": profile["dominant_share_of_total_r"],
                      "dominant_sample": profile["dominant_sample"],
                      "ex_dominant_trades": profile["ex_dominant_trades"],
                      "ex_dominant_expectancy_r": profile["ex_dominant_expectancy_r"],
                      "buckets": profile["buckets"],
                      "floor": MIN_SLICE_TRADES})

        # ---- 3. regime concentration --------------------------------------
        concentration = regime_concentration(trades)
        record["measurements"]["regime_concentration"] = concentration
        if concentration.get("concentrated"):
            file(ChallengeKind.REGIME_ARTEFACT,
                 f"{sid} earns {concentration['dominant_share']:.0%} of its total R "
                 f"in the {concentration['dominant_regime']} regime alone, on "
                 f"{concentration['dominant_sample']} trades. An edge that lives in "
                 f"one thinly-sampled regime is a description of that period.",
                 concentration)

        # ---- 4. adversarial re-test on a split they did not pick ----------
        split = self._unchosen_split(row)
        retest = adversarial_retest(frame, strategy, skip_fraction=split)
        retest["split_fraction"] = split
        retest["why_this_split"] = (
            f"{split:.2f} is neither adversarial_retest's 0.50 default nor the "
            f"{WF_MIN_TRAIN_FRACTION:.2f} anchor the walk-forward uses, so it is a "
            "division of the history its owner did not select")
        record["measurements"]["adversarial_retest"] = retest
        first = retest.get("first_half", {})
        if ("error" not in retest and first.get("expectancy_r", 0.0) > 0
                and retest.get("consistency_ratio", 0.0) < OOS_FAILURE_RATIO):
            second = retest.get("second_half", {})
            file(ChallengeKind.OUT_OF_SAMPLE_FAILURE,
                 f"{sid} does not survive a split its owner did not choose. "
                 f"On the first {split:.0%} of the history it makes "
                 f"{first['expectancy_r']:+.3f}R over {first['trades']} trades; on "
                 f"the remainder {second.get('expectancy_r', 0.0):+.3f}R over "
                 f"{second.get('trades', 0)} trades - a consistency ratio of "
                 f"{retest['consistency_ratio']:.2f}. An edge that appears in one "
                 f"half and not the other was fitted to the first.",
                 retest)
        return record

    @staticmethod
    def _unchosen_split(row: Dict[str, Any]) -> float:
        """A split fraction the finding's owner demonstrably did not use.

        Deterministic, so two runs of the same debate file the same challenge.
        """
        declared = {0.5, float(WF_MIN_TRAIN_FRACTION)}
        for key in ("split_fraction", "skip_fraction", "min_train_fraction"):
            value = row.get(key)
            if isinstance(value, (int, float)):
                declared.add(round(float(value), 2))
        for candidate in (0.35, 0.65, 0.4, 0.6, 0.45):
            if round(candidate, 2) not in declared:
                return candidate
        return 0.35

    # ==================================================================
    # rebut
    # ==================================================================
    def _rebut_task(self, task: Task) -> AgentResult:
        """Answer every challenge filed against me - each one with a measurement.

        Conceding a well-measured challenge is doing this job correctly. An
        unmeasured ``REBUTTED`` is downgraded to ``UNANSWERED`` by the pooler,
        so asserting is strictly worse than conceding, and this method never
        files a verdict it has not measured.
        """
        ctx = self.require_context()
        symbol = self._symbol(task)
        frame = ctx.frame(symbol)

        mine, own_note = self._read_findings(self.role, symbol)
        by_id = {str(r.get("strategy_id")): r for r in mine if r.get("strategy_id")}

        incoming: List[Dict[str, Any]] = []
        unavailable: List[str] = []
        for opponent in opponents_of(self.role):
            rows, note = self._read_artefact_rows(opponent, "challenges", "challenges")
            if note:
                unavailable.append(note)
                self.log(note)
                continue
            for row in rows:
                if str(row.get("target_owner")) != self.id:
                    continue
                if str(row.get("symbol", symbol)).upper() != symbol:
                    continue
                incoming.append(row)

        rebuttals: List[Rebuttal] = []
        unanswerable: List[Dict[str, Any]] = []
        for row in incoming:
            rebuttal, problem = self._answer(ctx, frame, symbol, row, by_id)
            if rebuttal is not None:
                rebuttals.append(rebuttal)
            else:
                unanswerable.append(problem or {})

        tally: Dict[str, int] = {}
        for r in rebuttals:
            tally[r.verdict.value] = tally.get(r.verdict.value, 0) + 1

        section = {
            "generated_et": et_stamp(),
            "owner": self.id,
            "symbol": symbol,
            "source": "deterministic",
            "challenges_received": len(incoming),
            "rebuttals": [r.to_dict() for r in rebuttals],
            "verdict_tally": tally,
            "unanswerable": unanswerable,
            "rivals_unavailable": unavailable,
            "own_findings_note": own_note or f"{len(by_id)} own finding(s) on file",
            "posture": (
                "Conceding a well-measured challenge is doing the job correctly. "
                "Breakout edges genuinely do concentrate in expansion regimes, so "
                "a correct regime-concentration measurement is conceded, or the "
                "claim is narrowed to PARTIAL with the surviving scope and its "
                "sample size stated. Every verdict here carries the measurement "
                "that produced it; an unmeasured REBUTTED is downgraded to "
                "UNANSWERED and is therefore worse than a concession."),
        }
        path = self.publish("rebuttals", section,
                            f"{symbol}: {len(rebuttals)} measured rebuttal(s) to "
                            f"{len(incoming)} challenge(s)")

        if not incoming:
            summary = (f"{symbol}: no challenges were filed against {self.id}"
                       + (" - " + "; ".join(unavailable) if unavailable else "")
                       + ". Nothing to answer.")
        else:
            summary = (
                f"{symbol}: answered {len(rebuttals)} of {len(incoming)} challenge(s) "
                f"against {self.id} "
                f"({', '.join(f'{k} x{v}' for k, v in sorted(tally.items()))})"
                + (f"; {len(unanswerable)} could not be measured and were left "
                   f"UNANSWERED rather than denied" if unanswerable else "")
                + ". Every verdict carries its measurement.")
        self.log(summary)

        return AgentResult(
            ok=True, summary=summary,
            payload={"symbol": symbol,
                     "challenges_received": len(incoming),
                     "rebuttals_filed": len(rebuttals),
                     "verdict_tally": tally,
                     "unanswerable": unanswerable,
                     "rivals_unavailable": unavailable,
                     "rebuttals": [r.to_dict() for r in rebuttals]},
            artefacts=[path])

    def _answer(self, ctx, frame: SymbolFrame, symbol: str, row: Dict[str, Any],
                by_id: Dict[str, Dict[str, Any]]
                ) -> Tuple[Optional[Rebuttal], Optional[Dict[str, Any]]]:
        """One measured answer to one challenge, or a stated inability to measure."""
        sid = str(row.get("target_strategy_id") or "")
        challenger = str(row.get("challenger") or "")
        try:
            kind = ChallengeKind(str(row.get("kind")))
        except ValueError:
            return None, {"strategy_id": sid, "challenger": challenger,
                          "reason": f"unknown challenge kind {row.get('kind')!r}"}

        strategy = ctx.registry.get(sid)
        if strategy is None:
            return None, {"strategy_id": sid, "challenger": challenger,
                          "kind": kind.value,
                          "reason": f"{sid} is not in the shared registry, so the "
                                    "objection cannot be re-measured here. Left "
                                    "UNANSWERED rather than denied."}

        finding = by_id.get(sid, {})
        start = int(finding.get("window_start_index") or 0)
        trades = list(run_portfolio(frame, [strategy], start=start)[sid].trades)
        metrics = compute_metrics(trades)
        scope = {"rebuttal_window_start_index": start,
                 "rebuttal_trades": metrics.trades,
                 "rebuttal_expectancy_r": round(metrics.expectancy_r, 4)}

        def rebut(verdict: Verdict, argument: str,
                  measurement: Dict[str, Any]) -> Tuple[Rebuttal, None]:
            return Rebuttal(responder=self.id, challenger=challenger,
                            target_strategy_id=sid, verdict=verdict,
                            argument=argument,
                            measurement={**scope, **measurement}), None

        if kind is ChallengeKind.SAMPLE_TOO_SMALL:
            if metrics.trades < MIN_TRADES_FOR_RANK:
                return rebut(Verdict.CONCEDED,
                             f"Conceded. Re-measured, {sid} produced "
                             f"{metrics.trades} trades - below the "
                             f"{MIN_TRADES_FOR_RANK}-trade floor. The sample does "
                             f"not support the claim and the finding should be "
                             f"disqualified.",
                             {"floor": MIN_TRADES_FOR_RANK})
            return rebut(Verdict.REBUTTED,
                         f"Re-measured over the window this finding was published "
                         f"on (bar {start} onward), {sid} produced {metrics.trades} "
                         f"trades at {metrics.expectancy_r:+.3f}R, clearing the "
                         f"{MIN_TRADES_FOR_RANK}-trade floor. The challenger "
                         f"appears to have measured a different window.",
                         {"floor": MIN_TRADES_FOR_RANK})

        if kind is ChallengeKind.REGIME_ARTEFACT:
            return self._answer_concentration(row, sid, trades, rebut)

        if kind is ChallengeKind.OUT_OF_SAMPLE_FAILURE:
            return self._answer_oos(row, sid, frame, strategy, rebut)

        if kind is ChallengeKind.COST_FRAGILE:
            stress = cost_stress(frame, strategy)
            if stress.get("survives"):
                return rebut(Verdict.REBUTTED,
                             f"{sid} survives doubled slippage: "
                             f"{stress['stressed_expectancy_r']:+.4f}R against a "
                             f"baseline of {stress['baseline_expectancy_r']:+.4f}R, "
                             f"retaining {stress['retained_fraction']:.0%} of the "
                             f"edge over {metrics.trades} trades.", stress)
            return rebut(Verdict.CONCEDED,
                         f"Conceded. Under doubled slippage {sid} returns "
                         f"{stress['stressed_expectancy_r']:+.4f}R - the edge does "
                         f"not survive a cost assumption a live account would "
                         f"actually meet.", stress)

        if kind is ChallengeKind.REDUNDANT:
            return self._answer_redundant(row, sid, finding, rebut)

        if kind is ChallengeKind.DATA_MINING:
            trials = int(finding.get("trials_searched") or 1)
            deflated = deflated_expectancy(metrics, trials)
            measurement = {"trials_searched": trials,
                           "deflated_expectancy_r": round(deflated, 6),
                           "raw_expectancy_r": round(metrics.expectancy_r, 4),
                           "t_statistic": round(metrics.t_statistic, 4)}
            if deflated <= 0:
                return rebut(Verdict.CONCEDED,
                             f"Conceded. Against the {trials:,} hypotheses this "
                             f"seat actually searched - the family universe times "
                             f"the temporal slices it conditions on - {sid}'s "
                             f"expectancy deflates to {deflated:+.5f}R. Nothing "
                             f"survives the search that produced it.", measurement)
            return rebut(Verdict.REBUTTED,
                         f"{sid} still carries {deflated:+.5f}R after deflation "
                         f"against {trials:,} searched hypotheses, which already "
                         f"charges this seat for every session and time-of-day "
                         f"slice it looked at, over {metrics.trades} trades.",
                         measurement)

        return None, {"strategy_id": sid, "challenger": challenger,
                      "kind": kind.value,
                      "reason": "no measurement procedure is defined here for "
                                "this kind; left UNANSWERED rather than denied"}

    def _answer_concentration(self, row: Dict[str, Any], sid: str,
                              trades: Sequence[Trade], rebut):
        """Answer a regime or time-of-day concentration objection.

        Breakout edges genuinely do concentrate in expansion regimes - that is
        what a breakout *is*. So the question is never "is it concentrated" but
        "is there anything left when the dominant bucket is removed, on a sample
        worth quoting". If there is, the claim narrows to that scope. If there
        is not, the challenge is right.
        """
        axis = str((row.get("measurement") or {}).get("axis") or "regime")
        if axis not in ("regime", *TEMPORAL_AXES):
            axis = "regime"

        if axis == "regime":
            measured = regime_concentration(trades)
            dominant = measured.get("dominant_regime")
            share = measured.get("dominant_share", 0.0) or 0.0
            sample = measured.get("dominant_sample", 0)
            rest = [t for t in trades if _bucket_of(t, "regime") != dominant]
            concentrated = bool(measured.get("concentrated"))
        else:
            profile = _slice_profile(trades, axis)
            measured = profile
            dominant = profile.get("dominant_bucket")
            share = profile.get("dominant_share_of_total_r", 0.0) or 0.0
            sample = profile.get("dominant_sample", 0)
            rest = [t for t in trades if _bucket_of(t, axis) != dominant]
            concentrated = bool(profile.get("single_bucket_artefact")
                                or share >= CONCENTRATION_SHARE)

        ex = compute_metrics(rest)
        measurement = {
            "axis": axis, "dominant_bucket": dominant,
            "dominant_share_of_total_r": round(share, 4),
            "dominant_sample": sample,
            "ex_dominant_trades": ex.trades,
            "ex_dominant_expectancy_r": round(ex.expectancy_r, 4),
            "ex_dominant_profit_factor": round(ex.profit_factor, 3),
            "residual_sample_floor": MIN_SLICE_TRADES,
            "re_measurement": measured,
        }

        if not concentrated:
            return rebut(Verdict.REBUTTED,
                         f"Re-measured, {sid} is not concentrated on {axis}: the "
                         f"largest bucket '{dominant}' holds "
                         f"{_share_phrase(share)} total R "
                         f"on {sample} trades, and the remaining {ex.trades} trades "
                         f"average {ex.expectancy_r:+.3f}R - the edge is present "
                         f"outside the bucket the challenge names.", measurement)

        if ex.expectancy_r > 0 and ex.trades >= MIN_SLICE_TRADES:
            return rebut(Verdict.PARTIAL,
                         f"Accepted in part, and the claim is narrowed. {sid} does "
                         f"concentrate in '{dominant}' ({_share_phrase(share)} "
                         f"total R on {sample} trades) - a breakout edge concentrating in "
                         f"expansion is the mechanism, not a defect. But it is not "
                         f"only that bucket: excluding it, {ex.trades} trades still "
                         f"average {ex.expectancy_r:+.3f}R (profit factor "
                         f"{ex.profit_factor:.2f}), clearing the "
                         f"{MIN_SLICE_TRADES}-trade floor. Scope of the surviving "
                         f"claim: {sid} outside '{dominant}', {ex.trades} trades. "
                         f"The unconditional claim is withdrawn.", measurement)

        return rebut(Verdict.CONCEDED,
                     f"Conceded. {sid} earns {_share_phrase(share)} its total R in "
                     f"'{dominant}' on {sample} trades, and removing that bucket "
                     f"leaves {ex.trades} trades averaging {ex.expectancy_r:+.3f}R. "
                     f"There is no residual edge to narrow the claim to - the "
                     f"finding describes one {axis} bucket, not a rule.",
                     measurement)

    def _answer_oos(self, row: Dict[str, Any], sid: str, frame: SymbolFrame,
                    strategy: Strategy, rebut):
        """Answer an adversarial re-test by re-running it, plus its complement."""
        declared = (row.get("measurement") or {}).get("split_fraction")
        theirs = float(declared) if isinstance(declared, (int, float)) else 0.5
        theirs = min(0.9, max(0.1, theirs))
        mirror = round(1.0 - theirs, 2)

        first = adversarial_retest(frame, strategy, skip_fraction=theirs)
        second = adversarial_retest(frame, strategy, skip_fraction=mirror)
        measurement = {"challenger_split": theirs, "challenger_split_result": first,
                       "mirror_split": mirror, "mirror_split_result": second}

        if "error" in first and "error" in second:
            return rebut(Verdict.CONCEDED,
                         f"Conceded by default: {sid} cannot be re-split for a "
                         f"re-test ({first['error']}), so the objection stands "
                         f"unanswered on the evidence available.", measurement)

        holds_first = bool(first.get("both_halves_positive"))
        holds_mirror = bool(second.get("both_halves_positive"))
        if holds_first and holds_mirror:
            return rebut(Verdict.REBUTTED,
                         f"{sid} is positive in both halves of the {theirs:.0%} "
                         f"split the challenge used (consistency "
                         f"{first.get('consistency_ratio', 0.0):.2f}) and of the "
                         f"complementary {mirror:.0%} split (consistency "
                         f"{second.get('consistency_ratio', 0.0):.2f}). The edge is "
                         f"not an artefact of one division of the history.",
                         measurement)
        if holds_first or holds_mirror:
            held, failed = ((mirror, theirs) if holds_mirror else (theirs, mirror))
            return rebut(Verdict.PARTIAL,
                         f"Accepted in part. {sid} holds in both halves of the "
                         f"{held:.0%} split but not of the {failed:.0%} one, so the "
                         f"edge is split-dependent and the unconditional claim is "
                         f"withdrawn. It should be treated as unproven out of "
                         f"sample until a full walk-forward says otherwise.",
                         measurement)
        return rebut(Verdict.CONCEDED,
                     f"Conceded. {sid} fails in both the {theirs:.0%} split the "
                     f"challenge chose (consistency "
                     f"{first.get('consistency_ratio', 0.0):.2f}) and its "
                     f"complement (consistency "
                     f"{second.get('consistency_ratio', 0.0):.2f}). It was fitted "
                     f"to the window it was found in.", measurement)

    def _answer_redundant(self, row: Dict[str, Any], sid: str,
                          finding: Dict[str, Any], rebut):
        """Answer a redundancy objection by recomputing the overlap myself."""
        mine = self._prints(finding)
        measurement_in = row.get("measurement") or {}
        theirs = [tuple(p) for p in (measurement_in.get("fingerprint") or [])
                  if isinstance(p, (list, tuple)) and len(p) == 2]

        if not mine:
            return None, {"strategy_id": sid, "kind": ChallengeKind.REDUNDANT.value,
                          "reason": "no fingerprint on file for my own finding, so "
                                    "the overlap cannot be recomputed here; left "
                                    "UNANSWERED rather than denied"}
        if not theirs:
            # Their challenge carried an overlap figure but not the fingerprint
            # behind it. Recomputing is impossible, and denying a measured
            # objection without a counter-measurement would be downgraded to
            # UNANSWERED anyway - so concede on their number.
            reported = measurement_in.get("effective_overlap",
                                          measurement_in.get("raw_overlap"))
            if isinstance(reported, (int, float)) and reported >= REDUNDANCY_THRESHOLD:
                return rebut(Verdict.CONCEDED,
                             f"Conceded on the challenger's measurement: "
                             f"{float(reported):.0%} fingerprint overlap is above the "
                             f"{REDUNDANCY_THRESHOLD:.0%} threshold. Their challenge "
                             f"did not carry the fingerprint itself, so it cannot be "
                             f"recomputed here - but denying a measured objection "
                             f"without a counter-measurement would be worthless. "
                             f"Count the edge once.",
                             {"challenger_reported_overlap": float(reported),
                              "my_trades": len(mine),
                              "recomputable": False,
                              "threshold": REDUNDANCY_THRESHOLD})
            return None, {"strategy_id": sid, "kind": ChallengeKind.REDUNDANT.value,
                          "reason": "the challenge carried neither a fingerprint nor "
                                    "an overlap figure above threshold; left "
                                    "UNANSWERED"}

        measured = _overlap_measure(mine, theirs)
        overlap = measured.get("effective_overlap", measured["raw_overlap"])
        if overlap >= REDUNDANCY_THRESHOLD:
            return rebut(Verdict.CONCEDED,
                         f"Conceded. Recomputed, {sid} overlaps the challenger's "
                         f"strategy at {overlap:.0%} of fingerprints over the bars "
                         f"both swept - above the {REDUNDANCY_THRESHOLD:.0%} "
                         f"threshold. These are the same trades under two names and "
                         f"the edge should be counted once.", measured)
        return rebut(Verdict.REBUTTED,
                     f"Recomputed on the bars both sweeps actually cover, the "
                     f"overlap is {overlap:.0%} - below the "
                     f"{REDUNDANCY_THRESHOLD:.0%} threshold "
                     f"({measured.get('my_trades_in_common_window', 0)} of my trades "
                     f"against {measured.get('their_trades_in_common_window', 0)} of "
                     f"theirs in the common window). The raw figure is diluted when "
                     f"two specialists sweep different windows; restricted to the "
                     f"shared bars these are different trades.", measured)

    # ==================================================================
    # Reading rivals - every path tolerates absence
    # ==================================================================
    def _read_artefact_rows(self, owner: Role, artefact: str, key: str
                            ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Rows from another specialist's artefact, or the reason there are none.

        The other two specialists are written in parallel and may not exist at
        all when this runs, so ``read_from`` returning ``None`` is an ordinary
        outcome that is reported, not an error. The shape is treated as
        untrusted: a bare list, a dict with the expected key, or something else
        entirely all resolve to "no usable rows" plus a stated reason.
        """
        try:
            doc = self.read_from(owner, artefact)
        except Exception as exc:                            # noqa: BLE001
            return [], (f"{owner.value}: '{artefact}' could not be read "
                        f"({type(exc).__name__}: {exc})")
        if doc is None:
            return [], (f"{owner.value}: has published no '{artefact}' artefact "
                        f"(not yet staffed, or it has not run)")
        rows = doc.get(key) if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            return [], (f"{owner.value}: '{artefact}' carries no '{key}' list "
                        f"(got {type(rows).__name__})")
        usable = [r for r in rows if isinstance(r, dict)]
        if not usable:
            return [], f"{owner.value}: '{artefact}' published an empty '{key}' list"
        return usable, None

    def _read_findings(self, owner: Role, symbol: str
                       ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """One specialist's findings for this symbol, or the reason there are none."""
        rows, note = self._read_artefact_rows(owner, "findings", "findings")
        if note:
            return [], note
        matched = [r for r in rows
                   if str(r.get("symbol", symbol)).upper() == symbol.upper()]
        if not matched:
            return [], (f"{owner.value}: published findings, but none for {symbol}")
        return matched, None

    @staticmethod
    def _prints(row: Dict[str, Any]) -> List[Tuple[int, int]]:
        """A published finding's trade fingerprint, defensively parsed.

        JSON turns the tuples into lists and there is no guarantee another
        specialist wrote the field at all, so anything malformed is dropped
        rather than raised - a missing fingerprint means "cannot measure
        overlap", which is reported, not a crash.
        """
        raw = row.get("trade_fingerprint") or []
        out: List[Tuple[int, int]] = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    out.append((int(item[0]), int(item[1])))
                except (TypeError, ValueError):
                    continue
        return out

    # ==================================================================
    # Shared helpers
    # ==================================================================
    def _empty(self, symbol: str, reason: str) -> AgentResult:
        """A truthful nothing-to-report, published so the absence is on record."""
        section = {"generated_et": et_stamp(), "owner": self.id, "symbol": symbol,
                   "source": "deterministic", "families": list(self.families),
                   "findings": [], "withheld": [], "trials_searched": 1,
                   "live_eligible": [], "live_eligibility_note": reason}
        path = self.publish("findings", section, f"{symbol}: {reason}")
        return AgentResult(ok=True, summary=f"{symbol}: {reason}",
                           payload={"symbol": symbol, "findings": [],
                                    "families": list(self.families),
                                    "note": reason},
                           artefacts=[path])

    # ---- optional narrative, never a number ---------------------------
    #: Stable by construction - no timestamps, no prices - because it is the
    #: cached prefix for every call this agent makes.
    SYSTEM = """
You are the liquidity, breakout and session research specialist on a futures
research desk. You own the LIQUIDITY, OPENING_RANGE and BREAKOUT families.

Your working hypothesis is that the strongest intraday structure is temporal
rather than indicator-based: the opening drive, the initial balance, the lunch
lull and the close. You therefore read every table below through time of day
and session - and you know the trap that comes with it, which is that slicing
by session multiplies the hypotheses tested and demands the harshest deflation
of anyone on the desk.

Hard limits on your output:

- You may not state, adjust, round or re-derive any number. Every statistic in
  the evidence was measured; your commentary references it, it does not produce
  it.
- Quote the sample size with every result you mention. A session slice quoted
  without its n is the most persuasive way to lie with a backtest.
- Never treat a slice below 30 trades as evidence, however good it looks. Say
  it is too thin.
- You may not declare anything live-eligible. That flag is set by the
  robustness suite alone.
- "This universe shows no edge" is a complete and valuable answer. Synthetic or
  short samples usually produce exactly that, and saying so is correct.
""".strip()
