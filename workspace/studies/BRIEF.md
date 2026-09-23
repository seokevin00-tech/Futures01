# Study brief — read this before you start

You are one of twenty agents each studying a different slice of the same strategy
library. Your findings will be merged into one report, and five developers will then
try to build an algorithm from it. **A wrong finding is worse than no finding**: it
costs five developers' work and it is the kind of error that reaches a real $50,000
account. Prefer "no detectable difference" to a claim you cannot support.

## Use the toolkit — do not reinvent it

```python
import sys; sys.path.insert(0, 'workspace/studies')
import toolkit as T
```

- `T.measure(symbol, tf, window_days, budget=4000)` → one dict per surviving strategy,
  carrying `conditions`, `signals`, `filters`, `group`, exit fields and full metrics.
  Clones (identical realised trades) are already collapsed. Floor is 20 trades.
- `T.ab(rows, predicate, "with", "without")` → **matched comparison**, the workhorse.
  Both arms come from the same generated population over the same bars, so they share
  symbol, timeframe, window, costs and search size. Returns both arms' medians, a
  rank-sum test and a verdict.
- `T.scan_rows()` → 8,521 already-measured strategies from the completed scans. Free to
  query, no compute. Use it for breadth; use `measure` when you need a slice the scans
  did not record.
- `T.free_t(n_screened)`, `T.wilson(wins, n)`, `T.corr(a, b)`, `T.mann_whitney_u(a, b)`.
- `T.save(study_id, title, question, payload, headline, caveats)` → writes your findings.

## Rules that make twenty studies comparable

1. **Every comparative claim goes through `T.ab`.** A league table of the best things
   containing X tells you what the search found, not whether X did anything. If you
   find yourself sorting and taking the top 10, stop and write a predicate instead.
2. **Never call a difference real on medians alone.** The rank-sum `z` decides. `|z| <
   1.96` means say "no detectable difference" — that is a finding, not a failure.
3. **Report sample size in both arms.** A split of 200 vs 3 is not a comparison.
4. **Deflation.** At these search sizes roughly 4 t-units are free. If you quote a `t`,
   quote `T.free_t(screened)` beside it. No strategy in any scan so far has cleared it;
   do not announce one without showing the arithmetic.
5. **Win rate needs an interval.** `T.wilson`. 75% on 20 trades is compatible with 53%.
6. **Replication beats magnitude.** A +0.05R edge holding across three windows and three
   symbols is worth far more than a +0.80R edge in one cell. Prefer breadth.

## Minimum coverage

Run your analysis across at least these cells:

    symbols    MGC, MES, NQ        (add MNQ, MCL if your question needs more)
    timeframes 240 and 60          (add 15 only if your question is about fast setups)
    windows    274, 180, 90        (these three are the ones all timeframes support)

That is 18 `T.measure` calls at minimum, roughly 20–60 seconds each.

## What to save

Call `T.save` exactly once with:
- `payload`: your numbers, as a dict. Include every `T.ab` result in full.
- `headline`: one sentence a developer could act on, or "nothing actionable found".
- `caveats`: what would make your finding wrong. Be specific.

## What to reply with

Under 25 lines: your headline, your two or three strongest numbers with both arm sizes
and the rank-sum z, and anything you found that contradicts an assumption in this brief.
Do not paste tables or JSON — the aggregator reads your saved file.
