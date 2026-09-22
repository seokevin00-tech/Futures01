"""Base class for every team member.

A :class:`TeamAgent` binds a role to its private workspace and its bus
connection, and gives it exactly one entry point - :meth:`handle` - which the
manager calls with a task. Anything an agent wants to share, it publishes;
anything it wants to ask, it sends. There is no other channel.
"""

from __future__ import annotations

import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..timeutil import et_stamp, now_et
from .board import Task
from .bus import Message, MessageBus, MessageKind
from .roles import Role, ROLES, RoleSpec
from .workspace import TeamFilesystem, Workspace

__all__ = ["AgentResult", "TeamAgent"]


@dataclass
class AgentResult:
    """What an agent hands back to the manager."""

    ok: bool
    summary: str = ""
    payload: Any = None
    artefacts: List[str] = field(default_factory=list)
    error: str = ""
    duration_s: float = 0.0
    messages_sent: int = 0

    def to_dict(self) -> dict:
        return {"ok": self.ok, "summary": self.summary, "artefacts": self.artefacts,
                "error": self.error, "duration_s": round(self.duration_s, 3),
                "messages_sent": self.messages_sent}

    @staticmethod
    def failure(error: str) -> "AgentResult":
        return AgentResult(ok=False, error=error, summary=error)


class TeamAgent(ABC):
    """One agent: a role, a workspace it alone writes to, and a bus handle."""

    def __init__(self, role: Role, fs: TeamFilesystem, bus: MessageBus, *,
                 config: Any = None, llm: Any = None):
        self.role = role
        self.spec: RoleSpec = ROLES[role]
        self.fs = fs
        self.workspace: Workspace = fs.workspace(role)
        self.bus = bus
        self.config = config
        self.llm = llm
        self._sent = 0
        self.bus.subscribe(role, self.on_message)

    # ---- identity -----------------------------------------------------
    @property
    def id(self) -> str:
        return self.role.value

    @property
    def title(self) -> str:
        return self.spec.title

    def accepts(self, kind: str) -> bool:
        return kind in self.spec.accepts

    # ---- messaging ----------------------------------------------------
    def send(self, recipient: Role, subject: str, payload: Any = None,
             kind: MessageKind = MessageKind.DIRECT) -> Optional[Message]:
        msg = self.bus.send(self.role, recipient, subject, payload, kind=kind)
        self._sent += 1
        return msg

    def ask(self, recipient: Role, subject: str, payload: Any = None) -> Any:
        self._sent += 1
        return self.bus.request(self.role, recipient, subject, payload)

    def announce(self, subject: str, payload: Any = None) -> Message:
        self._sent += 1
        return self.bus.broadcast(self.role, subject, payload)

    def publish(self, name: str, payload: Any, summary: str = "") -> str:
        """Write an artefact into this agent's own ``out/`` and tell the team."""
        path = self.workspace.publish(name, payload)
        self.bus.publish(self.role, name, summary or name, {"path": str(path)})
        self._sent += 1
        self.log(f"published {name}" + (f" - {summary}" if summary else ""))
        return str(path)

    def read_from(self, owner: Role, artefact: str) -> Any:
        """Read another agent's published artefact.

        Refused for roles marked ``independent`` reading a peer analyst - the
        three live analysts must reach their conclusions without seeing each
        other's, or the disagreement the decision layer relies on is
        manufactured rather than real.
        """
        if self.spec.independent and ROLES[owner].independent and owner is not self.role:
            raise PermissionError(
                f"'{self.id}' may not read '{owner.value}' - the live analysts form "
                "their views independently")
        return self.fs.read_artefact(owner, artefact)

    def inbox(self) -> List[Message]:
        return self.bus.drain(self.role)

    def on_message(self, message: Message) -> Optional[Any]:
        """Default handler: a REQUEST is answered by running it as a task."""
        if message.kind is MessageKind.REQUEST:
            payload = message.payload if isinstance(message.payload, dict) else {}
            kind = payload.get("kind", "")
            if self.accepts(kind):
                task = Task(task_id=f"req-{message.seq}", kind=kind,
                            title=message.subject, payload=payload,
                            assigned_to=self.role)
                return self.run(task).to_dict()
        return None

    # ---- logging ------------------------------------------------------
    def log(self, line: str) -> None:
        self.workspace.append_log(line)

    # ---- execution ----------------------------------------------------
    def run(self, task: Task) -> AgentResult:
        """Execute a task with timing, logging and failure containment."""
        if not self.accepts(task.kind):
            return AgentResult.failure(
                f"'{self.id}' does not accept task kind '{task.kind}' "
                f"(accepts: {sorted(self.spec.accepts)})")
        started = time.time()
        before = self._sent
        self.log(f"START {task.task_id} {task.kind}: {task.title}")
        try:
            result = self.handle(task)
        except Exception as exc:                        # noqa: BLE001
            # One agent failing must not abort the run. The manager decides
            # whether to retry, skip or halt.
            detail = f"{type(exc).__name__}: {exc}"
            self.log(f"FAIL  {task.task_id}: {detail}\n{traceback.format_exc()}")
            return AgentResult(ok=False, error=detail, summary=detail,
                               duration_s=time.time() - started)
        if result is None:
            result = AgentResult(ok=True, summary="completed")
        result.duration_s = time.time() - started
        result.messages_sent = self._sent - before
        self.log(f"{'DONE ' if result.ok else 'FAIL '} {task.task_id} "
                 f"({result.duration_s:.2f}s): {result.summary}")
        return result

    @abstractmethod
    def handle(self, task: Task) -> Optional[AgentResult]:
        """Do the work. Raise on failure; the base class contains it."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id}>"
