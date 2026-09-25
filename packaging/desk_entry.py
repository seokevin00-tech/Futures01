"""Frozen-application entry point.

Kept as its own module rather than pointing PyInstaller at
``futures_agents/ui/launcher.py`` directly, so the analysis starts from a script
whose only job is to start the console. Importing the package's ``__main__``
as a build target confuses module resolution in the frozen build.
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def _ensure_importable() -> None:
    """Put the repository root on ``sys.path`` when running from source.

    The frozen build does not need this - PyInstaller's ``pathex`` resolves the
    package at analysis time. But running this file directly to check the build
    target works puts ``packaging/`` on the path instead of the repository root,
    and the import below fails. Making the entry point runnable both ways means
    the thing that gets packaged is the thing that was tested.
    """
    if getattr(sys, "frozen", False):
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)


def main() -> int:
    # Required before anything else on Windows and macOS spawn-based starts, or
    # a frozen process that ever forks re-executes its own bootloader.
    multiprocessing.freeze_support()
    _ensure_importable()
    from futures_agents.ui.launcher import main as run
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
