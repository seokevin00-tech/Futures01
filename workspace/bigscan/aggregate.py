"""Combine every scanned cell into the two tables that were asked for.

Both tables are dangerous on their own, and the danger is not symmetric with
how convincing they look:

* **Highest win rate** is the table most likely to be topped by a strategy
  that loses money. On this desk's own data the correlation between win rate
  and expectancy is +0.037. So every win-rate row carries expectancy, payoff
  ratio, and a 95% Wilson interval - because a 100% win rate on 16 trades has
  a lower bound near 80%, and on 4 trades a lower bound near 40%.

* **Highest payoff ratio** is topped by lottery tickets: a strategy risking 1
  to make 6 that wins one trade in eight is a losing strategy with a beautiful
  ratio. So every payoff row carries win rate and expectancy.

A row is only ever *interesting*. Whether it is *real* is the deflation
column: at the search sizes here, three to four t-units of apparent edge are
free, and a row below that threshold is indistinguishable from noise.
"""
import json
import os
from collections import defaultdict

CELLS = os.environ.get("SCAN_OUT", "workspace/bigscan/cells")
ROWS_OUT = os.environ.get("SCAN_ROWS", "workspace/bigscan/all_rows.json")
TF_NAME = {1440: "1d", 240: "4h", 60: "1h", 30: "30m", 15: "15m", 5: "5m"}


def load():
    out = []
    for f in sorted(os.listdir(CELLS)):
        if not f.endswith(".json"):
            continue
        d = json.load(open(os.path.join(CELLS, f)))
        if d.get("skipped") or not d.get("rows"):
            continue
        out.append(d)
    return out


def bucket(window: int) -> str:
    """Map a real window length onto the label the question asked for."""
    if window >= 250:
        return "9mo"
    if window >= 150:
        return "6mo"
    if window >= 80:
        return "3mo"
    if window >= 50:
        return "~2mo"
    return "1mo"


def enrich(cells):
    """Flatten to rows, each carrying its cell's context and noise threshold."""
    rows = []
    for c in cells:
        for r in c["rows"]:
            r = dict(r)
            r.update(symbol=c["symbol"], tf=c["tf"], window=c["window"],
                     bucket=bucket(c["window"]), free_t=c["free_t"],
                     span=c["window_span"], screened=c["screened"],
                     survives=r["t"] > c["free_t"])
            rows.append(r)
    return rows


def fmt_row(r, mode):
    tf = TF_NAME.get(r["tf"], f"{r['tf']}m")
    flag = "YES" if r["survives"] else "no"
    if mode == "win":
        return (f"| {r['symbol']:<4s} | {tf:>4s} | {r['bucket']:>4s} | {r['group']:<15s} "
                f"| {r['n']:>4d} | {r['win']*100:>5.1f}% | {r['win_lo']*100:>4.0f}-{r['win_hi']*100:<4.0f} "
                f"| {r['exp']:>+7.3f} | {r['rr']:>5.2f} | {r['t']:>5.2f} | {flag:>3s} |")
    return (f"| {r['symbol']:<4s} | {tf:>4s} | {r['bucket']:>4s} | {r['group']:<15s} "
            f"| {r['n']:>4d} | {r['rr']:>6.2f} | {r['win']*100:>5.1f}% "
            f"| {r['exp']:>+7.3f} | {r['maxdd']:>5.1f} | {r['t']:>5.2f} | {flag:>3s} |")


def table(rows, mode, key, n=20, min_trades=20):
    elig = [r for r in rows if r["n"] >= min_trades]
    elig.sort(key=key, reverse=True)
    head_win = ("| sym  |   tf | win  | group           |    n |   win | 95% CI    "
                "|     exp |   R:R |     t | real|")
    head_rr = ("| sym  |   tf | win  | group           |    n |    R:R |   win "
               "|     exp | maxDD |     t | real|")
    sep = "|" + "|".join("-" * w for w in ([6, 6, 6, 17, 6, 7, 11, 9, 7, 7, 5]
                                           if mode == "win" else
                                           [6, 6, 6, 17, 6, 8, 7, 9, 7, 7, 5])) + "|"
    L = [head_win if mode == "win" else head_rr, sep]
    L += [fmt_row(r, mode) for r in elig[:n]]
    return "\n".join(L), len(elig)


