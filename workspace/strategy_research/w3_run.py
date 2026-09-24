"""Driver: one symbol's eight cells (60m/240m x 30/90/180/274d) plus replication."""
import json
import os
import sys
import time

sys.path.insert(0, "/home/user/Futures01")
os.chdir("/home/user/Futures01")

import workspace.strategy_research.w3_rank as W  # noqa: E402
import workspace.studies.toolkit as T  # noqa: E402

SYM = sys.argv[1]
WINDOWS = [30, 90, 180, 274]
TFS = [60, 240]

cells = {}
for tf in TFS:
    for w in WINDOWS:
        t0 = time.time()
        try:
            c = W.run_cell(SYM, tf, w)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            c = dict(symbol=SYM, tf=tf, window=w, skipped=f"{type(exc).__name__}: {exc}")
        W.save_cell(c)
        cells[f"{tf}_{w}"] = c
        print(f"[{SYM}] {tf}m {w}d done in {time.time()-t0:.0f}s", flush=True)

# ---- disjoint-slice replication of the 274d (9-month) top rows ----
rep = {}
for tf in TFS:
    c = cells.get(f"{tf}_274", {})
    if c.get("skipped"):
        continue
    ids = list(dict.fromkeys(c.get("top25_ids", [])[:10] + c.get("soft_top25_ids", [])[:10]))
    if not ids:
        continue
    t0 = time.time()
    try:
        rep[str(tf)] = W.replicate(SYM, tf, ids)
        rep[str(tf)]["ranked_ids_f20"] = c.get("top25_ids", [])[:10]
        rep[str(tf)]["ranked_ids_f10"] = c.get("soft_top25_ids", [])[:10]
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        rep[str(tf)] = {"error": f"{type(exc).__name__}: {exc}"}
    print(f"[{SYM}] replication {tf}m done in {time.time()-t0:.0f}s", flush=True)

os.makedirs(W.OUTDIR, exist_ok=True)
with open(f"{W.OUTDIR}/replication_{SYM}.json", "w") as fh:
    json.dump(rep, fh, indent=1, default=str)
T.save(f"rank_nq_es_grains_{SYM}_replication",
       f"{SYM} disjoint-slice replication of the 9-month top 10",
       "Which of the 274-day top 10 are positive in all three NON-OVERLAPPING thirds?",
       rep,
       "The only column in this report with evidential weight.",
       caveats=W.CAVEATS + [
           "The thirds are disjoint from EACH OTHER but not from the 274-day ranking "
           "window: for NQ/ES (319d of data) two of three thirds sit entirely inside "
           "the 274d window; for the grains (420-456d) the oldest third is entirely "
           "outside it and is the only genuinely out-of-sample slice."])
print(f"[{SYM}] ALL DONE", flush=True)
