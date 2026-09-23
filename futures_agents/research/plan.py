"""A resumable, budget-gated research plan.

The problem this solves is not compute. A full pass over the strategy space is
537 core-hours; a sweep of a thousand strategies over five thousand bars takes
five seconds. Compute is cheap and bounded, and it does not touch a Claude
usage limit at all - it runs in a subprocess.

What consumes the session and weekly budget is **reading results**. So the
plan is built so that a chunk's output is a few hundred bytes of aggregate,
never a dump of trades, and so that stopping between any two chunks loses
nothing: state lives on disk, and a resumed run skips what is already done.

Two stages, because searching more is not the same as searching better:

* **Screen** - a wide, cheap pass. No walk-forward, no Monte Carlo, no
  parameter sensitivity. Its only job is to reject the 90% of candidates that
  never trade or lose outright, at roughly 5 seconds per thousand strategies.
* **Validate** - the expensive suite, run only on survivors: disjoint-period
  replication, anchored walk-forward, Monte Carlo against the real account.

Deflation is charged against the **screened** population, not the validated
one. Selecting 40 candidates from 20,000 and then deflating against 40 would
hide the search entirely, which is the single easiest way to manufacture a
result that looks significant and is not.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["ChunkState", "Chunk", "ResearchPlan", "build_plan"]

#: Where chunk state and results live. Deliberately outside the repository:
#: these are run artefacts, not source.
DEFAULT_STATE_DIR = "workspace/research_plan"


class ChunkState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"      # nothing survived the screen; validation pointless


@dataclass
class Chunk:
    """One unit of work, sized to be finishable and resumable.

    ``est_seconds`` is a projection from measured throughput - about five
    seconds per thousand strategies per five thousand bars - and exists so the
    plan can be ordered cheapest-first. A chunk that overruns its estimate is
    not an error; the estimate is for sequencing, not enforcement.
    """

    chunk_id: str
    stage: str               # "screen" | "validate"
    symbol: str
    horizon: str
    budget: int              # strategies to generate
    est_seconds: float
    state: ChunkState = ChunkState.PENDING
    depends_on: Optional[str] = None
    result_path: Optional[str] = None
    note: str = ""

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        d = dict(d)
        d["state"] = ChunkState(d.get("state", "PENDING"))
        return cls(**d)


@dataclass
class ResearchPlan:
    """An ordered, resumable list of chunks with a budget gate between them."""

    chunks: List[Chunk] = field(default_factory=list)
    state_dir: str = DEFAULT_STATE_DIR

    # ---- persistence -------------------------------------------------
    @property
    def manifest_path(self) -> str:
        return os.path.join(self.state_dir, "manifest.json")

    def save(self) -> str:
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as fh:
            json.dump({"chunks": [c.to_dict() for c in self.chunks]}, fh, indent=1)
        return self.manifest_path

    @classmethod
    def load(cls, state_dir: str = DEFAULT_STATE_DIR) -> Optional["ResearchPlan"]:
        path = os.path.join(state_dir, "manifest.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(chunks=[Chunk.from_dict(c) for c in raw.get("chunks", [])],
                   state_dir=state_dir)

    # ---- scheduling --------------------------------------------------
    def ready(self) -> List[Chunk]:
        """Chunks whose dependency is satisfied and which are not yet done."""
        done = {c.chunk_id for c in self.chunks if c.state is ChunkState.DONE}
        skipped = {c.chunk_id for c in self.chunks if c.state is ChunkState.SKIPPED}
        out = []
        for c in self.chunks:
            if c.state is not ChunkState.PENDING:
                continue
            if c.depends_on is None:
                out.append(c)
            elif c.depends_on in done:
                out.append(c)
            elif c.depends_on in skipped:
                c.state = ChunkState.SKIPPED
                c.note = "screen produced no survivors"
        return out

    def next_batch(self, workers: int, multiplier: float = 1.0) -> List[Chunk]:
        """The next chunks to run, narrowed by the budget multiplier.

        ``multiplier`` comes from the usage monitor: 1.0 when clear, less when
        slowing down, and the caller must not call this at all when paused.
        Narrowing the BATCH rather than the chunk keeps every unit of work
        whole, so a slowdown never leaves a half-measured symbol.
        """
        avail = self.ready()
        avail.sort(key=lambda c: (c.est_seconds, c.chunk_id))
        n = max(1, int(round(workers * max(0.0, min(1.0, multiplier)))))
        return avail[:n]

    def mark(self, chunk_id: str, state: ChunkState, *,
             result_path: Optional[str] = None, note: str = "") -> None:
        for c in self.chunks:
            if c.chunk_id == chunk_id:
                c.state = state
                if result_path:
                    c.result_path = result_path
                if note:
                    c.note = note
        self.save()

    def summary(self) -> Dict[str, Any]:
        by = {s.value: 0 for s in ChunkState}
        for c in self.chunks:
            by[c.state.value] += 1
        remaining = sum(c.est_seconds for c in self.chunks
                        if c.state in (ChunkState.PENDING, ChunkState.RUNNING))
        return {"total": len(self.chunks), "by_state": by,
                "est_remaining_seconds": round(remaining, 1)}


#: Bars available per horizon in the supplied data, used for the estimate.
_BARS = {"position": 1859, "swing": 5000, "intraday": 3753, "fast": 5000}
#: Measured: ~5s per 1000 strategies per 5000 bars.
_RATE = 5.0 / 1000 / 5000


def build_plan(symbols: Sequence[str], horizons: Sequence[str], *,
               screen_budget: int = 6000, state_dir: str = DEFAULT_STATE_DIR
               ) -> ResearchPlan:
    """A screen chunk per (symbol, horizon), each followed by its validation.

    Validation depends on its own screen and on nothing else, so the units are
    independent and any number of workers can run them without coordination.
    """
    chunks: List[Chunk] = []
    for sym in symbols:
        for hz in horizons:
            bars = _BARS.get(hz, 3000)
            sid = f"screen:{sym}:{hz}"
            chunks.append(Chunk(
                chunk_id=sid, stage="screen", symbol=sym, horizon=hz,
                budget=screen_budget,
                est_seconds=round(screen_budget * bars * _RATE, 1)))
            chunks.append(Chunk(
                chunk_id=f"validate:{sym}:{hz}", stage="validate", symbol=sym,
                horizon=hz, budget=0, depends_on=sid,
                # Validation runs a handful of survivors through three periods
                # plus a walk-forward, so it is bounded by folds rather than by
                # the screen budget.
                est_seconds=round(bars * _RATE * 400 * 4, 1)))
    return ResearchPlan(chunks=chunks, state_dir=state_dir)
