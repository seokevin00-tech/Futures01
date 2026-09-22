"""Per-agent private workspaces.

Every agent owns exactly one directory and may write only inside it. That is
enforced here by resolving every path and refusing anything that escapes the
root - not by asking agents to behave. Two agents writing the same research
file is the kind of bug that produces a confident, wrong callout and leaves no
trace of which agent was responsible.

Reading is deliberately asymmetric: an agent may read another agent's
*published* artefacts (its ``out/`` directory) but never its private scratch
space. That is what lets the decision layer consume analyst conclusions while
keeping each analyst's working notes its own.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..timeutil import et_stamp, now_et
from .roles import Role, ROLES

__all__ = ["WorkspaceViolation", "Workspace", "TeamFilesystem"]


class WorkspaceViolation(PermissionError):
    """Raised when an agent attempts to write outside its own workspace."""


def _safe_name(name: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))
    return cleaned.strip("._") or "unnamed"


class Workspace:
    """One agent's directory, with write access confined to it.

    Layout::

        workspace/<role>/
            out/        published artefacts other agents may read
            scratch/    private working files - never read by anyone else
            log/        this agent's own activity log
    """

    def __init__(self, root: Path, role: Role, *, create: bool = True):
        self.role = role
        self.root = Path(root).resolve()
        self.out = self.root / "out"
        self.scratch = self.root / "scratch"
        self.log_dir = self.root / "log"
        if create:
            for d in (self.root, self.out, self.scratch, self.log_dir):
                d.mkdir(parents=True, exist_ok=True)

    # ---- guards -------------------------------------------------------
    def _resolve_for_write(self, relative: str) -> Path:
        """Resolve a write path, refusing anything outside this workspace.

        Checked after resolution so that ``../``, absolute paths and symlinks
        are all caught rather than only the obvious cases.
        """
        candidate = (self.root / str(relative)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise WorkspaceViolation(
                f"agent '{self.role.value}' may not write to {candidate} - "
                f"its workspace is {self.root}") from None
        return candidate

    # ---- writing ------------------------------------------------------
    def write_text(self, relative: str, content: str) -> Path:
        path = self._resolve_for_write(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: a half-written artefact that another agent reads
        # mid-write is worse than no artefact at all.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return path

    def write_json(self, relative: str, payload: Any, *, indent: int = 2) -> Path:
        from ..schema import to_json
        return self.write_text(relative, to_json(payload, indent=indent))

    def publish(self, name: str, payload: Any, *, as_json: bool = True) -> Path:
        """Publish an artefact for other agents to read."""
        fname = _safe_name(name)
        if as_json:
            if not fname.endswith(".json"):
                fname += ".json"
            return self.write_json(f"out/{fname}", payload)
        return self.write_text(f"out/{fname}", str(payload))

    def scratch_json(self, name: str, payload: Any) -> Path:
        fname = _safe_name(name)
        if not fname.endswith(".json"):
            fname += ".json"
        return self.write_json(f"scratch/{fname}", payload)

    def append_log(self, line: str) -> Path:
        """Append one Eastern-Time-stamped line to this agent's log."""
        path = self.log_dir / "activity.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"[{et_stamp()}] {line.rstrip()}\n")
        return path

    # ---- reading ------------------------------------------------------
    def read_text(self, relative: str) -> Optional[str]:
        path = (self.root / str(relative)).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            return None
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def read_json(self, relative: str) -> Optional[Any]:
        raw = self.read_text(relative)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def artefacts(self) -> List[str]:
        if not self.out.is_dir():
            return []
        return sorted(p.name for p in self.out.iterdir() if p.is_file())

    def clear_scratch(self) -> None:
        if self.scratch.is_dir():
            shutil.rmtree(self.scratch)
        self.scratch.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"<Workspace {self.role.value} at {self.root}>"


class TeamFilesystem:
    """Allocates and brokers access to every agent's workspace.

    ``read_artefact`` is the only sanctioned cross-agent read path, and it can
    only reach another agent's ``out/`` directory.
    """

    def __init__(self, base: str = "workspace"):
        self.base = Path(base).resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self.shared = self.base / "_shared"
        self.shared.mkdir(parents=True, exist_ok=True)
        self._spaces: Dict[Role, Workspace] = {}
        for role in Role:
            self._spaces[role] = Workspace(self.base / role.folder, role)

    def workspace(self, role: Role) -> Workspace:
        return self._spaces[role]

    def read_artefact(self, owner: Role, name: str) -> Optional[Any]:
        """Read another agent's published artefact. Private scratch is not
        reachable through this method by construction."""
        ws = self._spaces[owner]
        fname = _safe_name(name)
        if not fname.endswith(".json"):
            fname += ".json"
        return ws.read_json(f"out/{fname}")

    def list_artefacts(self) -> Dict[str, List[str]]:
        return {role.value: self._spaces[role].artefacts() for role in Role}

    def shared_path(self, name: str) -> Path:
        return self.shared / _safe_name(name)

    def write_shared(self, name: str, payload: Any) -> Path:
        """The shared blackboard. Only the manager writes here in normal
        operation; it is the team's single agreed view of the run."""
        from ..schema import to_json
        path = self.shared_path(name if name.endswith(".json") else f"{name}.json")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(to_json(payload, indent=2))
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return path

    def read_shared(self, name: str) -> Optional[Any]:
        path = self.shared_path(name if name.endswith(".json") else f"{name}.json")
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def tree(self) -> str:
        lines = [f"{self.base}/"]
        for role in Role:
            ws = self._spaces[role]
            arts = ws.artefacts()
            lines.append(f"  {role.folder}/")
            lines.append(f"    out/      {len(arts)} artefact(s)"
                         + (f": {', '.join(arts[:4])}" if arts else ""))
            lines.append("    scratch/  (private)")
        shared = sorted(p.name for p in self.shared.iterdir() if p.is_file())
        lines.append(f"  _shared/    {len(shared)} file(s)"
                     + (f": {', '.join(shared[:4])}" if shared else ""))
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<TeamFilesystem base={self.base} agents={len(self._spaces)}>"
