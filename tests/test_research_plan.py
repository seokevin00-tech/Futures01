"""The research plan: resumable, dependency-gated, and budget-narrowed.

The properties worth pinning are not about backtesting at all. They are that
stopping between any two chunks loses nothing, that a validation never runs
against a screen that produced nothing, and that a budget slowdown narrows the
batch rather than the unit of work - a half-measured symbol is worse than an
unmeasured one, because it looks like a result.
"""

from __future__ import annotations

import pytest

from futures_agents.research.plan import (Chunk, ChunkState, ResearchPlan,
                                          build_plan)


@pytest.fixture
def plan(tmp_path):
    return build_plan(["MNQ", "MES"], ["position", "swing"],
                      state_dir=str(tmp_path / "state"))


def test_every_screen_has_a_validation_that_depends_on_it(plan):
    screens = {c.chunk_id for c in plan.chunks if c.stage == "screen"}
    vals = [c for c in plan.chunks if c.stage == "validate"]
    assert len(vals) == len(screens)
    for v in vals:
        assert v.depends_on in screens


def test_validation_is_not_ready_until_its_screen_is_done(plan):
    ready = {c.chunk_id for c in plan.next_batch(99)}
    assert all(c.startswith("screen:") for c in ready), ready
    plan.mark("screen:MNQ:position", ChunkState.DONE)
    assert "validate:MNQ:position" in {c.chunk_id for c in plan.next_batch(99)}
    assert "validate:MES:position" not in {c.chunk_id for c in plan.next_batch(99)}


def test_a_barren_screen_skips_its_validation_instead_of_running_it(plan):
    """Running the expensive suite on nothing is not harmless - it produces an
    artefact that reads like a measured negative rather than an absence."""
    plan.mark("screen:MNQ:swing", ChunkState.SKIPPED)
    plan.next_batch(99)          # resolution happens during scheduling
    v = next(c for c in plan.chunks if c.chunk_id == "validate:MNQ:swing")
    assert v.state is ChunkState.SKIPPED
    assert "no survivors" in v.note


def test_the_budget_multiplier_narrows_the_batch_not_the_chunk(plan):
    full = plan.next_batch(4, multiplier=1.0)
    slowed = plan.next_batch(4, multiplier=0.5)
    assert len(slowed) < len(full)
    # Every chunk that DOES run is untouched: same budget, same scope.
    for c in slowed:
        assert c.budget == next(x.budget for x in plan.chunks
                                if x.chunk_id == c.chunk_id)


def test_a_slowdown_never_produces_an_empty_batch(plan):
    """Narrowing to zero would stall the plan silently rather than slow it."""
    assert plan.next_batch(4, multiplier=0.0)
    assert plan.next_batch(1, multiplier=0.01)


def test_state_survives_a_restart(plan, tmp_path):
    plan.mark("screen:MNQ:position", ChunkState.DONE, result_path="x.json")
    plan.mark("screen:MES:swing", ChunkState.FAILED, note="boom")
    reloaded = ResearchPlan.load(plan.state_dir)
    assert reloaded is not None
    by = {c.chunk_id: c for c in reloaded.chunks}
    assert by["screen:MNQ:position"].state is ChunkState.DONE
    assert by["screen:MNQ:position"].result_path == "x.json"
    assert by["screen:MES:swing"].state is ChunkState.FAILED
    # A resumed run must not redo completed work.
    assert "screen:MNQ:position" not in {c.chunk_id for c in reloaded.next_batch(99)}


def test_cheapest_chunks_are_scheduled_first(plan):
    batch = plan.next_batch(99)
    costs = [c.est_seconds for c in batch]
    assert costs == sorted(costs)


def test_summary_counts_every_chunk(plan):
    s = plan.summary()
    assert s["total"] == len(plan.chunks)
    assert sum(s["by_state"].values()) == s["total"]
    assert s["est_remaining_seconds"] > 0
