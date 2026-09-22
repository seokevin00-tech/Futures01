"""Strategy primitives: conditions, exit models and the Strategy object.

A strategy here is a *composition*, not a hand-written function:

    Strategy = [signal conditions] + [filter conditions] + exit model + scope

That structure is what makes the combinatorial research in ``combinator.py``
possible - the system can enumerate and test thousands of confluence
combinations because every strategy is assembled from the same typed parts,
and every part is independently testable.

Two rules are enforced rather than trusted:

* A strategy fires only when every signal condition agrees on a direction.
  Conditions that disagree cancel the setup rather than being out-voted.
* Stops and targets are computed from data available at the signal bar only.
  ``ExitModel`` never sees a future bar.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import (Any, Callable, Dict, FrozenSet, Iterable, List, Optional,
                    Sequence, Tuple)

from ..config import ContractSpec, get_contract, tf_label
from ..features import FeatureSnapshot, TFSnapshot
from ..schema import Direction, Evidence, MarketRegime
from ..timeutil import to_et

__all__ = [
    "ConditionKind", "ConditionResult", "Condition", "StopKind", "TargetKind",
    "ExitModel",
    "StrategyFilters", "StrategySignal", "Strategy",
    "CONDITION_ERRORS", "condition_errors", "reset_condition_errors",
]

#: ``(condition name, exception type) -> count`` for every exception
#: :meth:`Condition.evaluate` has swallowed in this process. Purely
#: observational - nothing reads it to make a decision, so it cannot change a
#: result - but it is the difference between a broken condition reporting "0%
#: trigger rate" and reporting "raised on all 40,000 evaluations".
CONDITION_ERRORS: Dict[Tuple[str, str], int] = {}


def condition_errors() -> Dict[Tuple[str, str], int]:
    """A copy of the swallowed-exception tally."""
    return dict(CONDITION_ERRORS)


def reset_condition_errors() -> None:
    """Clear the tally - call before a sweep whose errors you want to attribute."""
    CONDITION_ERRORS.clear()


class ConditionKind(str, Enum):
    """A condition either proposes a direction or vetoes the setup."""

    SIGNAL = "SIGNAL"      # contributes a direction; all signals must agree
    FILTER = "FILTER"      # direction-agnostic gate; must pass


@dataclass(frozen=True)
class ConditionResult:
    """Outcome of evaluating one condition on one bar."""

    triggered: bool
    direction: Direction = Direction.NEUTRAL
    detail: str = ""
    value: Any = None
    strength: float = 1.0          # 0-1, how emphatically the condition fired

    @staticmethod
    def no() -> "ConditionResult":
        return ConditionResult(False)

    @staticmethod
    def yes(direction: Direction = Direction.NEUTRAL, detail: str = "",
            value: Any = None, strength: float = 1.0) -> "ConditionResult":
        return ConditionResult(True, direction, detail, value, strength)


#: Signature every condition function implements.
ConditionFn = Callable[[FeatureSnapshot, int], ConditionResult]


@dataclass(frozen=True)
class Condition:
    """A named, reusable market condition evaluated on one timeframe."""

    name: str
    group: str
    fn: ConditionFn
    kind: ConditionKind = ConditionKind.SIGNAL
    description: str = ""
    #: Timeframe override. ``None`` means "the strategy's primary timeframe".
    timeframe: Optional[int] = None
    #: Minimum bars of history the condition needs before it can be trusted.
    warmup_bars: int = 50

    def bind(self, timeframe: int) -> "Condition":
        """Return a copy of this condition pinned to a specific timeframe."""
        return replace(self, timeframe=timeframe)

    def evaluate(self, snap: FeatureSnapshot, default_tf: int,
                 cache: Optional[Dict[Tuple[str, int], ConditionResult]] = None
                 ) -> ConditionResult:
        """Evaluate on one bar, optionally memoising through ``cache``.

        A portfolio sweep evaluates thousands of strategies against the same
        bar, and they share conditions heavily. The cache turns that from
        (strategies x conditions) evaluations per bar into (distinct conditions
        x timeframes) - two orders of magnitude on a realistic sweep. The key
        includes the timeframe because the same condition bound to 5m and 15m
        is genuinely two different computations.
        """
        tf = self.timeframe or default_tf
        key = (self.name, tf)
        if cache is not None:
            hit = cache.get(key)
            if hit is not None:
                return hit
        if snap.tf(tf) is None:
            res = ConditionResult.no()
        else:
            try:
                res = self.fn(snap, tf)
            except (TypeError, ValueError, ZeroDivisionError, KeyError,
                    IndexError) as exc:
                # A condition that cannot be computed is not a condition that
                # fired. Swallowing this keeps one bad bar from aborting a
                # 4,000-strategy sweep; the backtest simply records no signal.
                #
                # But swallowing silently cannot tell "this bar is degenerate"
                # from "this code is wrong", and the difference is the whole
                # result: fifteen conditions once called fmt_price with the
                # wrong second argument, raised on every single bar, and were
                # recorded as confluences with a 0% trigger rate instead of as
                # broken. So the guard stays and the count is kept. One raise
                # in a hundred thousand is a degenerate bar; a raise on every
                # evaluation is a defect, and now it is visible without
                # re-running the sweep under a debugger.
                CONDITION_ERRORS[(self.name, type(exc).__name__)] = (
                    CONDITION_ERRORS.get((self.name, type(exc).__name__), 0) + 1)
                res = ConditionResult.no()
        if cache is not None:
            cache[key] = res
        return res

    @property
    def label(self) -> str:
        return f"{self.name}@{tf_label(self.timeframe)}" if self.timeframe else self.name

    def __str__(self) -> str:
        return self.label


# --------------------------------------------------------------------------
# Exits
# --------------------------------------------------------------------------

class TargetKind(str, Enum):
    """Where the target comes from.

    ``R_MULTIPLE`` is the default and was until now the only option: targets
    are multiples of the stop distance. That is self-consistent and it quietly
    defeats the entire point of honing an entry on a lower timeframe - tighten
    the stop and every target moves proportionally closer, so a 4-hour thesis
    entered on a 15-minute trigger does not capture the 4-hour move, it
    captures a fifteen-minute-sized version of it. Measured: dropping the entry
    from 240m to 15m changed realised reward:risk from 0.87 to 0.91, when the
    ATR arithmetic said it should have gone to roughly 4:1.

    The anchored kinds fix that by deriving the target from the ANCHOR
    timeframe in price, independent of the stop. Then a tight stop genuinely
    buys reward:risk, because the objective stays where the thesis put it.
    """

    R_MULTIPLE = "R_MULTIPLE"              # multiples of the stop distance
    ANCHOR_ATR = "ANCHOR_ATR"              # multiples of the ANCHOR timeframe's ATR
    ANCHOR_STRUCTURE = "ANCHOR_STRUCTURE"  # the anchor timeframe's own swing objective


class StopKind(str, Enum):
    ATR = "ATR"                # N x ATR from entry
    STRUCTURE = "STRUCTURE"    # beyond the last confirmed swing, padded
    FIXED_TICKS = "FIXED_TICKS"
    VWAP_BAND = "VWAP_BAND"    # beyond the opposing VWAP band
    RANGE = "RANGE"            # beyond the opening range / prior bar range


@dataclass(frozen=True)
class ExitModel:
    """How a strategy defines its stop, targets and time limit.

    Targets are expressed in R (multiples of the stop distance) so that a
    strategy's statistics are comparable across symbols and volatility regimes,
    and so that position sizing has a single unambiguous risk unit.
    """

    stop_kind: StopKind = StopKind.ATR
    stop_mult: float = 1.5
    #: Where targets come from. See :class:`TargetKind`.
    target_kind: TargetKind = TargetKind.R_MULTIPLE
    #: For ANCHOR_ATR: multiples of the anchor timeframe's ATR.
    #: For ANCHOR_STRUCTURE: fraction of the distance to the anchor swing.
    anchor_mult: Tuple[float, ...] = (1.0, 2.0, 3.0)
    #: Reject the setup when the anchored target is closer than this in R.
    #: Without it an anchored target can land inside the stop distance and the
    #: trade becomes a negative-expectancy coin flip that still "fired".
    min_reward_risk: float = 1.5
    stop_pad_ticks: int = 2            # buffer beyond a structural level
    targets_r: Tuple[float, ...] = (1.0, 2.0, 3.0)
    #: Fraction of the position taken off at each target. Must sum to <= 1.
    scale_out: Tuple[float, ...] = (0.5, 0.3, 0.2)
    breakeven_at_r: Optional[float] = 1.0
    trail_atr_mult: Optional[float] = None
    time_stop_bars: Optional[int] = 60
    #: Exit at the session close regardless - an intraday system should not
    #: carry overnight gap risk it never measured.
    exit_at_session_close: bool = True

    def __post_init__(self) -> None:
        if self.stop_mult <= 0:
            raise ValueError("stop_mult must be positive")
        if not self.targets_r:
            raise ValueError("at least one target is required")
        if any(t <= 0 for t in self.targets_r):
            raise ValueError("targets_r must all be positive")
        if list(self.targets_r) != sorted(self.targets_r):
            raise ValueError("targets_r must be ascending")
        if sum(self.scale_out) > 1.0 + 1e-9:
            raise ValueError(f"scale_out sums to {sum(self.scale_out)}, must be <= 1")

    @property
    def label(self) -> str:
        t = "/".join(f"{t:g}" for t in self.targets_r)
        return f"{self.stop_kind.value}x{self.stop_mult:g}->{t}R"

    def stop_price(self, snap: FeatureSnapshot, tf: int, direction: Direction,
                   entry: float, spec: ContractSpec) -> Optional[float]:
        """Compute the stop from information available at the signal bar.

        Returns ``None`` when the stop cannot be placed - an unmeasurable stop
        means an unmeasurable risk, and the trade is simply not taken.
        """
        s = snap.tf(tf)
        if s is None:
            return None
        sign = direction.sign
        if sign == 0:
            return None
        pad = self.stop_pad_ticks * spec.tick_size

        if self.stop_kind is StopKind.ATR:
            a = s["atr"]
            if not a:
                return None
            dist = self.stop_mult * a
        elif self.stop_kind is StopKind.STRUCTURE:
            level = s.last_swing_low if sign > 0 else s.last_swing_high
            if level is None:
                return None
            dist = abs(entry - level) * self.stop_mult + pad
        elif self.stop_kind is StopKind.FIXED_TICKS:
            dist = self.stop_mult * spec.tick_size
        elif self.stop_kind is StopKind.VWAP_BAND:
            band = s["vwap_l1"] if sign > 0 else s["vwap_u1"]
            if band is None:
                return None
            dist = abs(entry - band) * self.stop_mult + pad
        elif self.stop_kind is StopKind.RANGE:
            orr = snap.opening_range
            if orr is None or orr.size <= 0:
                a = s["atr"]
                if not a:
                    return None
                dist = self.stop_mult * a
            else:
                dist = orr.size * self.stop_mult * 0.5
        else:
            return None

        # A stop inside the noise floor is not a stop, it is a donation.
        min_dist = spec.min_stop_ticks * spec.tick_size
        dist = max(dist, min_dist)
        raw = entry - sign * dist
        return spec.round_to_tick(raw)

    def target_prices(self, entry: float, stop: float, direction: Direction,
                      spec: ContractSpec, *,
                      snap: Optional[FeatureSnapshot] = None,
                      anchor_tf: Optional[int] = None) -> List[float]:
        """Target prices for this setup.

        ``snap``/``anchor_tf`` are only consulted by the anchored kinds, and an
        anchored kind falls back to R multiples when the anchor data is not
        available - a missing ATR must not silently produce a target at the
        entry price.
        """
        risk = abs(entry - stop)
        sign = direction.sign

        if (self.target_kind is not TargetKind.R_MULTIPLE
                and snap is not None and anchor_tf is not None):
            anchored = self._anchored_targets(entry, direction, snap, anchor_tf, spec)
            if anchored:
                return anchored

        return [spec.round_to_tick(entry + sign * risk * r) for r in self.targets_r]

    def _anchored_targets(self, entry: float, direction: Direction,
                          snap: "FeatureSnapshot", anchor_tf: int,
                          spec: ContractSpec) -> List[float]:
        s = snap.tf(anchor_tf)
        if s is None:
            return []
        sign = direction.sign

        if self.target_kind is TargetKind.ANCHOR_ATR:
            a = s.get("atr")
            if not a:
                return []
            return [spec.round_to_tick(entry + sign * a * m) for m in self.anchor_mult]

        # ANCHOR_STRUCTURE: trade towards the anchor's own swing objective -
        # the level the thesis is actually about - taking fractions of the way
        # there so the runner has somewhere to run to.
        objective = s.last_swing_high if sign > 0 else s.last_swing_low
        if objective is None:
            return []
        span = (objective - entry) * sign
        if span <= 0:
            return []                      # the objective is already behind us
        out = []
        for m in self.anchor_mult:
            frac = min(1.0, m / max(self.anchor_mult))
            out.append(spec.round_to_tick(entry + sign * span * frac))
        return sorted(set(out), reverse=sign < 0)


# --------------------------------------------------------------------------
# Scope filters
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyFilters:
    """Where and when a strategy is permitted to trade.

    Every dimension here becomes a slice in the performance database, which is
    what lets the system answer "this works in the morning but not at lunch"
    from measurement rather than from folklore.
    """

    sessions: Optional[FrozenSet[str]] = None        # None = any session
    regimes: Optional[FrozenSet[str]] = None
    volatility: Optional[FrozenSet[str]] = None
    days_of_week: Optional[FrozenSet[str]] = None
    rth_only: bool = True
    min_minutes_since_open: Optional[float] = None
    max_minutes_since_open: Optional[float] = None
    require_alignment: Optional[float] = None        # |alignment| threshold

    def passes(self, snap: FeatureSnapshot) -> Tuple[bool, str]:
        if self.rth_only and not snap.is_rth:
            return False, "outside RTH"
        if self.sessions and snap.session not in self.sessions:
            return False, f"session {snap.session} not permitted"
        if self.regimes and snap.regime.regime not in self.regimes:
            return False, f"regime {snap.regime.regime} not permitted"
        if self.volatility and snap.regime.volatility not in self.volatility:
            return False, f"volatility {snap.regime.volatility} not permitted"
        if self.days_of_week and snap.day_of_week not in self.days_of_week:
            return False, f"day {snap.day_of_week} not permitted"
        mso = snap.minutes_since_open
        if self.min_minutes_since_open is not None and mso < self.min_minutes_since_open:
            return False, "too early in session"
        if self.max_minutes_since_open is not None and mso > self.max_minutes_since_open:
            return False, "too late in session"
        if self.require_alignment is not None:
            if abs(snap.alignment()) < self.require_alignment:
                return False, "timeframes not aligned"
        return True, ""

    def label(self) -> str:
        bits = []
        if self.sessions:
            bits.append("+".join(sorted(self.sessions)))
        if self.regimes:
            bits.append("+".join(sorted(self.regimes)))
        if self.volatility:
            bits.append("vol:" + "+".join(sorted(self.volatility)))
        if self.require_alignment:
            bits.append(f"align>={self.require_alignment:g}")
        return ",".join(bits) or "any"


# --------------------------------------------------------------------------
# Signals and strategies
# --------------------------------------------------------------------------

@dataclass
class StrategySignal:
    """A strategy's proposed trade at one bar, before risk sizing."""

    strategy_id: str
    strategy_name: str
    group: str
    symbol: str
    ts: Any
    bar_index: int
    direction: Direction
    entry: float
    stop: float
    targets: List[float]
    primary_tf: int
    timeframes: List[int]
    confluences: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    regime: str = "UNKNOWN"
    volatility: str = "NORMAL"
    session: str = ""
    time_bucket: str = ""
    day_of_week: str = ""
    strength: float = 0.0
    invalidation: str = ""

    @property
    def risk_points(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_risk(self) -> float:
        if not self.targets or self.risk_points <= 0:
            return 0.0
        return abs(self.targets[-1] - self.entry) / self.risk_points

    @property
    def first_target_rr(self) -> float:
        if not self.targets or self.risk_points <= 0:
            return 0.0
        return abs(self.targets[0] - self.entry) / self.risk_points

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "strategy_name": self.strategy_name,
            "group": self.group, "symbol": self.symbol,
            "ts": to_et(self.ts).isoformat() if self.ts else None,
            "direction": self.direction.value, "entry": self.entry, "stop": self.stop,
            "targets": self.targets, "primary_tf": self.primary_tf,
            "confluences": self.confluences, "conflicts": self.conflicts,
            "regime": self.regime, "volatility": self.volatility,
            "session": self.session, "strength": round(self.strength, 3),
            "reward_risk": round(self.reward_risk, 3),
        }


