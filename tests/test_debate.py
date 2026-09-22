"""The research debate protocol.

The tests that matter here guard *silent* failures — cases where the protocol
keeps working and quietly stops protecting anything.
"""

from __future__ import annotations

import json

import pytest

from futures_agents.agents.debate import (
    Challenge, ChallengeKind, Finding, PooledRanking, Rebuttal, Verdict,
    find_redundancy, pool_findings, render_debate_log, trade_overlap,
)
from futures_agents.backtest.metrics import Metrics


def _finding(owner, sid, expectancy=0.25, trades=120, offset=0, fingerprint=None):
    m = Metrics(trades=trades, expectancy_r=expectancy, std_r=1.2,
                profit_factor=1.0 + expectancy * 3, max_drawdown_r=5.0,
                max_consecutive_losses=4)
    m.t_statistic = expectancy / (1.2 / trades ** 0.5)
    return Finding(owner=owner, symbol="MNQ", strategy_id=sid, metrics=m,
                   robustness_score=0.6, live_eligible=True,
                   trade_fingerprint=fingerprint or [(i * 10 + offset, 1)
                                                     for i in range(trades)],
                   r_series=[expectancy] * trades)


# ---------------------------------------------------------------------------
# Serialisation: the silent failure
# ---------------------------------------------------------------------------

def test_the_trade_fingerprint_survives_publication():
    """Redundancy detection is defined on the fingerprint.

    A Finding serialised without it cannot be compared to anyone else's, and
    find_redundancy then returns an empty list — which reads exactly like
    "these edges are independent". Two specialists reporting the identical edge
    would pass pooling as corroboration, and nothing would report an error.
    """
    a = _finding("research_trend", "S-A")
    b = _finding("research_reversion", "S-B", fingerprint=a.trade_fingerprint)

    live = find_redundancy([a, b])
    assert live, "the fixture must actually be redundant"

    published = json.loads(json.dumps([a.to_dict(), b.to_dict()]))
    rehydrated = [Finding.from_dict(d) for d in published]
    assert find_redundancy(rehydrated) == live, (
        "redundancy detection did not survive a publish/parse round trip")


def test_an_empty_measurement_survives_publication_as_empty():
    """An empty measurement is what makes a challenge unsubstantiated. If a
    round trip invented one, an opinion would be promoted into evidence."""
    c = Challenge("a", "b", "S-A", "MNQ", ChallengeKind.DATA_MINING, "no numbers")
    assert not c.is_substantiated
    restored = Challenge.from_dict(json.loads(json.dumps(c.to_dict())))
    assert restored.measurement == {}
    assert not restored.is_substantiated


def test_a_real_measurement_survives_publication():
    c = Challenge("a", "b", "S-A", "MNQ", ChallengeKind.COST_FRAGILE,
                  "dies under doubled slippage",
                  measurement={"retained_fraction": 0.21, "survives": False})
    restored = Challenge.from_dict(json.loads(json.dumps(c.to_dict())))
    assert restored.is_substantiated
    assert restored.measurement["retained_fraction"] == pytest.approx(0.21)


def test_an_unmeasured_rebuttal_stays_unsubstantiated_through_a_round_trip():
    r = Rebuttal("owner", "challenger", "S-A", Verdict.REBUTTED, "it is fine")
    assert not Rebuttal.from_dict(json.loads(json.dumps(r.to_dict()))).is_substantiated


# ---------------------------------------------------------------------------
# The three rules
# ---------------------------------------------------------------------------

def test_an_unsubstantiated_challenge_is_discarded():
    a = _finding("research_trend", "S-A")
    result = pool_findings("MNQ", [a], [
        Challenge("research_reversion", "research_trend", "S-A", "MNQ",
                  ChallengeKind.DATA_MINING, "I doubt it")])
    assert result.challenges_discarded == 1
    assert len(result.ranked) == 1, "an opinion must not move the ranking"


def test_an_unmeasured_rebuttal_does_not_answer_a_measured_challenge():
    a = _finding("research_trend", "S-A")
    b = _finding("research_reversion", "S-B", offset=5000)
    challenge = Challenge("research_reversion", "research_trend", "S-A", "MNQ",
                          ChallengeKind.OUT_OF_SAMPLE_FAILURE, "collapses",
                          measurement={"consistency_ratio": 0.08})
    asserted = Rebuttal("research_trend", "research_reversion", "S-A",
                        Verdict.REBUTTED, "it is fine really")
    result = pool_findings("MNQ", [a, b], [challenge], [asserted])
    assert [d["strategy_id"] for d in result.disqualified] == ["S-A"]


