"""Anchor the thesis high, hone the entry low, keep the objective where it was.

The point of entering a 4-hour thesis on a 15-minute trigger is reward:risk:
the objective stays where the 4-hour chart put it while the stop shrinks to
the 15-minute chart's noise. That only works if the target is a PRICE. With
targets expressed as multiples of the stop - which was the only option until
now - tightening the stop drags every target in with it, and the trade becomes
a fifteen-minute-sized version of the same idea rather than the 4-hour move on
a tight stop.

Measured on 120 days of MNQ, 240m anchor, same conditions throughout:

    target kind    entry tf   median stop   planned R:R
    R_MULTIPLE     240m           179 pt        8.00
    R_MULTIPLE      15m            56 pt        8.00   <- stop fell 3x, R:R did not move
    R_MULTIPLE       5m            35 pt        8.00
    ANCHOR_ATR     240m           179 pt        2.00
    ANCHOR_ATR      15m            56 pt        6.63
    ANCHOR_ATR       5m            35 pt       10.56   <- the mechanism working
"""

from __future__ import annotations

from datetime import date

import pytest

from futures_agents.data.loader import synthetic_series
from futures_agents.features import build_symbol_frame
from futures_agents.strategies.base import (ExitModel, StopKind, Strategy,
                                            TargetKind)
from futures_agents.strategies.library import get_condition

ANCHOR_TF = 240
FIXTURE_END = date(2026, 3, 17)


@pytest.fixture(scope="module")
def frame():
    series = synthetic_series("MNQ", days=60, seed=5, end_date=FIXTURE_END)
    return build_symbol_frame(series, [5, 15, 60, ANCHOR_TF])


def _strategy(execution_tf, target_kind):
    return Strategy(
        name=f"anchor_{execution_tf}", symbol="MNQ", group="TREND",
        primary_tf=ANCHOR_TF,
        conditions=tuple(get_condition(n) for n in
                         ("ema_stack", "structure_trend", "adx_trending")),
        execution_tf=execution_tf,
        trigger_conditions=((get_condition("pullback_to_support"),)
                            if execution_tf else ()),
        exit=ExitModel(StopKind.ATR, 1.5, targets_r=(2.0, 4.0, 8.0),
                       scale_out=(0.4, 0.3, 0.3), breakeven_at_r=1.5,
                       time_stop_bars=40, exit_at_session_close=False,
                       target_kind=target_kind, anchor_mult=(0.75, 1.5, 3.0),
                       min_reward_risk=1.0))


def _signals(frame, strategy, step=13):
    out = []
    for i in range(1500, len(frame.base), step):
        sig = strategy.evaluate(frame.snapshot(i))
        if sig is not None and abs(sig.entry - sig.stop) > 0:
            out.append(sig)
    return out


def _median_rr(signals):
    rr = sorted(abs(s.targets[-1] - s.entry) / abs(s.entry - s.stop) for s in signals)
    return rr[len(rr) // 2]


def _median_stop(signals):
    d = sorted(abs(s.entry - s.stop) for s in signals)
    return d[len(d) // 2]


def test_honing_the_entry_tightens_the_stop(frame):
    """The premise. If the lower timeframe did not produce a tighter stop there
    would be nothing for the rest of this to trade on."""
    wide = _signals(frame, _strategy(None, TargetKind.ANCHOR_ATR))
    tight = _signals(frame, _strategy(15, TargetKind.ANCHOR_ATR))
    assert wide and tight
    assert _median_stop(tight) < _median_stop(wide) / 2.0


def test_r_multiple_targets_cannot_expand_reward_for_risk(frame):
    """The defect, pinned so it cannot come back as a default.

    Targets as multiples of the stop are self-referential: the planned R:R is
    whatever the last target multiple says, whatever the stop does.
    """
    rrs = []
    for execution_tf in (None, 15, 5):
        signals = _signals(frame, _strategy(execution_tf, TargetKind.R_MULTIPLE))
        assert signals, f"no signals at execution_tf={execution_tf}"
        rrs.append(_median_rr(signals))
    assert max(rrs) - min(rrs) < 0.01, (
        f"R-multiple R:R should be invariant to the entry timeframe, got {rrs}")


def test_anchored_targets_expand_reward_for_risk(frame):
    """The fix. Same 240m objective, tighter entry, better ratio."""
    same = _signals(frame, _strategy(None, TargetKind.ANCHOR_ATR))
    honed = _signals(frame, _strategy(15, TargetKind.ANCHOR_ATR))
    assert same and honed
    assert _median_rr(honed) > _median_rr(same) * 2.0, (
        f"anchored R:R only went {_median_rr(same):.2f} -> {_median_rr(honed):.2f}")


def test_reward_risk_floor_rejects_a_target_inside_the_stop(frame):
    """An anchored target is not guaranteed to be further away than the stop.
    Without the floor the strategy would happily take a 0.3:1 setup."""
    strict = _strategy(15, TargetKind.ANCHOR_ATR)
    strict = Strategy(**{**strict.__dict__, "_id": None,
                         "exit": ExitModel(
                             StopKind.ATR, 1.5, targets_r=(2.0,), scale_out=(1.0,),
                             target_kind=TargetKind.ANCHOR_ATR,
                             anchor_mult=(0.05,), min_reward_risk=1.5,
                             exit_at_session_close=False)})
    assert not _signals(frame, strict), (
        "a target at 0.05 anchor-ATR is inside the stop and must be rejected")


def test_execution_tf_must_be_finer_than_the_anchor():
    with pytest.raises(ValueError, match="coarser"):
        Strategy(name="backwards", symbol="MNQ", group="TREND", primary_tf=15,
                 conditions=(get_condition("ema_stack"),), execution_tf=240)


def test_trigger_conditions_need_an_execution_timeframe():
    with pytest.raises(ValueError, match="execution_tf"):
        Strategy(name="no_exec", symbol="MNQ", group="TREND", primary_tf=240,
                 conditions=(get_condition("ema_stack"),),
                 trigger_conditions=(get_condition("pullback_to_support"),))


def test_a_disagreeing_trigger_kills_the_setup(frame):
    """The lower timeframe saying "not here" is information, not noise to
    average out. A trigger that fires the other way must veto."""
    honed = _strategy(15, TargetKind.ANCHOR_ATR)
    plain = _strategy(None, TargetKind.ANCHOR_ATR)
    assert len(_signals(frame, honed)) < len(_signals(frame, plain))
