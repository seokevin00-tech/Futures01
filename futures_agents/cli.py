"""Command-line entry point.

Nine verbs over one :class:`~futures_agents.orchestrator.Orchestrator`. The CLI
itself contains no trading logic at all - it parses flags, builds one config,
configures the alert renderer from it and hands over. Anything it prints that
reports a market event or a decision goes through
:mod:`futures_agents.alerts`, so the Eastern-Time stamp leads and the format is
identical whether the output is a terminal or a log file.

``demo`` is the verb a new reader runs first, and it is the one that must never
require anything: no API key, no data vendor, no network. It generates
deterministic synthetic bars, runs a complete cycle over them, and prints the
staffing report, the callouts, the account state and the workspace tree. If
half the domain agents are missing it still completes and says which half.

Exit codes::

    0   the command did what it said
    1   the command ran but produced nothing usable (e.g. every agent failed)
    2   the command could not run (bad arguments, missing module)
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import alerts
from .alerts import Priority
from .config import SystemConfig, load_config, tf_label
from .orchestrator import Orchestrator
from .team.roles import Role
from .timeutil import et_stamp, now_et, to_et

__all__ = ["main", "build_parser"]

PROG = "futures-agents"

#: Global flags are attached to the top-level parser *and* to every subparser,
#: so ``cli --no-llm demo`` and ``cli demo --no-llm`` both work. The subparser
#: copies use ``SUPPRESS`` defaults, otherwise the subparser's default would
#: silently overwrite a value the user set before the subcommand.
_GLOBAL_DEFAULTS: Dict[str, Any] = {
    "no_llm": False, "no_flash": False, "no_color": False,
    "config": None, "db": None, "equity": None,
}


# --------------------------------------------------------------------------
# Argument plumbing
# --------------------------------------------------------------------------

def _global_flags() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("global options")
    g.add_argument("--no-llm", action="store_true", default=argparse.SUPPRESS,
                   help="run the deterministic path only - no model calls")
    g.add_argument("--no-flash", action="store_true", default=argparse.SUPPRESS,
                   help="print alert banners once, without the flash or the bell")
    g.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                   help="plain text, no ANSI styling")
    g.add_argument("--config", metavar="PATH", default=argparse.SUPPRESS,
                   help="JSON config file to load before applying flags")
    g.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS,
                   help="SQLite database path (default data/futures_agents.sqlite3)")
    g.add_argument("--equity", type=float, metavar="N", default=argparse.SUPPRESS,
                   help="starting account equity in dollars (default 50000)")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _global_flags()
    parser = argparse.ArgumentParser(
        prog=PROG, parents=[common],
        description="Multi-agent futures research, backtesting and live "
                    "decision support for a $50,000 account.",
        epilog="Start with:  python3 -m futures_agents.cli demo --no-llm",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest="command", metavar="COMMAND")

    d = subs.add_parser("demo", parents=[common],
                        help="end-to-end run on synthetic data - no API key needed")
    d.add_argument("--symbols", default="MNQ,MES", help="comma-separated (default MNQ,MES)")
    d.add_argument("--days", type=int, default=10,
                   help="days of synthetic history to generate (default 10)")
    d.add_argument("--max-strategies", type=int, default=60,
                   help="cap on the generated strategy universe per symbol (default 60)")
    d.add_argument("--live-only", action="store_true",
                   help="skip the research plan and run only the live path")
    d.set_defaults(func=cmd_demo)

    r = subs.add_parser("research", parents=[common],
                        help="full research cycle: verify, news, strategies, decide")
    r.add_argument("--symbols", default=None, help="comma-separated, e.g. MNQ,MES")
    r.add_argument("--days", type=int, default=120,
                   help="days of history to load (default 120)")
    r.add_argument("--max-strategies", type=int, default=None,
                   help="cap on the strategy universe per symbol")
    r.set_defaults(func=cmd_research)

    lv = subs.add_parser("live", parents=[common],
                         help="live path only: news, analysts, decision, risk, journal")
    lv.add_argument("--symbols", default=None, help="comma-separated, e.g. MNQ,MES")
    lv.add_argument("--days", type=int, default=60,
                    help="days of history to load (default 60)")
    lv.add_argument("--loop", type=float, metavar="SECONDS", default=None,
                    help="repeat every SECONDS until interrupted")
    lv.set_defaults(func=cmd_live)

    b = subs.add_parser("backtest", parents=[common],
                        help="backtest a symbol's strategy universe")
    b.add_argument("--symbol", default="MNQ")
    b.add_argument("--strategies", type=int, default=None,
                   help="how many strategies to test")
    b.add_argument("--days", type=int, default=120,
                   help="days of history to load (default 120)")
    b.set_defaults(func=cmd_backtest)

    w = subs.add_parser("walkforward", parents=[common],
                        help="walk-forward and robustness analysis for a symbol")
    w.add_argument("--symbol", default="MNQ")
    w.add_argument("--folds", type=int, default=None, help="number of folds")
    w.add_argument("--days", type=int, default=120,
                   help="days of history to load (default 120)")
    w.set_defaults(func=cmd_walkforward)

    rp = subs.add_parser("replay", parents=[common],
                         help="re-run the live cycle at successive past instants")
    rp.add_argument("--symbol", default="MNQ")
    rp.add_argument("--start", default=None, help="ISO timestamp, Eastern Time")
    rp.add_argument("--end", default=None, help="ISO timestamp, Eastern Time")
    rp.add_argument("--step", type=int, default=30, help="minutes per step (default 30)")
    rp.add_argument("--steps", type=int, default=4, help="maximum steps (default 4)")
    rp.add_argument("--days", type=int, default=20,
                    help="days of history to generate (default 20)")
    rp.set_defaults(func=cmd_replay)

    s = subs.add_parser("status", parents=[common],
                        help="account state, staffing, task board, storage counts")
    s.set_defaults(func=cmd_status)

    j = subs.add_parser("journal", parents=[common], help="the trading journal")
    j.add_argument("--symbol", default=None)
    j.add_argument("--limit", type=int, default=25)
    j.set_defaults(func=cmd_journal)

    ro = subs.add_parser("roster", parents=[common],
                         help="the team roster and workspace tree")
    ro.set_defaults(func=cmd_roster)

    dash = subs.add_parser("dashboard", parents=[common],
                           help="render the HTML dashboard, if that module exists")
    dash.add_argument("--open", dest="open_browser", action="store_true",
                      help="open the rendered dashboard in a browser")
    dash.add_argument("--out", default=None, help="output path")
    dash.set_defaults(func=cmd_dashboard)

    return parser


def _fill_global_defaults(args: argparse.Namespace) -> argparse.Namespace:
    for key, value in _GLOBAL_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    return args


def _symbols(value: Optional[str], fallback: Sequence[str]) -> List[str]:
    if not value:
        return [s.upper() for s in fallback]
    return [s.strip().upper() for s in value.split(",") if s.strip()]


def _build_config(args: argparse.Namespace, *,
                  symbols: Optional[Sequence[str]] = None,
                  max_strategies: Optional[int] = None) -> SystemConfig:
    """One config per invocation: file, then environment, then flags."""
    cfg = load_config(args.config) if args.config else SystemConfig.from_env()
    if symbols:
        cfg.symbols = tuple(s.upper() for s in symbols)
    if args.db:
        cfg.db_path = args.db
    if args.no_llm:
        cfg.enable_llm = False
    if args.no_flash:
        cfg.flash_enabled = False
        cfg.bell_enabled = False
    if args.equity is not None:
        cfg.account = replace(cfg.account, starting_equity=float(args.equity))
    if max_strategies is not None:
        cfg.max_combinations = int(max_strategies)
    cfg.validate()
    return cfg


def _configure_alerts(args: argparse.Namespace, cfg: SystemConfig) -> None:
    """Point the process-wide renderer at this invocation's preferences."""
    alerts.configure(
        flash_enabled=cfg.flash_enabled and not args.no_flash,
        bell_enabled=cfg.bell_enabled and not args.no_flash,
        color=False if args.no_color else None,   # None = detect from the stream
        flash_cycles=cfg.flash_cycles,
    )


