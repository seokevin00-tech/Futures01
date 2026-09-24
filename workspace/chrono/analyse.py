"""Is there a chronological pattern to WHICH strategy group works?

Three questions, in increasing order of how well the data can answer them:

1. **Persistence** - does the group that won last month win again? Tested on the
   argmax, which throws away almost all the data (one number per month) and is
   therefore the weakest of the three.
2. **Autocorrelation** - does a group's own monthly expectancy predict its next
   month? Uses every month of every group, so it is far better powered.
3. **Cross-lag** - does group A this month predict group B next month? This is
   the chaining hypothesis stated directly, and it is the one the question is
   really about.

The trap is 13 groups: a transition matrix has 169 cells against ~100 months,
and cross-lag has 169 correlations. Both will produce "significant" cells by
chance alone. Every test here is therefore reported against an explicit null
and corrected, and the argmax tests are additionally compared against a
permutation that shuffles the month ORDER - which destroys any chronology while
preserving each group's marginal strength exactly.
"""
import glob
import json
import math
import random
import statistics as st
from collections import Counter, defaultdict

MIN_TRADES = 20          # a month-group cell needs this many trades to count
MIN_MONTHS = 24          # a series needs this many months to be worth testing


def corr(a, b):
    if len(a) < 6:
        return 0.0
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def fisher_z(r, n):
    if n < 6 or abs(r) >= 0.999:
        return 0.0
    return 0.5 * math.log((1 + r) / (1 - r)) * math.sqrt(n - 3)


def load(path):
    d = json.load(open(path))
    months = sorted(d["series"])
    grid = {}                       # group -> {month: mean net_r}
    for m in months:
        for g, v in d["series"][m].items():
            if len(v) >= MIN_TRADES:
                grid.setdefault(g, {})[m] = st.mean(v)
    return d, months, grid


def main():
    for path in sorted(glob.glob("workspace/chrono/ledgers/*.json")):
        d, months, grid = load(path)
        tag = f"{d['symbol']} {d['tf']}m"
        usable = {g: s for g, s in grid.items() if len(s) >= MIN_MONTHS}
        print("=" * 78)
        print(f"{tag}   {d['months']} months  {d['span']}   "
              f"{d['trades']:,} trades   groups with >={MIN_MONTHS} months: {len(usable)}")
        if len(usable) < 3 or len(months) < MIN_MONTHS:
            print("   too short for a chronology test - skipped\n")
            continue

        # ---- 1. persistence of the monthly winner ----------------------
        winners = []
        for m in months:
            cands = {g: s[m] for g, s in usable.items() if m in s}
            if len(cands) >= 3:
                winners.append((m, max(cands, key=cands.get)))
        seq = [g for _, g in winners]
        repeats = sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1])
        # Null: shuffle the ORDER of months. Marginal strength of each group is
        # preserved exactly; only the chronology is destroyed.
        null = []
        for _ in range(2000):
            s2 = seq[:]
            random.shuffle(s2)
            null.append(sum(1 for i in range(1, len(s2)) if s2[i] == s2[i - 1]))
        mu, sd = st.mean(null), (st.pstdev(null) or 1e-9)
        print(f"   winner repeats next month: {repeats}/{len(seq)-1} "
              f"vs {mu:.1f} shuffled   z={(repeats-mu)/sd:+.2f}")
        top = Counter(seq).most_common(4)
        print(f"   most frequent monthly winner: " +
              ", ".join(f"{g} {c}x" for g, c in top))

        # ---- 2. own-group autocorrelation at lag 1 ---------------------
        print("   autocorrelation (does a group's own month predict its next):")
        acs = []
        for g, s in sorted(usable.items()):
            ms = sorted(s)
            pairs = [(s[a], s[b]) for a, b in zip(ms, ms[1:])
                     if _adjacent(a, b)]
            if len(pairs) >= 12:
                r = corr([x for x, _ in pairs], [y for _, y in pairs])
                acs.append((g, r, len(pairs), fisher_z(r, len(pairs))))
        for g, r, n, z in sorted(acs, key=lambda x: -abs(x[1]))[:5]:
            print(f"      {g:<16s} r={r:+.3f}  n={n:>3d}  z={z:+.2f}")

        # ---- 3. cross-lag: does A this month predict B next month? -----
        cross = []
        for ga, sa in usable.items():
            for gb, sb in usable.items():
                if ga == gb:
                    continue
                ms = sorted(set(sa) & set(sb))
                pairs = [(sa[a], sb[b]) for a, b in zip(ms, ms[1:])
                         if _adjacent(a, b)]
                if len(pairs) >= 12:
                    r = corr([x for x, _ in pairs], [y for _, y in pairs])
                    cross.append((ga, gb, r, len(pairs), fisher_z(r, len(pairs))))
        if cross:
            k = len(cross)
            crit = _bonf_z(0.05, k)
            surv = [c for c in cross if abs(c[4]) >= crit]
            print(f"   cross-lag A(t) -> B(t+1): {k} pairs tested, "
                  f"Bonferroni needs |z|>={crit:.2f}, {len(surv)} survive")
            for ga, gb, r, n, z in sorted(cross, key=lambda c: -abs(c[4]))[:5]:
                flag = "  SURVIVES" if abs(z) >= crit else ""
                print(f"      {ga:<15s} -> {gb:<15s} r={r:+.3f} n={n:>3d} "
                      f"z={z:+.2f}{flag}")
        print()


def _adjacent(a, b):
    ya, ma = int(a[:4]), int(a[5:])
    yb, mb = int(b[:4]), int(b[5:])
    return (yb - ya) * 12 + (mb - ma) == 1


def _bonf_z(alpha, k):
    p = alpha / max(1, k) / 2
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if 0.5 * (1 - math.erf(mid / math.sqrt(2))) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


if __name__ == "__main__":
    random.seed(1)
    main()
