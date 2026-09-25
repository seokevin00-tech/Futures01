"""The desk's local risk console.

A standard-library HTTP server plus a single-page front end, packaged so that a
double-clicked executable reads its risk configuration, account state and even
its own UI assets from the folder it sits in rather than from inside the bundle.

Entry points::

    python -m futures_agents.ui              # run from source
    futures-desk                             # the packaged executable

The public surface is deliberately small - the UI talks to :mod:`.api`, and
everything else is implementation detail of how those handlers reach the disk.
"""

from __future__ import annotations

from .paths import ProjectPaths, paths, project_root
from .server import DeskServer, build_server

__all__ = ["ProjectPaths", "paths", "project_root", "DeskServer", "build_server",
           "main"]


def main(argv=None) -> int:
    """Console entry point; imported lazily so ``-m`` stays fast."""
    from .launcher import main as _main
    return _main(argv)
