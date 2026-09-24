# Scan reports

One dated, self-contained markdown report per strategy scan.

Each report is written to be readable months later without this session's context, and to
be re-runnable: the command that produced it is in the header, alongside the commit the
code was at. Row-level results live in `workspace/` and are gitignored as run artefacts —
the reports carry the conclusions, the summary tables and the cell inventory needed to
judge them.

## Naming

```
YYYY-MM-DD_<symbols>_<kind>.md
```

## What every report must state

A scan can be made to say almost anything by choosing what to leave out, so these four are
not optional:

1. **How many strategies were screened.** Without it, a top row cannot be judged, because
   the free-search threshold depends entirely on the search size.
2. **The deflation verdict.** Searching *n* strategies buys roughly `sqrt(2·ln n)` free
   t-units. A report that ranks without saying so invites the reader to trade the top row.
3. **Sample size on every row, and a confidence interval on any win rate.** A 75% win rate
   on 20 trades is statistically compatible with 53%.
4. **What the data could not support.** Window labels the span cannot carry, timeframes
   with too little history, symbols that were excluded and why.

## Reading the tables

Both standard rankings mislead when read alone, in opposite directions:

- **Highest win rate** promotes strategies that are right often and earn nothing — high
  hit rate paired with a payoff ratio below 1. Always read the expectancy column beside it.
- **Highest reward:risk** promotes lottery tickets — a 6:1 payoff at a 4% win rate is a
  losing strategy with a beautiful ratio. Always read the win rate column beside it.

Expectancy in R is the column that decides whether a strategy makes money. The other two
describe its shape.

## Index

| date | scope | report |
|---|---|---|
| 2026-09-23 | MGC, MES, NQ — 39 cells, 554k strategies | [deep scan](2026-09-23_MGC-MES-NQ_deep-scan.md) |
| 2026-09-24 | 22 studies, 24 agents, 912 matched comparisons | [study programme](2026-09-24_strategy-studies_21-study-programme.md) |
| 2026-09-24 | ORB and ICT — 6 studies, 5 placebo controls | [ORB and ICT](2026-09-24_ORB-and-ICT.md) |