def _say(text: str, priority: Priority = Priority.INFO) -> None:
    """One ET-stamped line. Used for everything that reports an event."""
    alerts.console.line(text, priority)


def _block(lines: Sequence[str]) -> None:
    print("\n".join(lines))


def _orchestrator(args: argparse.Namespace, cfg: SystemConfig, *,
                  history_days: int = 120,
                  build_strategies: bool = True) -> Orchestrator:
    return Orchestrator(cfg, history_days=history_days,
                        build_strategies=build_strategies)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_demo(args: argparse.Namespace) -> int:
    """Everything, on generated data, with nothing installed and no key."""
    symbols = _symbols(args.symbols, ("MNQ", "MES"))
    cfg = _build_config(args, symbols=symbols, max_strategies=args.max_strategies)
    _configure_alerts(args, cfg)

    _block([
        "",
        f"FUTURES AGENTS DEMO  [{et_stamp()}]",
        "",
        "  Synthetic, deterministic bars - no API key, no data vendor, no network.",
        f"  Symbols:            {', '.join(symbols)}",
        f"  History:            {args.days} day(s) of 1-minute bars per symbol",
        f"  Timeframes:         {', '.join(tf_label(t) for t in cfg.timeframes)}",
        f"  Strategy universe:  up to {cfg.max_combinations} per symbol",
        f"  Account:            ${cfg.account.starting_equity:,.2f}, "
        f"${cfg.account.max_total_drawdown:,.2f} max drawdown, "
        f"${cfg.account.daily_loss_limit:,.2f} daily loss limit",
        f"  Database:           {cfg.db_path}",
        "",
        "  Building the shared context and the strategy universe...",
    ])

    started = time.time()
    orc = _orchestrator(args, cfg, history_days=args.days)
    _say(f"context ready in {time.time() - started:.1f}s - "
         f"{orc.context.registry.total()} strategies, "
         f"{len(orc.team.agents)} of {len(Role) - 1} roles staffed",
         Priority.RESEARCH)

    if args.live_only:
        report = orc.run_live_cycle(symbols)
    else:
        report = orc.run_research_cycle(symbols)

    _block([""] + orc.status_lines())
    _block([""] + orc.roster_lines()[-14:])
    _block([
        "",
        f"DEMO COMPLETE  [{et_stamp()}]",
        f"  {report.summary()}",
        "",
        "  Next:",
        "    python3 -m futures_agents.cli status",
        "    python3 -m futures_agents.cli roster",
        "    python3 -m futures_agents.cli research --symbols MNQ,MES",
        "    python3 -m futures_agents.cli live --symbols MNQ --loop 60",
        "",
    ])
    return 0 if report.outcomes else 1


