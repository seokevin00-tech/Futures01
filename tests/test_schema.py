"""Schema objects: serialisation, coercion, clamping and price formatting.

``to_dict()`` is the wire format for every artefact an agent publishes and for
every payload that crosses the bus, so it has to be JSON-safe *and* lossless
for the fields downstream code reads back.

``fmt_price`` gets a section of its own because the failure is silent: ``%g``
renders 21850.25 as "21850.2" at six significant digits, which is a whole tick
of error on an index future - a stop quoted one tick away from the level that
was actually tested.
"""

from __future__ import annotations

import json
import math

import pytest

from futures_agents.schema import (AgentSignal, AnalystPrediction, Confidence,
                                   Decision, Direction, Evidence,
                                   HistoricalPerformance, JournalEntry,
                                   MarketRegime, NewsContext, NewsEvent,
                                   NewsReaction, NewsRisk, RiskAssessment,
                                   SignalStrength, StrategyStats, TradeCallout,
                                   VolatilityRegime, VolumeRegime, fmt_price,
                                   fmt_prices, from_json, to_json)


# --------------------------------------------------------------------------
# Object builders - populated, not defaults, so serialisation is really tested
# --------------------------------------------------------------------------

def an_evidence() -> Evidence:
    return Evidence(kind="indicator", name="rsi", value=71.25, timeframe=5,
                    detail="overbought on the 5m", supports=Direction.SHORT,
                    weight=0.8, source="analyst_a")


def a_history() -> HistoricalPerformance:
    return HistoricalPerformance(
        strategy_id="orb_5m", symbol="MNQ", timeframe=5, regime="TREND_UP",
        session="RTH_OPEN", trades=142, win_rate=0.47, avg_win_r=1.85,
        avg_loss_r=-1.0, profit_factor=1.64, expectancy_r=0.34,
        max_drawdown_r=7.5, max_consecutive_losses=5, sharpe=0.31,
        out_of_sample_trades=44, out_of_sample_expectancy_r=0.29,
        walk_forward_efficiency=0.85, robustness_score=0.77,
        sample_is_sufficient=True)


def a_prediction() -> AnalystPrediction:
    return AnalystPrediction(
        analyst_id="A", analyst_name="Technical", specialisation="structure",
        symbol="MNQ", direction=Direction.LONG, entry_zone=(21_850.25, 21_852.75),
        stop=21_840.25, target_1=21_870.25, target_2=21_890.25,
        expected_reward_risk=2.0, confidence=0.66, time_horizon="30m",
        primary_reason="break of structure with retest",
        supporting_confluences=["BOS_UP", "VWAP reclaim"],
        invalidation_conditions=["loss of 21840.25"],
        would_change_mind_if="a 5m close below VWAP",
        evidence=[an_evidence()], historical_performance=a_history(),
        reasoning="structure first", source="deterministic")


def a_risk_assessment() -> RiskAssessment:
    return RiskAssessment(
        approved=True, contracts=3, dollar_risk=60.0, account_risk_pct=0.0012,
        stop_distance_points=10.0, stop_distance_ticks=40.0, expected_cost=5.76,
        reward_risk_after_costs=1.9, remaining_daily_loss_budget=940.0,
        remaining_drawdown_buffer=4_000.0, buffer_consumed_if_stopped_pct=0.015,
        risk_multiplier_applied=1.0, warnings=["not yet live-eligible"],
        notes=["3 contract(s)"])


def a_signal() -> AgentSignal:
    return AgentSignal(
        agent_id="analyst_a", agent_role="ANALYST_A", symbol="MNQ", timeframe=5,
        timeframes_considered=[1, 5, 15], market_regime=MarketRegime.TREND_UP,
        volatility_regime=VolatilityRegime.NORMAL,
        volume_regime=VolumeRegime.ABOVE_AVERAGE, direction=Direction.LONG,
        entry=21_850.25, entry_zone=(21_850.25, 21_852.75), stop=21_840.25,
        targets=[21_870.25, 21_890.25], reward_risk=2.0, confidence=0.66,
        strategy="orb_5m", strategy_group="breakout",
        confluences=["BOS_UP"], conflicts=["daily is down"],
        invalidation="loss of 21840.25", supporting_data=[an_evidence()],
        historical_performance=a_history(), news_context_summary="quiet",
        time_horizon="30m", primary_reason="breakout retest",
        reasoning="...", source="hybrid")


