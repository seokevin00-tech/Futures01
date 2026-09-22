"""The agent team: identities, private workspaces, a message bus and a task board.

The team is the coordination layer that sits above the domain agents. It exists
to make three guarantees the specification depends on:

* **Every agent owns its own files.** An agent writes only inside its own
  workspace directory. Cross-agent writes are refused by
  :class:`~futures_agents.team.workspace.Workspace`, not merely discouraged, so
  one agent can never silently overwrite another's findings.

* **Agents can talk to each other.** :class:`~futures_agents.team.bus.MessageBus`
  carries typed, addressed, persisted messages - direct, broadcast and
  request/response - and every message is journalled for the audit trail.

* **Work is distributed, not assumed.** The
  :class:`~futures_agents.team.manager.ManagerAgent` owns the task board,
  decides which role a task belongs to, dispatches it and tracks it to
  completion. No agent picks its own work.
"""

from .roles import (Role, ROLES, RoleSpec, role_for_task, roles_for_task,
                    is_ambiguous, may_message)
from .workspace import Workspace, WorkspaceViolation, TeamFilesystem
from .bus import Message, MessageBus, MessageKind, Subscription
from .board import Task, TaskBoard, TaskStatus, TaskPriority
from .agent import TeamAgent, AgentResult
from .manager import ManagerAgent
from .developer import DeveloperAgent
from .team import Team, build_team

__all__ = [
    "Role", "ROLES", "RoleSpec", "role_for_task", "roles_for_task",
    "is_ambiguous", "may_message",
    "Workspace", "WorkspaceViolation", "TeamFilesystem",
    "Message", "MessageBus", "MessageKind", "Subscription",
    "Task", "TaskBoard", "TaskStatus", "TaskPriority",
    "TeamAgent", "AgentResult",
    "ManagerAgent", "DeveloperAgent",
    "Team", "build_team",
]
