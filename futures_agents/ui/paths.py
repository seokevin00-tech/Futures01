"""Where the desk application looks for its files.

This module exists to answer one question: **when the user double-clicks
``futures-desk.exe``, which directory is "the project folder"?**

The answer must not be the PyInstaller bundle. A one-file executable unpacks
itself into a temporary directory (``sys._MEIPASS``) that is deleted on exit, so
anything written there is lost and anything read from there is whatever was
frozen at build time. A risk configuration that reverts every time the
application restarts is worse than no configuration at all, and a UI that can
only be changed by rebuilding the executable is not a UI the desk owns.

So the rule is:

* **Mutable and user-owned files** - the risk configuration, the account state,
  the journal database - live in :func:`project_root`, which is the directory
  *containing* the executable. Edit them with a text editor; the application
  picks the change up on its next read. They are never bundled.
* **Presentation assets** - the HTML, CSS and JavaScript of the UI - are looked
  for in ``project_root()/assets`` first, so the desk can restyle its own
  screens without a rebuild. Only if that directory is absent does the
  application fall back to the copy inside the bundle, via
  :func:`bundled_assets`. A single executable therefore still runs alone, but a
  folder that carries its own ``assets/`` always wins.

``FA_DESK_HOME`` overrides the root entirely, which is what the tests use and
what lets one build serve two accounts from two folders.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

__all__ = [
    "is_frozen", "project_root", "bundled_assets", "ProjectPaths", "paths",
    "DESK_HOME_ENV",
]

#: Environment variable that overrides the project folder.
DESK_HOME_ENV = "FA_DESK_HOME"

#: Files the application owns inside the project folder. Names are deliberately
#: boring: someone opening the folder in Explorer should be able to guess what
#: each one is for without documentation.
CONFIG_NAME = "desk-config.json"
STATE_NAME = "desk-account.json"
ASSETS_DIRNAME = "assets"
DATA_DIRNAME = "data"


def is_frozen() -> bool:
    """True when running from a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def _meipass() -> Optional[Path]:
    """The bundle's temporary extraction directory, if we are inside one."""
    raw = getattr(sys, "_MEIPASS", None)
    return Path(raw) if raw else None


def project_root() -> Path:
    """The folder whose files this application reads and writes.

    Resolution order, first match wins:

    1. ``$FA_DESK_HOME``, if set - an explicit answer always beats a guess.
    2. The directory containing the executable, when frozen. Note this is
       ``sys.executable``'s parent and **never** ``sys._MEIPASS``: the point is
       to land next to the ``.exe`` the user clicked, not inside the archive it
       unpacked.
    3. The repository root, when running from source - two levels up from this
       file (``futures_agents/ui/paths.py`` -> repo).
    """
    override = os.environ.get(DESK_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_assets() -> Optional[Path]:
    """The read-only copy of the UI assets shipped inside the build, if any.

    Used only when the project folder has no ``assets/`` of its own, so that a
    bare executable with no sidecar folder still opens a working screen.
    """
    candidates: List[Path] = []
    mei = _meipass()
    if mei is not None:
        candidates.append(mei / "futures_agents" / "ui" / ASSETS_DIRNAME)
        candidates.append(mei / ASSETS_DIRNAME)
    candidates.append(Path(__file__).resolve().parent / ASSETS_DIRNAME)
    for c in candidates:
        if c.is_dir():
            return c
    return None


@dataclass(frozen=True)
class ProjectPaths:
    """Every path the application uses, resolved once and reported honestly.

    The UI renders these verbatim in its footer. A user who cannot see which
    file produced the number on screen has no way to correct it, so "which
    folder am I editing?" is answered on the page rather than in a manual.
    """

    root: Path

    # ---- the files the desk owns -------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / CONFIG_NAME

    @property
    def state_file(self) -> Path:
        return self.root / STATE_NAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    # ---- presentation -------------------------------------------------
    @property
    def external_assets(self) -> Path:
        return self.root / ASSETS_DIRNAME

    @property
    def assets_dir(self) -> Optional[Path]:
        """The directory actually serving the UI: external first, bundle second."""
        ext = self.external_assets
        if ext.is_dir():
            return ext
        return bundled_assets()

    @property
    def assets_are_external(self) -> bool:
        return self.external_assets.is_dir()

    def ensure(self) -> "ProjectPaths":
        """Create the directories the application writes into.

        Only directories - never a config file. Writing a default configuration
        on first run would mean a user who mistypes ``FA_DESK_HOME`` silently
        gets a fresh $50,000 account instead of an error naming the folder it
        looked in.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self

    def describe(self) -> dict:
        """What the UI footer and ``--where`` print."""
        assets = self.assets_dir
        return {
            "root": str(self.root),
            "frozen": is_frozen(),
            "config_file": str(self.config_file),
            "config_exists": self.config_file.is_file(),
            "state_file": str(self.state_file),
            "state_exists": self.state_file.is_file(),
            "data_dir": str(self.data_dir),
            "assets_dir": str(assets) if assets else None,
            "assets_are_external": self.assets_are_external,
            "assets_source": ("project folder" if self.assets_are_external
                              else "bundled fallback"),
        }


def paths() -> ProjectPaths:
    """The resolved paths for this process.

    Deliberately a function rather than a module-level constant: the tests and
    the launcher both set ``FA_DESK_HOME`` after import, and a constant captured
    at import time would ignore them.
    """
    return ProjectPaths(root=project_root())
