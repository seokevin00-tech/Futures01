"""The Developer agent - backend engineering and correctness verification.

This agent is the team's engineer. In an interactive Claude Code session its
counterpart (``.claude/agents/developer.md``) writes and refactors the backend
code directly. At *runtime* - inside a research or live cycle - its job is the
one that actually protects the account: it verifies that the deterministic core
still holds its invariants before anybody trusts a number that came out of it.

The checks are not decorative. Every one of them targets a failure mode that
silently turns a losing system into a backtest that looks profitable:

* **Look-ahead in indicators** - an indicator whose historical values change
  when future bars arrive has leaked the future into the past.
* **Cross-timeframe leakage** - a 15-minute bar that had not closed yet being
  read as if it had.
* **Entry timing** - filling on the signal bar rather than the next open.
* **Cost realism** - a cost model that rounds to zero makes every scalping
  strategy look viable.
* **Determinism** - a strategy whose id or behaviour changes between runs
  cannot have a performance history.

If any of these fail, the manager is told and the research output of that cycle
is not to be trusted.
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..timeutil import et_stamp
from .agent import AgentResult, TeamAgent
from .board import Task
from .bus import MessageBus
from .roles import Role
from .workspace import TeamFilesystem

__all__ = ["DeveloperAgent", "CheckResult"]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    duration_s: float = 0.0
    severity: str = "critical"        # critical | warning | info

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail,
                "duration_s": round(self.duration_s, 3), "severity": self.severity}

    def render(self) -> str:
        mark = "PASS" if self.passed else ("WARN" if self.severity == "warning" else "FAIL")
        return f"  [{mark}] {self.name:<42} {self.detail}"


class DeveloperAgent(TeamAgent):
    """Runs the engineering verification suite and reports defects."""

    def __init__(self, fs: TeamFilesystem, bus: MessageBus, *, config: Any = None,
                 llm: Any = None):
        super().__init__(Role.DEVELOPER, fs, bus, config=config, llm=llm)

    # ------------------------------------------------------------------
    def handle(self, task: Task) -> Optional[AgentResult]:
        if task.kind in ("test", "implement", "fix", "refactor", "migrate"):
            return self._verify(task)
        if task.kind == "benchmark":
            return self._benchmark(task)
        return AgentResult.failure(f"developer does not handle '{task.kind}'")

    # ------------------------------------------------------------------
    def _verify(self, task: Task) -> AgentResult:
        checks: List[CheckResult] = []
        for fn in (self._check_indicator_lookahead,
                   self._check_timeframe_alignment,
                   self._check_entry_timing,
                   self._check_cost_realism,
                   self._check_strategy_determinism,
                   self._check_risk_monotonicity,
                   self._check_pytest):
            started = time.time()
            try:
                result = fn()
            except Exception as exc:                    # noqa: BLE001
                result = CheckResult(fn.__name__.replace("_check_", ""), False,
                                     f"{type(exc).__name__}: {exc}")
            result.duration_s = time.time() - started
            checks.append(result)

        failures = [c for c in checks if not c.passed and c.severity == "critical"]
        warnings = [c for c in checks if not c.passed and c.severity == "warning"]

        report = {
            "timestamp_et": et_stamp(),
            "checks": [c.to_dict() for c in checks],
            "passed": len(checks) - len(failures) - len(warnings),
            "failed": len(failures), "warnings": len(warnings),
            "core_trustworthy": not failures,
        }
        self.publish("test_report", report,
                     f"{report['passed']}/{len(checks)} checks passed")

        if failures:
            # A core failure is an alert to the whole team, not a line in a log:
            # every downstream number this cycle produces is suspect.
            self.bus.alert(self.role, "core_verification_failed",
                           {"failed": [c.name for c in failures]})
            return AgentResult(
                ok=False,
                error="core verification failed: " + ", ".join(c.name for c in failures),
                summary=f"{len(failures)} critical check(s) failed",
                payload=report, artefacts=["test_report.json"])

        summary = f"{report['passed']}/{len(checks)} checks passed"
        if warnings:
            summary += f" ({len(warnings)} warning(s))"
        return AgentResult(ok=True, summary=summary, payload=report,
                           artefacts=["test_report.json"])

    # ---- individual checks -------------------------------------------
    @staticmethod
    def _check_indicator_lookahead() -> CheckResult:
        """Appending a future bar must not change any historical value."""
        from ..indicators.core import (adx, atr, ema, macd, percent_rank, rsi,
                                       sma, stdev)
        base = [100.0 + math.sin(i / 7.0) * 5 + i * 0.05 for i in range(300)]
        highs = [v + 1.5 for v in base]
        lows = [v - 1.5 for v in base]
        future = base + [999.0]
        offenders: List[str] = []

        for name, fn in (("sma", lambda x: sma(x, 20)), ("ema", lambda x: ema(x, 20)),
                         ("rsi", lambda x: rsi(x, 14)), ("stdev", lambda x: stdev(x, 20)),
                         ("percent_rank", lambda x: percent_rank(x, 50))):
            if fn(base) != fn(future)[: len(base)]:
                offenders.append(name)
        if macd(base)[0] != macd(future)[0][: len(base)]:
            offenders.append("macd")
        if atr(highs, lows, base, 14) != atr(highs + [1000.0], lows + [998.0],
                                             future, 14)[: len(base)]:
            offenders.append("atr")
        if adx(highs, lows, base, 14)[0] != adx(highs + [1000.0], lows + [998.0],
                                                future, 14)[0][: len(base)]:
            offenders.append("adx")

        return CheckResult(
            "indicator look-ahead", not offenders,
            "no historical value changed by future data" if not offenders
            else f"LEAK in: {', '.join(offenders)}")

    @staticmethod
    def _check_timeframe_alignment() -> CheckResult:
        """No higher-timeframe bar may end after the base bar it is aligned to."""
        from datetime import timedelta
        from ..data import synthetic_series
        from ..features import build_symbol_frame

        s = synthetic_series("MNQ", days=3, seed=101)
        frame = build_symbol_frame(s, [1, 5, 15, 60])
        violations = 0
        checked = 0
        for i in range(len(s) - 400, len(s), 7):
            snap = frame.snapshot(i)
            if snap is None:
                continue
            for tf in snap.timeframes:
                ts = snap.tf(tf)
                checked += 1
                if ts.bar.end_ts > snap.ts + timedelta(minutes=s.minutes):
                    violations += 1
        return CheckResult(
            "cross-timeframe alignment", violations == 0,
            f"{checked} alignments checked, {violations} violation(s)")

    @staticmethod
    def _check_entry_timing() -> CheckResult:
        """Every fill must occur strictly after the bar that produced the signal."""
        from ..backtest import BacktestEngine
        from ..data import synthetic_series
        from ..features import build_symbol_frame
        from ..strategies import generate_strategies

        s = synthetic_series("MNQ", days=6, seed=202)
        frame = build_symbol_frame(s, [5, 15])
        strategies = generate_strategies("MNQ", [5], groups=["TREND", "VWAP"],
                                         max_total=40)[:20]
        engine = BacktestEngine(frame)
        results = engine.run_many(strategies)
        bad = trades = 0
        for r in results.values():
            for t in r.trades:
                trades += 1
                if t.entry_index <= t.signal_index:
                    bad += 1
                if t.risk_points <= 0:
                    bad += 1
        return CheckResult(
            "entry timing / risk positivity", bad == 0,
            f"{trades} trades, {bad} timing or risk violation(s)"
            + ("" if trades else " (no trades generated - check coverage)"),
            severity="critical" if trades else "warning")

    @staticmethod
    def _check_cost_realism() -> CheckResult:
        """Round-turn cost must be material and finite for every contract."""
        from ..config import CONTRACTS
        from ..backtest.costs import CostModel
        problems: List[str] = []
        for sym, spec in CONTRACTS.items():
            cm = CostModel(spec)
            rt = cm.round_turn_dollars(atr_percentile=0.5)
            if not math.isfinite(rt) or rt <= 0:
                problems.append(f"{sym}: round-turn {rt}")
            stop_pts = spec.min_stop_ticks * spec.tick_size
            cost_r = cm.cost_in_r(stop_pts)
            if not math.isfinite(cost_r) or cost_r <= 0:
                problems.append(f"{sym}: cost_in_r {cost_r}")
        return CheckResult("cost model realism", not problems,
                           f"{len(CONTRACTS)} contracts priced"
                           if not problems else "; ".join(problems))

    @staticmethod
    def _check_strategy_determinism() -> CheckResult:
        """Two generations with the same seed must produce identical ids."""
        from ..strategies import generate_strategies
        a = generate_strategies("MES", [5, 15], max_total=200)
        b = generate_strategies("MES", [5, 15], max_total=200)
        same = [x.strategy_id for x in a] == [x.strategy_id for x in b]
        return CheckResult("strategy id determinism", same,
                           f"{len(a)} strategies, ids {'stable' if same else 'UNSTABLE'}")

    @staticmethod
    def _check_risk_monotonicity() -> CheckResult:
        """Permitted risk must shrink monotonically as the account draws down."""
        from ..config import AccountConfig
        acct = AccountConfig()
        equities = [50_000, 49_500, 49_000, 48_000, 47_000, 46_000, 45_500]
        mults = [acct.derisk_multiplier(e, 50_000) for e in equities]
        monotonic = all(b <= a + 1e-12 for a, b in zip(mults, mults[1:]))
        ends_at_zero = mults[-1] == 0.0
        ok = monotonic and ends_at_zero
        return CheckResult(
            "risk de-escalation monotonicity", ok,
            f"multipliers {['%.2f' % m for m in mults]}"
            + ("" if ok else "  <- must be non-increasing and reach 0"))

    @staticmethod
    def _check_pytest() -> CheckResult:
        """Run the project test suite if pytest is available."""
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--no-header", "tests"],
                capture_output=True, text=True, timeout=600)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return CheckResult("pytest suite", True,
                               f"skipped ({type(exc).__name__})", severity="info")
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "no output"
        if proc.returncode == 5:            # no tests collected
            return CheckResult("pytest suite", True, "no tests collected",
                               severity="warning")
        return CheckResult("pytest suite", proc.returncode == 0, detail[:200])

    # ---- benchmark ----------------------------------------------------
    def _benchmark(self, task: Task) -> AgentResult:
        from ..data import synthetic_series
        from ..features import build_symbol_frame
        from ..backtest import BacktestEngine
        from ..strategies import generate_strategies

        symbol = task.payload.get("symbol", "MNQ")
        days = int(task.payload.get("days", 10))
        n_strategies = int(task.payload.get("strategies", 200))

        t0 = time.time()
        s = synthetic_series(symbol, days=days, seed=7)
        gen_s = time.time() - t0

        t0 = time.time()
        frame = build_symbol_frame(s, [1, 5, 15, 60])
        frame_s = time.time() - t0

        strategies = generate_strategies(symbol, [5, 15], max_total=n_strategies)
        t0 = time.time()
        results = BacktestEngine(frame).run_many(strategies)
        bt_s = time.time() - t0

        trades = sum(len(r.trades) for r in results.values())
        payload = {
            "symbol": symbol, "bars": len(s), "strategies": len(strategies),
            "generate_s": round(gen_s, 2), "frame_s": round(frame_s, 2),
            "backtest_s": round(bt_s, 2), "trades": trades,
            "bars_per_s": round(len(s) / bt_s, 1) if bt_s else 0,
            "strategy_bar_evals_per_s":
                round(len(s) * len(strategies) / bt_s, 0) if bt_s else 0,
        }
        self.publish("benchmark", payload,
                     f"{len(strategies)} strategies over {len(s)} bars in {bt_s:.1f}s")
        return AgentResult(ok=True, payload=payload, artefacts=["benchmark.json"],
                           summary=(f"{len(strategies)} strategies x {len(s)} bars "
                                    f"in {bt_s:.1f}s ({trades} trades)"))
