"""Team infrastructure: workspace isolation, bus permissions, the task board.

These three mechanisms are what make a multi-agent run auditable rather than a
pile of files nobody owns:

* an agent can only write inside its own directory, checked after path
  resolution so ``../``, absolute paths and symlinks are all caught;
* the three live analysts physically cannot address each other, which is what
  makes "three independent opinions" true rather than aspirational;
* no task runs before its dependencies complete, and nothing silently
  disappears - an unroutable or orphaned task is BLOCKED with a reason.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from futures_agents.team.board import Task, TaskBoard, TaskPriority, TaskStatus
from futures_agents.team.bus import (MessageBus, MessageKind,
                                     MessagePermissionError)
from futures_agents.team.roles import (ROLES, Role, is_ambiguous, may_message,
                                       role_for_task, roles_for_task)
from futures_agents.team.workspace import (TeamFilesystem, Workspace,
                                           WorkspaceViolation)

ANALYSTS = (Role.ANALYST_A, Role.ANALYST_B, Role.ANALYST_C)


@pytest.fixture
def fs(tmp_path: Path) -> TeamFilesystem:
    return TeamFilesystem(base=str(tmp_path / "workspace"))


@pytest.fixture
def ws(fs: TeamFilesystem) -> Workspace:
    return fs.workspace(Role.ANALYST_A)


# --------------------------------------------------------------------------
# Workspace isolation
# --------------------------------------------------------------------------

def test_an_agent_can_write_inside_its_own_workspace(ws: Workspace):
    path = ws.write_text("scratch/notes.txt", "hello")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "hello"
    assert ws.root in path.parents


@pytest.mark.parametrize("relative", [
    "../escape.json",
    "../../escape.json",
    "out/../../escape.json",
    "scratch/../../../escape.json",
    "./../escape.json",
])
def test_parent_traversal_is_refused(ws: Workspace, relative):
    with pytest.raises(WorkspaceViolation):
        ws.write_text(relative, "should not land")


def test_absolute_paths_are_refused(ws: Workspace, tmp_path: Path):
    outside = tmp_path / "absolute.json"
    with pytest.raises(WorkspaceViolation):
        ws.write_text(str(outside), "should not land")
    assert not outside.exists()


def test_writing_into_another_agents_workspace_is_refused(fs: TeamFilesystem):
    a = fs.workspace(Role.ANALYST_A)
    b = fs.workspace(Role.ANALYST_B)
    target = os.path.relpath(str(b.out / "stolen.json"), str(a.root))
    assert target.startswith("..")
    with pytest.raises(WorkspaceViolation):
        a.write_text(target, "{}")
    assert not (b.out / "stolen.json").exists()


def test_a_symlink_out_of_the_workspace_is_refused(ws: Workspace, tmp_path: Path):
    """Resolution happens before the check precisely so this cannot slip past."""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = ws.root / "escape_link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):      # pragma: no cover
        pytest.skip("symlinks unavailable on this filesystem")
    with pytest.raises(WorkspaceViolation):
        ws.write_text("escape_link/loot.json", "{}")
    assert not (outside / "loot.json").exists()


def test_workspace_violation_is_a_permission_error(ws: Workspace):
    """Callers may catch the broad type; the message must name the offender."""
    assert issubclass(WorkspaceViolation, PermissionError)
    with pytest.raises(PermissionError) as excinfo:
        ws.write_json("../escape.json", {})
    assert Role.ANALYST_A.value in str(excinfo.value)


def test_publish_lands_in_the_agents_own_out_directory(ws: Workspace):
    path = ws.publish("prediction_a", {"direction": "LONG"}, )
    assert path.parent == ws.out
    assert path.name == "prediction_a.json"
    assert ws.artefacts() == ["prediction_a.json"]


def test_publish_cannot_be_used_to_escape(ws: Workspace, fs: TeamFilesystem):
    """The artefact name is sanitised, so a traversal attempt becomes a plain
    file name rather than an escape."""
    path = ws.publish("../../escape", {"x": 1})
    assert ws.root in path.parents
    assert path.parent == ws.out


def test_read_artefact_reaches_out_but_not_scratch(fs: TeamFilesystem):
    author = fs.workspace(Role.ANALYST_B)
    author.publish("prediction_b", {"direction": "SHORT"})
    author.scratch_json("private_notes", {"secret": True})

    assert fs.read_artefact(Role.ANALYST_B, "prediction_b") == {"direction": "SHORT"}
    assert fs.read_artefact(Role.ANALYST_B, "private_notes") is None
    assert fs.read_artefact(Role.ANALYST_B, "does_not_exist") is None


def test_reading_outside_the_workspace_returns_none_rather_than_leaking(
        ws: Workspace, tmp_path: Path):
    secret = tmp_path / "secret.txt"
    secret.write_text("classified", encoding="utf-8")
    assert ws.read_text(str(secret)) is None
    assert ws.read_text("../secret.txt") is None


def test_every_role_gets_its_own_directory(fs: TeamFilesystem):
    roots = {role: fs.workspace(role).root for role in Role}
    assert len(set(roots.values())) == len(Role)
    for root in roots.values():
        assert (root / "out").is_dir()
        assert (root / "scratch").is_dir()


def test_write_json_is_valid_json(ws: Workspace):
    path = ws.write_json("out/payload.json", {"a": [1, 2], "b": None})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": [1, 2], "b": None}


def test_clear_scratch_leaves_published_artefacts_alone(ws: Workspace):
    ws.publish("keep_me", {"k": 1})
    ws.scratch_json("throw_me", {"k": 2})
    ws.clear_scratch()
    assert ws.artefacts() == ["keep_me.json"]
    assert list(ws.scratch.iterdir()) == []


# --------------------------------------------------------------------------
# Bus permissions
# --------------------------------------------------------------------------

def test_analyst_to_analyst_messaging_raises():
    """Analyst independence is enforced in code, not by good intentions."""
    bus = MessageBus()
    with pytest.raises(MessagePermissionError):
        bus.send(Role.ANALYST_A, Role.ANALYST_B, "what do you think?")


@pytest.mark.parametrize("sender", ANALYSTS)
@pytest.mark.parametrize("recipient", ANALYSTS)
def test_no_analyst_may_address_any_other_analyst(sender, recipient):
    if sender is recipient:
        return
    bus = MessageBus()
    assert may_message(sender, recipient) is False
    with pytest.raises(MessagePermissionError):
        bus.send(sender, recipient, "coordinate?")


@pytest.mark.parametrize("sender", ANALYSTS)
def test_analyst_to_decision_succeeds(sender):
    bus = MessageBus()
    msg = bus.send(sender, Role.DECISION, "prediction ready", {"direction": "LONG"})
    assert msg.sender is sender
    assert msg.recipient is Role.DECISION
    assert msg.kind is MessageKind.DIRECT
    assert bus.pending(Role.DECISION) == 1
    assert bus.drain(Role.DECISION)[0].payload == {"direction": "LONG"}


def test_the_permission_error_lists_what_is_allowed():
    bus = MessageBus()
    with pytest.raises(MessagePermissionError) as excinfo:
        bus.send(Role.ANALYST_A, Role.ANALYST_C, "hello")
    text = str(excinfo.value)
    assert Role.ANALYST_A.value in text and Role.ANALYST_C.value in text
    assert Role.DECISION.value in text, "the message should name the legal routes"


def test_a_refused_message_is_not_journalled_or_delivered():
    bus = MessageBus()
    with pytest.raises(MessagePermissionError):
        bus.send(Role.ANALYST_A, Role.ANALYST_B, "leak")
    assert bus.journal == []
    assert bus.pending(Role.ANALYST_B) == 0


def test_broadcasts_reach_everyone_but_the_sender():
    """Announcements carry no permission check - hearing that the news context
    updated is not the same as being directed by another analyst."""
    bus = MessageBus()
    bus.broadcast(Role.NEWS_MACRO, "news_context updated", {"risk": "HIGH"})
    assert bus.pending(Role.NEWS_MACRO) == 0
    for role in (Role.ANALYST_A, Role.ANALYST_B, Role.ANALYST_C, Role.DECISION):
        assert bus.pending(role) == 1


def test_publish_is_a_broadcast_naming_the_artefact():
    bus = MessageBus()
    bus.publish(Role.RISK, "risk_assessment", "1 contract approved")
    msg = bus.drain(Role.DECISION)[0]
    assert msg.kind is MessageKind.PUBLISH
    assert msg.subject == "published:risk_assessment"
    assert msg.payload["artefact"] == "risk_assessment"


def test_permissions_can_be_disabled_only_explicitly():
    """The relaxed bus exists for harnesses; the default must be strict."""
    assert MessageBus().enforce_permissions is True
    lax = MessageBus(enforce_permissions=False)
    assert lax.send(Role.ANALYST_A, Role.ANALYST_B, "allowed here").seq == 1


def test_the_manager_may_address_every_role():
    bus = MessageBus()
    for role in Role:
        if role is Role.MANAGER:
            continue
        assert bus.send(Role.MANAGER, role, "status?").recipient is role


def test_message_journal_is_ordered_and_serialisable():
    bus = MessageBus()
    bus.send(Role.ANALYST_A, Role.DECISION, "first")
    bus.send(Role.ANALYST_B, Role.DECISION, "second")
    seqs = [m.seq for m in bus.journal]
    assert seqs == sorted(seqs) == [1, 2]
    json.dumps(bus.to_dict())       # must not raise


def test_a_failing_subscriber_does_not_take_down_the_bus():
    bus = MessageBus()

    def explode(msg):
        raise RuntimeError("handler is broken")

    bus.subscribe(Role.DECISION, explode, [MessageKind.DIRECT])
    msg = bus.send(Role.ANALYST_A, Role.DECISION, "still delivered")
    assert msg.seq == 1
    assert bus.pending(Role.DECISION) == 1


# --------------------------------------------------------------------------
# Task board: routing
# --------------------------------------------------------------------------

def test_an_unambiguous_kind_is_routed_to_its_owner():
    board = TaskBoard()
    task = board.add("backtest", "Backtest MNQ ORB")
    assert task.assigned_to is Role.STRATEGY_RESEARCH
    assert task.status is TaskStatus.ASSIGNED
    assert task.error == ""


def test_predict_is_ambiguous_and_is_blocked_with_an_explanation():
    """All three analysts accept ``predict`` on purpose. Handing it to whichever
    one came last in the dictionary would quietly reduce three independent
    opinions to one, so the board refuses to guess."""
    assert is_ambiguous("predict")
    assert set(roles_for_task("predict")) == set(ANALYSTS)
    assert role_for_task("predict") is None

    board = TaskBoard()
    task = board.add("predict", "Predict MNQ direction")
    assert task.assigned_to is None
    assert task.status is TaskStatus.BLOCKED
    assert task.error, "a blocked task with no explanation is untraceable"
    assert "predict" in task.error
    assert "assigned_to" in task.error
    for analyst in ANALYSTS:
        assert analyst.value in task.error
    assert task not in board.ready()


def test_predict_with_an_explicit_assignee_is_accepted():
    board = TaskBoard()
    task = board.add("predict", "Predict MNQ direction",
                     assigned_to=Role.ANALYST_B)
    assert task.assigned_to is Role.ANALYST_B
    assert task.status is TaskStatus.ASSIGNED
    assert task.error == ""
    assert task in board.ready()


def test_an_unknown_kind_is_blocked_rather_than_dropped():
    board = TaskBoard()
    task = board.add("read_the_tea_leaves", "Divine the close")
    assert task.status is TaskStatus.BLOCKED
    assert "no role accepts" in task.error
    assert len(board) == 1, "unowned work must still be visible on the board"


def test_every_declared_task_kind_has_at_least_one_owner():
    for spec in ROLES.values():
        for kind in spec.accepts:
            assert roles_for_task(kind), f"{kind!r} is declared but unroutable"


# --------------------------------------------------------------------------
# Task board: dependency gating
# --------------------------------------------------------------------------

def test_a_task_with_an_incomplete_dependency_is_not_ready():
    board = TaskBoard()
    first = board.add("backtest", "Backtest")
    second = board.add("rank_strategies", "Rank", depends_on=[first.task_id])

    assert board.dependencies_met(second) is False
    ready = board.ready()
    assert first in ready and second not in ready
    assert board.next_task() is first

    board.complete(board.start(first), result={"ok": True})
    assert board.dependencies_met(second) is True
    assert second in board.ready()
    assert board.next_task() is second


def test_a_dependency_on_a_missing_task_is_never_met():
    board = TaskBoard()
    task = board.add("backtest", "Backtest", depends_on=["T999"])
    assert board.dependencies_met(task) is False
    assert task not in board.ready()


def test_a_dependency_that_was_skipped_does_not_release_the_dependent():
    board = TaskBoard()
    first = board.add("backtest", "Backtest")
    second = board.add("rank_strategies", "Rank", depends_on=[first.task_id])
    board.skip(first, "trading halted")
    assert first.status is TaskStatus.SKIPPED
    assert board.dependencies_met(second) is False
    assert second.status is TaskStatus.BLOCKED


def test_ready_is_ordered_by_priority_then_creation():
    board = TaskBoard()
    normal = board.add("backtest", "Normal")
    critical = board.add("check_limits", "Critical", priority=TaskPriority.CRITICAL)
    low = board.add("robustness", "Low", priority=TaskPriority.LOW)
    assert [t.task_id for t in board.ready()] == [
        critical.task_id, normal.task_id, low.task_id]


# --------------------------------------------------------------------------
# Task board: failure, retry and cascade
# --------------------------------------------------------------------------

def test_a_failed_task_retries_once_then_fails_and_cascades():
    board = TaskBoard()
    flaky = board.add("backtest", "Flaky backtest")
    dependent = board.add("rank_strategies", "Rank", depends_on=[flaky.task_id])
    assert flaky.max_attempts == 2

    # First failure: back into the queue, not abandoned.
    board.start(flaky)
    board.fail(flaky, "transient feed error")
    assert flaky.attempts == 1
    assert flaky.status is TaskStatus.ASSIGNED
    assert flaky in board.ready()
    assert dependent.status is TaskStatus.ASSIGNED

    # Second failure: terminal, and the dependent can never run.
    board.start(flaky)
    board.fail(flaky, "transient feed error")
    assert flaky.attempts == 2
    assert flaky.status is TaskStatus.FAILED
    assert flaky.error == "transient feed error"
    assert dependent.status is TaskStatus.BLOCKED
    assert flaky.task_id in dependent.error
    assert dependent not in board.ready()


def test_the_cascade_reaches_every_dependent():
    board = TaskBoard()
    root = board.add("backtest", "Root")
    a = board.add("rank_strategies", "A", depends_on=[root.task_id])
    b = board.add("robustness", "B", depends_on=[root.task_id])
    unrelated = board.add("walk_forward", "Unrelated")

    board.start(root); board.fail(root, "boom")
    board.start(root); board.fail(root, "boom")

    assert a.status is TaskStatus.BLOCKED
    assert b.status is TaskStatus.BLOCKED
    assert unrelated.status is TaskStatus.ASSIGNED


def test_the_cascade_does_not_disturb_a_dependent_that_already_finished():
    board = TaskBoard()
    root = board.add("backtest", "Root")
    done = board.add("rank_strategies", "Already done", depends_on=[root.task_id])
    board.complete(board.start(done), result="fine")

    board.start(root); board.fail(root, "boom")
    board.start(root); board.fail(root, "boom")
    assert done.status is TaskStatus.DONE


def test_a_successful_retry_completes_normally():
    board = TaskBoard()
    task = board.add("backtest", "Flaky")
    board.start(task)
    board.fail(task, "transient")
    assert task.status is TaskStatus.ASSIGNED
    board.start(task)
    board.complete(task, result={"trades": 12})
    assert task.status is TaskStatus.DONE
    assert task.attempts == 2
    assert task.result == {"trades": 12}
    assert task.finished_et is not None


def test_board_status_summary_and_serialisation():
    board = TaskBoard()
    done = board.add("backtest", "Done")
    board.complete(board.start(done))
    board.add("predict", "Ambiguous")
    counts = board.counts()
    assert counts[TaskStatus.DONE.value] == 1
    assert counts[TaskStatus.BLOCKED.value] == 1
    assert board.is_complete is True          # terminal or blocked
    assert board.succeeded is False           # a blocked task is not success
    json.dumps(board.to_dict())


def test_task_status_terminality():
    assert TaskStatus.DONE.is_terminal
    assert TaskStatus.FAILED.is_terminal
    assert TaskStatus.SKIPPED.is_terminal
    assert not TaskStatus.BLOCKED.is_terminal
    assert not TaskStatus.ASSIGNED.is_terminal
