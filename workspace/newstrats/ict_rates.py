"""Firing rates and bar-level Jaccard overlap - before any performance number.

Two jobs:

1.  Every ICT condition's firing rate per symbol/timeframe.  Under 1% cannot
    support a strategy; over 95% is not a condition.
2.  Whether ``ote_zone`` (0.62-0.79) is a new condition at all, or whether it
    is ``fib_golden_pocket`` (0.618-0.786) with a different label.  Jaccard on
    the set of bars each fires on, plus a direction-aware Jaccard, because two
    conditions that fire on the same bars pointing opposite ways are not the
    same condition.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")

import ict_time as K                                   # noqa: E402
import toolkit as T                                    # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                # noqa: E402
from futures_agents.strategies.library import CONDITIONS as LIB  # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research/scratch/ict"
os.makedirs(OUT, exist_ok=True)

LIB_NAMES = ["fib_golden_pocket", "fib_shallow_retrace", "fib_extension_reached",
             "fib_sr_confluence", "avoid_lunch", "opening_drive_window",
             "power_hour", "after_opening_range", "pullback_to_support",
             "opening_range_breakout", "break_of_structure"]
OWN_NAMES = ["ote_zone", "ote_strict", "ote_0705", "gp_mirror", "shallow_mirror",
             "kz_london_open", "kz_ny_open", "kz_silver_bullet", "kz_london_close",
             "kz_asian_range", "kz_union", "kz_outside_all", "avoid_1500_1600"]


def fire_map(symbol: str, tf: int):
    """{condition_name: {bar_index: direction_value}} over the whole series."""
    series = T._series(symbol, tf, None)
    frame = build_symbol_frame(series, FRAMES[tf])
    conds = {n: LIB[n] for n in LIB_NAMES if n in LIB}
    conds.update({n: K.CONDITIONS[n] for n in OWN_NAMES})
    fired = {n: {} for n in conds}
    n_bars = 0
    for i in range(len(frame.base)):
        snap = frame.snapshot(i)
        if snap is None:
            continue
        n_bars += 1
        for n, c in conds.items():
            try:
                r = c.fn(snap, tf)
            except Exception:
                continue
            if r is not None and r.triggered:
                fired[n][i] = r.direction.value
    return fired, n_bars


def jaccard(a: dict, b: dict, *, directional: bool = False):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return None
    inter = sa & sb
    if directional:
        inter = {i for i in inter if a[i] == b[i]}
    return round(len(inter) / len(sa | sb), 4)


PAIRS = [("ote_zone", "fib_golden_pocket"), ("ote_zone", "gp_mirror"),
         ("gp_mirror", "fib_golden_pocket"), ("ote_strict", "fib_golden_pocket"),
         ("ote_0705", "fib_golden_pocket"), ("ote_zone", "fib_shallow_retrace"),
         ("fib_golden_pocket", "fib_shallow_retrace"),
         ("fib_golden_pocket", "fib_sr_confluence"),
         ("kz_union", "after_opening_range"), ("kz_silver_bullet", "avoid_lunch"),
         ("kz_ny_open", "opening_drive_window"), ("kz_london_close", "power_hour")]


def run(symbols, tfs):
    rep = {}
    for symbol in symbols:
        for tf in tfs:
            try:
                fired, n = fire_map(symbol, tf)
            except Exception as e:                       # noqa: BLE001
                rep[f"{symbol}_{tf}"] = {"error": repr(e)}
                continue
            cell = {"n_bars": n,
                    "firing_rate": {k: round(len(v) / n, 4) for k, v in fired.items()},
                    "n_fired": {k: len(v) for k, v in fired.items()},
                    "jaccard": {}, "jaccard_directional": {}}
            for a, b in PAIRS:
                if a in fired and b in fired:
                    cell["jaccard"][f"{a}|{b}"] = jaccard(fired[a], fired[b])
                    cell["jaccard_directional"][f"{a}|{b}"] = jaccard(
                        fired[a], fired[b], directional=True)
            rep[f"{symbol}_{tf}"] = cell
            print(symbol, tf, "bars", n,
                  "ote", cell["firing_rate"].get("ote_zone"),
                  "gp", cell["firing_rate"].get("fib_golden_pocket"),
                  "J", cell["jaccard"].get("ote_zone|fib_golden_pocket"), flush=True)
    return rep


if __name__ == "__main__":
    rep = run(["MNQ", "MES", "MGC", "MCL"], [60, 240])
    with open(f"{OUT}/firing_rates.json", "w") as fh:
        json.dump(rep, fh, indent=1)
    print("written", f"{OUT}/firing_rates.json")
