# futures-agents

A coordinated multi-agent futures research, backtesting and live decision-support
system, built around one constraint: **protecting a $50,000 account.**

Its job is not to predict every market move. It is to identify the situations
where the evidence provides a sufficiently strong edge, and to say *"no trade"*
the rest of the time — which, in practice, is most of the time.

```bash
python3 -m futures_agents.cli demo --no-llm
```

That runs the whole system end to end on generated data: no API key, no data
vendor, no network, no third-party packages.

---

## What it actually does

Thirteen agents, each owning its own files and talking over a permissioned
message bus:

| Agent | Job |
|---|---|
| **Manager** | Owns the task board. Decomposes, routes, gates on dependencies. Performs no market analysis. |
| **Developer** | Backend engineering, and verifies the deterministic core's invariants before anything downstream is trusted. |
| **News & Macro** | Economic calendar as recurrence rules, blackout windows, and a measured news→reaction database. |
| **Research Desk Lead** | Holds no strategy family. Pools the specialists' findings and scores their debate. |
| **Research: Trend** | Trend, pullback, momentum, multi-timeframe. |
| **Research: Reversion** | Mean reversion, reversal, VWAP. |
| **Research: Liquidity** | Liquidity, opening range, breakout. |
| **Analyst A** | Technical and market structure. |
| **Analyst B** | Quantitative and statistical. |
| **Analyst C** | Macro, news and cross-market context. |
| **Decision** | Weighs all evidence → LONG / SHORT / **NO TRADE**. |
| **Risk** | Independent of every prediction agent. Holds an unconditional veto. |
| **Journal** | Records every decision — including the ones not taken — and measures who was actually right. |

Two groups of three, with **opposite** communication rules, and the difference
is the point:

- The three **analysts** are structurally prevented from reading or messaging
  each other. Enforced in code, not by convention, because three agents that can
  see each other's conclusions produce one opinion and two echoes — and the
  decision layer consumes their *independence* as a signal.
- The three **researchers** are required to talk. An edge nobody tried to break
  is an edge nobody has tested.

## The research debate

Each specialist researches its own families, then cross-examines the other two
before anything reaches the live system. Three rules keep it from becoming
theatre:

1. **A challenge without a measurement is discarded.** "I doubt this" costs
   nothing and proves nothing.
2. **A rebuttal without a measurement is downgraded to unanswered.** So
   asserting is strictly worse than conceding, and conceding a well-measured
   challenge is doing the job correctly.
3. **Pooling is deterministic and symmetric.** Relabel the owners and the
   ranking is identical, so the desk lead cannot favour anyone. It holds no
   family, which is precisely why it adjudicates.

The sharpest tool is `adversarial_retest`: a challenger re-runs a rival's
strategy on a split its owner *did not choose*. An edge that only survives the
split its author picked was fitted to that split.

Challenge kinds are graded. `OUT_OF_SAMPLE_FAILURE`, `DATA_MINING` and
`SAMPLE_TOO_SMALL` disqualify a finding outright; `REGIME_ARTEFACT`,
`COST_FRAGILE` and `REDUNDANT` apply penalties, because "worse than claimed" and
"not real" are different findings. Redundant edges are collapsed by connected
component, so one edge found by two specialists counts once rather than reading
as corroboration.

The scorecard records challenges filed, upheld, received and survived — so both
padding (filing unsubstantiated objections) and stonewalling (asserting
rebuttals with no numbers) are visible in the numbers.

---

## The design decisions that matter

**Capital preservation outranks opportunity.** The hierarchy is: prevent account
failure → control drawdown → control per-trade risk → avoid low-quality setups →
find statistically supported opportunities → maximise risk-adjusted returns. In
that order, always.

**Risk is derived from the distance to failure, not from equity.** A $50,000
account with a $5,000 maximum drawdown has $5,000 at stake, not $50,000 — less a
protected reserve that is never spent. That buffer shrinks as the account draws
down, so position size becomes defensive automatically rather than by anyone
remembering to be.

**No trade is a first-class answer.** A `decide` task returning NO TRADE is a
*successful* task. A missed opportunity costs nothing; a low-quality trade costs
capital.

**The decision layer never takes a majority vote.** Analysts are weighted by
their measured accuracy in the *current regime*. Two analysts agreeing lose to
one dissenter with a track record. When no analyst has enough scored predictions,
weights fall back to equal and the callout *says so* rather than inventing
authority it does not have.

**Expectancy is not survival.** Two strategies with identical +0.2R expectancy
were measured at 0.3% and 98.9% probability of failing the account — decided
entirely by trade-distribution shape and position size. That is why sizing is a
separate, deterministic layer with veto power.

---

## Anti-overfitting

The system searches thousands of strategy combinations per symbol, so *some will
look excellent by pure chance*. That is the default outcome of a large search,
not a discovery.

- **Expectancy is deflated by the number of combinations searched.** Search 4,000
  and roughly 4.1 t-units of apparent significance are free. An unadjusted
  "t = 3.2, highly significant" from a large sweep means nothing.
