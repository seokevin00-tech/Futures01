"""Usage budget monitoring and dispatch gating.

The property under test that matters most is a negative one: the monitor must
never report a percentage it cannot compute. The subscription limit is not
readable from inside a session, so an uncalibrated monitor that produced a
confident number would be worse than no monitor - the operator would trust it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from futures_agents.team.budget import (
    BudgetLimits, BudgetMonitor, BudgetStatus, DEFAULT_WEIGHTS,
)


@pytest.fixture()
def transcripts(tmp_path):
    """A fake transcript tree with known, exact token counts."""
    root = tmp_path / "projects" / "proj"
    root.mkdir(parents=True)
    now = datetime.now(timezone.utc)

    def turn(minutes_ago, out_tokens):
        return json.dumps({
            "timestamp": (now - timedelta(minutes=minutes_ago)).isoformat()
                         .replace("+00:00", "Z"),
            "message": {"usage": {"input_tokens": 100, "output_tokens": out_tokens,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}},
        })

    # Three turns inside the 5h session window, one well outside it.
    (root / "main.jsonl").write_text("\n".join([
        turn(10, 1000), turn(60, 1000), turn(200, 1000), turn(60 * 24 * 2, 1000),
    ]), encoding="utf-8")
    # A subagent transcript: delegated work spends the same budget.
    sub = root / "sub"
    sub.mkdir()
    (sub / "agent-x.jsonl").write_text(turn(30, 500), encoding="utf-8")
    return str(tmp_path / "projects")


def test_an_uncalibrated_monitor_refuses_to_invent_a_percentage(transcripts):
    monitor = BudgetMonitor(transcript_root=transcripts)
    state = monitor.check()
    assert state.status is BudgetStatus.UNKNOWN
    assert state.session.pct_used is None, "a percentage was invented"
    assert state.weekly.pct_used is None
    assert any("not readable from inside a session" in w for w in state.warnings)


def test_unknown_fails_open_rather_than_halting_the_team(transcripts):
    """A monitor that cannot measure the limit must not stop the team on its
    own ignorance. It warns; it does not block."""
    monitor = BudgetMonitor(transcript_root=transcripts)
    allowed, state = monitor.gate()
    assert state.status is BudgetStatus.UNKNOWN
    assert allowed is True


def test_consumption_is_measured_not_estimated(transcripts):
    monitor = BudgetMonitor(transcript_root=transcripts)
    session = monitor.measure(timedelta(hours=5), "session")
    # Four turns fall inside five hours: three from main plus one subagent.
    assert session.turns == 4
    assert session.output_tokens == 3500
    assert session.input_tokens == 400
    expected = 400 * DEFAULT_WEIGHTS["input_tokens"] + 3500 * DEFAULT_WEIGHTS["output_tokens"]
    assert session.effective_tokens == pytest.approx(expected)


def test_subagent_usage_counts_against_the_budget(transcripts):
    """Work delegated to an agent spends the same budget as work done directly;
    a monitor that ignored it would under-report exactly when the team is
    busiest."""
    monitor = BudgetMonitor(transcript_root=transcripts)
    assert monitor.measure(timedelta(hours=5), "s").turns == 4
    # Removing the subagent file must reduce the count.
    import os
    for path in list(os.walk(transcripts)):
        for name in path[2]:
            if name.startswith("agent-"):
                os.remove(os.path.join(path[0], name))
    assert BudgetMonitor(transcript_root=transcripts).measure(
        timedelta(hours=5), "s").turns == 3


def test_a_window_excludes_turns_outside_it(transcripts):
    monitor = BudgetMonitor(transcript_root=transcripts)
    assert monitor.measure(timedelta(hours=5), "session").turns == 4
    assert monitor.measure(timedelta(days=7), "weekly").turns == 5


def test_calibration_makes_the_percentage_real(transcripts):
    monitor = BudgetMonitor(transcript_root=transcripts)
    monitor.calibrate(session_pct_observed=50.0)
    state = monitor.check()
    assert state.session.pct_used == pytest.approx(50.0, abs=0.01)
    assert state.status is BudgetStatus.OK


@pytest.mark.parametrize("observed,expected", [
    (50.0, BudgetStatus.OK),
    (74.9, BudgetStatus.OK),
    (75.0, BudgetStatus.SLOW_DOWN),
    (79.9, BudgetStatus.SLOW_DOWN),
    (80.0, BudgetStatus.PAUSED),
    (95.0, BudgetStatus.PAUSED),
])
def test_session_thresholds(transcripts, observed, expected):
    monitor = BudgetMonitor(transcript_root=transcripts)
    monitor.calibrate(session_pct_observed=observed)
    assert monitor.check().status is expected


@pytest.mark.parametrize("observed,expected", [
    (70.0, BudgetStatus.OK),
    (80.0, BudgetStatus.SLOW_DOWN),
    (89.9, BudgetStatus.SLOW_DOWN),
    (90.0, BudgetStatus.PAUSED),
])
def test_weekly_thresholds(transcripts, observed, expected):
    monitor = BudgetMonitor(transcript_root=transcripts)
    # Session deliberately left low so the weekly window is the binding one.
    monitor.calibrate(session_pct_observed=5.0, weekly_pct_observed=observed)
    assert monitor.check().status is expected


def test_the_worse_window_binds(transcripts):
    """Session fine, weekly exhausted: the team still stops."""
    monitor = BudgetMonitor(transcript_root=transcripts)
    monitor.calibrate(session_pct_observed=10.0, weekly_pct_observed=95.0)
    state = monitor.check()
    assert state.status is BudgetStatus.PAUSED
    assert not state.may_place_work
    assert any("weekly" in r for r in state.reasons)


def test_paused_forbids_work_and_slow_down_permits_it(transcripts):
    monitor = BudgetMonitor(transcript_root=transcripts)
    monitor.calibrate(session_pct_observed=77.0)
    assert monitor.gate()[0] is True, "SLOW_DOWN must not stop the team"
    assert monitor.budget_multiplier() == pytest.approx(0.4)

    monitor.calibrate(session_pct_observed=85.0)
    assert monitor.gate()[0] is False
    assert monitor.budget_multiplier() == pytest.approx(0.0)


def test_calibration_survives_a_restart(transcripts, tmp_path):
    state_file = tmp_path / "budget_state.json"
    first = BudgetMonitor(transcript_root=transcripts, state_path=str(state_file))
    first.calibrate(session_pct_observed=60.0)
    limit = first.limits.session_limit_tokens

    second = BudgetMonitor(transcript_root=transcripts, state_path=str(state_file))
    assert second.limits.session_limit_tokens == pytest.approx(limit)


def test_calibration_without_a_figure_is_an_error_not_a_guess(transcripts):
    monitor = BudgetMonitor(transcript_root=transcripts)
    assert "error" in monitor.calibrate()
    assert monitor.limits.session_limit_tokens is None


def test_the_manager_halts_dispatch_when_the_budget_pauses(tmp_path, transcripts):
    """The gate is consulted BEFORE work is placed. Checking afterwards would
    mean the task that breached the limit has already been paid for."""
    from futures_agents.config import load_config
    from futures_agents.team import build_team

    team = build_team(load_config(), workspace_root=str(tmp_path / "ws"))
    budget = team.manager.budget
    assert budget is not None, "the budget seat must be staffed"
    budget.monitor.transcript_root = transcripts

    for i in range(5):
        team.manager.board.add("check_limits", f"task {i}", payload={"symbol": "MNQ"})

    budget.monitor.calibrate(session_pct_observed=85.0)
    report = team.manager.run_board()

    assert report.halted
    assert report.tasks_done == 0, "no task may be dispatched once paused"
    assert "80%" in report.halt_reason or "pause threshold" in report.halt_reason