def a_callout() -> TradeCallout:
    return TradeCallout(
        symbol="MNQ", decision=Decision.LONG, entry=21_850.25,
        entry_zone=(21_850.25, 21_852.75), stop_loss=21_840.25,
        targets=[21_870.25, 21_890.25], expected_reward_risk=2.0, contracts=3,
        dollar_risk=60.0, account_risk_pct=0.0012, strategy="orb_5m",
        strategy_group="breakout", timeframe="5m",
        market_regime=MarketRegime.TREND_UP, news_risk=NewsRisk.LOW,
        historical_win_rate=0.47, historical_expectancy_r=0.34,
        max_historical_drawdown_r=7.5, analyst_agreement="2 of 3",
        confidence=0.66, trade_invalidation="loss of 21840.25",
        reason_for_entry="breakout retest", remaining_drawdown_buffer=4_000.0,
        buffer_consumed_if_stopped_pct=0.015,
        remaining_daily_loss_budget=940.0, account_equity=50_000.0,
        evidence_chain=[an_evidence()], analyst_predictions=[a_prediction()],
        risk_assessment=a_risk_assessment(), decision_rationale="aligned")


def a_journal_entry() -> JournalEntry:
    return JournalEntry(
        date_et="2026-03-17", time_et="10:05:00 EDT", symbol="MNQ",
        direction=Direction.LONG, entry=21_850.25, stop=21_840.25,
        targets=[21_870.25], strategy="orb_5m", strategy_group="breakout",
        timeframes=[1, 5, 15], indicators=["rsi", "vwap"],
        confluences=["BOS_UP"], market_regime=MarketRegime.TREND_UP,
        volatility_regime=VolatilityRegime.HIGH, session="RTH_OPEN",
        news_environment="quiet", final_decision=Decision.LONG,
        confidence=0.66, contracts=3, dollar_risk=60.0, result="WIN",
        exit_price=21_870.25, exit_reason="TARGET", realised_r=2.0,
        profit_loss=120.0, reward_risk_planned=2.0, thesis_correct=True)


def a_news_context() -> NewsContext:
    return NewsContext(
        risk=NewsRisk.MODERATE, headline_summary="CPI in an hour",
        macro_bias=Direction.SHORT, macro_bias_confidence=0.4,
        upcoming_events=[NewsEvent(title="CPI", category="CPI", impact="HIGH",
                                   affected_symbols=["MNQ", "MES"])],
        minutes_to_next_high_impact=58.0,
        historical_analogues=[NewsReaction(symbol="MNQ", category="CPI",
                                           surprise_direction="ABOVE",
                                           move_15m=-42.5, reverted=True)],
        cross_market={"DXY": "up", "yields": "up"}, sentiment="RISK_OFF",
        evidence=[an_evidence()], sources=["release schedule"])


ROUND_TRIP_OBJECTS = [
    pytest.param(an_evidence(), id="Evidence"),
    pytest.param(a_history(), id="HistoricalPerformance"),
    pytest.param(a_prediction(), id="AnalystPrediction"),
    pytest.param(a_risk_assessment(), id="RiskAssessment"),
    pytest.param(a_signal(), id="AgentSignal"),
    pytest.param(a_callout(), id="TradeCallout"),
    pytest.param(a_journal_entry(), id="JournalEntry"),
    pytest.param(a_news_context(), id="NewsContext"),
    pytest.param(NewsEvent(title="FOMC", impact="HIGH"), id="NewsEvent"),
    pytest.param(NewsReaction(symbol="MNQ", move_5m=-12.5), id="NewsReaction"),
    pytest.param(StrategyStats(strategy_id="orb_5m", live_trades=9), id="StrategyStats"),
]


# --------------------------------------------------------------------------
# to_dict round trips
# --------------------------------------------------------------------------