def cmd_research(args: argparse.Namespace) -> int:
    symbols = _symbols(args.symbols, SystemConfig().symbols)
    cfg = _build_config(args, symbols=symbols, max_strategies=args.max_strategies)
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=args.days)
    report = orc.run_research_cycle(symbols)
    return 0 if report.run and report.run.tasks_done else 1


def cmd_live(args: argparse.Namespace) -> int:
    symbols = _symbols(args.symbols, SystemConfig().symbols)
    cfg = _build_config(args, symbols=symbols)
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=args.days)

    if not args.loop:
        report = orc.run_live_cycle(symbols)
        return 0 if report.run and report.run.tasks_done else 1

    interval = max(1.0, float(args.loop))
    _say(f"live loop every {interval:.0f}s over {', '.join(symbols)} - "
         "Ctrl-C to stop", Priority.RESEARCH)
    cycles = 0
    try:
        while True:
            report = orc.run_live_cycle(symbols)
            cycles += 1
            # Refresh the page after the risk agent has republished, so what
            # it shows is always the post-veto callout.
            page = _render_dashboard(orc, refresh_seconds=30, quiet=cycles > 1)
            if report.halted:
                _say(f"halted after {cycles} cycle(s): {report.halt_reason} - "
                     "the loop stops here", Priority.HALT)
                return 0
            _say(f"cycle {cycles} complete"
                 + (f", dashboard {page}" if page else "")
                 + f" - sleeping {interval:.0f}s", Priority.INFO)
            time.sleep(interval)
    except KeyboardInterrupt:
        _say(f"interrupted after {cycles} cycle(s)", Priority.WARNING)
        return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = _build_config(args, symbols=[args.symbol],
                        max_strategies=args.strategies)
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=args.days)
    _say(f"backtesting {args.symbol.upper()} over {args.days} day(s), "
         f"{orc.context.registry.total()} strategies registered",
         Priority.RESEARCH)
    payload: Dict[str, Any] = {"timeframes": list(cfg.timeframes),
                               "days": args.days}
    if args.strategies:
        payload.update({"strategies": args.strategies,
                        "max_strategies": args.strategies,
                        "limit": args.strategies})
    result = orc.run_task("backtest", symbol=args.symbol,
                          title=f"Backtest {args.symbol.upper()}",
                          role=Role.STRATEGY_RESEARCH, payload=payload)
    return _report_result("backtest", result)


