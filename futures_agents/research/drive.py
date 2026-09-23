"""Run the plan in budget-gated batches.

The gate is the usage monitor with the thresholds the operator set: slow at
75% of the session limit, pause at 80%; slow at 80% of the weekly, pause at
90%. Between every batch the monitor is consulted, and the answer narrows the
batch rather than the chunk - a slowdown must never leave a symbol
half-measured.

**When the monitor cannot compute a percentage it says so and does not
guess.** Nothing inside a session reveals the subscription ceiling, so an
uncalibrated monitor reports measured consumption with the denominator marked
unknown, and this driver keeps running. A gate that invented a denominator
would pause on a number it made up, which is worse than no gate: it would look
like protection and be a coin flip.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import List, Optional

from ..team.budget import BudgetLimits, BudgetMonitor, BudgetStatus
from .plan import Chunk, ChunkState, ResearchPlan, build_plan

STATE_DIR = os.environ.get("RESEARCH_OUT", "workspace/research_plan")


def _monitor() -> BudgetMonitor:
    return BudgetMonitor(
        BudgetLimits(session_slow_at=75.0, session_pause_at=80.0,
                     weekly_slow_at=80.0, weekly_pause_at=90.0),
        state_path=os.path.join(STATE_DIR, "budget_state.json"))


def _run(chunk: Chunk) -> bool:
    cmd = [sys.executable, "-m", "futures_agents.research.runner",
           chunk.stage, chunk.symbol, chunk.horizon, str(chunk.budget)]
    env = dict(os.environ, RESEARCH_OUT=STATE_DIR, PYTHONPATH=os.getcwd())
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          timeout=1800)
    if proc.stdout.strip():
        print("   " + proc.stdout.strip(), flush=True)
    if proc.returncode != 0:
        print(f"   {chunk.chunk_id} FAILED: {proc.stderr.strip()[-200:]}", flush=True)
    return proc.returncode == 0


def drive(workers: int = 5, max_batches: int = 50,
          plan: Optional[ResearchPlan] = None) -> ResearchPlan:
    plan = plan or ResearchPlan.load(STATE_DIR) or build_plan(
        ["MNQ", "MES", "MGC"], ["position", "swing", "intraday"],
        state_dir=STATE_DIR)
    plan.save()
    mon = _monitor()

    for batch_no in range(max_batches):
        state = mon.check()
        if state.status is BudgetStatus.PAUSED:
            print(f"PAUSED by usage budget: {'; '.join(state.reasons)}", flush=True)
            print("  work is checkpointed; rerunning resumes where it stopped.",
                  flush=True)
            break
        mult = mon.budget_multiplier()
        batch = plan.next_batch(workers, mult)
        if not batch:
            break

        tag = (f"  [budget {state.status.value}, running "
               f"{len(batch)}/{workers} workers]" if mult < 1.0 else "")
        print(f"\nbatch {batch_no + 1}: {len(batch)} chunks{tag}", flush=True)
        for c in batch:
            plan.mark(c.chunk_id, ChunkState.RUNNING)
        procs = []
        for c in batch:
            procs.append((c, subprocess.Popen(
                [sys.executable, "-m", "futures_agents.research.runner",
                 c.stage, c.symbol, c.horizon, str(c.budget)],
                env=dict(os.environ, RESEARCH_OUT=STATE_DIR,
                         PYTHONPATH=os.getcwd()),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
        for c, p in procs:
            out, err = p.communicate(timeout=1800)
            if out.strip():
                print("   " + out.strip(), flush=True)
            if p.returncode == 0:
                plan.mark(c.chunk_id, ChunkState.DONE,
                          result_path=os.path.join(
                              STATE_DIR, f"{c.stage}_{c.symbol}_{c.horizon}.json"))
            else:
                plan.mark(c.chunk_id, ChunkState.FAILED,
                          note=err.strip().splitlines()[-1][:160] if err.strip() else "")

    s = plan.summary()
    print(f"\nplan: {s['by_state']['DONE']}/{s['total']} done, "
          f"{s['by_state']['PENDING']} pending, {s['by_state']['FAILED']} failed, "
          f"{s['by_state']['SKIPPED']} skipped", flush=True)
    return plan


if __name__ == "__main__":
    drive(workers=int(sys.argv[1]) if len(sys.argv) > 1 else 5)
