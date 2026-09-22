"""The task board - the manager's ledger of work.

Tasks are explicit objects with an owner, a status, a priority and dependencies.
Nothing on this team starts because an agent felt like it: the manager creates
the task, routes it by kind, and the board refuses to release work whose
dependencies have not completed. That is what makes a run reproducible and
auditable - the board *is* the record of who was asked to do what, when, and
what came back.
"""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..timeutil import et_stamp, now_et, to_et
from .roles import Role, is_ambiguous, role_for_task, roles_for_task

__all__ = ["TaskStatus", "TaskPriority", "Task", "TaskBoard"]


class TaskStatus(str, Enum):
    PENDING = "PENDING"          # created, not yet assigned
    ASSIGNED = "ASSIGNED"        # routed to a role, waiting to run
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"          # waiting on a dependency or an answer
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"          # deliberately not run (e.g. trading halted)

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.SKIPPED)


class TaskPriority(IntEnum):
    CRITICAL = 0     # risk limits, account protection
    HIGH = 1         # live decision path
    NORMAL = 2       # routine research
    LOW = 3          # background / nice to have


@dataclass
class Task:
    """One unit of work owned by exactly one role."""

    task_id: str
    kind: str
    title: str
    detail: str = ""
    assigned_to: Optional[Role] = None
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    depends_on: Tuple[str, ...] = ()
    payload: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    attempts: int = 0
    max_attempts: int = 2
    created_et: str = field(default_factory=lambda: to_et(now_et()).isoformat())
    started_et: Optional[str] = None
    finished_et: Optional[str] = None
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "kind": self.kind, "title": self.title,
            "detail": self.detail,
            "assigned_to": self.assigned_to.value if self.assigned_to else None,
            "status": self.status.value, "priority": int(self.priority),
            "depends_on": list(self.depends_on), "attempts": self.attempts,
            "error": self.error, "created_et": self.created_et,
            "started_et": self.started_et, "finished_et": self.finished_et,
            "duration_s": round(self.duration_s, 3),
        }

    def render(self) -> str:
        glyph = {
            TaskStatus.PENDING: "·", TaskStatus.ASSIGNED: "→",
            TaskStatus.IN_PROGRESS: "*", TaskStatus.BLOCKED: "!",
            TaskStatus.DONE: "✓", TaskStatus.FAILED: "✗", TaskStatus.SKIPPED: "-",
        }[self.status]
        who = self.assigned_to.value if self.assigned_to else "unassigned"
        dur = f" {self.duration_s:.1f}s" if self.duration_s else ""
        return f" {glyph} [{self.task_id}] {who:<18} {self.title}{dur}"


