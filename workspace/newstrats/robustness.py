"""Anti-overfitting checks for the geometry conditions."""
from __future__ import annotations

import json
import random
import statistics as st
import sys

sys.path.insert(0, "workspace/studies")
sys.path.insert(0, "workspace/newstrats")

import geometry as G   # noqa: E402
import toolkit as T    # noqa: E402


def repaint_check(symbol, tf, n_probes=120, seed=7):
    """Does the geometry visible at bar i change when later bars arrive?

    The definitive test for repainting and future-data leakage: rebuild the
    whole state from a series TRUNCATED at bar i and compare it with the state
    recorded against bar i when the full series was processed. Any difference
    is information from the future reaching bar i.
    """
    bars = list(T._series(symbol, tf, None).bars)
    full = G._zigzag_states(bars)
    rnd = random.Random(seed)
    idx = sorted(rnd.sample(range(200, len(bars)), min(n_probes, len(bars) - 200)))
    bad, checked = [], 0
    for i in idx:
        trunc = G._zigzag_states(bars[: i + 1])
        a, b = full[bars[i].ts], trunc[bars[i].ts]
        checked += 1
        if a != b:
            bad.append({"i": i, "ts": str(bars[i].ts),
                        "full_legs": len(a.legs), "trunc_legs": len(b.legs)})
    return {"symbol": symbol, "tf": tf, "probes": checked,
            "mismatches": len(bad), "examples": bad[:3]}


def confirmation_lag_cost(symbol, tf):
    """What the confirmation filter is worth, in firing-rate and timing terms.

    Rebuilds the same zigzag using each swing's FORMATION index instead of its
    ``confirmed_index`` - the classic look-ahead bug - and measures how much
    earlier the geometry would have been 'known'.
    """
    bars = list(T._series(symbol, tf, None).bars)
    from futures_agents.indicators.structure import find_swings
    sw = find_swings(bars)
    lag = [s.confirmed_index - s.index for s in sw]
    # how many bars of a typical impulse leg the lag consumes
    honest = G._zigzag_states(bars)
    leg_bars = [l.bars for g in honest.values() for l in g.legs]
    return {"symbol": symbol, "tf": tf, "swings": len(sw),
            "confirmation_lag_bars": {"min": min(lag), "max": max(lag),
                                      "mean": round(st.fmean(lag), 3)},
            "median_leg_length_bars": st.median(leg_bars) if leg_bars else None,
            "lag_as_pct_of_median_leg": (
                round(100 * st.fmean(lag) / st.median(leg_bars), 1)
                if leg_bars and st.median(leg_bars) else None)}


if __name__ == "__main__":
    out = {"repaint": [], "confirmation_lag": []}
    for s in ["MGC", "MES", "MNQ", "MCL"]:
        for tf in [240, 60]:
            r = repaint_check(s, tf)
            out["repaint"].append(r)
            print("repaint", s, tf, r["probes"], "probes", r["mismatches"],
                  "mismatches", flush=True)
            c = confirmation_lag_cost(s, tf)
            out["confirmation_lag"].append(c)
            print("   lag", c["confirmation_lag_bars"], "median leg",
                  c["median_leg_length_bars"], "bars ->",
                  c["lag_as_pct_of_median_leg"], "% of a leg", flush=True)
    json.dump(out, open("workspace/strategy_research/scratch/robust1.json", "w"),
              default=str)
