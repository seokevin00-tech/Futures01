"""Worker 2 driver. One process per timeframe; saves each cell the moment it finishes."""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/strategy_research/w2")

import toolkit as T  # noqa: E402
import w2rank as W  # noqa: E402

SYMS = ("MES", "MNQ")
WINDOWS = (30, 90, 180, 274)
BUDGET = 8000


def thin(cell: dict, keep: int = 40) -> dict:
    """What goes into the saved study: the ranking head, the census, the
    placebo position and the paired test - not 1,200 full rows."""
    out = {k: v for k, v in cell.items() if k not in ("rows", "rows_floorfree")}
    out["top"] = cell["rows"][:keep]
    out["top_floorfree"] = cell["rows_floorfree"][:keep]
    out["n_rows_floored"] = len(cell["rows"])
    out["n_rows_floorfree"] = len(cell["rows_floorfree"])
    out["clears_free_t"] = [
        dict(id=r["id"], arm=r["arm"], group=r["group"], n=r["n"], exp=r["exp"], t=r["t"])
        for r in cell["rows"] if r["t"] >= cell["free_t"]]
    out["clears_free_t_floorfree"] = sum(
        1 for r in cell["rows_floorfree"] if r["t"] >= cell["free_t"])
    return out


def do(tf: int) -> None:
    for window in WINDOWS:
        t0 = time.time()
        cells = {}
        for sym in SYMS:
            c = W.run_cell(sym, tf, window=window, budget=BUDGET)
            W.save_cell(c, f"{sym}_{tf}_{window}")
            cells[sym] = thin(c)
            print(f"[{tf}m/{window}d] {sym} rows={len(c['rows'])} "
                  f"placebo_rank={c['placebo'].get('best_placebo_rank')} "
                  f"of {c['placebo'].get('best_placebo_of')} "
                  f"({round(time.time()-t0)}s)", flush=True)
        T.save(f"rank_mes_mnq_{tf}_{window}",
               f"Recurring profitability ranking, MES+MNQ, {tf}m, {window}d",
               "Top 10 durable strategies over the trailing window, ranked "
               "alongside a placebo cohort drawn from the same machinery.",
               cells,
               headline=_headline(cells, tf, window),
               caveats=[
                   "MES and MNQ are the same index complex; agreement between "
                   "them is NOT independent evidence, and neither is agreement "
                   "with worker 3's NQ/ES. MNQ and NQ in csv/raw are the same "
                   "price series from two fetch snapshots.",
                   "The 30/90/180/274d windows are nested: a strategy in all "
                   "four top-10 lists is one observation seen four times.",
                   "rth_only forced OFF on every generated strategy (D24). "
                   "Non-RTH fills are charged thin-book slippage by the engine.",
                   "240m: the session-scale guard (390-minute threshold, D21) "
                   "never engages, so time-of-day conditions misbehave there; "
                   "no time-of-day condition is used as a headline result.",
                   "Clones collapsed on realised trades across the combined "
                   "real+placebo population before counting.",
                   "Placebo rule sets realise MORE trades per rule set than "
                   "their hosts (real multi-condition signals arrive in "
                   "clusters and the engine blocks re-entry while positioned), "
                   "so the placebo cohort clears the 20-trade floor at a higher "
                   "rate than the real one. See paired.median_trade_count_ratio.",
               ])
        print(f"[{tf}m/{window}d] saved ({round(time.time()-t0)}s)", flush=True)


def _headline(cells: dict, tf: int, window: int) -> str:
    bits = []
    for sym, c in cells.items():
        p = c.get("placebo", {})
        bits.append(f"{sym}: best placebo ranked {p.get('best_placebo_rank')}/"
                    f"{p.get('best_placebo_of')} ({p.get('n_placebo_in_top10')} in top 10)")
    return f"{tf}m/{window}d - " + "; ".join(bits)


def do_slices(tf: int) -> None:
    for sym in SYMS:
        sl = T.disjoint_slices(sym, tf, 3)
        for k, (a, b) in enumerate(sl, 1):
            t0 = time.time()
            c = W.run_cell(sym, tf, slice_days=(a, b), budget=BUDGET)
            if c.get("skipped"):
                print(f"[{tf}m/slice{k}] {sym} SKIPPED {c}", flush=True)
                continue
            W.save_cell(c, f"{sym}_{tf}_slice{k}")
            print(f"[{tf}m/slice{k}] {sym} ({a},{b}) rows={len(c['rows'])} "
                  f"placebo_rank={c['placebo'].get('best_placebo_rank')} "
                  f"({round(time.time()-t0)}s)", flush=True)


def do_slices_rth(tf: int) -> None:
    """The RTH-scoped arm carried into the three disjoint thirds. Run only
    because the RTH arm produced the single cell in which the real cohort beat
    the placebo cohort - a post-hoc find that has to be tested, not believed."""
    for sym in SYMS:
        for k, (a, b) in enumerate(T.disjoint_slices(sym, tf, 3), 1):
            c = W.run_cell(sym, tf, slice_days=(a, b), budget=BUDGET, rth=True)
            if c.get("skipped"):
                continue
            W.save_cell(c, f"{sym}_{tf}_slice{k}_rth")
            print(f"[{tf}m/slice{k} RTH] {sym} rows={len(c['rows'])} "
                  f"placebo_rank={c['placebo'].get('best_placebo_rank')}/"
                  f"{c['placebo'].get('best_placebo_of')}", flush=True)


def do_rth(tf: int) -> None:
    """Paired RTH arm at the 274d window: same rule sets, scope on vs off."""
    for sym in SYMS:
        c = W.run_cell(sym, tf, window=274, budget=BUDGET, rth=True)
        W.save_cell(c, f"{sym}_{tf}_274_rth")
        print(f"[{tf}m/274d RTH] {sym} rows={len(c['rows'])} "
              f"census={json.dumps(c['census']['real'])}", flush=True)


if __name__ == "__main__":
    what = sys.argv[1]
    tf = int(sys.argv[2])
    {"win": do, "slices": do_slices, "rth": do_rth,
     "slices_rth": do_slices_rth}[what](tf)
