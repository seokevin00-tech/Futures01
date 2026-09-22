"""Systematic generation of confluence combinations.

The specification asks for thousands of *reasonable* combinations rather than a
blind cartesian product. Two rules do most of the work:

**Diversity.** Signal conditions are drawn from *distinct* condition groups. A
confluence of five momentum indicators is five restatements of one observation;
it will backtest beautifully and fail live. Requiring different groups means a
five-factor setup really is trend + structure + order flow + location + volume.

**Bounded, deterministic sampling.** The full product across conditions,
timeframes, exits and filters runs to millions. The generator enumerates in a
fixed order and samples with a fixed seed, so a research run is reproducible and
a strategy id is stable between runs.

Generation is not selection. Everything here is a *hypothesis*; only the
walk-forward and robustness suite in ``backtest/`` decides what survives.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple

from ..config import TIMEFRAME_GROUPS, get_contract
from ..schema import Direction
from .base import (Condition, ConditionKind, ExitModel, StopKind, Strategy,
                   StrategyFilters)
from .library import CONDITIONS, CONDITION_GROUPS, get_condition

__all__ = [
    "CombinationSpec", "StrategyTemplate", "TEMPLATES", "generate_combinations",
    "generate_strategies", "expand_exit_models", "DEFAULT_EXITS",
]


# --------------------------------------------------------------------------
# Exit model catalogue
# --------------------------------------------------------------------------

def expand_exit_models(*, include_structure: bool = True,
                       include_aggressive: bool = False) -> List[ExitModel]:
    """A spread of stop/target geometries to test against each rule set.

    Deliberately modest in size. Exit parameters are the easiest place to
    overfit - sweep fifty of them and one will look wonderful by chance - so the
    catalogue covers genuinely different *shapes* (tight/wide, scalp/runner)
    rather than a fine grid of one shape.
    """
    models = [
        # Balanced ATR stop, three scale-out targets.
        ExitModel(StopKind.ATR, 1.5, targets_r=(1.0, 2.0, 3.0),
                  scale_out=(0.5, 0.3, 0.2), breakeven_at_r=1.0, time_stop_bars=60),
        # Wider stop, fewer targets - fewer stop-outs, larger risk unit.
        ExitModel(StopKind.ATR, 2.5, targets_r=(1.0, 2.0),
                  scale_out=(0.6, 0.4), breakeven_at_r=1.0, time_stop_bars=90),
        # Tight stop, runner target - low win rate, high payoff.
        ExitModel(StopKind.ATR, 1.0, targets_r=(1.5, 3.0, 5.0),
                  scale_out=(0.4, 0.3, 0.3), breakeven_at_r=1.5, time_stop_bars=120),
        # Scalp: single target, no runner.
        ExitModel(StopKind.ATR, 1.2, targets_r=(1.2,), scale_out=(1.0,),
                  breakeven_at_r=None, time_stop_bars=30),
    ]
    if include_structure:
        models += [
            ExitModel(StopKind.STRUCTURE, 1.0, stop_pad_ticks=4,
                      targets_r=(1.0, 2.0, 3.0), scale_out=(0.5, 0.3, 0.2),
                      breakeven_at_r=1.0, time_stop_bars=80),
            ExitModel(StopKind.VWAP_BAND, 1.0, stop_pad_ticks=3,
                      targets_r=(1.0, 2.0), scale_out=(0.5, 0.5),
                      breakeven_at_r=1.0, time_stop_bars=60),
        ]
    if include_aggressive:
        models += [
            ExitModel(StopKind.ATR, 0.75, targets_r=(2.0, 4.0), scale_out=(0.5, 0.5),
                      breakeven_at_r=2.0, time_stop_bars=45),
            ExitModel(StopKind.RANGE, 1.0, targets_r=(1.0, 2.0, 3.5),
                      scale_out=(0.4, 0.3, 0.3), breakeven_at_r=1.0, time_stop_bars=100),
        ]
    return models


DEFAULT_EXITS = expand_exit_models()


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyTemplate:
    """A family of strategies sharing an idea, a shape and a scope.

    ``required_groups`` picks exactly one condition from each named group;
    ``optional_groups`` contributes 0..``max_optional`` further conditions from
    distinct groups. That is what produces diverse confluences.
    """

    group: str
    description: str
    required_groups: Tuple[str, ...]
    optional_groups: Tuple[str, ...] = ()
    max_optional: int = 2
    base_filters: Tuple[str, ...] = ("volatility_normal", "volume_not_thin")
    #: Filters the generator switches on and off, producing a variant with and
    #: a variant without each. A filter nailed into ``base_filters`` is an
    #: assumption; one enumerated here is a tested variable - which is what the
    #: specification asks for with "news conditions", and the only way to learn
    #: whether standing aside for a release actually helps this strategy.
    optional_filters: Tuple[str, ...] = ()
    max_optional_filters: int = 2
    filters: StrategyFilters = field(default_factory=StrategyFilters)
    exits: Tuple[ExitModel, ...] = ()
    directions: Tuple[Direction, ...] = (Direction.LONG, Direction.SHORT)
    #: Conditions that must NOT appear together - they encode the same idea.
    exclusive: Tuple[Tuple[str, ...], ...] = ()


#: The per-symbol strategy groups named in the specification. Each is tested
#: independently for every contract; nothing is shared between symbols.
TEMPLATES: Tuple[StrategyTemplate, ...] = (
    StrategyTemplate(
        group="TREND",
        description="Trend continuation: structure and momentum aligned with the regime",
        required_groups=("trend", "structure"),
        optional_groups=("momentum", "orderflow", "volume", "multitimeframe", "vwap",
                         "profile", "imbalance", "regime"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin", "regime_trending"),
        exits=tuple(expand_exit_models()[:3]),
        optional_filters=("outside_news_blackout", "no_imminent_release", "adx_trending", "efficiency_high", "away_from_zone"),
    ),
    StrategyTemplate(
        group="PULLBACK",
        description="Buy the dip inside an established trend",
        required_groups=("trend", "meanreversion"),
        optional_groups=("structure", "vwap", "orderflow", "multitimeframe",
                         "fibonacci", "supplydemand"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin", "mtf_not_conflicted"),
        exits=tuple(expand_exit_models()[:4]),
        optional_filters=("outside_news_blackout", "no_imminent_release", "no_recent_imbalance"),
    ),
    StrategyTemplate(
        group="VWAP",
        description="VWAP as the session's fair-value reference",
        required_groups=("vwap",),
        optional_groups=("orderflow", "momentum", "structure", "volume", "trend",
                         "profile"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin"),
        exits=tuple(expand_exit_models()),
        optional_filters=("outside_news_blackout", "no_imminent_release", "vwap_proximity"),
        exclusive=(("above_vwap", "vwap_proximity"),),
    ),
    StrategyTemplate(
        group="REVERSAL",
        description="Exhaustion and absorption against the prevailing move",
        required_groups=("meanreversion", "orderflow"),
        optional_groups=("momentum", "liquidity", "structure", "vwap",
                         "supplydemand", "profile", "fibonacci"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin", "avoid_lunch"),
        exits=tuple(expand_exit_models()[:4]),
        optional_filters=("outside_news_blackout", "no_imminent_release", "no_recent_imbalance"),
    ),
    StrategyTemplate(
        group="MOMENTUM",
        description="Momentum ignition confirmed by participation",
        required_groups=("momentum", "volume"),
        optional_groups=("trend", "orderflow", "structure", "multitimeframe",
                         "imbalance", "openinterest", "regime"),
        max_optional=3,
        base_filters=("volatility_normal",),
        exits=tuple(expand_exit_models()[:3]),
        # post_news_window is NOT offered here. It passes 0.66% of bars, so
        # the "with the filter" arm takes almost no trades - measured, 0 of 50
        # MOMENTUM strategies cleared the 30-trade floor with it, against 22 of
        # 50 without. An arm that cannot produce a sample is not a control, and
        # it costs a hypothesis to learn nothing. The question it was meant to
        # answer - is the post-release reaction tradeable - is better asked as
        # a slice over realised trades, where every trade contributes.
        optional_filters=("outside_news_blackout", "relative_volume_high",
                          "volume_surge", "oi_expanding"),
    ),
    StrategyTemplate(
        group="OPENING_RANGE",
        description="Opening-range breakout and failure",
        required_groups=("liquidity",),
        optional_groups=("volume", "orderflow", "momentum", "trend", "vwap",
                         "profile"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin", "opening_drive_window"),
        filters=StrategyFilters(rth_only=True, max_minutes_since_open=150),
        exits=tuple(expand_exit_models()),
        optional_filters=("outside_news_blackout", "no_imminent_release", "relative_volume_high"),
    ),
    StrategyTemplate(
        group="LIQUIDITY",
        description="Stop runs at reference levels, then reversion",
        required_groups=("liquidity",),
        optional_groups=("orderflow", "structure", "vwap", "momentum", "volume",
                         "supplydemand", "imbalance"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin", "after_opening_range"),
        exits=tuple(expand_exit_models()),
        optional_filters=("outside_news_blackout", "no_imminent_release", "power_hour"),
    ),
    StrategyTemplate(
        group="MEAN_REVERSION",
        description="Fade statistical extension in a ranging market",
        required_groups=("meanreversion",),
        optional_groups=("momentum", "vwap", "orderflow", "structure",
                         "profile", "fibonacci"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin", "regime_ranging"),
        exits=tuple(expand_exit_models()[:4]),
        optional_filters=("outside_news_blackout", "no_imminent_release", "away_from_hvn"),
    ),
    StrategyTemplate(
        group="BREAKOUT",
        description="Expansion out of compression",
        required_groups=("structure", "volume"),
        optional_groups=("trend", "momentum", "orderflow", "liquidity",
                         "imbalance", "profile", "openinterest"),
        max_optional=3,
        base_filters=("volatility_compressed", "volume_not_thin"),
        exits=tuple(expand_exit_models()[:3]),
        optional_filters=("outside_news_blackout", "no_imminent_release", "volatility_expanding", "volume_surge", "oi_expanding"),
    ),
    StrategyTemplate(
        group="MULTI_TIMEFRAME",
        description="Higher-timeframe alignment as the primary edge",
        required_groups=("multitimeframe", "structure"),
        optional_groups=("trend", "momentum", "vwap", "orderflow",
                         "fibonacci", "supplydemand"),
        max_optional=2,
        base_filters=("volatility_normal", "volume_not_thin"),
        exits=tuple(expand_exit_models()[:3]),
        optional_filters=("outside_news_blackout", "no_imminent_release", "adx_trending"),
    ),
    StrategyTemplate(
        group="VOLUME_PROFILE",
        description="Trade location against the prior session's volume distribution",
        required_groups=("profile",),
        optional_groups=("volume", "orderflow", "vwap", "structure", "trend",
                         "momentum"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin"),
        exits=tuple(expand_exit_models()),
        optional_filters=("outside_news_blackout", "no_imminent_release", "away_from_hvn", "open_outside_value"),
        # POC reversion and value-area breakout are opposite readings of the
        # same profile; a confluence containing both is incoherent, not strong.
        exclusive=(("poc_reversion", "value_area_breakout"),
                   ("value_area_edge", "value_area_breakout")),
    ),
    StrategyTemplate(
        group="SUPPLY_DEMAND",
        description="Reaction at zones a decisive move departed from",
        required_groups=("supplydemand",),
        optional_groups=("structure", "orderflow", "imbalance", "trend",
                         "momentum", "volume"),
        max_optional=3,
        base_filters=("volatility_normal", "volume_not_thin"),
        exits=tuple(expand_exit_models()),
        optional_filters=("outside_news_blackout", "no_imminent_release", "no_recent_imbalance"),
        exclusive=(("zone_touch", "away_from_zone"),
                   ("fresh_zone_approach", "away_from_zone")),
    ),
    StrategyTemplate(
        group="FIBONACCI",
        description="Retracement of the last confirmed leg, in the direction of the trend",
        required_groups=("fibonacci", "trend"),
        optional_groups=("structure", "momentum", "vwap", "orderflow",
                         "supplydemand"),
        max_optional=2,
        base_filters=("volatility_normal", "volume_not_thin"),
        exits=tuple(expand_exit_models()[:4]),
        optional_filters=("outside_news_blackout", "no_imminent_release", "fib_sr_confluence", "efficiency_high"),
        # A shallow retracement and a deep one are mutually exclusive prices,
        # and an extension is the opposite trade to either.
        exclusive=(("fib_golden_pocket", "fib_shallow_retrace"),
                   ("fib_golden_pocket", "fib_extension_reached"),
                   ("fib_shallow_retrace", "fib_extension_reached")),
    ),
)

TEMPLATES_BY_GROUP: Dict[str, StrategyTemplate] = {t.group: t for t in TEMPLATES}


# --------------------------------------------------------------------------
# Combination generation
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CombinationSpec:
    """One concrete confluence, before it becomes a :class:`Strategy`."""

    symbol: str
    group: str
    primary_tf: int
    confirm_tfs: Tuple[int, ...]
    signal_conditions: Tuple[str, ...]
    filter_conditions: Tuple[str, ...]
    exit_index: int
    filters: StrategyFilters

    def describe(self) -> str:
        return (f"{self.symbol} {self.group} {self.primary_tf}m: "
                + " + ".join(self.signal_conditions))


def _signal_pools(template: StrategyTemplate) -> Tuple[List[List[str]], List[List[str]]]:
    """Condition names available per required / optional group, signals only."""
    def pool(group: str) -> List[str]:
        return sorted(n for n in CONDITION_GROUPS.get(group, [])
                      if CONDITIONS[n].kind is ConditionKind.SIGNAL)
    required = [pool(g) for g in template.required_groups]
    optional = [pool(g) for g in template.optional_groups]
    return [p for p in required if p], [p for p in optional if p]


#: Condition pairs that are the same statement wearing two names, enforced
#: whatever template draws them.
#:
#: The diversity rule assumes different condition *groups* mean different
#: evidence. Three reviewers measured the first pair independently and found
#: the same thing: on 20 days of 15-minute bars they co-fire on 3,980 bars and
#: have **never once disagreed on direction**. Both say "the close is beyond a
#: prior-session reference level", against two reference levels that mostly
#: coincide - so a confluence holding both counts one observation twice and
#: looks better corroborated than it is, which is precisely the failure the
#: diversity rule exists to prevent. ``exclusive`` is per-template and cannot
#: express that, because the property belongs to the conditions.
#:
#: Deliberately short. Several further pairs measure as near-duplicates on
#: synthetic data (the trend/momentum block especially), but the generator's
#: own trend persistence is a plausible cause and a structural explanation is
#: the bar for entry here. Use :func:`measure_condition_overlap` to rebuild
#: the candidate list on real data rather than promoting these on a hunch.
GLOBAL_EXCLUSIVE: Tuple[Tuple[str, str], ...] = (
    # Same statement, two reference levels that mostly coincide.
    ("value_area_breakout", "prior_day_breakout"),
    # Both are "price is stretched to an extreme in volatility units"; the
    # oscillator and the channel disagree on almost nothing.
    ("stoch_extreme", "keltner_outside"),
    ("stoch_extreme", "bollinger_mean_pull"),
)


def measure_condition_overlap(snapshots, timeframe: int, *,
                              min_cofires: int = 50):
    """Trigger overlap between every pair of SIGNAL conditions.

    Returns ``(a, b, jaccard, agreement, cofires)`` sorted by agreement then
    overlap, so a desk can rebuild :data:`GLOBAL_EXCLUSIVE` from its own data
    instead of inheriting a list measured on someone else's.
    """
    fired: Dict[str, Dict[int, str]] = {}
    for name, cond in CONDITIONS.items():
        if cond.kind is not ConditionKind.SIGNAL:
            continue
        hits: Dict[int, str] = {}
        for i, snap in enumerate(snapshots):
            res = cond.evaluate(snap, timeframe)
            if res.triggered:
                hits[i] = res.direction.value
        fired[name] = hits

    out = []
    names = sorted(fired)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            A, B = fired[a], fired[b]
            inter = set(A) & set(B)
            if len(inter) < min_cofires:
                continue
            union = set(A) | set(B)
            agree = sum(1 for k in inter if A[k] == B[k]) / len(inter)
            out.append((a, b, len(inter) / len(union), agree, len(inter)))
    out.sort(key=lambda r: (-r[3], -r[2]))
    return out


def _filter_sets(template: StrategyTemplate) -> List[Tuple[str, ...]]:
    """Every filter set to test: the base filters, plus each subset of the
    optional ones. Index 0 is always the bare base set, so "with the news
    filter" always has a like-for-like control to be compared against."""
    known = tuple(f for f in template.optional_filters if f in CONDITIONS)
    base = tuple(template.base_filters)
    # One optional filter at a time, each against the same bare control.
    # Enumerating every subset was combinatorial in the filter count and
    # bought nothing the paired test needs: what the desk wants to know is
    # whether THIS filter helps, which needs exactly two arms. Subsets cost
    # budget that comes straight out of the number of rule sets tested.
    return [base] + [base + (f,) for f in known]