class TaskBoard:
    """Ordered task store with dependency gating."""

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}
        self._order: List[str] = []
        self._counter = itertools.count(1)

    # ---- creation -----------------------------------------------------
    def add(self, kind: str, title: str, *, detail: str = "",
            priority: TaskPriority = TaskPriority.NORMAL,
            depends_on: Sequence[str] = (), payload: Optional[Dict[str, Any]] = None,
            assigned_to: Optional[Role] = None) -> Task:
        """Create a task and route it to the owning role.

        Routing is by declared task kind. An unroutable kind is created
        ``BLOCKED`` rather than silently dropped - unowned work is exactly the
        kind of thing that disappears from a pipeline unnoticed.
        """
        tid = f"T{next(self._counter):03d}"
        owner = assigned_to or role_for_task(kind)
        task = Task(
            task_id=tid, kind=kind, title=title, detail=detail,
            assigned_to=owner, priority=priority,
            depends_on=tuple(depends_on), payload=dict(payload or {}),
            status=TaskStatus.ASSIGNED if owner else TaskStatus.BLOCKED,
        )
        if owner is None:
            if is_ambiguous(kind):
                task.error = (
                    f"task kind {kind!r} is accepted by "
                    f"{[r.value for r in roles_for_task(kind)]} - pass an explicit "
                    "assigned_to")
            else:
                task.error = f"no role accepts task kind {kind!r}"
        self._tasks[tid] = task
        self._order.append(tid)
        return task

    # ---- access -------------------------------------------------------
    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def all(self) -> List[Task]:
        return [self._tasks[t] for t in self._order]

    def for_role(self, role: Role) -> List[Task]:
        return [t for t in self.all() if t.assigned_to is role]

    def by_status(self, status: TaskStatus) -> List[Task]:
        return [t for t in self.all() if t.status is status]

    def dependencies_met(self, task: Task) -> bool:
        for dep in task.depends_on:
            d = self._tasks.get(dep)
            if d is None or d.status is not TaskStatus.DONE:
                return False
        return True

    def ready(self) -> List[Task]:
        """Assigned tasks whose dependencies are satisfied, highest priority
        first, then creation order."""
        out = [t for t in self.all()
               if t.status is TaskStatus.ASSIGNED and self.dependencies_met(t)]
        out.sort(key=lambda t: (int(t.priority), self._order.index(t.task_id)))
        return out

    def next_task(self) -> Optional[Task]:
        r = self.ready()
        return r[0] if r else None

    # ---- transitions --------------------------------------------------
    def start(self, task: Task) -> Task:
        task.status = TaskStatus.IN_PROGRESS
        task.attempts += 1
        task.started_et = to_et(now_et()).isoformat()
        return task

    def complete(self, task: Task, result: Any = None) -> Task:
        task.status = TaskStatus.DONE
        task.result = result
        task.finished_et = to_et(now_et()).isoformat()
        task.duration_s = self._elapsed(task)
        return task

    def fail(self, task: Task, error: str) -> Task:
        task.error = error
        task.finished_et = to_et(now_et()).isoformat()
        task.duration_s = self._elapsed(task)
        # Retry once before giving up - a transient failure (a rate limit, a
        # slow feed) should not abandon a task the whole run depends on.
        if task.attempts < task.max_attempts:
            task.status = TaskStatus.ASSIGNED
            task.finished_et = None
        else:
            task.status = TaskStatus.FAILED
            self._cascade_block(task)
        return task

    def skip(self, task: Task, reason: str) -> Task:
        task.status = TaskStatus.SKIPPED
        task.error = reason
        task.finished_et = to_et(now_et()).isoformat()
        self._cascade_block(task)
        return task

    def block(self, task: Task, reason: str) -> Task:
        task.status = TaskStatus.BLOCKED
        task.error = reason
        return task

    def _cascade_block(self, task: Task) -> None:
        """A dependent of a failed task can never run; mark it, do not leave it
        sitting in ASSIGNED forever."""
        for t in self.all():
            if task.task_id in t.depends_on and not t.status.is_terminal:
                self.block(t, f"dependency {task.task_id} did not complete")

    @staticmethod
    def _elapsed(task: Task) -> float:
        if not task.started_et or not task.finished_et:
            return 0.0
        return (datetime.fromisoformat(task.finished_et)
                - datetime.fromisoformat(task.started_et)).total_seconds()

    # ---- reporting ----------------------------------------------------
    def counts(self) -> Dict[str, int]:
        return dict(Counter(t.status.value for t in self.all()))

    def by_role_counts(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for t in self.all():
            who = t.assigned_to.value if t.assigned_to else "unassigned"
            out[who][t.status.value] += 1
        return {k: dict(v) for k, v in sorted(out.items())}

    @property
    def is_complete(self) -> bool:
        return all(t.status.is_terminal or t.status is TaskStatus.BLOCKED
                   for t in self.all())

    @property
    def succeeded(self) -> bool:
        return all(t.status in (TaskStatus.DONE, TaskStatus.SKIPPED)
                   for t in self.all())

    def render(self, *, limit: int = 60) -> str:
        lines = [f"TASK BOARD  [{et_stamp()}]",
                 "  " + "  ".join(f"{k}={v}" for k, v in sorted(self.counts().items()))]
        for t in self.all()[:limit]:
            lines.append(t.render())
        remaining = len(self._order) - limit
        if remaining > 0:
            lines.append(f"  ... and {remaining} more")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"tasks": [t.to_dict() for t in self.all()],
                "counts": self.counts(), "by_role": self.by_role_counts()}

    def __len__(self) -> int:
        return len(self._tasks)

    def __repr__(self) -> str:
        return f"<TaskBoard tasks={len(self._tasks)} {self.counts()}>"