@pytest.mark.parametrize("obj", ROUND_TRIP_OBJECTS)
def test_to_dict_survives_a_json_round_trip(obj):
    """The artefact written to disk must read back byte-identical in content."""
    d = obj.to_dict()
    assert isinstance(d, dict)
    restored = json.loads(json.dumps(d))
    assert restored == d


@pytest.mark.parametrize("obj", ROUND_TRIP_OBJECTS)
def test_to_dict_contains_no_enum_or_dataclass_leakage(obj):
    """Anything that is not a JSON primitive would blow up at publish time."""
    def walk(value, path="root"):
        if isinstance(value, dict):
            for k, v in value.items():
                assert isinstance(k, str), f"{path}: non-string key {k!r}"
                walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")
        else:
            assert value is None or isinstance(value, (str, int, float, bool)), (
                f"{path}: {type(value).__name__} is not JSON-native")
    walk(obj.to_dict())


@pytest.mark.parametrize("obj", ROUND_TRIP_OBJECTS)
def test_to_json_is_stable_and_sorted(obj):
    """Identical content must produce an identical string, which is what keeps
    the LLM agents' cached prompt prefixes stable."""
    first, second = to_json(obj), to_json(obj)
    assert first == second
    assert from_json(first) == obj.to_dict()


def test_agent_signal_round_trip_preserves_every_field():
    sig = a_signal()
    d = sig.to_dict()
    assert d["direction"] == "LONG"
    assert d["market_regime"] == "TREND_UP"
    assert d["volatility_regime"] == "NORMAL"
    assert d["volume_regime"] == "ABOVE_AVERAGE"
    assert d["entry"] == pytest.approx(21_850.25)
    assert d["entry_zone"] == [21_850.25, 21_852.75]
    assert d["targets"] == [21_870.25, 21_890.25]
    assert d["supporting_data"][0]["supports"] == "SHORT"
    assert d["historical_performance"]["trades"] == 142
    assert d["source"] == "hybrid"
    assert set(d) >= {"signal_id", "timestamp_et", "confluences", "conflicts",
                      "invalidation", "reasoning"}


def test_trade_callout_round_trip_nests_its_evidence_chain():
    d = a_callout().to_dict()
    assert d["decision"] == "LONG"
    assert d["news_risk"] == "LOW"
    assert d["risk_assessment"]["approved"] is True
    assert d["analyst_predictions"][0]["analyst_id"] == "A"
    assert d["analyst_predictions"][0]["evidence"][0]["name"] == "rsi"
    assert d["evidence_chain"][0]["timeframe"] == 5
    assert d["stop_loss"] == pytest.approx(21_840.25)


def test_journal_entry_round_trip_coerces_its_enums():
    d = a_journal_entry().to_dict()
    assert d["direction"] == "LONG"
    assert d["final_decision"] == "LONG"
    assert d["market_regime"] == "TREND_UP"
    assert d["volatility_regime"] == "HIGH"
    assert d["result"] == "WIN"
    assert d["thesis_correct"] is True


def test_news_context_round_trip_nests_events_and_reactions():
    d = a_news_context().to_dict()
    assert d["risk"] == "MODERATE"
    assert d["macro_bias"] == "SHORT"
    assert d["upcoming_events"][0]["title"] == "CPI"
    assert d["historical_analogues"][0]["move_15m"] == pytest.approx(-42.5)
    assert d["cross_market"]["DXY"] == "up"


def test_analyst_prediction_targets_property_skips_missing_levels():
    p = a_prediction()
    assert p.targets == [21_870.25, 21_890.25]
    assert p.target_3 is None


def test_risk_assessment_veto_reason_joins_every_veto():
    ra = RiskAssessment(vetoes=["stop too tight", "sample too small"])
    assert ra.approved is False
    assert ra.veto_reason == "stop too tight; sample too small"


def test_defaults_are_serialisable_too():
    """An agent that returns a bare object must not break the publish path."""
    for cls in (Evidence, HistoricalPerformance, AnalystPrediction,
                RiskAssessment, AgentSignal, TradeCallout, JournalEntry,
                NewsContext, NewsEvent, NewsReaction, StrategyStats):
        obj = cls(kind="x", name="y") if cls is Evidence else cls()
        json.dumps(obj.to_dict())
        assert to_json(obj)


