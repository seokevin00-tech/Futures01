"""The desk's JSON API: every handler a pure function of its payload.

Each handler takes a decoded request body and returns a JSON-ready ``dict``.
None of them touch ``http``, which is what makes the risk arithmetic on the
screens testable without opening a socket - the tests call these directly.

Two design points worth stating, because both are about not lying to the user:

**The engine's verdict is never edited.** :func:`preview` runs the real
:meth:`~futures_agents.risk.manager.RiskManager.assess`, and since nothing in
this library is live-eligible, the honest outcome for a hand-typed ticket with no
measured history is a veto. The UI does not hide that. What it *also* computes,
under a separate and clearly labelled key, is ``discretionary`` - the size the
account's own limits would permit if the user accepts that the read is
discretionary chart-reading. Those are two different numbers with two different
standings and the API keeps them apart rather than blending them into one
comfortable answer.

**Cost of a stop is shown before the stop is chosen.** :func:`preview` returns a
``stop_ladder``: what every plausible stop distance costs per contract, in
dollars and as a percentage of equity. The brief's central warning - that MNQ and
MES risk roughly $2,700 at the median structural stop, 5.4% of a $50,000 account
per contract - stops being a sentence someone once read and becomes a row the
user can see while dragging the stop.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import (CONTRACTS, DEFAULT_SYMBOLS, TIMEFRAME_LABELS, AccountConfig,
                      ContractSpec, SystemConfig, get_contract, correlated_symbols)
from ..risk.account import AccountState, OpenPosition
from ..risk.manager import RiskManager, TradeProposal, TradingMode
from ..schema import Direction, HistoricalPerformance, NewsRisk
from ..timeutil import now_et, to_et, et_stamp
from . import guidance
from .paths import ProjectPaths, paths
from .state import (ConfigError, account_state_to_dict, coerce_account_config,
                    coerce_system_fields, load_account_state, load_system_config,
                    save_account_state, save_system_config)

__all__ = ["ApiError", "bootstrap", "preview", "update_config", "update_account",
           "add_position", "close_position", "reset_day", "staffing", "ROUTES"]


class ApiError(Exception):
    """A request that cannot be served, with an HTTP status attached."""

    def __init__(self, message: str, status: int = 400, field: str = ""):
        self.status = status
        self.field = field
        super().__init__(message)


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

def _contract_payload(spec: ContractSpec) -> dict:
    """Everything the UI needs to do arithmetic on one contract locally.

    The browser recomputes tick and dollar figures as the user drags a slider,
    so it needs the specification rather than a round trip per keystroke. The
    server stays the authority on *approval*; the browser only previews cost.
    """
    return {
        "symbol": spec.symbol,
        "name": spec.name,
        "exchange": spec.exchange,
        "tick_size": spec.tick_size,
        "point_value": spec.point_value,
        "tick_value": round(spec.tick_value, 6),
        "round_turn_cost": round(spec.round_turn_cost, 2),
        "commission_per_side": spec.commission_per_side,
        "exchange_fee_per_side": spec.exchange_fee_per_side,
        "typical_slippage_ticks": spec.typical_slippage_ticks,
        "min_stop_ticks": spec.min_stop_ticks,
        "typical_atr_points": spec.typical_atr_points,
        "is_micro": spec.is_micro,
        "full_size_symbol": spec.full_size_symbol,
        "micro_symbol": spec.micro_symbol,
        "correlation_group": spec.correlation_group,
        "correlated_with": sorted(correlated_symbols(spec.symbol)),
        "rth_open": spec.rth_open,
        "rth_close": spec.rth_close,
        # Decimals for display, derived from the tick so a 0.001 tick does not
        # render as a rounded 0.00.
        "price_decimals": max(0, min(6, -int(math.floor(math.log10(spec.tick_size))))
                              if spec.tick_size > 0 else 2),
        # What one contract risks at a stop of roughly one ATR - the scale a
        # structural stop actually lands at. Computed from the spec rather than
        # quoted from prose, so it is right for every contract and cannot drift
        # out of agreement with the tick and point values above.
        "median_stop_points": round(spec.typical_atr_points, 6),
        "median_stop_dollars": round(spec.typical_atr_points * spec.point_value, 2),
    }


#: Field metadata for the Risk Studio: label, group, widget and, crucially, what
#: the field actually does. A risk control the user does not understand is a risk
#: control they will set wrongly, so the explanation ships with the input.
ACCOUNT_FIELD_META: Tuple[Dict[str, Any], ...] = (
    # ---- the account itself ------------------------------------------
    {"key": "starting_equity", "label": "Starting equity", "group": "Account",
     "kind": "money", "min": 500, "max": 1_000_000, "step": 500,
     "help": "The account's opening balance. Live equity is tracked separately on "
             "the Account panel; this is the anchor for a static drawdown."},
    {"key": "max_total_drawdown", "label": "Max total drawdown", "group": "Account",
     "kind": "money", "min": 100, "max": 100_000, "step": 100,
     "help": "Dollars from the peak at which the account is considered failed. "
             "Everything else is derived from the distance to this line."},
    {"key": "trailing_drawdown", "label": "Drawdown trails the peak",
     "group": "Account", "kind": "bool",
     "help": "On: the failure line follows each new equity high, so profit is "
             "protected but never banked. Off: the line is fixed below starting "
             "equity."},
    {"key": "protected_buffer_pct", "label": "Protected reserve",
     "group": "Account", "kind": "pct", "min": 0, "max": 0.9, "step": 0.01,
     "help": "Share of the drawdown allowance that is never spent. Position size "
             "comes from the distance to failure MINUS this reserve, so the "
             "account gets smaller as it loses without anyone remembering to."},

    # ---- per trade ----------------------------------------------------
    {"key": "base_risk_pct_of_buffer", "label": "Risk per trade (of usable buffer)",
     "group": "Per trade", "kind": "pct", "min": 0, "max": 0.5, "step": 0.005,
     "help": "The primary dial. Risk is a fraction of the REMAINING usable buffer, "
             "not of equity, which is what makes sizing self-tightening."},
    {"key": "max_risk_pct_of_equity", "label": "Hard ceiling (of equity)",
     "group": "Per trade", "kind": "pct", "min": 0, "max": 0.05, "step": 0.00025,
     "help": "An absolute cap on any single trade as a share of equity, applied "
             "after the buffer calculation. The binding one wins."},
    {"key": "min_dollar_risk", "label": "Minimum dollar risk", "group": "Per trade",
     "kind": "money", "min": 0, "max": 5_000, "step": 5,
     "help": "Below this the trade is noise and is vetoed rather than taken tiny."},
    {"key": "max_dollar_risk", "label": "Maximum dollar risk", "group": "Per trade",
     "kind": "money", "min": 10, "max": 20_000, "step": 25,
     "help": "Absolute per-trade ceiling in dollars, whatever the percentages say."},

    # ---- the session --------------------------------------------------
    {"key": "daily_loss_limit", "label": "Daily loss limit (hard)", "group": "Session",
     "kind": "money", "min": 50, "max": 50_000, "step": 50,
     "help": "Reaching this halts the session outright. No trade may be sized so "
             "that losing it would breach this line."},
    {"key": "daily_soft_loss_limit", "label": "Daily loss limit (soft)",
     "group": "Session", "kind": "money", "min": 0, "max": 50_000, "step": 50,
     "help": "Past this, risk per trade is halved. Must sit inside the hard limit "
             "or it could never fire."},
    {"key": "daily_profit_lockdown", "label": "Profit lockdown", "group": "Session",
     "kind": "money", "min": 0, "max": 50_000, "step": 50,
     "help": "Once the day's peak profit reaches this, giveback protection arms."},
    {"key": "daily_giveback_pct", "label": "Giveback tolerance", "group": "Session",
     "kind": "pct", "min": 0, "max": 1.0, "step": 0.05,
     "help": "Surrender this share of a locked-down day's peak profit and the "
             "session goes observation-only. Giving back a good day is how a "
             "winning week becomes a losing one."},
    {"key": "max_trades_per_day", "label": "Max trades per day", "group": "Session",
     "kind": "int", "min": 1, "max": 50, "step": 1,
     "help": "A cap on activity, not on quality. Hitting it ends the session's "
             "trading, not its analysis."},
    {"key": "max_consecutive_losses", "label": "Max consecutive losses",
     "group": "Session", "kind": "int", "min": 1, "max": 20, "step": 1,
     "help": "After this many in a row the session becomes observation-only."},

    # ---- exposure -----------------------------------------------------
    {"key": "max_concurrent_positions", "label": "Max concurrent positions",
     "group": "Exposure", "kind": "int", "min": 1, "max": 10, "step": 1,
     "help": "Total open positions allowed at once."},
    {"key": "max_correlated_positions", "label": "Max per correlation group",
     "group": "Exposure", "kind": "int", "min": 1, "max": 10, "step": 1,
     "help": "MNQ and MES are one index complex. Two longs there is one larger "
             "position, not a diversified pair - which is why the default is 1."},

    # ---- quality gates ------------------------------------------------
    {"key": "min_reward_risk", "label": "Minimum reward:risk", "group": "Quality gates",
     "kind": "ratio", "min": 0, "max": 10, "step": 0.1,
     "help": "Measured to the final target. Note the measurement: win rate and "
             "payoff cancel out, so a demanding ratio buys a lower hit rate rather "
             "than a better expectancy."},
    {"key": "min_confidence", "label": "Minimum confidence", "group": "Quality gates",
     "kind": "pct", "min": 0, "max": 1, "step": 0.01,
     "help": "Floor on the decision layer's stated confidence."},
    {"key": "min_backtest_trades", "label": "Minimum measured trades",
     "group": "Quality gates", "kind": "int", "min": 0, "max": 1000, "step": 5,
     "help": "Sample-size floor. A 75% win rate on 20 trades is compatible with "
             "53%, so a small sample cannot support a live decision."},
    {"key": "min_expectancy_r", "label": "Minimum expectancy (R)",
     "group": "Quality gates", "kind": "r", "min": -1, "max": 2, "step": 0.01,
     "help": "Per-trade expectancy in R the strategy must have shown. This is the "
             "column that decides whether a strategy makes money."},

    # ---- volatility ---------------------------------------------------
    {"key": "max_atr_multiple_of_median", "label": "Volatility ceiling",
     "group": "Volatility", "kind": "ratio", "min": 1, "max": 10, "step": 0.1,
     "help": "ATR above this multiple of its median means stop distance and "
             "slippage are both unreliable - no trade."},
    {"key": "min_atr_multiple_of_median", "label": "Volatility floor",
     "group": "Volatility", "kind": "ratio", "min": 0, "max": 2, "step": 0.05,
     "help": "ATR below this multiple means too little movement to cover costs."},

    # ---- news ---------------------------------------------------------
    {"key": "news_blackout_before_min", "label": "News blackout before (min)",
     "group": "News", "kind": "int", "min": 0, "max": 240, "step": 1,
     "help": "Minutes before a high-impact release during which entries are "
             "blocked."},
    {"key": "news_blackout_after_min", "label": "News blackout after (min)",
     "group": "News", "kind": "int", "min": 0, "max": 240, "step": 1,
     "help": "Minutes after. Fifteen is the conservative end of the -10/+20 event "
             "window in the monetary-policy-surprise literature; it is a citation, "
             "not something measured on this repository's bars."},
    {"key": "high_impact_risk_multiplier", "label": "Size multiplier near news",
     "group": "News", "kind": "ratio", "min": 0, "max": 1, "step": 0.05,
     "help": "Applied to risk when a catalyst is close but outside the blackout."},
)


# --------------------------------------------------------------------------
# Loading the world for one request
# --------------------------------------------------------------------------

class _World:
    """Config, account and risk manager for a single request.

    Rebuilt per request on purpose: the files in the project folder are the
    source of truth and the user is invited to edit them by hand, so anything
    held between requests would go stale the moment they did.
    """

    def __init__(self, p: Optional[ProjectPaths] = None):
        self.paths = (p or paths()).ensure()
        try:
            self.config: SystemConfig = load_system_config(self.paths)
            self.account: AccountState = load_account_state(self.config, self.paths)
        except ConfigError as exc:
            raise ApiError(str(exc), status=422, field=getattr(exc, "field", "")) from None
        self.risk = RiskManager(self.config.account, self.account)


def _account_payload(world: _World) -> dict:
    st = world.account
    mode, reasons = world.risk.mode()
    budget, multiplier, notes = world.risk.risk_budget()
    day = st.day
    return {
        "equity": round(st.equity, 2),
        "peak_equity": round(st.peak_equity, 2),
        "failure_equity": round(st.failure_equity, 2),
        "drawdown": round(st.drawdown, 2),
        "drawdown_pct": round(st.drawdown_pct, 5),
        "remaining_drawdown": round(st.remaining_drawdown, 2),
        "usable_buffer": round(st.usable_buffer, 2),
        "buffer_consumed_pct": round(st.buffer_consumed_pct, 4),
        "derisk_multiplier": round(st.derisk_multiplier, 4),
        "has_failed": st.has_failed,
        "closed_trades": st.closed_trades,
        "lifetime_wins": st.lifetime_wins,
        "lifetime_losses": st.lifetime_losses,
        "consecutive_losses": st.consecutive_losses,
        "open_positions": [p.to_dict() for p in st.open_positions],
        "open_risk": round(st.open_risk, 2),
        "equity_curve": [[ts, round(v, 2)] for ts, v in st.equity_curve[-200:]],
        "day": day.to_dict() if day else None,
        "remaining_daily_loss_budget": round(st.remaining_daily_loss_budget, 2),
        "mode": mode.value,
        "mode_can_trade": mode.can_trade,
        "mode_reasons": reasons,
        "next_trade_budget": round(budget, 2),
        "next_trade_multiplier": round(multiplier, 4),
        "next_trade_notes": notes,
    }


def bootstrap(payload: Optional[dict] = None, *, p: Optional[ProjectPaths] = None
              ) -> dict:
    """Everything the UI needs to render its first frame."""
    world = _World(p)
    cfg = world.config
    return {
        "generated_et": et_stamp(),
        "paths": world.paths.describe(),
        "account_config": {
            **cfg.account.to_dict(),
            "derisk_ladder": [list(r) for r in cfg.account.derisk_ladder],
        },
        "account_field_meta": [dict(m) for m in ACCOUNT_FIELD_META],
        "system": {
            "model": cfg.model, "effort": cfg.effort, "enable_llm": cfg.enable_llm,
            "enable_web_search": cfg.enable_web_search, "timezone": cfg.timezone,
            "symbols": list(cfg.symbols),
            "timeframes": list(cfg.timeframes),
            "db_path": cfg.db_path, "data_dir": cfg.data_dir,
        },
        "account": _account_payload(world),
        "contracts": {s: _contract_payload(spec) for s, spec in CONTRACTS.items()},
        "default_symbols": list(DEFAULT_SYMBOLS),
        "timeframe_labels": {str(k): v for k, v in TIMEFRAME_LABELS.items()},
        "guidance": guidance.as_dict(),
        "agents": staffing(p=p),
    }


# --------------------------------------------------------------------------
# The risk preview - the core screen
# --------------------------------------------------------------------------

def _direction(raw: Any) -> Direction:
    try:
        return Direction(str(raw).upper().strip())
    except ValueError:
        raise ApiError(f"{raw!r} is not LONG, SHORT or NEUTRAL", field="direction") from None


def _news_risk(raw: Any) -> NewsRisk:
    if raw in (None, ""):
        return NewsRisk.NONE
    try:
        return NewsRisk(str(raw).upper().strip())
    except ValueError:
        raise ApiError(f"{raw!r} is not a known news-risk level",
                       field="news_risk") from None


def _historical(raw: Optional[dict]) -> Optional[HistoricalPerformance]:
    """Build a :class:`HistoricalPerformance` only when the user supplied one.

    Returning ``None`` for an empty block is deliberate: the risk engine vetoes a
    proposal with no measured history, and that veto is the correct answer for a
    hand-typed ticket. Fabricating a neutral-looking record here would convert an
    honest refusal into a silent approval.
    """
    if not raw or not isinstance(raw, dict):
        return None
    if not any(str(v).strip() not in ("", "0", "0.0", "None")
               for v in raw.values()):
        return None
    hp = HistoricalPerformance()
    for key, value in raw.items():
        if not hasattr(hp, key) or value in (None, ""):
            continue
        current = getattr(hp, key)
        try:
            if isinstance(current, bool):
                setattr(hp, key, bool(value))
            elif isinstance(current, int):
                setattr(hp, key, int(round(float(value))))
            elif isinstance(current, float):
                setattr(hp, key, float(value))
            else:
                setattr(hp, key, str(value))
        except (TypeError, ValueError):
            raise ApiError(f"historical.{key}: {value!r} is not a number",
                           field=f"historical.{key}") from None
    return hp


def _proposal_from(payload: dict) -> TradeProposal:
    symbol = str(payload.get("symbol", "")).upper().strip()
    try:
        get_contract(symbol)
    except KeyError as exc:
        raise ApiError(str(exc), field="symbol") from None

    def num(key: str, default: float = 0.0) -> float:
        raw = payload.get(key, default)
        if raw in (None, ""):
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ApiError(f"{key}: {raw!r} is not a number", field=key) from None

    targets_raw = payload.get("targets") or []
    if isinstance(targets_raw, (int, float, str)):
        targets_raw = [targets_raw]
    targets: List[float] = []
    for t in targets_raw:
        if t in (None, ""):
            continue
        try:
            targets.append(float(t))
        except (TypeError, ValueError):
            raise ApiError(f"targets: {t!r} is not a number", field="targets") from None

    atr = payload.get("atr")
    atr_median = payload.get("atr_median")
    return TradeProposal(
        symbol=symbol,
        direction=_direction(payload.get("direction", "NEUTRAL")),
        entry=num("entry"),
        stop=num("stop"),
        targets=targets,
        confidence=num("confidence"),
        strategy_id=str(payload.get("strategy_id") or ""),
        strategy_name=str(payload.get("strategy_name") or ""),
        timeframe=int(num("timeframe")),
        regime=str(payload.get("regime") or "UNKNOWN"),
        volatility=str(payload.get("volatility") or "NORMAL"),
        session=str(payload.get("session") or ""),
        news_risk=_news_risk(payload.get("news_risk")),
        minutes_to_high_impact=(float(payload["minutes_to_high_impact"])
                                if payload.get("minutes_to_high_impact") not in (None, "")
                                else None),
        historical=_historical(payload.get("historical")),
        atr=float(atr) if atr not in (None, "") else None,
        atr_median=float(atr_median) if atr_median not in (None, "") else None,
        analyst_agreement=num("analyst_agreement"),
    )


#: Stop distances to cost out, as multiples of the supplied ATR. The 0.5 row is
#: the measured floor; the 1.0 row is roughly where a structural stop lands.
_ATR_LADDER: Tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)


def _stop_ladder(spec: ContractSpec, world: _World, atr: Optional[float],
                 budget: float) -> List[dict]:
    """What each candidate stop distance costs, per contract and in total.

    This is the answer to "why can't I take this trade?" before it is asked. The
    ``below_floor`` flag marks distances the measurement rejected outright (under
    0.5 ATR, or inside the contract's own noise floor in ticks).
    """
    scale = atr if atr and atr > 0 else spec.typical_atr_points
    if not scale or scale <= 0:
        return []
    equity = world.account.equity or 1.0
    rows: List[dict] = []
    for mult in _ATR_LADDER:
        points = scale * mult
        ticks = points / spec.tick_size if spec.tick_size else 0.0
        per_contract = points * spec.point_value
        contracts = int(math.floor(budget / per_contract)) if per_contract > 0 else 0
        rows.append({
            "atr_multiple": mult,
            "points": round(points, 6),
            "ticks": round(ticks, 1),
            "per_contract_dollars": round(per_contract, 2),
            "per_contract_pct_of_equity": round(per_contract / equity, 5),
            "contracts_at_budget": contracts,
            "total_dollars_at_budget": round(contracts * per_contract, 2),
            "below_atr_floor": mult < 0.5,
            "below_tick_floor": ticks < spec.min_stop_ticks,
            "affordable": contracts >= 1,
        })
    return rows


def _loss_walk(world: _World, dollar_risk: float, steps: int = 6) -> List[dict]:
    """Where the account stands after N consecutive losses at this size.

    The engine already de-risks as the buffer is consumed, but a multiplier
    ladder is abstract. Walking it forward turns "0.75x at 25% consumed" into
    "your fourth loss in a row leaves you observation-only with $1,400 of buffer",
    which is the form a person can actually act on.

    This is projection, not prediction: it assumes each trade loses exactly its
    full stop, which is the pessimistic case and the only one worth planning for.
    """
    if dollar_risk <= 0:
        return []
    cfg = world.config.account
    equity = world.account.equity
    peak = world.account.peak_equity
    day_pnl = world.account.daily_pnl
    consecutive = world.account.day.consecutive_losses if world.account.day else 0
    trades = world.account.day.trades_taken if world.account.day else 0
    out: List[dict] = []
    for n in range(1, steps + 1):
        equity -= dollar_risk
        day_pnl -= dollar_risk
        consecutive += 1
        trades += 1
        usable = cfg.usable_buffer(equity, peak)
        mult = cfg.derisk_multiplier(equity, peak)
        failed = equity <= cfg.failure_equity(peak)
        if failed:
            mode = "HALTED (account failed)"
        elif day_pnl <= -cfg.daily_loss_limit:
            mode = "HALTED (daily loss limit)"
        elif usable <= 0:
            mode = "HALTED (buffer exhausted)"
        elif consecutive >= cfg.max_consecutive_losses:
            mode = "OBSERVATION_ONLY (consecutive losses)"
        elif trades >= cfg.max_trades_per_day:
            mode = "OBSERVATION_ONLY (trade cap)"
        elif day_pnl <= -cfg.daily_soft_loss_limit:
            mode = "REDUCED (soft daily limit)"
        elif mult < 1.0:
            mode = "REDUCED (drawdown de-risk)"
        else:
            mode = "NORMAL"
        out.append({
            "loss_number": n,
            "equity": round(equity, 2),
            "day_pnl": round(day_pnl, 2),
            "usable_buffer": round(usable, 2),
            "derisk_multiplier": round(mult, 4),
            "next_size_multiplier": round(mult, 4),
            "mode": mode,
            "terminal": failed or mode.startswith("HALTED"),
        })
        if failed or mode.startswith("HALTED"):
            break
    return out


def _discretionary_sizing(world: _World, proposal: TradeProposal,
                          spec: ContractSpec) -> dict:
    """Size the trade on the account's limits alone, ignoring the history gate.

    Reported under its own key and its own label. This is what the account could
    carry if the user accepts the read is discretionary chart-reading - which,
    given that nothing here cleared a significance threshold, is what every read
    in this repository is. It is not an approval and the UI must not colour it as
    one.
    """
    budget, multiplier, notes = world.risk.risk_budget(proposal)
    per_contract = proposal.risk_points * spec.point_value
    contracts = int(math.floor(budget / per_contract)) if per_contract > 0 else 0
    dollar_risk = contracts * per_contract
    equity = world.account.equity or 1.0
    usable = world.account.usable_buffer
    cost = 2.0 * (spec.commission_per_side + spec.exchange_fee_per_side) * contracts
    slip = spec.typical_slippage_ticks * spec.tick_value * contracts
    first_target_gross = (abs(proposal.targets[0] - proposal.entry) * spec.point_value
                          * contracts) if proposal.targets else 0.0
    return {
        "budget": round(budget, 2),
        "multiplier": round(multiplier, 4),
        "notes": notes,
        "per_contract_dollars": round(per_contract, 2),
        "per_contract_pct_of_equity": round(per_contract / equity, 5),
        "contracts": contracts,
        "dollar_risk": round(dollar_risk, 2),
        "account_risk_pct": round(dollar_risk / equity, 5),
        "buffer_consumed_if_stopped_pct": round(dollar_risk / usable, 5) if usable > 0 else None,
        "expected_cost": round(cost + slip, 2),
        "first_target_net_r": (round((first_target_gross - cost - slip) / dollar_risk, 3)
                               if dollar_risk > 0 else 0.0),
        "label": "Discretionary sizing - what the account's limits allow. NOT an "
                 "approval: the engine's verdict is the block above.",
    }


#: The equity index contracts that ``CALLOUT.md`` treats as a single complex.
#:
#: This does NOT match ``config.py``'s correlation groups, which place MNQ/NQ in
#: ``US_EQUITY_TECH`` and MES/ES/MYM/YM in ``US_EQUITY_BROAD``. The risk engine
#: therefore permits a simultaneous MNQ and MES position, counting them as two
#: diversified trades, while the brief states plainly that they are one larger
#: position on the same underlying risk.
#:
#: The engine's semantics are left alone here - changing a correlation group
#: changes sizing behaviour across the whole system, and that is a decision for
#: whoever owns the risk policy, not for a screen. What the screen does instead
#: is say so, as an advisory beside the trade, so the gap is visible at the
#: moment it would cost something rather than buried in a config file.
INDEX_COMPLEX = frozenset({"MNQ", "NQ", "MES", "ES", "MYM", "YM"})


def _index_complex_advisory(world: _World, symbol: str) -> Optional[dict]:
    """Warn when a second index-complex position would look diversified."""
    if symbol.upper() not in INDEX_COMPLEX:
        return None
    others = [pos.symbol for pos in world.account.open_positions
              if pos.symbol in INDEX_COMPLEX and pos.symbol != symbol.upper()]
    if not others:
        return None
    engine_blocks = bool(world.account.correlated_exposure(symbol))
    return {
        "symbol": symbol.upper(),
        "open_in_complex": others,
        "engine_blocks": engine_blocks,
        "note": (
            f"{symbol.upper()} and {', '.join(others)} are one index complex. "
            + ("The risk engine's correlation groups already block this."
               if engine_blocks else
               "The risk engine's correlation groups treat them as SEPARATE and "
               "will not block this - it counts as two diversified trades when it "
               "is one larger position on the same underlying risk.")),
    }


def preview(payload: dict, *, p: Optional[ProjectPaths] = None) -> dict:
    """Score a trade ticket: engine verdict, sizing, stop costs, loss projection."""
    world = _World(p)
    proposal = _proposal_from(payload or {})
    spec = get_contract(proposal.symbol)

    assessment = world.risk.assess(proposal)
    budget, _mult, _notes = world.risk.risk_budget(proposal)
    disc = _discretionary_sizing(world, proposal, spec)

    # The 0.5-ATR floor is a measured rule, not the engine's own check, so it is
    # reported here rather than silently folded into the veto list.
    atr = proposal.atr if proposal.atr and proposal.atr > 0 else None
    floor_points = (atr * 0.5) if atr else None
    ticks = spec.ticks_between(proposal.entry, proposal.stop)

    now = to_et(now_et())
    in_dead_hour = now.hour == 15

    sized_risk = (assessment.dollar_risk if assessment.approved
                  else disc["dollar_risk"])

    return {
        "generated_et": et_stamp(),
        "symbol": proposal.symbol,
        "contract": _contract_payload(spec),
        "framework": guidance.framework_for(proposal.symbol).to_dict(),
        "proposal": {
            "direction": proposal.direction.value,
            "entry": proposal.entry,
            "stop": proposal.stop,
            "targets": list(proposal.targets),
            "risk_points": round(proposal.risk_points, 6),
            "risk_ticks": round(ticks, 1),
            "reward_risk": round(proposal.reward_risk, 3),
            "first_target_rr": round(proposal.first_target_rr, 3),
            "has_history": proposal.historical is not None,
        },
        "assessment": assessment.to_dict(),
        "discretionary": disc,
        "stop_ladder": _stop_ladder(spec, world, atr, budget),
        "guards": {
            "atr_half_floor_points": round(floor_points, 6) if floor_points else None,
            "below_atr_half_floor": bool(floor_points and proposal.risk_points < floor_points),
            "tick_floor": spec.min_stop_ticks,
            "below_tick_floor": ticks < spec.min_stop_ticks,
            "in_1500_1600_et": in_dead_hour,
            "now_et": now.strftime("%Y-%m-%d %H:%M %Z"),
            "dead_hour_note": "No intraday entries 15:00-16:00 ET (z = -4.43, "
                              "median -0.617R, replicated).",
            "index_complex": _index_complex_advisory(world, proposal.symbol),
        },
        "loss_walk": _loss_walk(world, sized_risk),
        "account": _account_payload(world),
    }


# --------------------------------------------------------------------------
# Mutations
# --------------------------------------------------------------------------

def update_config(payload: dict, *, p: Optional[ProjectPaths] = None) -> dict:
    """Write the risk configuration back to ``desk-config.json``."""
    world = _World(p)
    body = payload or {}
    try:
        account_cfg = coerce_account_config(world.config.account,
                                           body.get("account") or {})
        cfg = coerce_system_fields(world.config, body.get("system") or {})
    except ConfigError as exc:
        raise ApiError(str(exc), status=422, field=exc.field) from None
    cfg.account = account_cfg
    try:
        cfg.validate()
    except ValueError as exc:
        raise ApiError(str(exc), status=422) from None
    written = save_system_config(cfg, world.paths)
    out = bootstrap(p=p)
    out["saved"] = str(written)
    return out


def update_account(payload: dict, *, p: Optional[ProjectPaths] = None) -> dict:
    """Set live account figures: equity, peak, and today's ledger."""
    world = _World(p)
    body = payload or {}
    st = world.account

    def num(key: str, current: float, minimum: float = 0.0) -> float:
        if key not in body or body[key] in (None, ""):
            return current
        try:
            val = float(body[key])
        except (TypeError, ValueError):
            raise ApiError(f"{key}: {body[key]!r} is not a number", 422, key) from None
        if val < minimum:
            raise ApiError(f"{key}: must be at least {minimum:g}", 422, key)
        return val

    st.equity = num("equity", st.equity)
    st.peak_equity = max(num("peak_equity", st.peak_equity), st.equity)
    if st.day is not None:
        st.day.realised_pnl = num("day_realised_pnl", st.day.realised_pnl,
                                  minimum=-1e12)
        st.day.peak_pnl = max(st.day.peak_pnl, st.day.realised_pnl)
        if "day_trades_taken" in body:
            st.day.trades_taken = int(num("day_trades_taken", st.day.trades_taken))
        if "day_consecutive_losses" in body:
            st.day.consecutive_losses = int(
                num("day_consecutive_losses", st.day.consecutive_losses))
    save_account_state(st, world.paths)
    return {"account": _account_payload(world),
            "saved": str(world.paths.state_file)}


def add_position(payload: dict, *, p: Optional[ProjectPaths] = None) -> dict:
    """Record an open position so exposure and correlation limits see it."""
    world = _World(p)
    body = payload or {}
    symbol = str(body.get("symbol", "")).upper().strip()
    try:
        spec = get_contract(symbol)
    except KeyError as exc:
        raise ApiError(str(exc), field="symbol") from None
    if world.account.position_for(symbol) is not None:
        raise ApiError(f"already holding a position in {symbol}", 409, "symbol")

    def num(key: str, default: float = 0.0) -> float:
        try:
            return float(body.get(key, default) or default)
        except (TypeError, ValueError):
            raise ApiError(f"{key} is not a number", field=key) from None

    entry, stop = num("entry"), num("stop")
    contracts = max(1, int(num("contracts", 1)))
    pos = OpenPosition(
        symbol=symbol, direction=_direction(body.get("direction", "LONG")),
        contracts=contracts, entry=entry, stop=stop,
        targets=[float(t) for t in (body.get("targets") or []) if t not in (None, "")],
        strategy_id=str(body.get("strategy_id") or ""),
        dollar_risk=round(abs(entry - stop) * spec.point_value * contracts, 2),
        callout_id=str(body.get("callout_id") or ""),
    )
    world.account.open_position(pos)
    save_account_state(world.account, world.paths)
    return {"account": _account_payload(world), "opened": pos.to_dict()}


def close_position(payload: dict, *, p: Optional[ProjectPaths] = None) -> dict:
    """Close a tracked position at a realised P&L, updating equity and the day."""
    world = _World(p)
    body = payload or {}
    symbol = str(body.get("symbol", "")).upper().strip()
    try:
        pnl = float(body.get("pnl", 0.0) or 0.0)
    except (TypeError, ValueError):
        raise ApiError("pnl is not a number", field="pnl") from None
    closed = world.account.close_position(symbol, pnl)
    if closed is None:
        raise ApiError(f"no open position in {symbol}", 404, "symbol")
    save_account_state(world.account, world.paths)
    return {"account": _account_payload(world), "closed": closed.to_dict(),
            "pnl": round(pnl, 2)}


def reset_day(payload: Optional[dict] = None, *, p: Optional[ProjectPaths] = None
              ) -> dict:
    """Start a fresh session ledger, leaving equity and the peak alone."""
    world = _World(p)
    from ..risk.account import DayState
    from ..timeutil import trading_day
    world.account.day = DayState(day=trading_day(now_et()),
                                 starting_equity=world.account.equity)
    save_account_state(world.account, world.paths)
    return {"account": _account_payload(world)}


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

#: The roster, described for the UI. Kept here rather than read from
#: ``.claude/agents/*.md`` because those files are the *prompts* - this is the
#: pipeline, and the order is the order a cycle actually runs in.
AGENT_ROSTER: Tuple[Dict[str, Any], ...] = (
    {"key": "analyst-a", "name": "Technical analyst", "stage": 1,
     "reads": "price action, structure, liquidity, trend, VWAP, volume",
     "note": "Independent read. Does not see the other analysts' work."},
    {"key": "analyst-b", "name": "Quantitative analyst", "stage": 1,
     "reads": "historical probabilities, regime, volatility, expected value",
     "note": "Independent read, from the measured tables."},
    {"key": "analyst-c", "name": "Macro / news analyst", "stage": 1,
     "reads": "releases, central banks, yields, the dollar, correlations",
     "note": "Independent read. The only analyst with web access."},
    {"key": "decision", "name": "Confluence decision", "stage": 2,
     "reads": "all three analysts, weighted by measured accuracy",
     "note": "Concludes LONG, SHORT or NO TRADE. Two signals plus one filter is "
             "the ceiling - more confluence measured worse."},
    {"key": "risk", "name": "Risk manager", "stage": 3,
     "reads": "the proposal against the account's live state",
     "note": "Holds an unconditional veto. Deterministic - no LLM, no opinion."},
    {"key": "journal", "name": "Journal", "stage": 4,
     "reads": "the decision and, later, the outcome",
     "note": "Records the prediction, then scores it. This is what makes accuracy "
             "measurable rather than remembered."},
)


def staffing(payload: Optional[dict] = None, *, p: Optional[ProjectPaths] = None
             ) -> dict:
    """Which agent roles are staffed, and whether a reasoning model is reachable.

    Imports the orchestrator lazily. It pulls in the whole strategy and data
    stack, which is a slow import and an unnecessary one for a user who only
    wants to adjust their stop distance.
    """
    world = _World(p)
    out: Dict[str, Any] = {
        "roster": [dict(a) for a in AGENT_ROSTER],
        "cycle": ["analyst-a + analyst-b + analyst-c (independent, parallel)",
                  "decision (weighs them)",
                  "risk (sizes or vetoes)",
                  "render the callout in its direction colour",
                  "journal (logs the prediction for later scoring)"],
        "llm_enabled_in_config": world.config.enable_llm,
        "api_key_present": bool(__import__("os").environ.get("ANTHROPIC_API_KEY")),
        "model": world.config.model,
        "effort": world.config.effort,
    }
    try:
        from ..team.roles import Role, staffing_report
        report = staffing_report()
        out["available_classes"] = report.get("available", [])
        out["roles"] = sorted(r.value for r in Role)
    except Exception as exc:                      # pragma: no cover - import guard
        out["available_classes"] = []
        out["roles"] = []
        out["staffing_error"] = f"{type(exc).__name__}: {exc}"
    if not out["api_key_present"]:
        out["note"] = ("No ANTHROPIC_API_KEY in the environment, so the reasoning "
                       "agents run their deterministic fallback path. Every number "
                       "on the risk screens is computed without a model and is "
                       "unaffected.")
    return out


#: ``method -> path -> handler``. The server does nothing but dispatch through
#: this table, so adding an endpoint is a one-line change in one place.
ROUTES: Dict[str, Dict[str, Any]] = {
    "GET": {
        "/api/bootstrap": bootstrap,
        "/api/agents": staffing,
    },
    "POST": {
        "/api/preview": preview,
        "/api/config": update_config,
        "/api/account": update_account,
        "/api/position/open": add_position,
        "/api/position/close": close_position,
        "/api/day/reset": reset_day,
    },
}
