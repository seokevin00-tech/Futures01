"""The calculator: what would I do, right now, on this contract.

This is the small, practical end of the system. It reads the latest bars,
evaluates the registered strategies at the most recent closed bar, and works
out the arithmetic a trader actually needs: direction, entry, stop, targets,
position size for a $50,000 account, reward-to-risk, and what would have to
happen for the idea to be wrong.

Three things it will not do, each because the specification insists and each
because the alternative is how accounts die:

**It will say NO TRADE.** That is a legitimate and common answer, not a
failure to produce output. On the data in this repository it is the near
universal answer, and saying so plainly is the whole point.

**It sizes from distance-to-failure, not from equity.** A $50,000 account with
a $5,000 trailing drawdown has $5,000 of room, not $50,000, and the position
is derived from the room less a protected reserve.

**It shows its work.** Every number carries where it came from - which
strategy, which timeframe, what the stop is measured against - so a callout
can be traced back rather than taken on faith.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from .alerts import Priority, alert
from .backtest.metrics import compute_metrics
from .config import AccountConfig, ContractSpec, get_contract, tf_label
from .data.loader import load_csv, load_symbol
from .features import build_symbol_frame
from .risk.account import AccountState
from .risk.manager import RiskManager, TradeProposal
from .schema import Direction, fmt_price
from .strategies.base import Strategy
from .backtest.engine import run_portfolio
from .schema import HistoricalPerformance
from .strategies.combinator import TEMPLATES, generate_strategies
from .timeutil import et_stamp, to_et

__all__ = ["Calculation", "calculate", "render"]

_ALL_GROUPS = [t.group for t in TEMPLATES]


@dataclass
class Calculation:
    """One decision, with the arithmetic that produced it."""

    symbol: str
    timeframe: int
    when: datetime
    price: float
    spec: ContractSpec
    decision: str = "NO TRADE"          # LONG | SHORT | NO TRADE
    entry: Optional[float] = None
    stop: Optional[float] = None
    targets: List[float] = field(default_factory=list)
    contracts: int = 0
    risk_dollars: float = 0.0
    reward_risk: Optional[float] = None
    strategy_id: str = ""
    strategy_name: str = ""
    confluences: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    invalidation: str = ""
    reasons: List[str] = field(default_factory=list)
    account_room: float = 0.0
    history: Optional[Any] = None
    risk_warnings: List[str] = field(default_factory=list)
    bars_seen: int = 0
    candidates: int = 0

    @property
    def is_trade(self) -> bool:
        return self.decision in ("LONG", "SHORT")

    def to_dict(self) -> dict:
        return {k: (v.to_dict() if hasattr(v, "to_dict") else v)
                for k, v in self.__dict__.items() if k != "spec"}


def _position_size(spec: ContractSpec, account: AccountConfig,
                   equity: float, stop_points: float,
                   peak_equity: Optional[float] = None) -> tuple[int, float, float]:
    """``(contracts, dollars_at_risk, per_trade_budget)``.

    Equity is the wrong denominator. What can be lost before the account is
    finished is the distance to its failure threshold, less the reserve the
    configuration holds back, and that is a far smaller number than the
    balance: $5,000 of room on a $50,000 account, and less again after the
    protected buffer.

    Every figure comes from :class:`AccountConfig` rather than a local
    constant. An earlier draft reached for attributes that do not exist behind
    ``hasattr`` guards and silently fell back to invented fractions - which
    would have sized positions off numbers nobody configured.
    """
    peak = peak_equity if peak_equity is not None else equity
    usable = account.usable_buffer(equity, peak)
    per_trade = usable * account.base_risk_pct_of_buffer
    per_trade = min(per_trade, account.max_dollar_risk,
                    equity * account.max_risk_pct_of_equity)
    if per_trade < account.min_dollar_risk:
        return 0, 0.0, per_trade
    per_contract = stop_points * spec.point_value
    if per_contract <= 0:
        return 0, 0.0, per_trade
    n = int(per_trade // per_contract)
    return max(0, n), n * per_contract, per_trade


def calculate(symbol: str, *, timeframe: int = 60, data_dir: str = "csv/raw",
              suffix: str = "1h", equity: float = 50_000.0,
              budget: int = 1500, at_index: Optional[int] = None) -> Calculation:
    """Evaluate ``symbol`` at its most recent closed bar."""
    account = AccountConfig()
    try:
        series = load_csv(f"{data_dir}/{symbol}_{suffix}.csv", symbol,
                          _minutes_for(suffix))
    except FileNotFoundError:
        series = load_symbol(symbol, days=120)

    tfs = sorted({timeframe, timeframe * 4} & {1, 5, 15, 30, 60, 240, 1440}) or [timeframe]
    if timeframe not in tfs:
        tfs.append(timeframe)
    frame = build_symbol_frame(series, sorted(set(tfs)))
    i = len(series) - 1 if at_index is None else min(at_index, len(series) - 1)
    snap = frame.snapshot(i)
    spec = get_contract(symbol)

    calc = Calculation(symbol=symbol, timeframe=timeframe, when=snap.ts,
                       price=snap.price, spec=spec, bars_seen=len(series),
                       account_room=max(0.0, equity - account.failure_equity(equity)))

    strategies = generate_strategies(symbol, sorted(set(tfs)), groups=_ALL_GROUPS,
                                     max_total=budget, seed=1)
    calc.candidates = len(strategies)

    fired: List[tuple[Strategy, Any]] = []
    cache: Dict[Any, Any] = {}
    for s in strategies:
        sig = s.evaluate(snap, cache)
        if sig is not None:
            fired.append((s, sig))

    if not fired:
        calc.reasons.append(
            f"no strategy fired at this bar - {len(strategies)} evaluated")
        return calc

    # Agreement across whatever fired is the only conviction available without
    # a measured track record; it is reported, never treated as an edge.
    longs = [x for x in fired if x[1].direction is Direction.LONG]
    shorts = [x for x in fired if x[1].direction is Direction.SHORT]
    side = longs if len(longs) >= len(shorts) else shorts
    if longs and shorts:
        calc.conflicts.append(
            f"{len(longs)} strategies long, {len(shorts)} short - not a consensus")
    if not side:
        calc.reasons.append("strategies fired but agreed on no direction")
        return calc

    strategy, signal = max(side, key=lambda x: x[1].strength)

    # ---- measure the candidate before proposing it --------------------
    # The risk layer refuses a strategy with no track record, and it is right
    # to: an unmeasured edge is not an edge. So measure it here, on the bars
    # BEFORE this one, rather than handing the risk layer an empty record and
    # reporting the refusal as if it were a market judgement.
    history = _measure(frame, strategy, until=i)
    calc.history = history
    stop_points = abs(signal.entry - signal.stop)
    contracts, risk_dollars, per_trade_budget = _position_size(
        spec, account, equity, stop_points)

    calc.entry, calc.stop = signal.entry, signal.stop
    calc.targets = list(signal.targets)
    calc.strategy_id, calc.strategy_name = strategy.strategy_id, strategy.name
    calc.confluences = list(signal.confluences)
    calc.conflicts.extend(signal.conflicts)
    calc.invalidation = signal.invalidation
    calc.contracts, calc.risk_dollars = contracts, risk_dollars
    if calc.targets and stop_points > 0:
        calc.reward_risk = abs(calc.targets[-1] - calc.entry) / stop_points

    # ---- the independent risk layer, which can refuse -----------------
    state = AccountState(config=account, equity=equity)
    state.peak_equity = equity
    risk = RiskManager(account, state)
    proposal = TradeProposal(
        symbol=symbol, direction=signal.direction, entry=signal.entry,
        stop=signal.stop, targets=list(signal.targets),
        confidence=min(1.0, signal.strength), strategy_id=strategy.strategy_id,
        strategy_name=strategy.name, timeframe=strategy.primary_tf,
        regime=signal.regime, volatility=signal.volatility,
        session=signal.session, atr=snap.tf(strategy.primary_tf).get("atr")
        if snap.tf(strategy.primary_tf) else None,
        historical=history,
        analyst_agreement=(len(side) - len(fired) + len(side)) / max(1, len(fired)),
        minutes_to_high_impact=(None if snap.minutes_to_high_impact == float("inf")
                                else snap.minutes_to_high_impact))
    assessment = risk.assess(proposal, when=snap.ts)

    calc.risk_warnings = list(assessment.warnings)
    if not assessment.approved:
        # The vetoes ARE the answer. Reporting "the risk layer refused" without
        # them tells the operator nothing they can act on, and an earlier draft
        # read a "reasons" attribute that does not exist - so every veto
        # rendered as one generic line.
        calc.reasons.extend(assessment.vetoes or ["risk layer refused, no reason given"])
        calc.decision = "NO TRADE"
        return calc
    if contracts <= 0:
        calc.reasons.append(
            f"position sizes to zero contracts: a {stop_points:.2f}-point stop "
            f"costs ${stop_points * spec.point_value:,.0f} per contract against "
            f"a ${per_trade_budget:,.0f} per-trade budget "
            f"(from ${calc.account_room:,.0f} of room to failure)")
        return calc

    calc.decision = signal.direction.value
    return calc


def _measure(frame, strategy: Strategy, *, until: int) -> HistoricalPerformance:
    """The strategy's record on the bars strictly before ``until``.

    Measured in-sample on purpose and labelled as such. It is the evidence the
    risk layer needs to size anything at all, and it is emphatically not a
    validated edge - the research plan exists to decide that, and on this data
    it has yet to approve one.
    """
    res = run_portfolio(frame, [strategy], end=until)
    m = compute_metrics(res[strategy.strategy_id].trades)
    return HistoricalPerformance(
        strategy_id=strategy.strategy_id, symbol=strategy.symbol,
        timeframe=strategy.primary_tf, trades=m.trades, win_rate=m.win_rate,
        avg_win_r=m.avg_win_r, avg_loss_r=m.avg_loss_r,
        profit_factor=m.profit_factor, expectancy_r=m.expectancy_r,
        max_drawdown_r=m.max_drawdown_r)


def _minutes_for(suffix: str) -> int:
    return {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}.get(suffix, 60)


def render(calc: Calculation, *, use_alert: bool = True) -> str:
    """The callout, timestamped in Eastern before the content."""
    spec = calc.spec
    px = lambda v: fmt_price(v, _decimals(spec))
    head = (f"[{et_stamp(calc.when)}]  {calc.symbol} {tf_label(calc.timeframe)}  "
            f"{calc.decision}")
    lines = [head, f"  last {px(calc.price)}   {calc.bars_seen:,} bars   "
                   f"{calc.candidates} strategies evaluated"]

    if calc.is_trade:
        lines += [
            f"  entry   {px(calc.entry)}",
            f"  stop    {px(calc.stop)}   "
            f"({abs(calc.entry - calc.stop):.2f} pts = "
            f"${abs(calc.entry - calc.stop) * spec.point_value:,.0f}/contract)",
            f"  targets {', '.join(px(t) for t in calc.targets)}",
            f"  size    {calc.contracts} contract(s), ${calc.risk_dollars:,.0f} at risk "
            f"of ${calc.account_room:,.0f} room to failure",
        ]
        if calc.reward_risk:
            lines.append(f"  R:R     {calc.reward_risk:.2f} : 1")
        lines.append(f"  from    {calc.strategy_id}")
        if calc.history is not None and calc.history.trades:
            h = calc.history
            lines.append(f"  record  {h.trades} prior trades, {h.win_rate*100:.0f}% win, "
                         f"{h.expectancy_r:+.3f}R, PF {h.profit_factor:.2f}  "
                         f"(in-sample, not a validated edge)")
        for c in calc.confluences[:6]:
            lines.append(f"     + {c}")
        if calc.invalidation:
            lines.append(f"  wrong if {calc.invalidation}")
    else:
        for r in calc.reasons[:5]:
            lines.append(f"  - {r}")
    for c in calc.conflicts[:3]:
        lines.append(f"  ! {c}")
    for w in calc.risk_warnings[:3]:
        lines.append(f"  ~ {w}")

    text = "\n".join(lines)
    if use_alert:
        alert(text, Priority.CALLOUT if calc.is_trade else Priority.INFO)
    return text


def _decimals(spec: ContractSpec) -> int:
    tick = f"{spec.tick_size:.10f}".rstrip("0")
    return len(tick.split(".")[1]) if "." in tick else 0


def scan(symbols: Sequence[str], **kwargs) -> List[Calculation]:
    """Run the calculation across several contracts, independently.

    Independently is the operative word: nothing measured on one contract
    informs another, and the results are never ranked against each other.
    """
    return [calculate(s, **kwargs) for s in symbols]
