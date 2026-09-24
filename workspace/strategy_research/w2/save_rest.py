"""Save the non-window cells as studies: disjoint slices, RTH arm, robustness."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/strategy_research/w2")

import toolkit as T  # noqa: E402
from publish import load, thin_like  # noqa: E402

CAV = [
    "MES and MNQ are the same index complex. Agreement between them is NOT "
    "independent evidence, and neither is agreement with worker 3's NQ/ES - "
    "MNQ and NQ in csv/raw are the same price series from two fetch snapshots. "
    "The only independent contracts in this study are worker 1's MGC and MCL.",
    "rth_only forced OFF on the main arm (D24); a paired RTH arm is reported "
    "beside it. Non-RTH fills are charged thin-book slippage by the engine.",
    "At 240m the session-scale guard never engages (D21, threshold 390 minutes) "
    "so time-of-day conditions misbehave there; no 240m headline result rests "
    "on one.",
    "Placebo entry schedules are drawn inside the window they are built in, so "
    "a placebo cannot be carried into a disjoint slice as the SAME schedule - "
    "only as the same host+flavour recipe re-randomised there.",
]


def main():
    for tf in (60, 240):
        cells = {}
        for sym in ("MES", "MNQ"):
            for k in (1, 2, 3):
                c = load(f"{sym}_{tf}_slice{k}")
                if c:
                    cells[f"{sym}_slice{k}"] = thin_like(c)
                c2 = load(f"{sym}_{tf}_slice{k}_rth")
                if c2:
                    cells[f"{sym}_slice{k}_rth"] = thin_like(c2)
        if cells:
            bits = "; ".join(
                f"{k}: placebo {v['placebo'].get('best_placebo_rank')}/"
                f"{v['placebo'].get('best_placebo_of')}" for k, v in cells.items())
            T.save(f"rank_mes_mnq_{tf}_disjoint_slices",
                   f"Disjoint-slice replication cells, MES+MNQ, {tf}m",
                   "Three non-overlapping thirds of the data, each ranked from "
                   "scratch with its own placebo cohort. The only column in the "
                   "ranking report with evidential weight.",
                   cells, headline=f"{tf}m disjoint thirds - " + bits, caveats=CAV)
        rc = {}
        for sym in ("MES", "MNQ"):
            c = load(f"{sym}_{tf}_274_rth")
            if c:
                rc[sym] = thin_like(c)
        if rc:
            T.save(f"rank_mes_mnq_{tf}_274_rth",
                   f"RTH-scoped arm, MES+MNQ, {tf}m, 274d",
                   "The same rule sets with rth_only=True, as the paired control "
                   "for D24 and because StrategyFilters.label() omits rth_only "
                   "from the identity hash, so the two scopes must be run apart.",
                   rc, headline=f"{tf}m RTH arm", caveats=CAV)

    rob = json.load(open("/home/user/Futures01/workspace/strategy_research/"
                         "rank_w2_robustness_report.json"))
    keep = {k: v for k, v in rob.items()
            if k not in ("holdout_and_walk_forward",)}
    keep["walk_forward_summaries"] = {
        k: {kk: vv for kk, vv in v.items() if kk != "walk_forward"}
        for k, v in rob["holdout_and_walk_forward"].items()}
    T.save("rank_mes_mnq_robustness",
           "Worker 2 robustness: placebo ranks, replication, walk-forward, audits",
           "Is anything in the MES/MNQ 60m/240m rankings distinguishable from a "
           "cohort with the entry signal destroyed, and does selection survive "
           "out of sample?",
           keep,
           headline=("Best placebo ranked 1st in 12 of 16 window cells; the "
                     "null-adjusted test never favours the real cohort except "
                     "in the post-hoc MNQ 60m RTH arm; 0 rows clear free_t in "
                     "any cell; selection beat its own fold base rate in 8 of "
                     "24 walk-forward folds."),
           caveats=CAV)
    print("saved")


if __name__ == "__main__":
    main()
