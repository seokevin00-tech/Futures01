"""The inter-agent message bus.

Agents talk through typed, addressed, persisted messages rather than through
shared mutable state. Three properties matter:

* **Permissioned.** A send is checked against the sender role's ``may_message``
  set. The three live analysts cannot address each other, which is what keeps
  their predictions genuinely independent rather than three restatements of
  whichever one spoke first.

* **Journalled.** Every message is retained in order with an Eastern-Time
  stamp, so any callout can be replayed back to the exact conversation that
  produced it.

* **Synchronous and deterministic.** Delivery is in-process and ordered. A
  research run is reproducible; there is no background scheduler to make two
  runs differ.
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import (Any, Callable, Deque, Dict, Iterable, List, Optional,
                    Sequence, Tuple)

from ..timeutil import et_stamp, now_et, to_et
from .roles import Role, ROLES, may_message

__all__ = ["MessageKind", "Message", "Subscription", "MessageBus",
           "MessagePermissionError"]


class MessagePermissionError(PermissionError):
    """Raised when a role addresses a role it is not permitted to address."""


class MessageKind(str, Enum):
    DIRECT = "DIRECT"             # point-to-point
    BROADCAST = "BROADCAST"       # to every agent that will listen
    REQUEST = "REQUEST"           # expects a RESPONSE
    RESPONSE = "RESPONSE"
    PUBLISH = "PUBLISH"           # "artefact X is ready"
    STATUS = "STATUS"             # progress report to the manager
    ALERT = "ALERT"               # something needs attention now


@dataclass
class Message:
    """One addressed message."""

    seq: int
    kind: MessageKind
    sender: Role
    recipient: Optional[Role]     # None for BROADCAST
    subject: str
    payload: Any = None
    in_reply_to: Optional[int] = None
    timestamp_et: str = field(default_factory=lambda: to_et(now_et()).isoformat())

    def to_dict(self) -> dict:
        from ..schema import _default
        return {
            "seq": self.seq, "kind": self.kind.value, "sender": self.sender.value,
            "recipient": self.recipient.value if self.recipient else "*",
            "subject": self.subject, "in_reply_to": self.in_reply_to,
            "timestamp_et": self.timestamp_et,
            "payload": json.loads(json.dumps(self.payload, default=_default))
            if self.payload is not None else None,
        }

    def render(self) -> str:
        to = self.recipient.value if self.recipient else "ALL"
        stamp = et_stamp(datetime.fromisoformat(self.timestamp_et))
        return (f"[{stamp}] #{self.seq:04d} {self.kind.value:9s} "
                f"{self.sender.value} -> {to}: {self.subject}")


@dataclass
class Subscription:
    role: Role
    kinds: Tuple[MessageKind, ...]
    handler: Callable[[Message], Optional[Any]]


class MessageBus:
    """Ordered, permissioned, journalled in-process message bus."""

    def __init__(self, *, enforce_permissions: bool = True,
                 max_journal: int = 20_000):
        self.enforce_permissions = enforce_permissions
        self._seq = itertools.count(1)
        self._journal: List[Message] = []
        self._max_journal = max_journal
        self._inbox: Dict[Role, Deque[Message]] = defaultdict(deque)
        self._subs: List[Subscription] = []
        self._responses: Dict[int, Message] = {}

    # ---- wiring -------------------------------------------------------
    def subscribe(self, role: Role, handler: Callable[[Message], Optional[Any]],
                  kinds: Sequence[MessageKind] = ()) -> Subscription:
        sub = Subscription(role, tuple(kinds) if kinds else tuple(MessageKind), handler)
        self._subs.append(sub)
        return sub

    # ---- sending ------------------------------------------------------
    def send(self, sender: Role, recipient: Role, subject: str,
             payload: Any = None, *, kind: MessageKind = MessageKind.DIRECT,
             in_reply_to: Optional[int] = None) -> Message:
        """Send a point-to-point message, subject to role permissions."""
        if self.enforce_permissions and not may_message(sender, recipient):
            raise MessagePermissionError(
                f"'{sender.value}' is not permitted to message '{recipient.value}'. "
                f"Permitted: {sorted(r.value for r in ROLES[sender].may_message)}")
        msg = Message(seq=next(self._seq), kind=kind, sender=sender,
                      recipient=recipient, subject=subject, payload=payload,
                      in_reply_to=in_reply_to)
        self._record(msg)
        self._inbox[recipient].append(msg)
        self._deliver(msg, recipient)
        return msg

    def broadcast(self, sender: Role, subject: str, payload: Any = None,
                  *, kind: MessageKind = MessageKind.BROADCAST) -> Message:
        """Announce something to everyone. Broadcasts carry no permission check
        because they are announcements, not instructions - an analyst may hear
        that the news context updated without being able to direct anyone."""
        msg = Message(seq=next(self._seq), kind=kind, sender=sender,
                      recipient=None, subject=subject, payload=payload)
        self._record(msg)
        for role in Role:
            if role is not sender:
                self._inbox[role].append(msg)
        for sub in list(self._subs):
            if sub.role is not sender and kind in sub.kinds:
                self._safe(sub, msg)
        return msg

    def request(self, sender: Role, recipient: Role, subject: str,
                payload: Any = None) -> Optional[Any]:
        """Send a REQUEST and return the handler's return value, if any."""
        msg = self.send(sender, recipient, subject, payload,
                        kind=MessageKind.REQUEST)
        result = None
        for sub in list(self._subs):
            if sub.role is recipient and MessageKind.REQUEST in sub.kinds:
                result = self._safe(sub, msg)
                break
        if result is not None:
            self.send(recipient, sender, f"re: {subject}", result,
                      kind=MessageKind.RESPONSE, in_reply_to=msg.seq)
        return result

    def publish(self, sender: Role, artefact: str, summary: str = "",
                payload: Any = None) -> Message:
        return self.broadcast(sender, f"published:{artefact}",
                              {"artefact": artefact, "summary": summary,
                               "payload": payload},
                              kind=MessageKind.PUBLISH)

    def alert(self, sender: Role, subject: str, payload: Any = None) -> Message:
        return self.broadcast(sender, subject, payload, kind=MessageKind.ALERT)

    # ---- receiving ----------------------------------------------------
    def drain(self, role: Role) -> List[Message]:
        """Take everything waiting for ``role``, oldest first."""
        box = self._inbox[role]
        out = list(box)
        box.clear()
        return out

    def peek(self, role: Role) -> List[Message]:
        return list(self._inbox[role])

    def pending(self, role: Role) -> int:
        return len(self._inbox[role])

    # ---- journal ------------------------------------------------------
    @property
    def journal(self) -> List[Message]:
        return list(self._journal)

    def transcript(self, limit: int = 60, kinds: Sequence[MessageKind] = ()) -> str:
        msgs = self._journal
        if kinds:
            wanted = set(kinds)
            msgs = [m for m in msgs if m.kind in wanted]
        return "\n".join(m.render() for m in msgs[-limit:])

    def to_dict(self) -> dict:
        return {"messages": [m.to_dict() for m in self._journal]}

    def _record(self, msg: Message) -> None:
        self._journal.append(msg)
        if len(self._journal) > self._max_journal:
            del self._journal[: len(self._journal) - self._max_journal]

    def _deliver(self, msg: Message, recipient: Role) -> None:
        for sub in list(self._subs):
            if sub.role is recipient and msg.kind in sub.kinds:
                self._safe(sub, msg)

    @staticmethod
    def _safe(sub: Subscription, msg: Message) -> Optional[Any]:
        """A failing handler must not take down the bus - the failure is
        reported to the caller's log and the run continues."""
        try:
            return sub.handler(msg)
        except Exception as exc:                      # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    def __repr__(self) -> str:
        return f"<MessageBus messages={len(self._journal)} subs={len(self._subs)}>"