def _violates_exclusive(names: Sequence[str],
                        exclusive: Sequence[Sequence[str]]) -> bool:
    s = set(names)
    return any(len(s.intersection(pair)) > 1
               for pair in tuple(exclusive) + GLOBAL_EXCLUSIVE)


def generate_combinations(
    symbol: str,
    timeframes: Sequence[int],
    *,
    groups: Optional[Sequence[str]] = None,
    confirm_map: Optional[Dict[int, Tuple[int, ...]]] = None,
    max_total: int = 4_000,
    max_per_template: Optional[int] = None,
    seed: int = 20260922,
    min_signals: int = 2,
    max_signals: int = 4,
) -> List[CombinationSpec]:
    """Enumerate confluence specifications for one symbol.

    ``confirm_map`` maps a primary timeframe to the higher timeframes used for
    confirmation; it defaults to the next two timeframes up, which is what makes
    the generated set test *timeframe groups* and not only single timeframes.
    """
    get_contract(symbol)                    # fail fast on an unknown contract
    tfs = sorted({int(t) for t in timeframes})
    if not tfs:
        raise ValueError("at least one timeframe is required")
    # A caller that names groups gets exactly those. One that does not gets
    # this contract's profile if it has one, and the full set if it does not -
    # so an unprofiled symbol is tested broadly rather than silently narrowed
    # by a prior written for something else.
    if groups:
        wanted = list(groups)
    else:
        from .profiles import groups_for
        wanted = list(groups_for(symbol, [t.group for t in TEMPLATES])
                      or [t.group for t in TEMPLATES])

    if confirm_map is None:
        confirm_map = {}
        for i, tf in enumerate(tfs):
            confirm_map[tf] = tuple(tfs[i + 1:i + 3])

    rng = random.Random(f"{seed}:{symbol}")
    out: List[CombinationSpec] = []
    per_template = max_per_template or max(1, max_total // max(1, len(wanted)))

    for group in wanted:
        template = TEMPLATES_BY_GROUP.get(group)
        if template is None:
            raise KeyError(f"Unknown strategy group {group!r}")
        required, optional = _signal_pools(template)
        if not required:
            continue
        filter_sets = _filter_sets(template)

        # Enumerate RULE SETS - everything except the filter variant - and
        # sample those, then emit every filter variant of each one sampled.
        #
        # Sampling finished specs instead looks equivalent and is not. Each
        # rule set appears in the pool once per filter variant, the pool runs
        # to millions and the budget is thousands, so drawing both members of
        # a pair is a coincidence: measured at 0.1% of filtered specs. The
        # optional-filter dimension exists precisely so "with the news filter"
        # has a like-for-like control, and a control that is never drawn is
        # not a control. Sampling the rule set keeps the pair intact by
        # construction.
        rule_sets: List[Tuple[Tuple[str, ...], int, int]] = []
        for base_combo in itertools.product(*required):
            n_opt_max = min(template.max_optional, max_signals - len(base_combo))
            for n_opt in range(0, max(0, n_opt_max) + 1):
                for opt_groups in itertools.combinations(range(len(optional)), n_opt):
                    for opt_choice in itertools.product(*(optional[g] for g in opt_groups)):
                        names = tuple(base_combo) + tuple(opt_choice)
                        if len(names) < min_signals or len(names) > max_signals:
                            continue
                        if len(set(names)) != len(names):
                            continue
                        if _violates_exclusive(names, template.exclusive):
                            continue
                        # Exclusions are checked again per filter set below,
                        # because a declared pair can name a FILTER and the
                        # signal-only check above cannot see one.
                        for tf in tfs:
                            for ei in range(len(template.exits) or 1):
                                rule_sets.append((tuple(sorted(names)), tf, ei))

        # The budget is spent in whole pairs, so a template with four filter
        # variants tests a quarter as many rule sets rather than breaking the
        # pairing to fit.
        budget = max(1, per_template // max(1, len(filter_sets)))
        if len(rule_sets) > budget:
            rule_sets = rng.sample(rule_sets, budget)
        rule_sets.sort()

        for names, tf, ei in rule_sets:
            for filt in filter_sets:
                # The signal-only check ran before filters were attached, so a
                # pair naming a filter was silently inert: VWAP declared
                # above_vwap and vwap_proximity mutually exclusive and 25 of
                # 400 generated strategies held both. Any exclusion involving
                # a filter was decoration.
                if _violates_exclusive(tuple(names) + tuple(filt),
                                       template.exclusive):
                    continue
                out.append(CombinationSpec(
                    symbol=symbol.upper(), group=group, primary_tf=tf,
                    confirm_tfs=confirm_map.get(tf, ()),
                    signal_conditions=names, filter_conditions=filt,
                    exit_index=ei, filters=template.filters))

    if len(out) > max_total:
        # Trim by rule set too: dropping individual specs would orphan the
        # controls the sampling above was careful to keep.
        keyed: Dict[Tuple[str, int, Tuple[str, ...], int], List[CombinationSpec]] = {}
        for c in out:
            keyed.setdefault((c.group, c.primary_tf, c.signal_conditions,
                              c.exit_index), []).append(c)
        keys = sorted(keyed)
        rng.shuffle(keys)
        kept: List[CombinationSpec] = []
        for k in keys:
            if len(kept) + len(keyed[k]) > max_total:
                continue
            kept.extend(keyed[k])
        out = kept
    out.sort(key=lambda c: (c.group, c.primary_tf, c.signal_conditions,
                            c.exit_index, c.filter_conditions))
    return out


def _build_strategy(spec: CombinationSpec) -> Optional[Strategy]:
    template = TEMPLATES_BY_GROUP[spec.group]
    exits = template.exits or tuple(DEFAULT_EXITS)
    exit_model = exits[min(spec.exit_index, len(exits) - 1)]

    conds: List[Condition] = []
    for n in spec.signal_conditions:
        conds.append(get_condition(n))
    for n in spec.filter_conditions:
        conds.append(get_condition(n))

    # Bind higher-timeframe conditions to the confirmation timeframe so that a
    # "multi-timeframe" strategy really reads a different timeframe rather than
    # re-reading its own.
    if spec.confirm_tfs:
        htf = spec.confirm_tfs[0]
        bound: List[Condition] = []
        for c in conds:
            if c.group in ("multitimeframe",) or (
                    c.group == "structure" and c.kind is ConditionKind.SIGNAL
                    and len(spec.signal_conditions) >= 3):
                bound.append(c.bind(htf))
            else:
                bound.append(c)
        conds = bound

    extra = tuple(n for n in spec.filter_conditions
                  if n not in template.base_filters)
    name = f"{spec.group.lower()}_{'_'.join(spec.signal_conditions)}"
    if extra:
        # Two strategies that differ only by a filter must not share a name;
        # the ids differ, and a report keyed on name would merge them.
        name += "__f_" + "_".join(sorted(extra))
    try:
        return Strategy(
            name=name[:120], symbol=spec.symbol, group=spec.group,
            primary_tf=spec.primary_tf, conditions=tuple(conds),
            exit=exit_model, filters=spec.filters,
            allowed_directions=template.directions,
            confirm_tfs=spec.confirm_tfs,
            description=template.description,
        )
    except ValueError:
        return None


def generate_strategies(
    symbol: str,
    timeframes: Sequence[int],
    *,
    groups: Optional[Sequence[str]] = None,
    max_total: int = 4_000,
    seed: int = 20260922,
    **kwargs,
) -> List[Strategy]:
    """Generate concrete, deduplicated :class:`Strategy` objects for one symbol.

    Deduplication is by content hash, so two different generation paths that
    arrive at the same rule set produce one strategy, not two.
    """
    specs = generate_combinations(symbol, timeframes, groups=groups,
                                  max_total=max_total, seed=seed, **kwargs)
    seen: Dict[str, Strategy] = {}
    for spec in specs:
        st = _build_strategy(spec)
        if st is not None:
            seen.setdefault(st.strategy_id, st)
    return sorted(seen.values(), key=lambda s: (s.group, s.primary_tf, s.name))