def cmd_walkforward(args: argparse.Namespace) -> int:
    cfg = _build_config(args, symbols=[args.symbol])
    if args.folds:
        cfg.walk_forward_folds = int(args.folds)
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=args.days)
    _say(f"walk-forward on {args.symbol.upper()} over {args.days} day(s), "
         f"{cfg.walk_forward_folds} folds", Priority.RESEARCH)
    result = orc.run_task("walk_forward", symbol=args.symbol,
                          title=f"Walk-forward {args.symbol.upper()}",
                          role=Role.STRATEGY_RESEARCH,
                          payload={"folds": cfg.walk_forward_folds,
                                   "days": args.days,
                                   "timeframes": list(cfg.timeframes)})
    return _report_result("walk_forward", result)


def cmd_replay(args: argparse.Namespace) -> int:
    cfg = _build_config(args, symbols=[args.symbol])
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=args.days)
    start = _parse_when(args.start)
    end = _parse_when(args.end)
    reports = orc.replay(args.symbol, start=start, end=end, step=args.step,
                         max_steps=args.steps)
    if not reports:
        _say("replay produced no cycles", Priority.WARNING)
        return 1
    _block(["", f"REPLAY SUMMARY  [{et_stamp()}]"]
           + [f"  {r.started_et}  {r.summary()}" for r in reports])
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=1, build_strategies=False)
    _block(orc.status_lines())
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=1, build_strategies=False)
    _block(orc.journal_lines(symbol=args.symbol, limit=args.limit))
    return 0


def cmd_roster(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=1, build_strategies=False)
    _block(orc.roster_lines())
    return 0


def _render_dashboard(orc: Orchestrator, *, path: Optional[str] = None,
                      refresh_seconds: Optional[int] = None,
                      quiet: bool = False) -> Optional[str]:
    """Render the HTML dashboard, or explain why it could not be rendered.

    ``dashboard.py`` is owned by another agent. The import stays guarded so
    that a checkout without it - or with a broken copy of it - costs the
    operator a message rather than the command.

    The callouts come from storage, which holds the copy the orchestrator
    emitted: that is the risk layer's post-veto callout, so the page can never
    show a contract count the risk layer refused.
    """
    try:
        from . import dashboard as dashboard_module      # type: ignore
    except ImportError as exc:
        if not quiet:
            _block([
                f"DASHBOARD UNAVAILABLE  [{et_stamp()}]",
                "  futures_agents/dashboard.py could not be imported.",
                f"  {type(exc).__name__}: {exc}",
                "  Every other command works without it.",
            ])
        return None
    except Exception as exc:                            # noqa: BLE001
        if not quiet:
            _block([f"DASHBOARD FAILED TO IMPORT  [{et_stamp()}]",
                    f"  {type(exc).__name__}: {exc}"])
        return None

    render = getattr(dashboard_module, "render_dashboard", None)
    if not callable(render):
        if not quiet:
            _block([f"DASHBOARD UNAVAILABLE  [{et_stamp()}]",
                    "  futures_agents/dashboard.py defines no "
                    "render_dashboard() function."])
        return None

    fs = orc.team.fs
    callouts = [row["payload"] for row in orc.storage.recent_callouts(limit=50)
                if row.get("payload")]
    predictions = [p for p in (fs.read_artefact(role, name) for role, name in (
        (Role.ANALYST_A, "prediction_a"), (Role.ANALYST_B, "prediction_b"),
        (Role.ANALYST_C, "prediction_c"))) if p]
    pool: Dict[str, Any] = {
        "orchestrator": orc, "orc": orc, "context": orc.context, "ctx": orc.context,
        "config": orc.config, "cfg": orc.config, "storage": orc.storage,
        "account": orc.account, "team": orc.team, "fs": fs,
        "path": path, "out": path, "out_path": path, "output": path,
        "callouts": callouts, "predictions": predictions,
        "news": fs.read_artefact(Role.NEWS_MACRO, "news_context"),
        "task_board": orc.team.manager.board,
        "journal": orc.storage.journal_entries(limit=50),
        "refresh_seconds": refresh_seconds,
        "now": orc.context.now(),
    }
    if not callouts and not quiet:
        _say("no callouts stored yet - the dashboard will render empty; run "
             "`demo` or `live` first", Priority.WARNING)
    try:
        result = render(**_matching_kwargs(render, pool))
    except Exception as exc:                            # noqa: BLE001
        if not quiet:
            _block([f"DASHBOARD RENDER FAILED  [{et_stamp()}]",
                    f"  {type(exc).__name__}: {exc}",
                    "  render_dashboard() is owned by the dashboard agent; its "
                    "error is reported here rather than swallowed."])
        return None
    return result if isinstance(result, str) else getattr(result, "path", None)


