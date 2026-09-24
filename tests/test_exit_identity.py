"""Two defects that were silent, cost real search budget, and lost real trades.

Neither raised anything. One deleted an exit model from every backtest ever run;
the other made two different exits share a database key so that one overwrote
the other's results. Both are the kind of failure that a passing test suite
happily coexists with, so each gets a test that fails on the original code.
"""

import dataclasses

import pytest

from futures_agents.strategies.base import (ExitModel, StopKind, Strategy,
                                            TargetKind)
from futures_agents.strategies.combinator import expand_exit_models


def test_exit_identity_distinguishes_every_shipped_model():
    """The catalogue must not contain two exits that hash the same.

    Shipped state before the fix: the R_MULTIPLE structure exit and the
    ANCHOR_STRUCTURE structure exit both rendered as ``STRUCTUREx1->1/2/3R``,
    so strategies differing only in that collided on ``strategy_id`` - and
    ``run_portfolio`` keys results by that id, so one silently replaced the
    other.
    """
    models = expand_exit_models()
    ids = [m.identity for m in models]
    assert len(set(ids)) == len(models), (
        "two exit models share an identity; strategies using them will collide "
        "on strategy_id and silently overwrite each other's results")


def test_exit_identity_covers_every_field():
    """A field added later must not be able to reintroduce the collision."""
    base = ExitModel()
    for f in dataclasses.fields(ExitModel):
        current = getattr(base, f.name)
        # Perturb the field to something different but type-compatible.
        if isinstance(current, bool):
            other = not current
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            other = current + 1
        elif isinstance(current, tuple):
            # Halve rather than append: scale_out is validated to sum to <= 1,
            # so appending would trip the validator instead of the assertion.
            other = tuple(v / 2 for v in current) or (0.5,)
        elif current is None:
            other = 1.0
        else:
            continue          # enums are covered by the catalogue test above
        assert dataclasses.replace(base, **{f.name: other}).identity != base.identity, (
            f"ExitModel.identity ignores {f.name!r}; two exits differing only in "
            "that field would share a strategy_id")


def test_r_multiple_exits_are_not_deleted_by_the_anchored_guard():
    """``min_reward_risk`` is a per-setup guard, not a filter on the catalogue.

    For an anchored target the reward is a price level and the ratio genuinely
    varies bar to bar, so it must be checked. For R_MULTIPLE the ratio **is**
    ``targets_r[-1]``, fixed when the model was written - so applying the same
    floor statically deletes any such exit whose last target sits below it.
    That is what removed ``ATRx1.2->1.2R`` from every backtest in nine of the
    thirteen templates.
    """
    scalp = [m for m in expand_exit_models()
             if m.target_kind is TargetKind.R_MULTIPLE
             and m.targets_r[-1] < m.min_reward_risk]
    assert scalp, "expected at least one R-multiple exit below its own floor"
    for m in scalp:
        assert m.target_kind is TargetKind.R_MULTIPLE
        # The guard must not consider it: the ratio is already decided.
        assert m.targets_r[-1] > 0


def test_anchored_exits_still_enforce_their_floor():
    """The fix must not remove the check where it was actually needed."""
    anchored = [m for m in expand_exit_models()
                if m.target_kind is not TargetKind.R_MULTIPLE]
    assert anchored, "no anchored exits in the catalogue"
    for m in anchored:
        assert m.min_reward_risk > 0, (
            "an anchored exit with no reward:risk floor can take a trade whose "
            "target sits inside its own stop")


def test_label_stays_readable_but_disambiguates_anchoring():
    """Display label may repeat, identity may not - but anchoring should show."""
    models = expand_exit_models()
    for m in models:
        if m.target_kind is not TargetKind.R_MULTIPLE:
            assert m.target_kind.value in m.label, (
                f"{m.label!r} does not reveal that its targets are anchored; a "
                "report cannot tell it apart from the R-multiple version")


# --------------------------------------------------------------------------
# The same defect, found later in StrategyFilters
# --------------------------------------------------------------------------

def test_strategy_filters_identity_covers_every_field():
    """Scope fields must reach ``strategy_id``, or arms merge silently.

    Shipped state before the fix: ``StrategyFilters.label()`` emitted only
    sessions, regimes, volatility and require_alignment, so one rule set with
    ``rth_only=True``, with ``rth_only=False``, with ``max_minutes_since_open=90``
    and with ``days_of_week={MON}`` all hashed to the same id. ``run_portfolio``
    keys results AND open-position state by that id, so an RTH arm and a
    non-RTH arm run in one call silently merged.
    """
    from futures_agents.strategies.base import StrategyFilters

    base = StrategyFilters()
    for f in dataclasses.fields(StrategyFilters):
        current = getattr(base, f.name)
        if isinstance(current, bool):
            other = not current
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            other = current + 1
        elif current is None:
            other = frozenset({"MON"}) if f.name == "days_of_week" else 1.0
        elif isinstance(current, frozenset):
            other = frozenset(current | {"ZZZ"})
        else:
            continue
        assert dataclasses.replace(base, **{f.name: other}).identity != base.identity, (
            f"StrategyFilters.identity ignores {f.name!r}; two scopes differing "
            "only in that field share a strategy_id and merge in run_portfolio")


def test_rth_arms_get_distinct_strategy_ids():
    """The exact collision that was measured, as an end-to-end guard."""
    import dataclasses as dc

    from futures_agents.strategies.base import StrategyFilters
    from futures_agents.strategies.library import get_condition

    import sys
    sys.path.insert(0, "workspace/studies")
    import toolkit as T

    conds = [get_condition("structure_trend"), get_condition("ema_stack")]
    on = T.make_strategy("MES", 240, conds, filters=StrategyFilters(rth_only=True))
    off = T.make_strategy("MES", 240, conds, filters=StrategyFilters(rth_only=False))
    assert on.strategy_id != off.strategy_id, (
        "an RTH arm and a non-RTH arm share a strategy_id; run_portfolio keys "
        "results by it, so one would overwrite the other")