def test_to_json_handles_containers_of_schema_objects():
    payload = {"predictions": [a_prediction(), a_prediction()],
               "regime": MarketRegime.RANGE}
    text = to_json(payload)
    restored = from_json(text)
    assert restored["regime"] == "RANGE"
    assert len(restored["predictions"]) == 2


# --------------------------------------------------------------------------
# Direction / Decision coercion
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("LONG", Direction.LONG), ("long", Direction.LONG), (" Long ", Direction.LONG),
    ("BUY", Direction.LONG), ("L", Direction.LONG), ("1", Direction.LONG),
    ("UP", Direction.LONG), ("BULLISH", Direction.LONG),
    ("SHORT", Direction.SHORT), ("sell", Direction.SHORT), ("S", Direction.SHORT),
    ("-1", Direction.SHORT), ("DOWN", Direction.SHORT),
    ("bearish", Direction.SHORT),
    ("NEUTRAL", Direction.NEUTRAL), ("", Direction.NEUTRAL),
    (None, Direction.NEUTRAL), ("gibberish", Direction.NEUTRAL),
    (0, Direction.NEUTRAL), (1, Direction.LONG), (-1, Direction.SHORT),
])
def test_direction_coercion(value, expected):
    """Model output arrives as free text; it must land on a legal value or on
    NEUTRAL, never on an exception in the middle of a live decision."""
    assert Direction.coerce(value) is expected


def test_direction_coerce_is_idempotent():
    for d in Direction:
        assert Direction.coerce(d) is d
        assert Direction.coerce(Direction.coerce(d)) is d


@pytest.mark.parametrize("direction,sign", [
    (Direction.LONG, 1), (Direction.SHORT, -1), (Direction.NEUTRAL, 0)])
def test_direction_sign(direction, sign):
    assert direction.sign == sign


@pytest.mark.parametrize("value,expected", [
    ("LONG", Decision.LONG), ("buy", Decision.LONG),
    ("SHORT", Decision.SHORT), ("sell", Decision.SHORT),
    ("NO TRADE", Decision.NO_TRADE), ("NO_TRADE", Decision.NO_TRADE),
    ("no_trade", Decision.NO_TRADE), ("", Decision.NO_TRADE),
    (None, Decision.NO_TRADE), ("maybe", Decision.NO_TRADE),
])
def test_decision_coercion(value, expected):
    """Anything unrecognised must fall to NO TRADE - the safe default, and a
    first-class outcome rather than a failure."""
    assert Decision.coerce(value) is expected


def test_decision_coerce_is_idempotent():
    for d in Decision:
        assert Decision.coerce(d) is d


def test_decision_actionability_and_direction_mapping():
    assert Decision.LONG.is_actionable is True
    assert Decision.SHORT.is_actionable is True
    assert Decision.NO_TRADE.is_actionable is False
    assert Decision.LONG.as_direction is Direction.LONG
    assert Decision.SHORT.as_direction is Direction.SHORT
    assert Decision.NO_TRADE.as_direction is Direction.NEUTRAL


def test_decision_value_is_the_spelling_the_specification_uses():
    assert Decision.NO_TRADE.value == "NO TRADE"


def test_news_risk_only_blackout_blocks_entry():
    assert NewsRisk.BLACKOUT.blocks_entry is True
    for risk in (NewsRisk.NONE, NewsRisk.LOW, NewsRisk.MODERATE, NewsRisk.HIGH):
        assert risk.blocks_entry is False


def test_market_regime_trending_flag():
    assert MarketRegime.TREND_UP.is_trending and MarketRegime.TREND_DOWN.is_trending
    for r in (MarketRegime.RANGE, MarketRegime.COMPRESSION,
              MarketRegime.VOLATILE_EXPANSION, MarketRegime.UNKNOWN):
        assert r.is_trending is False


def test_enums_serialise_as_their_string_value():
    assert json.dumps({"d": Direction.LONG.value}) == '{"d": "LONG"}'
    assert SignalStrength.STRONG.value == "STRONG"


