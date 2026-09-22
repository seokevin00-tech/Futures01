"""Per-symbol research profiles.

The specification requires each contract to be treated independently. Running
the same thirteen templates on three symbols and backtesting them separately
satisfies the letter of that and not the point: MGC's pit session is
08:20-13:30, so an opening-range template calibrated to the 09:30 equity open
was being tested on a contract whose day is shaped differently.

These tests guard the two ways a profile can go wrong quietly: naming a
template that does not exist (filtered out silently, narrowing a contract's
research by accident) and failing to actually differentiate.
"""

from __future__ import annotations

import pytest

from futures_agents.config import get_contract
from futures_agents.strategies.combinator import TEMPLATES, generate_strategies
from futures_agents.strategies.profiles import (SYMBOL_PROFILES, groups_for,
                                                profile_for, timeframes_for,
                                                unknown_groups)

TEMPLATE_GROUPS = {t.group for t in TEMPLATES}


def test_no_profile_names_a_template_that_does_not_exist():
    """The failure this catches is silent by design elsewhere: `groups_for`
    filters unknown names out, so a typo would narrow a contract's research and
    the run would look deliberate. It already caught one - MNQ carried an
    `IMBALANCE_ANY` that was never a template."""
    bad = unknown_groups()
    assert not bad, f"profiles naming no template: {bad}"


def test_every_profiled_symbol_is_a_real_contract():
    for symbol in SYMBOL_PROFILES:
        get_contract(symbol)           # raises if unknown


def test_profiles_actually_differentiate():
    """Three profiles that agree on everything are one profile written out
    three times, and would be worse than none - they would imply a per-contract
    judgement that had not been made."""
    sets = {s: set(p.groups) for s, p in SYMBOL_PROFILES.items()}
    symbols = sorted(sets)
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            assert sets[a] != sets[b], f"{a} and {b} have identical families"
            overlap = len(sets[a] & sets[b]) / len(sets[a] | sets[b])
            assert overlap < 0.75, (
                f"{a} and {b} overlap {overlap:.0%} - not meaningfully distinct")


def test_an_unprofiled_symbol_is_tested_broadly():
    """No opinion must mean the full set, never an empty one. A profile lookup
    that returned () for an unknown contract would silently generate nothing."""
    assert groups_for("ZZZ", sorted(TEMPLATE_GROUPS)) is None
    strategies = generate_strategies("MCL", [5, 15, 60], max_total=600, seed=1)
    assert {s.group for s in strategies} == TEMPLATE_GROUPS


@pytest.mark.parametrize("symbol", sorted(SYMBOL_PROFILES))
def test_generation_follows_the_profile(symbol: str):
    strategies = generate_strategies(symbol, [5, 15, 60], max_total=600, seed=1)
    assert strategies
    produced = {s.group for s in strategies}
    expected = set(groups_for(symbol, sorted(TEMPLATE_GROUPS)) or ())
    assert produced == expected
    for excluded in profile_for(symbol).excluded:
        assert excluded not in produced


def test_an_explicit_group_list_overrides_the_profile():
    """A prior has to be checkable. If a profile could not be overridden, the
    family it excludes could never be tested and the exclusion would be an
    assertion rather than a hypothesis."""
    excluded = next(iter(profile_for("MGC").excluded))
    strategies = generate_strategies("MGC", [5, 15, 60], groups=[excluded],
                                     max_total=200, seed=1)
    assert strategies and {s.group for s in strategies} == {excluded}


def test_timeframes_respect_each_contract_s_noise_floor():
    """MNQ's 120-point typical ATR against a 0.97-point round turn is what
    makes 1-minute generation viable there. MES pays 1.20% of a typical ATR per
    round turn against MNQ's 0.81%, so its fastest timeframe is slower."""
    assert 1 in (timeframes_for("MNQ") or ())
    assert 1 not in (timeframes_for("MES") or ())
    assert 1 not in (timeframes_for("MGC") or ())
    assert min(timeframes_for("MGC")) >= 15


def test_gold_is_not_confined_to_the_equity_session():
    """MGC's RTH is 08:20-13:30 and its drivers run around the clock, so an
    equity-session confinement would discard most of its information."""
    assert get_contract("MGC").rth_open == "08:20"
    assert profile_for("MGC").rth_only is False
    assert profile_for("MNQ").rth_only is True
    assert "OPENING_RANGE" in profile_for("MGC").excluded


def test_every_exclusion_carries_a_reason():
    """An exclusion without a reason is indistinguishable from an omission."""
    for symbol, prof in SYMBOL_PROFILES.items():
        for group, reason in prof.excluded.items():
            assert len(reason) > 20, f"{symbol}/{group} has no real reason"
        assert not set(prof.groups) & set(prof.excluded), (
            f"{symbol} both includes and excludes a family")


def test_no_profile_starves_its_contract():
    """A profile narrows on purpose, but a contract that generates almost
    nothing is not being researched independently - it is being skipped. The
    floor is deliberately low: this catches a profile that collapsed, not one
    that is merely selective."""
    for symbol in sorted(SYMBOL_PROFILES):
        strategies = generate_strategies(symbol, [5, 15, 60], max_total=600, seed=1)
        assert len(strategies) >= 200, (
            f"{symbol} generated only {len(strategies)} strategies")
        per_family = {}
        for s in strategies:
            per_family[s.group] = per_family.get(s.group, 0) + 1
        thin = {g: n for g, n in per_family.items() if n < 10}
        assert not thin, f"{symbol} families with almost no strategies: {thin}"
