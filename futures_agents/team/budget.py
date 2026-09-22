"""Usage budget monitoring and dispatch gating.

Measures what this session has actually consumed and decides whether the team
may keep placing work. Consumption comes from the Claude Code transcripts on
disk, which record exact per-turn token counts - so the *numerator* is real,
measured data, never an estimate.

**The limit is not.** Claude Code's session and weekly subscription limits live
server-side and are not exposed to a running session. Nothing on disk, in the
environment, or reachable over the API tells this process what the ceiling is.
That has one unavoidable consequence, and it is the most important thing in
this module:

    Without a configured limit, this monitor reports consumption and refuses
    to report a percentage.

A budget guard that invents a denominator would produce confident, wrong
percentages and would be strictly worse than no guard at all - the operator
would trust it and blow through the limit anyway. So :class:`BudgetState`
carries an explicit ``UNKNOWN`` status, and the gate fails *open* with a
warning rather than silently pretending everything is fine.

To make the percentages real, give it the denominator once:

    monitor.calibrate(session_pct_observed=42.0)   # from /usage in the client

or configure explicit token limits. Calibration is the easier path: the
operator reads the percentage their client already shows, and the monitor
solves for the implied ceiling from measured consumption.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..timeutil import et_stamp, now_et, to_et

__all__ = [
    "BudgetStatus", "BudgetLimits", "WindowUsage", "BudgetState",
    "BudgetMonitor", "DEFAULT_WEIGHTS",
]


#: Relative cost weights used to collapse the token components into one
#: "effective tokens" figure. Derived from published Opus pricing ratios
#: (output 5x input, cache write 1.25x, cache read 0.1x). This is a documented
#: ASSUMPTION about how a subscription limit is consumed, not a measurement -
#: which is exactly why calibration against an observed percentage is the
#: recommended path: it cancels out whatever the true weighting is.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "input_tokens": 1.0,
    "output_tokens": 5.0,
    "cache_creation_input_tokens": 1.25,
    "cache_read_input_tokens": 0.1,
}

#: Claude Code meters usage in rolling blocks; the session block is 5 hours and
#: the longer window is 7 days. Both are configurable because neither is
#: documented as a contract.
SESSION_WINDOW = timedelta(hours=5)
WEEKLY_WINDOW = timedelta(days=7)


class BudgetStatus(str, Enum):
    """What the team is permitted to do right now."""

    OK = "OK"                  # full speed
    SLOW_DOWN = "SLOW_DOWN"    # past the first threshold - conserve
    PAUSED = "PAUSED"          # past the second - place no new work
    UNKNOWN = "UNKNOWN"        # consumption measured, ceiling not known

    @property
    def may_place_work(self) -> bool:
        """UNKNOWN deliberately allows work. A monitor that cannot measure the
        limit must not silently halt the team on a guess - it warns instead."""
        return self is not BudgetStatus.PAUSED

    @property
    def should_conserve(self) -> bool:
        return self in (BudgetStatus.SLOW_DOWN, BudgetStatus.PAUSED)


@dataclass
class BudgetLimits:
    """Thresholds, in percent of the applicable limit."""

    session_slow_at: float = 75.0
    session_pause_at: float = 80.0
    weekly_slow_at: float = 80.0
    weekly_pause_at: float = 90.0

    #: Effective-token ceilings. ``None`` means "not known" - the honest
    #: default, since nothing available to this process reveals them.
    session_limit_tokens: Optional[float] = None
    weekly_limit_tokens: Optional[float] = None

    session_window: timedelta = SESSION_WINDOW
    weekly_window: timedelta = WEEKLY_WINDOW

    def to_dict(self) -> dict:
        return {
            "session_slow_at_pct": self.session_slow_at,
            "session_pause_at_pct": self.session_pause_at,
            "weekly_slow_at_pct": self.weekly_slow_at,
            "weekly_pause_at_pct": self.weekly_pause_at,
            "session_limit_tokens": self.session_limit_tokens,
            "weekly_limit_tokens": self.weekly_limit_tokens,
            "session_window_hours": self.session_window.total_seconds() / 3600.0,
            "weekly_window_days": self.weekly_window.total_seconds() / 86400.0,
        }


@dataclass
class WindowUsage:
    """Measured consumption inside one time window."""

    label: str
    window: timedelta
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    effective_tokens: float = 0.0
    limit_tokens: Optional[float] = None

    @property
    def pct_used(self) -> Optional[float]:
        """Percent of the limit consumed, or ``None`` when the limit is unknown.

        Returning None rather than 0.0 is deliberate: 0.0 reads as "plenty of
        room left", which is the opposite of "we have no idea".
        """
        if not self.limit_tokens:
            return None
        return 100.0 * self.effective_tokens / self.limit_tokens

    @property
    def remaining_tokens(self) -> Optional[float]:
        if not self.limit_tokens:
            return None
        return max(0.0, self.limit_tokens - self.effective_tokens)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "window_hours": round(self.window.total_seconds() / 3600.0, 2),
            "since": to_et(self.since).isoformat() if self.since else None,
            "until": to_et(self.until).isoformat() if self.until else None,
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "effective_tokens": round(self.effective_tokens, 1),
            "limit_tokens": self.limit_tokens,
            "pct_used": None if self.pct_used is None else round(self.pct_used, 2),
            "remaining_tokens": (None if self.remaining_tokens is None
                                 else round(self.remaining_tokens, 1)),
        }


@dataclass
class BudgetState:
    """The monitor's verdict, with the reasoning attached."""

    status: BudgetStatus = BudgetStatus.UNKNOWN
    session: Optional[WindowUsage] = None
    weekly: Optional[WindowUsage] = None
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked_et: str = field(default_factory=lambda: to_et(now_et()).isoformat())

    @property
    def may_place_work(self) -> bool:
        return self.status.may_place_work

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "may_place_work": self.may_place_work,
            "session": self.session.to_dict() if self.session else None,
            "weekly": self.weekly.to_dict() if self.weekly else None,
            "reasons": self.reasons, "warnings": self.warnings,
            "checked_et": self.checked_et,
        }

    def render(self) -> str:
        lines = [f"USAGE BUDGET  [{et_stamp()}]", f"  status: {self.status.value}"]
        for window in (self.session, self.weekly):
            if window is None:
                continue
            pct = window.pct_used
            shown = f"{pct:5.1f}%" if pct is not None else "  ?  "
            limit = (f"{window.limit_tokens:,.0f}" if window.limit_tokens
                     else "unknown - not measurable from here")
            lines.append(
                f"  {window.label:<8} {shown} of limit   "
                f"effective {window.effective_tokens:>12,.0f} / {limit}"
                f"   ({window.turns} turns)")
        for reason in self.reasons:
            lines.append(f"  - {reason}")
        for warning in self.warnings:
            lines.append(f"  warning: {warning}")
        return "\n".join(lines)