def test_a_measured_rebuttal_does_answer_it():
    a = _finding("research_trend", "S-A")
    b = _finding("research_reversion", "S-B", offset=5000)
    challenge = Challenge("research_reversion", "research_trend", "S-A", "MNQ",
                          ChallengeKind.OUT_OF_SAMPLE_FAILURE, "collapses",
                          measurement={"consistency_ratio": 0.08})
    measured = Rebuttal("research_trend", "research_reversion", "S-A",
                        Verdict.REBUTTED, "holds across three splits",
                        measurement={"splits_tested": 3, "min_expectancy_r": 0.18})
    result = pool_findings("MNQ", [a, b], [challenge], [measured])
    assert not result.disqualified
    assert result.challenges_rebutted == 1


def test_pooling_does_not_mutate_its_inputs():
    """A scoring function whose result depends on how many times it has been
    called cannot be audited."""
    a = _finding("research_trend", "S-A")
    challenge = Challenge("research_reversion", "research_trend", "S-A", "MNQ",
                          ChallengeKind.REGIME_ARTEFACT, "one thin regime",
                          measurement={"dominant_share": 0.91})
    first = pool_findings("MNQ", [a], [challenge])
    second = pool_findings("MNQ", [a], [challenge])
    assert challenge.verdict is Verdict.UNANSWERED, "the caller's object was mutated"
    assert first.challenges_upheld == second.challenges_upheld == 1
    assert ([r["pooled_score"] for r in first.ranked]
            == [r["pooled_score"] for r in second.ranked])


def test_pooling_is_symmetric_across_owners():
    """Relabel who found what and the ranking must be identical, or the desk
    lead is favouring a specialist."""
    findings = [_finding("research_trend", "S-A", 0.25),
                _finding("research_reversion", "S-B", 0.30, offset=3000),
                _finding("research_liquidity", "S-C", 0.20, offset=7000)]
    swap = {"research_trend": "research_liquidity",
            "research_liquidity": "research_trend"}
    relabelled = [_finding(swap.get(f.owner, f.owner), f.strategy_id,
                           f.metrics.expectancy_r,
                           fingerprint=f.trade_fingerprint) for f in findings]
    assert ([r["strategy_id"] for r in pool_findings("MNQ", findings, []).ranked]
            == [r["strategy_id"] for r in pool_findings("MNQ", relabelled, []).ranked])


# ---------------------------------------------------------------------------
# Redundancy
# ---------------------------------------------------------------------------

def test_redundancy_is_collapsed_transitively():
    """If A is the same edge as B and B the same as C, all three are one edge.
    Collapsing pairwise let a finding be 'kept' in one pair and 'absorbed' in
    the next, leaving the first pointing at a survivor no longer in the ranking.
    """
    a = _finding("research_trend", "S-A", 0.25)
    d = _finding("research_liquidity", "S-D", 0.22, fingerprint=a.trade_fingerprint)
    e = _finding("research_reversion", "S-E", 0.28, fingerprint=a.trade_fingerprint)
    other = _finding("research_reversion", "S-B", 0.30, offset=9000)

    result = pool_findings("MNQ", [a, d, e, other], [])
    kept = {c["kept"] for c in result.redundant_clusters}
    absorbed = {sid for c in result.redundant_clusters for sid in c["absorbed"]}
    ranked = {r["strategy_id"] for r in result.ranked}

    assert len(result.redundant_clusters) == 1, "one edge, one cluster"
    assert not kept & absorbed, "a finding was both kept and absorbed"
    assert not absorbed & ranked, "an absorbed finding is still ranked"
    assert len(ranked) == 2


def test_trade_overlap_is_direction_aware():
    longs = [(10, 1), (20, 1), (30, 1)]
    shorts = [(10, -1), (20, -1), (30, -1)]
    assert trade_overlap(longs, longs) == pytest.approx(1.0)
    assert trade_overlap(longs, shorts) == pytest.approx(0.0)


def test_a_fatal_challenge_disqualifies_rather_than_penalising():
    a = _finding("research_trend", "S-A")
    b = _finding("research_reversion", "S-B", offset=5000)
    result = pool_findings("MNQ", [a, b], [
        Challenge("research_reversion", "research_trend", "S-A", "MNQ",
                  ChallengeKind.SAMPLE_TOO_SMALL, "22 trades",
                  measurement={"trades": 22})])
    assert [d["strategy_id"] for d in result.disqualified] == ["S-A"]
    assert [r["strategy_id"] for r in result.ranked] == ["S-B"]


def test_a_graded_challenge_penalises_without_disqualifying():
    a = _finding("research_trend", "S-A")
    result = pool_findings("MNQ", [a], [
        Challenge("research_reversion", "research_trend", "S-A", "MNQ",
                  ChallengeKind.COST_FRAGILE, "dies at 2x slippage",
                  measurement={"retained_fraction": 0.21})])
    row = result.ranked[0]
    assert 0 < row["pooled_score"] < row["base_score"]
    assert row["penalty_applied"] == pytest.approx(0.35)


def test_the_debate_log_renders_without_a_ranking():
    assert "nothing survived cross-examination" in render_debate_log(
        PooledRanking(symbol="MNQ"), [], [])