def cmd_dashboard(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    _configure_alerts(args, cfg)
    orc = _orchestrator(args, cfg, history_days=1, build_strategies=False)
    path = _render_dashboard(orc, path=args.out)
    if path is None:
        return 2
    _say(f"dashboard rendered: {path}", Priority.RESEARCH)
    if args.open_browser and os.path.exists(str(path)):
        import webbrowser
        webbrowser.open(f"file://{os.path.abspath(str(path))}")
    return 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _matching_kwargs(func: Callable[..., Any],
                     pool: Dict[str, Any]) -> Dict[str, Any]:
    """Pass a teammate's function only the arguments it actually declares.

    The dashboard's signature is not fixed by the integration contract, so this
    adapts to whatever it asks for instead of guessing once and crashing.
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return {k: v for k, v in pool.items()
                if k in ("orchestrator", "config", "storage", "context")}
    out: Dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL,
                          inspect.Parameter.VAR_KEYWORD):
            continue
        if name in pool and pool[name] is not None:
            out[name] = pool[name]
    return out


def _report_result(label: str, result: Any) -> int:
    """Print one agent result: summary, artefacts, then a compact payload."""
    if result.ok:
        _say(f"{label}: {result.summary}", Priority.RESEARCH)
    else:
        _say(f"{label} FAILED: {result.error}", Priority.WARNING)
    if getattr(result, "artefacts", None):
        _block([f"  artefacts: {', '.join(result.artefacts)}"])
    payload = getattr(result, "payload", None)
    if payload is not None:
        try:
            text = json.dumps(payload, indent=2, default=str)
        except (TypeError, ValueError):
            text = str(payload)
        lines = text.splitlines()
        _block(["  " + ln for ln in lines[:40]])
        if len(lines) > 40:
            _block([f"  ... {len(lines) - 40} more line(s)"])
    return 0 if result.ok else 1


def _parse_when(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return to_et(datetime.fromisoformat(value))
    except ValueError:
        raise SystemExit(
            f"{PROG}: could not parse {value!r} as an ISO timestamp "
            "(e.g. 2026-09-22T10:15)")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = _fill_global_defaults(parser.parse_args(argv))

    if not getattr(args, "command", None):
        parser.print_help()
        print("\nRun the demo first - it needs nothing installed:\n"
              f"  python3 -m futures_agents.cli demo --no-llm\n")
        return 1

    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        _say("interrupted", Priority.WARNING)
        return 1
    except BrokenPipeError:
        # Output was piped into something that stopped reading (``| head``).
        # Silence the interpreter's shutdown noise rather than printing a
        # traceback that has nothing to do with the run.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except Exception:                               # noqa: BLE001
            pass
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception as exc:                            # noqa: BLE001
        # A traceback is the right thing for an engineer and the wrong thing for
        # an operator mid-session, so print both: the one-line cause first.
        print(f"\n[{et_stamp()}] {PROG}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