# --------------------------------------------------------------------------
# Confidence clamping
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (0.0, 0.0), (0.5, 0.5), (1.0, 1.0),
    (1.4, 1.0), (99.0, 1.0),            # over-confident model output
    (-0.2, 0.0), (-50.0, 0.0),
    ("0.75", 0.75),                     # JSON sometimes arrives as a string
    (1, 1.0), (0, 0.0),
])
def test_confidence_is_clamped_to_the_unit_interval(raw, expected):
    assert Confidence(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "high", "", [], {}, object()])
def test_confidence_of_unusable_input_is_zero(raw):
    """A confidence that cannot be parsed is no confidence at all - it must not
    become an exception in the middle of a live decision."""
    assert Confidence(raw) == 0.0


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf")])
def test_confidence_of_nan_and_infinity_is_zero(raw):
    """NaN would poison every comparison it touched, including the min-confidence
    gate in the risk layer."""
    out = Confidence(raw)
    assert out == 0.0
    assert not math.isnan(out)


def test_confidence_output_is_always_a_float_in_range():
    for raw in (-1e9, -1.0, 0.0, 0.33, 1.0, 1e9, "2", "-2"):
        out = Confidence(raw)
        assert isinstance(out, float)
        assert 0.0 <= out <= 1.0


# --------------------------------------------------------------------------
# fmt_price preserves ticks
# --------------------------------------------------------------------------

def test_fmt_price_preserves_a_quarter_tick():
    """The regression this function exists for: %g would render "21850.2"."""
    assert fmt_price(21_850.25) == "21850.25"
    assert fmt_price(21_850.25) != "21850.2"
    assert f"{21_850.25:g}" == "21850.2", "the %g failure mode still exists"


@pytest.mark.parametrize("price,expected", [
    (21_850.25, "21850.25"),
    (21_850.75, "21850.75"),
    (21_850.5, "21850.5"),
    (21_850.0, "21850"),          # trailing zeros trimmed
    (5_900.25, "5900.25"),
    (2_401.1, "2401.1"),          # MGC, 0.1 tick
    (70.03, "70.03"),             # MCL, 0.01 tick
    (1.005, "1.005"),
    (0.0, "0"),
    (-21_850.25, "-21850.25"),
])
def test_fmt_price_renders_every_tick_grid_exactly(price, expected):
    assert fmt_price(price) == expected


def test_fmt_price_round_trips_back_to_the_same_number():
    """The real requirement: the rendered string must re-parse to the price."""
    for price in (21_850.25, 21_850.75, 5_900.5, 2_401.3, 70.07, 1_234.125):
        assert float(fmt_price(price)) == pytest.approx(price)


def test_fmt_price_handles_none_and_explicit_decimals():
    assert fmt_price(None) == "-"
    assert fmt_price(21_850.25, 2) == "21850.25"
    assert fmt_price(21_850.25, 0) == "21850"
    assert fmt_price(21_850.0, 2) == "21850.00"


def test_fmt_prices_joins_a_target_ladder():
    assert fmt_prices([21_870.25, 21_890.5]) == "21870.25, 21890.5"
    assert fmt_prices([]) == "-"


def test_rendered_blocks_quote_ticks_not_rounded_prices():
    """End to end: the tick must survive all the way into the rendered text a
    human reads, because that text is what a trade is placed from."""
    text = a_prediction().render()
    assert "21850.25" in text
    assert "21840.25" in text
    assert "21850.2 " not in text and "21850.2\n" not in text

    block = a_signal().render_block()
    assert "21850.25" in block and "21840.25" in block
    assert "21870.25, 21890.25" in block


def test_agent_signal_block_carries_the_specified_rows():
    """The shared-state block is a fixed format other agents parse."""
    block = a_signal().render_block()
    required = ["SYMBOL", "TIMEFRAME", "MARKET REGIME", "DIRECTION", "ENTRY",
                "STOP", "TARGETS", "RISK/REWARD", "CONFIDENCE", "STRATEGY",
                "CONFLUENCES", "CONFLICTS", "INVALIDATION", "SUPPORTING DATA",
                "HISTORICAL PERFORMANCE", "NEWS CONTEXT", "TIMESTAMP"]
    positions = [block.index(row) for row in required]
    assert positions == sorted(positions), "the block rows are out of order"
    assert "EDT" in block or "EST" in block, "the ET label must be on the stamp"