@dataclass
class Strategy:
    """A fully specified, testable trading strategy."""

    name: str
    symbol: str
    group: str
    primary_tf: int
    conditions: Tuple[Condition, ...]
    exit: ExitModel = field(default_factory=ExitModel)
    filters: StrategyFilters = field(default_factory=StrategyFilters)
    allowed_directions: Tuple[Direction, ...] = (Direction.LONG, Direction.SHORT)
    confirm_tfs: Tuple[int, ...] = ()
    #: Timeframe the ENTRY is located on, when it differs from the timeframe
    #: the thesis is built on.
    #:
    #: ``primary_tf`` is the anchor: where the confluence is read and the
    #: target lives. ``execution_tf`` is where the trade is actually placed,
    #: and - this is the whole point - where the STOP is measured. A 4-hour
    #: thesis stopped on 4-hour structure risks a 4-hour ATR to make a 4-hour
    #: move, which is about 1:1 before costs. The same thesis entered on a
    #: 15-minute trigger risks a 15-minute ATR for the same move: measured on
    #: this desk's data, the 240m ATR is a median 4.5x the 15m ATR, so the
    #: reward-to-risk goes from 1.0:1 to roughly 4.3:1 without the target
    #: moving at all.
    #:
    #: The catch, and why ``trigger_conditions`` exists: a tight stop under a
    #: big thesis is only an edge if the entry is located somewhere the noise
    #: does not reach. Otherwise it is the same trade with a stop that gets
    #: hit more often, which is strictly worse.
    execution_tf: Optional[int] = None
    #: Conditions evaluated on ``execution_tf`` that must fire AND agree with
    #: the anchor's direction before the trade is taken. This is the "hone in
    #: on the location" half: the anchor says which way and roughly where, the
    #: trigger says exactly when.
    trigger_conditions: Tuple[Condition, ...] = ()
    description: str = ""
    _id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.conditions:
            raise ValueError(f"{self.name}: a strategy needs at least one condition")
        if not any(c.kind is ConditionKind.SIGNAL for c in self.conditions):
            raise ValueError(
                f"{self.name}: at least one SIGNAL condition is required - "
                "a strategy of pure filters has no entry trigger")
        if self.trigger_conditions and self.execution_tf is None:
            raise ValueError(
                f"{self.name}: trigger conditions need an execution_tf to "
                "evaluate on, otherwise they silently re-read the anchor")
        if self.execution_tf is not None and self.execution_tf > self.primary_tf:
            raise ValueError(
                f"{self.name}: execution_tf {self.execution_tf}m is coarser "
                f"than the {self.primary_tf}m anchor. Entering on a slower "
                "timeframe than the thesis widens the stop, which is the "
                "opposite of the point.")

    # ---- identity ---------------------------------------------------
    @property
    def strategy_id(self) -> str:
        """Stable content hash. Two identical strategies get the same id, so
        the performance database cannot accumulate duplicate rows for what is
        really the same rule set."""
        if self._id is None:
            parts = [
                self.symbol, str(self.primary_tf), self.group,
                "|".join(sorted(c.label for c in self.conditions)),
                self.exit.label, self.filters.label(),
                "".join(sorted(d.value for d in self.allowed_directions)),
                ",".join(str(t) for t in sorted(self.confirm_tfs)),
                str(self.execution_tf or ""),
                "|".join(sorted(c.label for c in self.trigger_conditions)),
            ]
            digest = hashlib.sha1("::".join(parts).encode()).hexdigest()[:12]
            object.__setattr__(self, "_id", f"{self.symbol}-{self.primary_tf}m-{digest}")
        return self._id

    @property
    def timeframes(self) -> List[int]:
        tfs = {self.primary_tf, *self.confirm_tfs}
        tfs.update(c.timeframe for c in self.conditions if c.timeframe)
        if self.execution_tf:
            tfs.add(self.execution_tf)
        tfs.update(c.timeframe for c in self.trigger_conditions if c.timeframe)
        return sorted(t for t in tfs if t)

    @property
    def entry_tf(self) -> int:
        """Where the trade is placed and the stop is measured."""
        return self.execution_tf or self.primary_tf

    @property
    def signal_conditions(self) -> List[Condition]:
        return [c for c in self.conditions if c.kind is ConditionKind.SIGNAL]

    @property
    def filter_conditions(self) -> List[Condition]:
        return [c for c in self.conditions if c.kind is ConditionKind.FILTER]

    @property
    def indicators(self) -> List[str]:
        return sorted({c.group for c in self.conditions})

    # ---- evaluation --------------------------------------------------
    def evaluate(self, snap: FeatureSnapshot,
                 cache: Optional[Dict[Tuple[str, int], ConditionResult]] = None
                 ) -> Optional[StrategySignal]:
        """Evaluate the strategy at one bar. Returns None when it does not fire.

        Uses only ``snap``, which by construction contains no data from after
        its own bar.
        """
        ok, why = self.filters.passes(snap)
        if not ok:
            return None

        spec = snap.spec
        confluences: List[str] = []
        conflicts: List[str] = []
        evidence: List[Evidence] = []
        strength_sum = 0.0

        for cond in self.filter_conditions:
            res = cond.evaluate(snap, self.primary_tf, cache)
            if not res.triggered:
                return None
            confluences.append(f"{cond.label}: {res.detail}" if res.detail else cond.label)

        direction: Optional[Direction] = None
        for cond in self.signal_conditions:
            res = cond.evaluate(snap, self.primary_tf, cache)
            if not res.triggered or res.direction is Direction.NEUTRAL:
                return None
            if direction is None:
                direction = res.direction
            elif res.direction is not direction:
                # Signals disagree - this is not a setup, it is a coin flip.
                return None
            strength_sum += max(0.0, min(1.0, res.strength))
            confluences.append(f"{cond.label}: {res.detail}" if res.detail else cond.label)
            evidence.append(Evidence(
                kind="indicator", name=cond.name, value=res.value,
                timeframe=cond.timeframe or self.primary_tf, detail=res.detail,
                supports=res.direction, weight=res.strength, source=self.strategy_id))

        if direction is None or direction not in self.allowed_directions:
            return None

        # ---- hone the entry on the execution timeframe -------------------
        # The anchor has said which way. The trigger says whether price is
        # somewhere worth risking a tight stop on, and it must AGREE - a
        # trigger firing the other way is the lower timeframe telling you the
        # location is wrong, not a detail to average out.
        for cond in self.trigger_conditions:
            res = cond.evaluate(snap, self.entry_tf, cache)
            if not res.triggered:
                return None
            if (cond.kind is ConditionKind.SIGNAL
                    and res.direction is not direction):
                conflicts.append(
                    f"{tf_label(self.entry_tf)} trigger {cond.name} disagrees")
                return None
            confluences.append(f"{cond.label} [{tf_label(self.entry_tf)}]: {res.detail}"
                               if res.detail else cond.label)

        entry = spec.round_to_tick(snap.price)
        # The stop is measured where the trade is placed, not where the thesis
        # was formed. This is the mechanism that expands reward-to-risk.
        stop = self.exit.stop_price(snap, self.entry_tf, direction, entry, spec)
        if stop is None or abs(entry - stop) < spec.tick_size:
            return None
        # A stop inside the contract's own noise floor is not a tight stop, it
        # is a coin flip with good manners. min_stop_ticks is per-contract
        # precisely so this check means something on both MGC and MNQ.
        if abs(entry - stop) < spec.min_stop_ticks * spec.tick_size:
            return None
        targets = self.exit.target_prices(entry, stop, direction, spec,
                                          snap=snap, anchor_tf=self.primary_tf)
        if not targets:
            return None
        # With an anchored target the reward is no longer guaranteed to exceed
        # the risk, so it has to be checked rather than assumed.
        risk = abs(entry - stop)
        reward = abs(targets[-1] - entry)
        if risk <= 0 or reward / risk < self.exit.min_reward_risk:
            return None

        # Record higher-timeframe disagreement as a conflict rather than hiding
        # it - the decision layer weighs conflicts explicitly.
        for tf in self.confirm_tfs:
            s = snap.tf(tf)
            if s is None:
                continue
            if direction is Direction.LONG and s.structure_trend == "DOWNTREND":
                conflicts.append(f"{tf_label(tf)} structure is DOWNTREND")
            elif direction is Direction.SHORT and s.structure_trend == "UPTREND":
                conflicts.append(f"{tf_label(tf)} structure is UPTREND")

        n_signals = max(1, len(self.signal_conditions))
        return StrategySignal(
            strategy_id=self.strategy_id, strategy_name=self.name, group=self.group,
            symbol=self.symbol, ts=snap.ts, bar_index=snap.base_index,
            direction=direction, entry=entry, stop=stop, targets=targets,
            primary_tf=self.primary_tf, timeframes=self.timeframes,
            confluences=confluences, conflicts=conflicts, evidence=evidence,
            regime=snap.regime.regime, volatility=snap.regime.volatility,
            session=snap.session, time_bucket=snap.time_bucket,
            day_of_week=snap.day_of_week,
            strength=strength_sum / n_signals,
            invalidation=self._invalidation_text(snap, direction, stop),
        )

    def _invalidation_text(self, snap: FeatureSnapshot, direction: Direction,
                           stop: float) -> str:
        side = "below" if direction is Direction.LONG else "above"
        return (f"{tf_label(self.primary_tf)} close {side} {stop:g}, or loss of "
                f"{'; '.join(c.name for c in self.signal_conditions[:2])}")

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "name": self.name, "symbol": self.symbol,
            "group": self.group, "primary_tf": self.primary_tf,
            "confirm_tfs": list(self.confirm_tfs),
            "conditions": [c.label for c in self.conditions],
            "signal_conditions": [c.label for c in self.signal_conditions],
            "filter_conditions": [c.label for c in self.filter_conditions],
            "exit": self.exit.label, "filters": self.filters.label(),
            "directions": [d.value for d in self.allowed_directions],
            "description": self.description,
        }

    def __repr__(self) -> str:
        return f"<Strategy {self.strategy_id} {self.name} {len(self.conditions)}c>"
