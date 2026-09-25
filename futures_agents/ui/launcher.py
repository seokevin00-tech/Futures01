"""``futures-desk`` - start the local server and open the console.

This is the module PyInstaller is pointed at, and the module ``python -m
futures_agents.ui`` runs. It does three things and then gets out of the way:
resolve the project folder, start the server on loopback, and open a browser at
the tokenised URL.

The startup banner prints the resolved paths rather than a version string,
because the first question anyone has when a number looks wrong is *which file
did that come from* - and for a double-clicked executable the answer is not
obvious from anything else on screen.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from typing import List, Optional, Sequence

from .paths import DESK_HOME_ENV, ProjectPaths, paths
from .server import DeskServer, build_server

__all__ = ["main", "banner"]


def banner(server: DeskServer) -> str:
    """The startup report: where the files are and how to reach the UI."""
    info = server.paths.describe()
    width = 74
    lines = [
        "=" * width,
        "  FUTURES DESK - risk console",
        "=" * width,
        f"  Open:            {server.app_url}",
        "",
        f"  Project folder:  {info['root']}",
        f"  Risk config:     {os.path.basename(info['config_file'])}"
        f"{'' if info['config_exists'] else '   (not present - built-in defaults in use)'}",
        f"  Account state:   {os.path.basename(info['state_file'])}"
        f"{'' if info['state_exists'] else '   (not present - will be created on first save)'}",
        f"  UI assets:       {info['assets_dir']}  [{info['assets_source']}]",
        "",
        "  Both JSON files are yours to edit in any text editor. The console "
        "re-reads",
        "  them on every request, so a change shows up on your next click - "
        "nothing",
        "  is compiled into the executable.",
    ]
    if not info["assets_are_external"]:
        lines += [
            "",
            "  The UI is running from the copy inside the build. To restyle it, "
            "copy",
            f"  the assets folder to {os.path.join(info['root'], 'assets')} and "
            "reload.",
        ]
    lines += ["", "  Ctrl-C to stop.", "=" * width]
    return "\n".join(lines)


def _open_browser(url: str, delay: float = 0.6) -> None:
    """Open the console once the server is actually accepting connections.

    ``webbrowser.open`` on a URL that is not yet listening shows an error page
    that the user then has to reload, so this waits out the socket setup. It is
    also entirely best-effort: a headless machine has no browser, and failing to
    open one is not a reason to refuse to serve.
    """
    def go() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(delay, go).start()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="futures-desk",
        description="Local risk console for the futures desk. Reads its "
                    "configuration and account state from files in the project "
                    "folder beside the executable.")
    p.add_argument("--host", default="127.0.0.1",
                   help="interface to bind (default 127.0.0.1, loopback only)")
    p.add_argument("--port", type=int, default=8787,
                   help="port to listen on; 0 lets the OS choose (default 8787)")
    p.add_argument("--home", default=None, metavar="DIR",
                   help=f"project folder to read and write (also ${DESK_HOME_ENV})")
    p.add_argument("--no-browser", action="store_true",
                   help="start the server without opening a browser")
    p.add_argument("--where", action="store_true",
                   help="print the resolved paths and exit")
    p.add_argument("--open-access", action="store_true",
                   help="disable the session-token check. Only for scripted "
                        "probing on a machine you control.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    if args.home:
        os.environ[DESK_HOME_ENV] = args.home
    project: ProjectPaths = paths().ensure()

    if args.where:
        info = project.describe()
        for key in sorted(info):
            print(f"{key:22} {info[key]}")
        return 0

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {args.host} exposes this console beyond this "
              f"machine.\n"
              f"         It can rewrite your risk configuration and account state. "
              f"Use 127.0.0.1\n"
              f"         unless you specifically need remote access and trust the "
              f"network.\n", file=sys.stderr)

    server = build_server(host=args.host, port=args.port, project=project,
                          open_access=args.open_access)
    if args.open_access:
        print("WARNING: --open-access disables the session-token check.",
              file=sys.stderr)

    print(banner(server), flush=True)
    if not args.no_browser:
        _open_browser(server.app_url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
