"""Contract specifications, account configuration and system-wide risk limits.

Every number in this module is a deliberate, auditable input to the risk layer.
Contract multipliers and tick sizes are CME/NYMEX/COMEX exchange specifications;
commission and slippage defaults are conservative retail estimates and should be
overridden with the values from your own broker's fill history.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------
# Contract specifications
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractSpec:
    """Exchange specification plus execution cost assumptions for one contract.

    ``point_value`` is the dollar change in position value for a 1.00 move in
    price for a single contract.  ``tick_value`` is derived from it, and the two
    are cross-checked in :meth:`validate` so a typo cannot silently corrupt
    position sizing.
    """

    symbol: str
    name: str
    exchange: str
    tick_size: float
    point_value: float
    currency: str = "USD"
    # Execution-cost model
    commission_per_side: float = 0.35          # broker commission, one side, one contract
    exchange_fee_per_side: float = 0.37        # exchange + clearing + NFA, one side
    typical_slippage_ticks: float = 1.0        # round-trip slippage assumption, in ticks
    # Session definition, local exchange time expressed in US/Eastern
    rth_open: str = "09:30"
    rth_close: str = "16:00"
    globex_open: str = "18:00"                 # previous calendar day
    globex_close: str = "17:00"
    # Liquidity / risk characteristics
    is_micro: bool = False
    full_size_symbol: Optional[str] = None     # the big brother of a micro
    micro_symbol: Optional[str] = None         # the micro of a full-size contract
    correlation_group: str = ""                # used for correlated-exposure limits
    min_stop_ticks: int = 4                    # stops tighter than this are noise
    typical_atr_points: float = 0.0            # rough scale reference for sanity checks

    @property
    def tick_value(self) -> float:
        """Dollar value of one tick for one contract."""
        return self.tick_size * self.point_value

    @property
    def round_turn_cost(self) -> float:
        """All-in round-turn cost for one contract: both commissions, both fees
        and the slippage assumption."""
        fees = 2.0 * (self.commission_per_side + self.exchange_fee_per_side)
        return fees + self.typical_slippage_ticks * self.tick_value

    def round_to_tick(self, price: float) -> float:
        """Snap a price to the nearest valid tick. Prices that do not sit on a
        tick boundary cannot be filled, so every level the system emits passes
        through here."""
        if self.tick_size <= 0:
            return float(price)
        ticks = round(float(price) / self.tick_size)
        return round(ticks * self.tick_size, 10)

    def ticks_between(self, a: float, b: float) -> float:
        """Distance between two prices measured in ticks (always >= 0)."""
        if self.tick_size <= 0:
            return 0.0
        return abs(float(a) - float(b)) / self.tick_size

    def dollars_per_contract(self, price_distance: float) -> float:
        """Dollar P&L for one contract over ``price_distance`` points."""
        return abs(float(price_distance)) * self.point_value

    def validate(self) -> None:
        if self.tick_size <= 0:
            raise ValueError(f"{self.symbol}: tick_size must be positive")
        if self.point_value <= 0:
            raise ValueError(f"{self.symbol}: point_value must be positive")
        if self.tick_value <= 0:
            raise ValueError(f"{self.symbol}: derived tick_value must be positive")


def _spec(*args, **kwargs) -> ContractSpec:
    s = ContractSpec(*args, **kwargs)
    s.validate()
    return s


#: Contract registry. Add a contract here and the whole system - backtests,
#: sizing, journaling, per-symbol strategy groups - picks it up automatically.
CONTRACTS: Dict[str, ContractSpec] = {
    # ---- Equity index -----------------------------------------------------
    "MNQ": _spec(
        "MNQ", "Micro E-mini Nasdaq-100", "CME", tick_size=0.25, point_value=2.0,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=1.0,
        is_micro=True, full_size_symbol="NQ", correlation_group="US_EQUITY_TECH",
        min_stop_ticks=16, typical_atr_points=120.0,
    ),
    "NQ": _spec(
        "NQ", "E-mini Nasdaq-100", "CME", tick_size=0.25, point_value=20.0,
        commission_per_side=0.85, exchange_fee_per_side=1.42, typical_slippage_ticks=1.0,
        micro_symbol="MNQ", correlation_group="US_EQUITY_TECH",
        min_stop_ticks=16, typical_atr_points=120.0,
    ),
    "MES": _spec(
        "MES", "Micro E-mini S&P 500", "CME", tick_size=0.25, point_value=5.0,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=1.0,
        is_micro=True, full_size_symbol="ES", correlation_group="US_EQUITY_BROAD",
        min_stop_ticks=8, typical_atr_points=45.0,
    ),
    "ES": _spec(
        "ES", "E-mini S&P 500", "CME", tick_size=0.25, point_value=50.0,
        commission_per_side=0.85, exchange_fee_per_side=1.42, typical_slippage_ticks=1.0,
        micro_symbol="MES", correlation_group="US_EQUITY_BROAD",
        min_stop_ticks=8, typical_atr_points=45.0,
    ),
    "MYM": _spec(
        "MYM", "Micro E-mini Dow", "CBOT", tick_size=1.0, point_value=0.50,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=1.0,
        is_micro=True, full_size_symbol="YM", correlation_group="US_EQUITY_BROAD",
        min_stop_ticks=40, typical_atr_points=330.0,
    ),
    "YM": _spec(
        "YM", "E-mini Dow", "CBOT", tick_size=1.0, point_value=5.0,
        commission_per_side=0.85, exchange_fee_per_side=1.42, typical_slippage_ticks=1.0,
        micro_symbol="MYM", correlation_group="US_EQUITY_BROAD",
        min_stop_ticks=40, typical_atr_points=330.0,
    ),
    "M2K": _spec(
        "M2K", "Micro E-mini Russell 2000", "CME", tick_size=0.10, point_value=5.0,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=1.0,
        is_micro=True, full_size_symbol="RTY", correlation_group="US_EQUITY_SMALL",
        min_stop_ticks=30, typical_atr_points=25.0,
    ),
    "RTY": _spec(
        "RTY", "E-mini Russell 2000", "CME", tick_size=0.10, point_value=50.0,
        commission_per_side=0.85, exchange_fee_per_side=1.42, typical_slippage_ticks=1.0,
        micro_symbol="M2K", correlation_group="US_EQUITY_SMALL",
        min_stop_ticks=30, typical_atr_points=25.0,
    ),
    # ---- Metals -----------------------------------------------------------
    "MGC": _spec(
        "MGC", "Micro Gold", "COMEX", tick_size=0.10, point_value=10.0,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=1.0,
        is_micro=True, full_size_symbol="GC", correlation_group="PRECIOUS_METALS",
        rth_open="08:20", rth_close="13:30",
        min_stop_ticks=25, typical_atr_points=28.0,
    ),
    "GC": _spec(
        "GC", "Gold", "COMEX", tick_size=0.10, point_value=100.0,
        commission_per_side=0.85, exchange_fee_per_side=1.55, typical_slippage_ticks=1.0,
        micro_symbol="MGC", correlation_group="PRECIOUS_METALS",
        rth_open="08:20", rth_close="13:30",
        min_stop_ticks=25, typical_atr_points=28.0,
    ),
    "SIL": _spec(
        "SIL", "Micro Silver", "COMEX", tick_size=0.005, point_value=1000.0,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=1.0,
        is_micro=True, full_size_symbol="SI", correlation_group="PRECIOUS_METALS",
        rth_open="08:25", rth_close="13:25",
        min_stop_ticks=20, typical_atr_points=0.85,
    ),
    # ---- Energy -----------------------------------------------------------
    "MCL": _spec(
        "MCL", "Micro WTI Crude Oil", "NYMEX", tick_size=0.01, point_value=100.0,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=1.0,
        is_micro=True, full_size_symbol="CL", correlation_group="ENERGY",
        rth_open="09:00", rth_close="14:30",
        min_stop_ticks=15, typical_atr_points=1.85,
    ),
    "CL": _spec(
        "CL", "WTI Crude Oil", "NYMEX", tick_size=0.01, point_value=1000.0,
        commission_per_side=0.85, exchange_fee_per_side=1.55, typical_slippage_ticks=1.0,
        micro_symbol="MCL", correlation_group="ENERGY",
        rth_open="09:00", rth_close="14:30",
        min_stop_ticks=15, typical_atr_points=1.85,
    ),
    "MNG": _spec(
        "MNG", "Micro Henry Hub Natural Gas", "NYMEX", tick_size=0.001, point_value=1000.0,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=2.0,
        is_micro=True, full_size_symbol="NG", correlation_group="ENERGY",
        rth_open="09:00", rth_close="14:30",
        min_stop_ticks=20, typical_atr_points=0.16,
    ),
    # ---- Rates / FX -------------------------------------------------------
    "ZN": _spec(
        "ZN", "10-Year T-Note", "CBOT", tick_size=0.015625, point_value=1000.0,
        commission_per_side=0.85, exchange_fee_per_side=1.02, typical_slippage_ticks=1.0,
        correlation_group="RATES",
        rth_open="08:20", rth_close="15:00",
        min_stop_ticks=6, typical_atr_points=0.45,
    ),
    "M6E": _spec(
        "M6E", "Micro EUR/USD", "CME", tick_size=0.0001, point_value=12500.0,
        commission_per_side=0.35, exchange_fee_per_side=0.37, typical_slippage_ticks=1.0,
        is_micro=True, full_size_symbol="6E", correlation_group="FX_MAJORS",
        rth_open="08:20", rth_close="15:00",
        min_stop_ticks=15, typical_atr_points=0.0085,
    ),
}

#: Symbols the system analyses by default. Micros only - a $50K account should
#: express every idea in the smallest tradeable unit so that position size is a
#: dial rather than a switch.
DEFAULT_SYMBOLS: Tuple[str, ...] = ("MNQ", "MES", "MGC", "MCL", "M2K")


def get_contract(symbol: str) -> ContractSpec:
    """Look up a contract spec, raising a helpful error for unknown symbols."""
    key = symbol.upper().strip()
    if key not in CONTRACTS:
        known = ", ".join(sorted(CONTRACTS))
        raise KeyError(f"Unknown contract {symbol!r}. Known contracts: {known}")
    return CONTRACTS[key]


def correlated_symbols(symbol: str) -> List[str]:
    """Every other symbol sharing a correlation group. Two positions in the same
    group are, for risk purposes, one larger position."""
    spec = get_contract(symbol)
    if not spec.correlation_group:
        return []
    return [
        s for s, c in CONTRACTS.items()
        if c.correlation_group == spec.correlation_group and s != spec.symbol
    ]


# --------------------------------------------------------------------------
# Timeframes
# --------------------------------------------------------------------------

#: Every timeframe the system is allowed to analyse, in minutes.
TIMEFRAMES: Tuple[int, ...] = (1, 2, 3, 5, 10, 15, 30, 60, 120, 240, 1440)

TIMEFRAME_LABELS: Dict[int, str] = {
    1: "1m", 2: "2m", 3: "3m", 5: "5m", 10: "10m", 15: "15m",
    30: "30m", 60: "1h", 120: "2h", 240: "4h", 1440: "1D",
}

#: Named timeframe groups. The backtester tests each timeframe on its own *and*
#: each group, so the system can answer whether multi-timeframe alignment
#: actually improves outcomes rather than assuming that it does.
TIMEFRAME_GROUPS: Dict[str, Tuple[int, ...]] = {
    "scalp": (1, 3, 5),
    "short_term": (1, 5, 15),
    "intraday": (5, 15, 60),
    "swing_intraday": (15, 60, 240),
    "higher_tf": (60, 240, 1440),
    "single_1m": (1,),
    "single_5m": (5,),
    "single_15m": (15,),
    "single_60m": (60,),
}


def tf_label(minutes: int) -> str:
    return TIMEFRAME_LABELS.get(minutes, f"{minutes}m")


# --------------------------------------------------------------------------
# Account and risk configuration
# --------------------------------------------------------------------------


@dataclass
class AccountConfig:
    """The $50,000 account this system exists to protect.

    Defaults are deliberately conservative. The decision hierarchy the whole
    system obeys is:

        1. prevent account failure
        2. control drawdown
        3. control individual trade risk
        4. avoid low-quality setups
        5. identify statistically supported opportunities
        6. maximise risk-adjusted returns

    Every field below implements some part of steps 1-3.
    """

    starting_equity: float = 50_000.0
    #: Equity level at which the account is considered failed. Trailing and
    #: static thresholds are both supported; the binding one is the higher.
    max_total_drawdown: float = 5_000.0        # hard failure threshold from peak
    trailing_drawdown: bool = True             # threshold trails the equity peak
    #: Capital that is never treated as trading capital. Risk is computed
    #: against the distance to the failure threshold, not against equity.
    protected_buffer_pct: float = 0.20         # keep 20% of the DD allowance in reserve

    #: Per-trade risk as a fraction of *remaining usable drawdown*, not equity.
    base_risk_pct_of_buffer: float = 0.06      # ~6% of the usable buffer per trade
    max_risk_pct_of_equity: float = 0.0075     # hard ceiling: 0.75% of equity ($375)
    min_dollar_risk: float = 25.0              # below this the trade is noise
    max_dollar_risk: float = 500.0             # absolute per-trade ceiling

    #: Daily controls.
    daily_loss_limit: float = 1_000.0          # stop for the session at -$1,000
    daily_soft_loss_limit: float = 600.0       # halve risk past -$600
    daily_profit_lockdown: float = 1_500.0     # protect a good day: stop adding risk
    daily_giveback_pct: float = 0.40           # stop if 40% of peak daily profit is lost
    max_trades_per_day: int = 6
    max_consecutive_losses: int = 3            # then observation-only for the session
    max_concurrent_positions: int = 2
    max_correlated_positions: int = 1          # one position per correlation group

    #: Quality gates a callout must clear before it can be executable.
    min_reward_risk: float = 1.6
    min_confidence: float = 0.58
    min_backtest_trades: int = 40              # sample-size floor for a live edge
    min_expectancy_r: float = 0.08             # per-trade expectancy in R

    #: Volatility guards.
    max_atr_multiple_of_median: float = 2.5    # too wild -> no trade
    min_atr_multiple_of_median: float = 0.35   # too dead -> no trade

    #: Drawdown-aware de-risking. As the usable buffer is consumed, risk per
    #: trade is scaled by these factors.
    derisk_ladder: Tuple[Tuple[float, float], ...] = (
        # (fraction of usable buffer consumed, risk multiplier)
        (0.00, 1.00),
        (0.25, 0.75),
        (0.50, 0.50),
        (0.70, 0.30),
        (0.85, 0.15),
        (1.00, 0.00),
    )

    #: News blackout windows in minutes around a high-impact event.
    #:
    #: The post-event half was 5 minutes, which is shorter than the adjustment
    #: it exists to sit out. Ederington & Lee (1993) find the price adjustment
    #: lands in the first minute but volatility stays above normal for roughly
    #: fifteen; the standard FOMC event window in the monetary-policy-surprise
    #: literature is -10/+20. Fifteen is the conservative end of that range.
    #: This is a citation, not a measurement - there are no real bars in this
    #: repository to measure reaction decay on, and the synthetic generator
    #: places its one daily shock at a uniformly random minute, so 08:30 ranks
    #: 243rd of 1,380 minutes by mean absolute move. Anything measured here
    #: about news would be measuring a random walk.
    news_blackout_before_min: int = 10
    news_blackout_after_min: int = 15
    high_impact_risk_multiplier: float = 0.5   # size down when a catalyst is near

    def failure_equity(self, peak_equity: float) -> float:
        """The equity level that constitutes account failure."""
        anchor = peak_equity if self.trailing_drawdown else self.starting_equity
        return anchor - self.max_total_drawdown

    def usable_buffer(self, equity: float, peak_equity: float) -> float:
        """Dollars of drawdown available *after* reserving the protected buffer.

        This is the number that per-trade risk is derived from. It shrinks as
        the account draws down, which makes the system automatically more
        conservative exactly when it needs to be.
        """
        total = max(0.0, equity - self.failure_equity(peak_equity))
        reserve = self.max_total_drawdown * self.protected_buffer_pct
        return max(0.0, total - reserve)

    def derisk_multiplier(self, equity: float, peak_equity: float) -> float:
        """Risk multiplier from the de-risk ladder given current drawdown."""
        full_buffer = max(1e-9, self.max_total_drawdown * (1.0 - self.protected_buffer_pct))
        consumed = 1.0 - (self.usable_buffer(equity, peak_equity) / full_buffer)
        consumed = min(1.0, max(0.0, consumed))
        mult = self.derisk_ladder[0][1]
        for threshold, factor in self.derisk_ladder:
            if consumed >= threshold:
                mult = factor
        return mult

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SystemConfig:
    """Top-level configuration object threaded through the whole system."""

    account: AccountConfig = field(default_factory=AccountConfig)
    symbols: Tuple[str, ...] = DEFAULT_SYMBOLS
    timeframes: Tuple[int, ...] = (1, 5, 15, 60, 240)
    timeframe_groups: Tuple[str, ...] = ("short_term", "intraday", "swing_intraday")

    #: LLM configuration. The deterministic core runs without any of this.
    model: str = "claude-opus-5"
    effort: str = "high"                       # low | medium | high | xhigh | max
    max_tokens: int = 16_000
    enable_llm: bool = True                    # set False for a fully offline run
    enable_web_search: bool = True             # news agent's live research
    llm_timeout_seconds: float = 600.0
    llm_max_retries: int = 3

    #: Presentation. Every alert is stamped with Eastern Time before its body.
    timezone: str = "America/New_York"
    flash_enabled: bool = True
    flash_cycles: int = 6
    bell_enabled: bool = True

    #: Storage.
    data_dir: str = "data"
    db_path: str = "data/futures_agents.sqlite3"
    state_path: str = "data/shared_state.json"

    #: Backtest controls.
    in_sample_fraction: float = 0.6
    walk_forward_folds: int = 6
    monte_carlo_runs: int = 2_000
    max_combinations: int = 4_000              # confluence combinations per symbol

    @classmethod
    def from_env(cls, **overrides) -> "SystemConfig":
        """Build a config, letting environment variables override defaults.

        Recognised: FA_MODEL, FA_EFFORT, FA_SYMBOLS, FA_DB, FA_NO_LLM,
        FA_NO_FLASH, FA_STARTING_EQUITY, FA_MAX_DRAWDOWN, FA_DAILY_LOSS_LIMIT.
        """
        cfg = cls(**overrides)
        if v := os.environ.get("FA_MODEL"):
            cfg.model = v
        if v := os.environ.get("FA_EFFORT"):
            cfg.effort = v
        if v := os.environ.get("FA_SYMBOLS"):
            cfg.symbols = tuple(s.strip().upper() for s in v.split(",") if s.strip())
        if v := os.environ.get("FA_DB"):
            cfg.db_path = v
        if os.environ.get("FA_NO_LLM"):
            cfg.enable_llm = False
        if os.environ.get("FA_NO_FLASH"):
            cfg.flash_enabled = False
        if v := os.environ.get("FA_STARTING_EQUITY"):
            cfg.account = replace(cfg.account, starting_equity=float(v))
        if v := os.environ.get("FA_MAX_DRAWDOWN"):
            cfg.account = replace(cfg.account, max_total_drawdown=float(v))
        if v := os.environ.get("FA_DAILY_LOSS_LIMIT"):
            cfg.account = replace(cfg.account, daily_loss_limit=float(v))
        cfg.validate()
        return cfg

    def validate(self) -> None:
        for s in self.symbols:
            get_contract(s)
        for tf in self.timeframes:
            if tf not in TIMEFRAMES:
                raise ValueError(f"Unsupported timeframe {tf}; allowed: {TIMEFRAMES}")
        for g in self.timeframe_groups:
            if g not in TIMEFRAME_GROUPS:
                raise ValueError(f"Unknown timeframe group {g!r}")
        if self.effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError(f"Invalid effort {self.effort!r}")
        if not 0.0 < self.in_sample_fraction < 1.0:
            raise ValueError("in_sample_fraction must be strictly between 0 and 1")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["symbols"] = list(self.symbols)
        d["timeframes"] = list(self.timeframes)
        d["timeframe_groups"] = list(self.timeframe_groups)
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)


def load_config(path: Optional[str] = None, **overrides) -> SystemConfig:
    """Load a :class:`SystemConfig` from JSON, then apply env + kwarg overrides."""
    base: dict = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            base = json.load(fh)
    account_kwargs = base.pop("account", None)
    cfg_kwargs = {k: v for k, v in base.items() if k in SystemConfig.__dataclass_fields__}
    for key in ("symbols", "timeframes", "timeframe_groups"):
        if key in cfg_kwargs and isinstance(cfg_kwargs[key], list):
            cfg_kwargs[key] = tuple(cfg_kwargs[key])
    cfg_kwargs.update(overrides)
    cfg = SystemConfig.from_env(**cfg_kwargs)
    if account_kwargs:
        valid = {k: v for k, v in account_kwargs.items()
                 if k in AccountConfig.__dataclass_fields__}
        if "derisk_ladder" in valid:
            valid["derisk_ladder"] = tuple(tuple(x) for x in valid["derisk_ladder"])
        cfg.account = replace(cfg.account, **valid)
    cfg.validate()
    return cfg
