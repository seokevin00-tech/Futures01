# Does strategy-group efficacy rotate chronologically?

**Question:** is there a sequence — MGC works on VWAP, then TREND, then MOMENTUM, then VWAP
again — and is that sequence unique per symbol?

**Answer: no detectable chaining. The one suggestive pattern is on MGC and it does not survive
correction.**

## Method

Monthly buckets, built from the **daily** series because that is the only one long enough: MGC
117 months (2016-09 → 2026-09, 56,670 trades), MES and MNQ 89 months each (2019-05 →, 34k and
39k trades). The hourly files give 11–12 months, which cannot support a transition matrix over
thirteen groups, and are reported as skipped rather than squeezed.

Every trade from every generated strategy counts once. **No floor on strategies and no ranking**,
because selecting before measuring would bake in the very persistence the test is looking for. A
month-group cell needs ≥20 trades; a series needs ≥24 months.

Three tests, in increasing order of statistical power:

1. **Persistence of the monthly winner** — weakest, since the argmax discards all but one number
   per month. Null is a **shuffle of the month order**, which destroys chronology while preserving
   each group's marginal strength exactly.
2. **Own-group autocorrelation** — does a group's month predict its own next month?
3. **Cross-lag** — does group A this month predict group B next month? **This is the chaining
   hypothesis stated directly**, and it is the best-powered of the three.

## Results

### Persistence of the winner: nothing

| symbol | winner repeats | shuffled null | z |
|---|---|---|---|
| MGC daily | 20 / 95 | 18.2 | **+0.49** |
| MNQ daily | 18 / 60 | 14.7 | **+1.00** |
| MES daily | 17 / 56 | 18.3 | **−0.37** |

The group that won last month wins again at almost exactly the rate a shuffled calendar produces.

Which groups win most often is symbol-specific, but only mildly: MGC MULTI_TIMEFRAME 29× / VWAP
20× / MOMENTUM 18×; MNQ MULTI_TIMEFRAME 18× / MOMENTUM 16× / VWAP 16×; MES VWAP 22× /
MOMENTUM 18× / MULTI_TIMEFRAME 17×. **The same three groups top all three symbols** — that is
shared structure, not a per-symbol signature.

### Own-group autocorrelation: weak, positive, uncorrected

The strongest are all on MGC — TREND r=+0.383 (n=25, z=+1.89), MULTI_TIMEFRAME r=+0.249 (z=+1.87),
VWAP r=+0.165 on 106 months (z=+1.69). None reaches |z|=1.96 individually, and there are 18 of
them across the three symbols, so **zero survive any correction**. MES and MNQ are flat to
negative (MES MOMENTUM −0.001, MNQ VWAP −0.045).

### Cross-lag chaining: the direct test, and it fails

| symbol | pairs tested | Bonferroni threshold | surviving |
|---|---|---|---|
| MGC daily | 20 | \|z\| ≥ 3.02 | **0** |
| MNQ daily | 12 | \|z\| ≥ 2.87 | **0** |
| MES daily | 6 | \|z\| ≥ 2.64 | **0** |

The largest effects, all on MGC and all short of their threshold:

```
VWAP(t)            -> TREND(t+1)      r=+0.528  n= 25  z=+2.75   (needs 3.02)
VWAP(t)            -> MOMENTUM(t+1)   r=+0.240  n= 97  z=+2.37
MULTI_TIMEFRAME(t) -> TREND(t+1)      r=+0.495  n= 20  z=+2.24
MULTI_TIMEFRAME(t) -> MOMENTUM(t+1)   r=+0.270  n= 55  z=+2.00
```

**This is the closest the hypothesis comes to being true**, and it is on the independent contract
with the longest history — which is where a real effect would show first. But note what it is *not*:

- It is **not a rotation**. All four leading effects are **positive** — a good VWAP month predicts a
  good TREND month, not a handover from one to the next. That is co-movement, groups rising and
  falling together, which is what you would expect if an underlying regime drives everything.
- The two largest rest on **n=25 and n=20 months**.
- They were selected by scanning 20 pairs; 20 tests at α=0.05 produce one |z|>1.96 by chance.
- MES and MNQ show **nothing at all**, and their largest cross-lags flip sign (MNQ MOMENTUM→VWAP
  −0.111, MES MULTI_TIMEFRAME→MOMENTUM −0.090).

## Conclusion

There is no evidence of a chronological chain, and no per-symbol signature. What weak structure
exists is **positive co-movement on MGC** — groups tending to be good or bad in the same months —
rather than a handover sequence.

Worth stating plainly: even had a chain been found, it would describe what already happened. Acting
on it requires knowing which group leads *before* the month it leads in, and that is a strictly
harder test than this one.
