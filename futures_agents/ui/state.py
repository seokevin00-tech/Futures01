"""Reading and writing the desk's two external files.

The application keeps exactly two mutable documents in the project folder:

* ``desk-config.json`` - the :class:`~futures_agents.config.SystemConfig`,
  including every knob of :class:`~futures_agents.config.AccountConfig`. This is
  the file the Risk Studio screen edits.
* ``desk-account.json`` - the live :class:`~futures_agents.risk.account.AccountState`:
  equity, the equity peak, today's ledger and any open positions.

Both are plain JSON, formatted for a human, and both are authoritative. Nothing
is cached across a request: every screen refresh re-reads from disk, so a user
who edits ``desk-config.json`` in a text editor while the application is running
sees the change on their next click. That is the whole point of not baking the
files into the executable, and caching would quietly undo it.

**Validation is loud.** A risk configuration is the one place where accepting a
malformed number and carrying on is the worst possible behaviour: silently
clamping ``daily_loss_limit`` to something "reasonable" would mean the account's
real limit is not the one on screen. So :func:`coerce_account_config` rejects
what it cannot parse, names the field, and changes nothing.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import fields, replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import (AccountConfig, SystemConfig, DEFAULT_SYMBOLS, get_contract,
                      load_config)
from ..risk.account import AccountState, DayState, OpenPosition
from ..risk.manager import RiskManager
from ..schema import Direction
from ..timeutil import now_et, trading_day
from .paths import ProjectPaths, paths

__all__ = [
    "ConfigError", "coerce_account_config", "coerce_system_fields",
    "load_system_config", "save_system_config",
    "load_account_state", "save_account_state",
    "account_state_to_dict", "account_state_from_dict",
    "risk_manager", "write_json_atomic",
]


class ConfigError(ValueError):
    """A value the user supplied cannot be used. Carries the field name."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"{field}: {message}")


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON via a temporary file and one rename.

    The account state is rewritten on every equity change. A process killed
    midway through a plain ``open(..., "w")`` leaves a truncated file, and a
    truncated account file means the next start cannot tell the desk how much
    drawdown it has left. ``os.replace`` is atomic on both POSIX and Windows, so
    the file on disk is always either the old document or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Typed coercion for values arriving from the browser
# --------------------------------------------------------------------------