def main():
    cells = load()
    rows = enrich(cells)
    print(f"cells with results: {len(cells)}   strategy rows: {len(rows):,}")
    print(f"total strategies screened: {sum(c['screened'] for c in cells):,}")
    print(f"survive deflation: {sum(1 for r in rows if r['survives']):,} of {len(rows):,}")
    print()

    print("#" * 100)
    print("TABLE 1 - HIGHEST WIN RATE  (min 20 trades)")
    print("#" * 100)
    t, n = table(rows, "win", lambda r: r["win"])
    print(t)
    print(f"({n:,} rows eligible)")
    print()

    print("#" * 100)
    print("TABLE 2 - HIGHEST REWARD:RISK  (min 20 trades)")
    print("#" * 100)
    t, n = table(rows, "rr", lambda r: r["rr"])
    print(t)
    print(f"({n:,} rows eligible)")
    print()

    # ---- the grid: best of each metric per timeframe x window -----------
    print("#" * 100)
    print("BEST PER TIMEFRAME x WINDOW  (min 20 trades)")
    print("#" * 100)
    grid = defaultdict(list)
    for r in rows:
        if r["n"] >= 20:
            grid[(r["tf"], r["bucket"])].append(r)
    order = ["9mo", "6mo", "3mo", "~2mo", "1mo"]
    print(f"| {'tf':>4s} | {'win':>4s} | {'best win rate':<34s} | {'best R:R':<34s} |")
    print("|" + "-" * 6 + "|" + "-" * 6 + "|" + "-" * 36 + "|" + "-" * 36 + "|")
    for tf in sorted(grid and {k[0] for k in grid}, reverse=True):
        for b in order:
            v = grid.get((tf, b))
            if not v:
                continue
            bw = max(v, key=lambda r: r["win"])
            br = max(v, key=lambda r: r["rr"])
            sw = f"{bw['symbol']} {bw['group'][:11]} {bw['win']*100:.0f}% n={bw['n']} e={bw['exp']:+.2f}"
            sr = f"{br['symbol']} {br['group'][:11]} {br['rr']:.1f}:1 w={br['win']*100:.0f}% e={br['exp']:+.2f}"
            print(f"| {TF_NAME.get(tf, str(tf)):>4s} | {b:>4s} | {sw:<34s} | {sr:<34s} |")
    print()

    # ---- does win rate predict money? ----------------------------------
    import statistics as st
    elig = [r for r in rows if r["n"] >= 20]
    if len(elig) > 10:
        w = [r["win"] for r in elig]
        e = [r["exp"] for r in elig]
        rr = [r["rr"] for r in elig]
        def corr(a, b):
            ma, mb = st.mean(a), st.mean(b)
            num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
            return num / den if den else 0.0
        print("#" * 100)
        print("DOES EITHER TABLE PREDICT PROFIT?")
        print("#" * 100)
        print(f"  correlation(win rate, expectancy)   = {corr(w, e):+.3f}")
        print(f"  correlation(payoff ratio, expectancy) = {corr(rr, e):+.3f}")
        hi = [r for r in elig if r["win"] >= 0.60]
        lo = [r for r in elig if r["win"] <= 0.45]
        if hi:
            print(f"  of {len(hi):,} strategies with win rate >= 60%: "
                  f"{sum(1 for r in hi if r['exp'] > 0) / len(hi):.1%} are profitable")
        if lo:
            print(f"  of {len(lo):,} strategies with win rate <= 45%: "
                  f"{sum(1 for r in lo if r['exp'] > 0) / len(lo):.1%} are profitable")
        hr = [r for r in elig if r["rr"] >= 3.0]
        if hr:
            print(f"  of {len(hr):,} strategies with payoff >= 3:1: "
                  f"{sum(1 for r in hr if r['exp'] > 0) / len(hr):.1%} are profitable")

    json.dump(rows, open(ROWS_OUT, "w"))
    print(f"\nall {len(rows):,} rows written to {ROWS_OUT}")


if __name__ == "__main__":
    main()