class BudgetMonitor:
    """Measures transcript usage and gates the team's dispatch."""

    def __init__(self, limits: Optional[BudgetLimits] = None, *,
                 transcript_root: Optional[str] = None,
                 weights: Optional[Dict[str, float]] = None,
                 state_path: Optional[str] = None):
        self.limits = limits or BudgetLimits()
        self.transcript_root = transcript_root or os.path.expanduser(
            "~/.claude/projects")
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.state_path = state_path
        if state_path:
            self._load_state()

    # ------------------------------------------------------------------
    # Measurement - this half is real
    # ------------------------------------------------------------------
    def _transcripts(self) -> List[str]:
        pattern = os.path.join(self.transcript_root, "**", "*.jsonl")
        return sorted(glob.glob(pattern, recursive=True))

    def _turns(self) -> Iterable[Tuple[datetime, Dict[str, Any]]]:
        """Every turn that recorded token usage, with its timestamp.

        Subagent transcripts live under the parent's directory and are included
        deliberately: work delegated to an agent consumes the same budget as
        work done directly, and a monitor that ignored it would under-report
        precisely when the team is most active.
        """
        for path in self._transcripts():
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        message = record.get("message")
                        if not isinstance(message, dict):
                            continue
                        usage = message.get("usage")
                        if not isinstance(usage, dict):
                            continue
                        stamp = record.get("timestamp")
                        if not stamp:
                            continue
                        try:
                            when = datetime.fromisoformat(
                                str(stamp).replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=timezone.utc)
                        yield when, usage
            except OSError:
                continue

    def effective(self, usage: Dict[str, Any]) -> float:
        return sum(float(usage.get(key, 0) or 0) * weight
                   for key, weight in self.weights.items())

    def measure(self, window: timedelta, label: str,
                limit: Optional[float] = None,
                now: Optional[datetime] = None) -> WindowUsage:
        """Measured consumption over the trailing ``window``."""
        end = to_et(now or now_et())
        start = end - window
        result = WindowUsage(label=label, window=window, since=start, until=end,
                             limit_tokens=limit)
        for when, usage in self._turns():
            when_et = to_et(when)
            if not (start <= when_et <= end):
                continue
            result.turns += 1
            result.input_tokens += int(usage.get("input_tokens", 0) or 0)
            result.output_tokens += int(usage.get("output_tokens", 0) or 0)
            result.cache_creation_tokens += int(
                usage.get("cache_creation_input_tokens", 0) or 0)
            result.cache_read_tokens += int(
                usage.get("cache_read_input_tokens", 0) or 0)
            result.effective_tokens += self.effective(usage)
        return result

    # ------------------------------------------------------------------
    # Calibration - how the denominator becomes real
    # ------------------------------------------------------------------
    def calibrate(self, *, session_pct_observed: Optional[float] = None,
                  weekly_pct_observed: Optional[float] = None,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
        """Solve for the limit from a percentage the operator can see.

        The operator reads the figure their Claude Code client already shows
        (``/usage``), and the monitor divides measured consumption by it. This
        is the recommended path because it cancels out whatever weighting the
        real limit uses - if the numerator and the reported percentage refer to
        the same underlying meter, the implied ceiling is right regardless of
        whether :data:`DEFAULT_WEIGHTS` is.
        """
        out: Dict[str, Any] = {}
        if session_pct_observed and session_pct_observed > 0:
            used = self.measure(self.limits.session_window, "session", now=now)
            implied = used.effective_tokens / (session_pct_observed / 100.0)
            self.limits.session_limit_tokens = implied
            out["session_limit_tokens"] = round(implied, 1)
            out["session_measured_effective"] = round(used.effective_tokens, 1)
        if weekly_pct_observed and weekly_pct_observed > 0:
            used = self.measure(self.limits.weekly_window, "weekly", now=now)
            implied = used.effective_tokens / (weekly_pct_observed / 100.0)
            self.limits.weekly_limit_tokens = implied
            out["weekly_limit_tokens"] = round(implied, 1)
            out["weekly_measured_effective"] = round(used.effective_tokens, 1)
        if not out:
            out["error"] = "no observed percentage supplied - nothing to calibrate"
        self._save_state()
        return out

    # ------------------------------------------------------------------
    # The verdict
    # ------------------------------------------------------------------
    def check(self, now: Optional[datetime] = None) -> BudgetState:
        """Measure both windows and decide whether work may be placed."""
        limits = self.limits
        session = self.measure(limits.session_window, "session",
                               limits.session_limit_tokens, now=now)
        weekly = self.measure(limits.weekly_window, "weekly",
                              limits.weekly_limit_tokens, now=now)
        state = BudgetState(session=session, weekly=weekly)

        session_pct = session.pct_used
        weekly_pct = weekly.pct_used

        if session_pct is None and weekly_pct is None:
            state.status = BudgetStatus.UNKNOWN
            state.warnings.append(
                "No usage limit is configured, and the subscription limit is not "
                "readable from inside a session. Consumption below is measured and "
                "real; the percentages are not computable. Run /usage in your "
                "Claude Code client and calibrate with the figure it reports.")
            state.reasons.append(
                f"measured this session window: {session.effective_tokens:,.0f} "
                f"effective tokens over {session.turns} turns")
            return state

        # The binding constraint is whichever window is furthest along.
        status = BudgetStatus.OK
        if weekly_pct is not None:
            if weekly_pct >= limits.weekly_pause_at:
                status = BudgetStatus.PAUSED
                state.reasons.append(
                    f"weekly usage {weekly_pct:.1f}% is at or past the "
                    f"{limits.weekly_pause_at:.0f}% pause threshold - placing no new "
                    "work until the weekly limit refreshes")
            elif weekly_pct >= limits.weekly_slow_at:
                status = BudgetStatus.SLOW_DOWN
                state.reasons.append(
                    f"weekly usage {weekly_pct:.1f}% is past the "
                    f"{limits.weekly_slow_at:.0f}% threshold - conserving")
        if session_pct is not None:
            if session_pct >= limits.session_pause_at:
                status = BudgetStatus.PAUSED
                state.reasons.append(
                    f"session usage {session_pct:.1f}% is at or past the "
                    f"{limits.session_pause_at:.0f}% pause threshold - placing no new "
                    "work until the session limit refreshes")
            elif (session_pct >= limits.session_slow_at
                  and status is not BudgetStatus.PAUSED):
                status = BudgetStatus.SLOW_DOWN
                state.reasons.append(
                    f"session usage {session_pct:.1f}% is past the "
                    f"{limits.session_slow_at:.0f}% threshold - conserving")

        if status is BudgetStatus.OK:
            parts = []
            if session_pct is not None:
                parts.append(f"session {session_pct:.1f}%")
            if weekly_pct is not None:
                parts.append(f"weekly {weekly_pct:.1f}%")
            state.reasons.append("within budget: " + ", ".join(parts))

        if session_pct is None:
            state.warnings.append(
                "session limit not configured - only the weekly window is gated")
        if weekly_pct is None:
            state.warnings.append(
                "weekly limit not configured - only the session window is gated")

        state.status = status
        self._save_state()
        return state

    def gate(self, now: Optional[datetime] = None) -> Tuple[bool, BudgetState]:
        """``(may_place_work, state)`` - the call a dispatcher makes."""
        state = self.check(now=now)
        return state.may_place_work, state

    def budget_multiplier(self, now: Optional[datetime] = None) -> float:
        """How much of normal workload to place, in [0, 1].

        Used to scale a sweep down rather than stopping outright: past the slow
        threshold the team keeps working, but smaller.
        """
        state = self.check(now=now)
        if state.status is BudgetStatus.PAUSED:
            return 0.0
        if state.status is BudgetStatus.SLOW_DOWN:
            return 0.4
        return 1.0

    # ------------------------------------------------------------------
    # Persistence, so a calibration survives a restart
    # ------------------------------------------------------------------
    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_path)) or ".",
                        exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as handle:
                json.dump({"limits": self.limits.to_dict(),
                           "weights": self.weights}, handle, indent=2)
        except OSError:
            pass

    def _load_state(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        stored = data.get("limits") or {}
        for attr, key in (("session_limit_tokens", "session_limit_tokens"),
                          ("weekly_limit_tokens", "weekly_limit_tokens"),
                          ("session_slow_at", "session_slow_at_pct"),
                          ("session_pause_at", "session_pause_at_pct"),
                          ("weekly_slow_at", "weekly_slow_at_pct"),
                          ("weekly_pause_at", "weekly_pause_at_pct")):
            if stored.get(key) is not None:
                setattr(self.limits, attr, stored[key])
        if isinstance(data.get("weights"), dict):
            self.weights.update(data["weights"])

    def __repr__(self) -> str:
        known = ("calibrated" if self.limits.session_limit_tokens
                 or self.limits.weekly_limit_tokens else "UNCALIBRATED")
        return f"<BudgetMonitor {known} root={self.transcript_root}>"