- **Walk-forward efficiency** (out-of-sample ÷ in-sample expectancy) must clear a
  threshold before a strategy is live-eligible.
- **Every generated combination is scored and retained**, including failures —
  ranking over survivors alone is survivorship bias.
- Entries fill at the **next bar's open**; when one bar contains both stop and
  target the **stop wins**; gaps fill at the open; slippage is in the fill price
  and commissions in dollars, charged exactly once.

---

## Honest status

**Verified:** the full pipeline runs end to end with real inter-agent handoffs;
590+ tests pass covering look-ahead, cross-timeframe alignment, entry timing,
cost accounting, risk monotonicity, workspace isolation and DST correctness; the
risk layer approves, sizes and vetoes correctly; the dashboard renders.

**Not verified:** anything visual in the dashboard (there is no browser in the
build environment), and every LLM path — those were exercised against stub
clients, never a live API call.

**The important caveat.** Everything so far has run on **synthetic data**, and on
it the system concludes NO TRADE essentially every time. Measured, on a
150-strategy MNQ sweep with a 6-fold anchored walk-forward:

```
Combined in-sample:      2,493 trades   +0.137R   PF 1.35
Combined out-of-sample:    457 trades   -0.050R   PF 0.89
Walk-forward efficiency: -0.37                    credible: NO
Live-eligible strategies: 0 of 150
```

That is a textbook picture of in-sample optimism, and nothing was tuned to
produce a survivor. One strategy genuinely *did* survive out of sample (+0.145R
over 68 trades, efficiency +0.639) and was still rejected: its t-statistic was
1.58 over 99 trades, while searching 150 combinations buys `sqrt(2·ln 150)` =
3.17 t-units for free, leaving a deflated expectancy of 0.000R. Perturbing its
exit geometry 25% left 14% of the edge — a spike, not a plateau. Two other
finalists failed on probability of account failure: 13.2% and 12.4% at $240 per
trade.

So:

> The machinery is sound, and demonstrably strict. It has not found an edge,
> because it has not been pointed at real market data.

Until it runs against real bars, NO TRADE will stay the near-universal answer.
Point it at real data before drawing any conclusion about whether an edge exists —
and expect the same machinery to reject most of what it finds there too.

---

## Using real data

Drop CSVs into `data/` as `{SYMBOL}_{minutes}m.csv` — the loader accepts column
names from Sierra Chart, NinjaTrader, TradingView, Databento and IQFeed
unmodified. Timestamps without an offset are read as Eastern Time.

```python
from futures_agents.data import CsvProvider
provider = CsvProvider("data", minutes=1)
```

A live feed is added by implementing `DataProvider`'s three methods. True bid/ask
volume is used for delta when the feed supplies it; without it, delta falls back
to a close-location proxy and is flagged `estimated` — a strategy validated on
estimated delta has been validated on a proxy, and the system says so.

---

## Commands

```
demo         end-to-end on synthetic data, no API key needed
research     full cycle: verify, news, strategies, walk-forward, decide
live         live path only: news, analysts, decision, risk, journal
backtest     backtest a symbol's strategy universe
walkforward  walk-forward and robustness analysis
replay       re-run the live cycle at successive past instants
status       account state, staffing, task board
journal      the trading journal
roster       the team roster and workspace tree
dashboard    render the HTML dashboard
```

Global flags: `--no-llm`, `--no-flash`, `--no-color`, `--config`, `--db`,
`--equity`.

Every alert leads with the Eastern Time stamp, and actionable callouts flash —
in the terminal (colour pulse, screen inversion, bell) and in the browser
(colour wash, alternating title, canvas favicon, optional sound behind an
explicit unmute). `prefers-reduced-motion` is respected.

---

## Layout

```
futures_agents/
  team/          roles, private workspaces, message bus, task board, manager
  agents/        the eight domain agents + the Claude client
  data/          bars, look-ahead-safe resampling, providers
  indicators/    52 primitives, none of which can read the future
  strategies/    conditions, confluence combinator, per-symbol registries
  backtest/      engine, costs, metrics, walk-forward, Monte Carlo, robustness
  risk/          account model and the independent risk manager
  features.py    multi-timeframe assembly with explicit alignment lag
  orchestrator.py, cli.py, dashboard.py, storage.py, alerts.py, timeutil.py
docs/AGENT_CONTRACT.md   the contract every agent implements
```

---

## Requirements

Python 3.11+. The deterministic core uses **only the standard library**. The
`anthropic` package is optional and needed only for the LLM reasoning paths;
without it every agent runs its deterministic path and reports that it did.

---

## This is not financial advice

This is research and decision-support software. It does not place orders. Futures
trading involves substantial risk of loss, and backtested performance — however
carefully validated — is not indicative of future results. The anti-overfitting
machinery exists to make the system *less* confident, not more; treat a NO TRADE
as the system working, and treat any signal it does produce as one input to your
own judgement.