def _as_float(field: str, value: Any, *, minimum: Optional[float] = None,
              maximum: Optional[float] = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ConfigError(field, f"{value!r} is not a number") from None
    if out != out or out in (float("inf"), float("-inf")):
        raise ConfigError(field, "must be a finite number")
    if minimum is not None and out < minimum:
        raise ConfigError(field, f"must be at least {minimum:g} (got {out:g})")
    if maximum is not None and out > maximum:
        raise ConfigError(field, f"must be at most {maximum:g} (got {out:g})")
    return out


def _as_int(field: str, value: Any, *, minimum: Optional[int] = None,
            maximum: Optional[int] = None) -> int:
    try:
        out = int(round(float(value)))
    except (TypeError, ValueError):
        raise ConfigError(field, f"{value!r} is not a whole number") from None
    if minimum is not None and out < minimum:
        raise ConfigError(field, f"must be at least {minimum} (got {out})")
    if maximum is not None and out > maximum:
        raise ConfigError(field, f"must be at most {maximum} (got {out})")
    return out


def _as_bool(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
    if value in (0, 1):
        return bool(value)
    raise ConfigError(field, f"{value!r} is not true or false")


#: Per-field bounds for :class:`AccountConfig`.
#:
#: These are *sanity* bounds, not opinions about good risk management: they stop
#: a typo or a stray keystroke from producing an unusable configuration, and
#: nothing more. A user who genuinely wants to risk 5% of equity per trade is
#: allowed to, and the UI will tell them what it means in dollars rather than
#: refuse. The engine's own guards still apply on top.
_ACCOUNT_BOUNDS: Dict[str, Tuple[str, Dict[str, Any]]] = {
    "starting_equity":          ("float", {"minimum": 500.0, "maximum": 1e9}),
    "max_total_drawdown":       ("float", {"minimum": 100.0, "maximum": 1e9}),
    "trailing_drawdown":        ("bool", {}),
    "protected_buffer_pct":     ("float", {"minimum": 0.0, "maximum": 0.95}),
    "base_risk_pct_of_buffer":  ("float", {"minimum": 0.0, "maximum": 1.0}),
    "max_risk_pct_of_equity":   ("float", {"minimum": 0.0, "maximum": 0.25}),
    "min_dollar_risk":          ("float", {"minimum": 0.0, "maximum": 1e7}),
    "max_dollar_risk":          ("float", {"minimum": 1.0, "maximum": 1e7}),
    "daily_loss_limit":         ("float", {"minimum": 1.0, "maximum": 1e8}),
    "daily_soft_loss_limit":    ("float", {"minimum": 0.0, "maximum": 1e8}),
    "daily_profit_lockdown":    ("float", {"minimum": 0.0, "maximum": 1e8}),
    "daily_giveback_pct":       ("float", {"minimum": 0.0, "maximum": 1.0}),
    "max_trades_per_day":       ("int", {"minimum": 1, "maximum": 200}),
    "max_consecutive_losses":   ("int", {"minimum": 1, "maximum": 50}),
    "max_concurrent_positions": ("int", {"minimum": 1, "maximum": 50}),
    "max_correlated_positions": ("int", {"minimum": 1, "maximum": 50}),
    "min_reward_risk":          ("float", {"minimum": 0.0, "maximum": 100.0}),
    "min_confidence":           ("float", {"minimum": 0.0, "maximum": 1.0}),
    "min_backtest_trades":      ("int", {"minimum": 0, "maximum": 100000}),
    "min_expectancy_r":         ("float", {"minimum": -10.0, "maximum": 10.0}),
    "max_atr_multiple_of_median": ("float", {"minimum": 0.1, "maximum": 100.0}),
    "min_atr_multiple_of_median": ("float", {"minimum": 0.0, "maximum": 10.0}),
    "news_blackout_before_min": ("int", {"minimum": 0, "maximum": 1440}),
    "news_blackout_after_min":  ("int", {"minimum": 0, "maximum": 1440}),
    "high_impact_risk_multiplier": ("float", {"minimum": 0.0, "maximum": 1.0}),
}


def _coerce_ladder(value: Any) -> Tuple[Tuple[float, float], ...]:
    """Validate the de-risk ladder: rising thresholds, non-rising multipliers.

    The ladder is the one field where a plausible-looking edit can invert the
    engine's behaviour. ``derisk_multiplier`` walks the rows in order and keeps
    the last whose threshold has been passed, so rows out of order mean the
    account de-risks into a *larger* size as it loses money. That is checked
    here rather than left to the caller to notice.
    """
    if not isinstance(value, (list, tuple)) or not value:
        raise ConfigError("derisk_ladder", "must be a non-empty list of "
                                           "[consumed_fraction, multiplier] pairs")
    rows: List[Tuple[float, float]] = []
    for i, row in enumerate(value):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ConfigError(f"derisk_ladder[{i}]",
                              "must be a pair [consumed_fraction, multiplier]")
        threshold = _as_float(f"derisk_ladder[{i}].consumed", row[0],
                              minimum=0.0, maximum=1.0)
        mult = _as_float(f"derisk_ladder[{i}].multiplier", row[1],
                         minimum=0.0, maximum=5.0)
        rows.append((threshold, mult))
    if rows[0][0] != 0.0:
        raise ConfigError("derisk_ladder[0].consumed",
                          "the first row must start at 0.0 so there is always a "
                          "multiplier in force")
    for i in range(1, len(rows)):
        if rows[i][0] <= rows[i - 1][0]:
            raise ConfigError(f"derisk_ladder[{i}].consumed",
                              f"thresholds must strictly increase "
                              f"({rows[i][0]:g} follows {rows[i - 1][0]:g})")
        if rows[i][1] > rows[i - 1][1]:
            raise ConfigError(f"derisk_ladder[{i}].multiplier",
                              "multipliers must not increase as drawdown grows - "
                              "this row would size the account UP as it loses")
    return tuple(rows)


def coerce_account_config(base: AccountConfig, payload: Dict[str, Any]
                          ) -> AccountConfig:
    """Apply a partial update from the browser to an :class:`AccountConfig`.

    Unknown keys are ignored rather than fatal, so a newer UI talking to an
    older build degrades instead of breaking. Known keys with unusable values
    raise, naming the field.
    """
    updates: Dict[str, Any] = {}
    for key, raw in (payload or {}).items():
        if key == "derisk_ladder":
            updates[key] = _coerce_ladder(raw)
            continue
        spec = _ACCOUNT_BOUNDS.get(key)
        if spec is None:
            continue
        kind, bounds = spec
        if kind == "float":
            updates[key] = _as_float(key, raw, **bounds)
        elif kind == "int":
            updates[key] = _as_int(key, raw, **bounds)
        else:
            updates[key] = _as_bool(key, raw)

    merged = replace(base, **updates)

    # ---- cross-field checks ------------------------------------------
    # Each of these is a combination that parses fine field by field but cannot
    # be honoured by the engine, so it is caught here where the field names are
    # still available to show next to the offending input.
    if merged.min_dollar_risk > merged.max_dollar_risk:
        raise ConfigError("min_dollar_risk",
                          f"minimum risk ${merged.min_dollar_risk:,.0f} exceeds the "
                          f"maximum ${merged.max_dollar_risk:,.0f} - no trade could "
                          "ever be sized")
    if merged.daily_soft_loss_limit > merged.daily_loss_limit:
        raise ConfigError("daily_soft_loss_limit",
                          f"the soft limit ${merged.daily_soft_loss_limit:,.0f} is "
                          f"beyond the hard limit ${merged.daily_loss_limit:,.0f}, so "
                          "it could never take effect")
    if merged.min_atr_multiple_of_median >= merged.max_atr_multiple_of_median:
        raise ConfigError("min_atr_multiple_of_median",
                          "the volatility floor is at or above the ceiling - every "
                          "trade would be vetoed")
    reserve = merged.max_total_drawdown * merged.protected_buffer_pct
    if reserve >= merged.max_total_drawdown:
        raise ConfigError("protected_buffer_pct",
                          "reserving the entire drawdown allowance leaves no usable "
                          "buffer, so no trade can ever be sized")
    return merged


_SYSTEM_SCALARS = {
    "model": str, "effort": str, "enable_llm": bool, "enable_web_search": bool,
    "timezone": str, "flash_enabled": bool, "bell_enabled": bool,
}


def coerce_system_fields(base: SystemConfig, payload: Dict[str, Any]) -> SystemConfig:
    """Apply a partial update to the non-account parts of the system config."""
    cfg = replace(base)
    for key, raw in (payload or {}).items():
        if key == "symbols":
            if not isinstance(raw, (list, tuple)) or not raw:
                raise ConfigError("symbols", "pick at least one symbol")
            syms: List[str] = []
            for s in raw:
                name = str(s).upper().strip()
                try:
                    get_contract(name)
                except KeyError as exc:
                    raise ConfigError("symbols", str(exc)) from None
                if name not in syms:
                    syms.append(name)
            cfg.symbols = tuple(syms)
        elif key == "effort":
            val = str(raw).lower().strip()
            if val not in ("low", "medium", "high", "xhigh", "max"):
                raise ConfigError("effort", f"{raw!r} is not a valid effort level")
            cfg.effort = val
        elif key in _SYSTEM_SCALARS:
            caster = _SYSTEM_SCALARS[key]
            setattr(cfg, key, _as_bool(key, raw) if caster is bool else str(raw))
    cfg.validate()
    return cfg


# --------------------------------------------------------------------------
# System config on disk
# --------------------------------------------------------------------------

def load_system_config(p: Optional[ProjectPaths] = None) -> SystemConfig:
    """Read ``desk-config.json``, falling back to the built-in defaults.

    ``load_config`` already merges file, environment and defaults, and tolerates
    a missing path, so a first run with no file produces the documented $50,000
    configuration rather than an error.
    """
    p = p or paths()
    cfg = load_config(str(p.config_file))
    # Storage belongs in the project folder, not wherever the process happens to
    # have been started from - a desk launched by double-click has a working
    # directory nobody chose.
    cfg.data_dir = str(p.data_dir)
    if not os.path.isabs(cfg.db_path):
        cfg.db_path = str(p.data_dir / Path(cfg.db_path).name)
    return cfg


def save_system_config(cfg: SystemConfig, p: Optional[ProjectPaths] = None) -> Path:
    """Persist the system config, account section included."""
    p = p or paths()
    payload = cfg.to_dict()
    payload["account"] = {k: v for k, v in cfg.account.to_dict().items()}
    payload["account"]["derisk_ladder"] = [list(row) for row in cfg.account.derisk_ladder]
    write_json_atomic(p.config_file, payload)
    return p.config_file


# --------------------------------------------------------------------------
# Account state on disk
# --------------------------------------------------------------------------

def account_state_to_dict(state: AccountState) -> Dict[str, Any]:
    """The persistable form: the inputs, not the derived reporting view.

    ``AccountState.to_dict`` is a *report* - it contains computed fields like
    ``usable_buffer`` that are functions of the config. Persisting those would
    mean a config change silently disagreed with the stored numbers, so this
    writes only what cannot be recomputed.
    """
    day = state.day
    return {
        "equity": round(state.equity, 2),
        "peak_equity": round(state.peak_equity, 2),
        "closed_trades": state.closed_trades,
        "lifetime_wins": state.lifetime_wins,
        "lifetime_losses": state.lifetime_losses,
        "consecutive_losses": state.consecutive_losses,
        "equity_curve": [[ts, round(v, 2)] for ts, v in state.equity_curve[-500:]],
        "open_positions": [p.to_dict() for p in state.open_positions],
        "day": {
            "day": str(day.day),
            "realised_pnl": round(day.realised_pnl, 2),
            "peak_pnl": round(day.peak_pnl, 2),
            "trough_pnl": round(day.trough_pnl, 2),
            "trades_taken": day.trades_taken,
            "wins": day.wins,
            "losses": day.losses,
            "consecutive_losses": day.consecutive_losses,
            "consecutive_wins": day.consecutive_wins,
            "callouts_issued": day.callouts_issued,
            "no_trade_decisions": day.no_trade_decisions,
            "starting_equity": round(day.starting_equity, 2),
        } if day else None,
    }


def _day_from_dict(d: Optional[Dict[str, Any]]) -> Optional[DayState]:
    if not d:
        return None
    try:
        when = date.fromisoformat(str(d.get("day")))
    except (TypeError, ValueError):
        when = trading_day(now_et())
    out = DayState(day=when)
    for key in ("realised_pnl", "peak_pnl", "trough_pnl", "starting_equity"):
        if key in d:
            setattr(out, key, _as_float(f"day.{key}", d[key]))
    for key in ("trades_taken", "wins", "losses", "consecutive_losses",
                "consecutive_wins", "callouts_issued", "no_trade_decisions"):
        if key in d:
            setattr(out, key, _as_int(f"day.{key}", d[key], minimum=0))
    return out


def _position_from_dict(d: Dict[str, Any], index: int) -> OpenPosition:
    label = f"open_positions[{index}]"
    symbol = str(d.get("symbol", "")).upper().strip()
    try:
        get_contract(symbol)
    except KeyError as exc:
        raise ConfigError(f"{label}.symbol", str(exc)) from None
    raw_dir = str(d.get("direction", "")).upper().strip()
    try:
        direction = Direction(raw_dir)
    except ValueError:
        raise ConfigError(f"{label}.direction",
                          f"{raw_dir!r} is not LONG, SHORT or NEUTRAL") from None
    return OpenPosition(
        symbol=symbol,
        direction=direction,
        contracts=_as_int(f"{label}.contracts", d.get("contracts", 1), minimum=1),
        entry=_as_float(f"{label}.entry", d.get("entry", 0.0)),
        stop=_as_float(f"{label}.stop", d.get("stop", 0.0)),
        targets=[_as_float(f"{label}.targets", t) for t in (d.get("targets") or [])],
        opened_et=str(d.get("opened_et") or ""),
        strategy_id=str(d.get("strategy_id") or ""),
        dollar_risk=_as_float(f"{label}.dollar_risk", d.get("dollar_risk", 0.0),
                              minimum=0.0),
        callout_id=str(d.get("callout_id") or ""),
    )


def account_state_from_dict(cfg: AccountConfig, d: Dict[str, Any]) -> AccountState:
    """Rebuild an :class:`AccountState` from its persisted form."""
    state = AccountState(
        config=cfg,
        equity=_as_float("equity", d.get("equity", cfg.starting_equity), minimum=0.0),
        peak_equity=_as_float("peak_equity", d.get("peak_equity", 0.0), minimum=0.0),
        day=_day_from_dict(d.get("day")),
        closed_trades=_as_int("closed_trades", d.get("closed_trades", 0), minimum=0),
        lifetime_wins=_as_int("lifetime_wins", d.get("lifetime_wins", 0), minimum=0),
        lifetime_losses=_as_int("lifetime_losses", d.get("lifetime_losses", 0),
                                minimum=0),
        consecutive_losses=_as_int("consecutive_losses",
                                   d.get("consecutive_losses", 0), minimum=0),
        equity_curve=[(str(ts), float(v)) for ts, v in (d.get("equity_curve") or [])
                      if isinstance(ts, str)],
    )
    state.open_positions = [
        _position_from_dict(p, i)
        for i, p in enumerate(d.get("open_positions") or [])
    ]
    return state


def load_account_state(cfg: SystemConfig, p: Optional[ProjectPaths] = None
                       ) -> AccountState:
    """Read ``desk-account.json``, or start a fresh account from the config."""
    p = p or paths()
    if not p.state_file.is_file():
        return AccountState(config=cfg.account)
    try:
        raw = json.loads(p.state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(str(p.state_file),
                          f"could not be read as JSON ({exc}). Fix or delete the "
                          "file; it is not overwritten automatically because doing "
                          "so would discard the account's drawdown history.") from None
    if not isinstance(raw, dict):
        raise ConfigError(str(p.state_file), "expected a JSON object at the top level")
    return account_state_from_dict(cfg.account, raw)


def save_account_state(state: AccountState, p: Optional[ProjectPaths] = None) -> Path:
    p = p or paths()
    write_json_atomic(p.state_file, account_state_to_dict(state))
    return p.state_file


def risk_manager(cfg: SystemConfig, state: AccountState) -> RiskManager:
    return RiskManager(cfg.account, state)
