"""Does trading the return to an order block / fair value gap pay, after costs?

Matched arms, never ``T.ab``. Defect D28: ``T.ab`` runs an unpaired rank-sum
over strategies, and variants of the same rule sets share 50-90% of their
trades, so the rank-sum treats correlated observations as independent and
inflates |z| by roughly 3.3x. Everything here is *paired*: the same base rule
set, the same cell, the same bars, the same exit, one condition swapped. The
statistic is a per-cell sign test on the paired Delta-expectancy, combined
across cells by Stouffer.

Three arms per base rule set, not two:

* ``base``                 - the control the brief demands
* ``base + ict_X``         - the claim
* ``base + ict_placebo_X`` - sham locations matched to ict_X's own firing rate
  and long/short mix. This is the arm that separates "the order block did
  something" from "adding any condition that fires 6% of the time and halves
  the trade count would have looked like this".

Every arm is run twice, on a 60/40 temporal split, with the zone geometry
computed once on the full series so the out-of-sample half is not cold-started.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T                                    # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.data.bars import BarSeries         # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                 # noqa: E402
from futures_agents.strategies.library import CONDITIONS  # noqa: E402

from workspace.newstrats import ict_blocks as I        # noqa: E402

SCRATCH = ("/tmp/claude-0/-home-user-Futures01/"
           "40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad")

CELLS = [("MGC", 60), ("MES", 60), ("NQ", 60), ("MNQ", 60), ("MCL", 60),
         ("MGC", 15), ("MES", 15), ("MNQ", 15), ("MCL", 15)]

#: Base rule sets, chosen for diversity of evidence and for firing often enough
#: that a 5-13% ICT condition on top still leaves a measurable sample.
BASES = ["structure_trend", "ema_stack", "price_above_ema50", "rsi_directional",
         "macd_directional", "above_vwap", "imbalance_pullback",
         "break_of_structure", "candle_close_strength", "cvd_directional",
         "regime_matches_direction", "bollinger_mean_pull"]

#: The conditions under test, each with its matched sham.
TESTS = [("ict_ob_fresh", "ict_placebo_ob"),
         ("ict_fvg_fresh", "ict_placebo_fvg"),
         ("ict_ob_return", "ict_placebo_ob"),
         ("ict_fvg_return", "ict_placebo_fvg"),
         ("ict_breaker_fresh", "ict_placebo_ob"),
         ("ict_ifvg_fresh", "ict_placebo_fvg"),
         ("ict_ob_newest", "ict_placebo_ob"),
         ("ict_fvg_newest", "ict_placebo_fvg")]

FLOOR = 20


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def sign_test(d: Sequence[float]) -> dict:
    pos = sum(1 for x in d if x > 0); neg = sum(1 for x in d if x < 0)
    n = pos + neg
    if n == 0:
        return {"pos": pos, "neg": neg, "n": 0, "z": 0.0, "p": 1.0}
    z = (pos - n / 2) / math.sqrt(n / 4)
    return {"pos": pos, "neg": neg, "n": n, "z": round(z, 3),
            "p": round(2 * (1 - phi(abs(z))), 5)}


def paired_t(d: Sequence[float]) -> float:
    d = [x for x in d if x == x]
    if len(d) < 3:
        return 0.0
    m = st.mean(d); s = st.pstdev(d) * math.sqrt(len(d) / (len(d) - 1))
    return m / (s / math.sqrt(len(d))) if s else 0.0


def stouffer(z: Sequence[float]) -> float:
    z = [x for x in z if x == x]
    return sum(z) / math.sqrt(len(z)) if z else 0.0


# ------------------------------------------------------------------ running
_FULL: Dict[Tuple[str, int], object] = {}


def full_frame(symbol: str, tf: int):
    key = (symbol.upper(), tf)
    if key not in _FULL:
        series = T._series(symbol, tf, None)
        frame = build_symbol_frame(series, FRAMES[tf])
        I.register_frame(frame, symbol)
        I.register_placebo(symbol, tf)
        _FULL[key] = frame
    return _FULL[key]


def split_frames(symbol: str, tf: int, frac: float = 0.6):
    """IS and OOS frames. Geometry is registered from the FULL series first, so
    the out-of-sample half inherits a warm chart instead of a cold start - and
    it cannot leak, because every zone carries its own confirmed_index."""
    full = full_frame(symbol, tf)
    bars = list(full.base.bars)
    cut = int(len(bars) * frac)
    a = BarSeries(symbol, tf, bars[:cut])
    b = BarSeries(symbol, tf, bars[cut:])
    return build_symbol_frame(a, FRAMES[tf]), build_symbol_frame(b, FRAMES[tf])


def build(symbol: str, tf: int, label: str, names: Sequence[str]):
    return T.make_strategy(symbol, tf, [CONDITIONS[n] for n in names],
                           group="ICT", name=label)


def run(frame, labelled) -> Dict[str, dict]:
    """``labelled`` is [(label, Strategy)]. Distinct labels can share a
    strategy_id - two sham arms are literally the same rule set - so the
    portfolio is deduped before running and the results mapped back."""
    uniq = {}
    for _, s in labelled:
        uniq.setdefault(s.strategy_id, s)
    res = run_portfolio(frame, list(uniq.values()))
    out = {}
    for label, s in labelled:
        m = compute_metrics(res[s.strategy_id].trades)
        out[label] = dict(n=m.trades, exp=round(m.expectancy_r, 4),
                          win=round(m.win_rate, 4), pf=round(m.profit_factor, 4),
                          rr=round(m.payoff_ratio, 4), t=round(m.t_statistic, 3),
                          maxdd=round(m.max_drawdown_r, 3),
                          sortino=round(m.sortino, 3))
    return out


def main():
    plans: List[Tuple[str, List[str]]] = []
    for b in BASES:
        plans.append((f"BASE::{b}", [b]))
        for test, sham in TESTS:
            plans.append((f"{test}::{b}", [b, test]))
            plans.append((f"SHAM_{test}::{b}", [b, sham]))
    solos = [n for n, _ in TESTS] + ["ict_placebo_ob", "ict_placebo_fvg"]
    for n in solos:
        plans.append((f"SOLO::{n}", [n]))

    out = {"cells": {}, "bases": BASES, "tests": [t for t, _ in TESTS],
           "n_strategies_per_cell": len(plans)}
    for sym, tf in CELLS:
        isf, oosf = split_frames(sym, tf)
        labelled = [(label, build(sym, tf, label, names)) for label, names in plans]
        row = {"IS": run(isf, labelled), "OOS": run(oosf, labelled)}
        out["cells"][f"{sym}-{tf}m"] = row
        print(f"{sym}-{tf}m  IS trades(base med)="
              f"{st.median([row['IS'][f'BASE::{b}']['n'] for b in BASES]):.0f} "
              f"OOS={st.median([row['OOS'][f'BASE::{b}']['n'] for b in BASES]):.0f}")
        sys.stdout.flush()

    # ---------------- paired analysis
    analysis = {}
    for test, sham in TESTS:
        rec = {}
        for period in ("IS", "OOS"):
            per_cell = {}
            for cell, row in out["cells"].items():
                r = row[period]
                d_base, d_sham, ns = [], [], []
                for b in BASES:
                    A = r.get(f"{test}::{b}"); B = r.get(f"BASE::{b}")
                    C = r.get(f"SHAM_{test}::{b}")
                    if not A or not B or A["n"] < FLOOR or B["n"] < FLOOR:
                        continue
                    d_base.append(A["exp"] - B["exp"])
                    ns.append(A["n"])
                    if C and C["n"] >= FLOOR:
                        d_sham.append(A["exp"] - C["exp"])
                if d_base:
                    per_cell[cell] = dict(
                        n_pairs=len(d_base),
                        med_delta_vs_base=round(st.median(d_base), 4),
                        mean_delta_vs_base=round(st.mean(d_base), 4),
                        t_vs_base=round(paired_t(d_base), 3),
                        n_pairs_sham=len(d_sham),
                        med_delta_vs_sham=(round(st.median(d_sham), 4)
                                           if d_sham else None),
                        t_vs_sham=round(paired_t(d_sham), 3) if d_sham else None,
                        med_trades=round(st.median(ns), 1))
            meds = [v["med_delta_vs_base"] for v in per_cell.values()]
            ts = [v["t_vs_base"] for v in per_cell.values()]
            meds_s = [v["med_delta_vs_sham"] for v in per_cell.values()
                      if v["med_delta_vs_sham"] is not None]
            ts_s = [v["t_vs_sham"] for v in per_cell.values()
                    if v["t_vs_sham"] is not None]
            rec[period] = dict(
                per_cell=per_cell, n_cells=len(per_cell),
                sign_vs_base=sign_test(meds), stouffer_vs_base=round(stouffer(ts), 3),
                sign_vs_sham=sign_test(meds_s), stouffer_vs_sham=round(stouffer(ts_s), 3),
                grand_med_vs_base=round(st.median(meds), 4) if meds else None,
                grand_med_vs_sham=round(st.median(meds_s), 4) if meds_s else None)
        analysis[test] = rec
        i, o = rec["IS"], rec["OOS"]
        print(f"{test:20s} IS  dExp={i['grand_med_vs_base']} sign={i['sign_vs_base']} "
              f"stouffer={i['stouffer_vs_base']} | vs SHAM {i['grand_med_vs_sham']} "
              f"sign={i['sign_vs_sham']} st={i['stouffer_vs_sham']}")
        print(f"{'':20s} OOS dExp={o['grand_med_vs_base']} sign={o['sign_vs_base']} "
              f"stouffer={o['stouffer_vs_base']} | vs SHAM {o['grand_med_vs_sham']} "
              f"sign={o['sign_vs_sham']} st={o['stouffer_vs_sham']}")
        sys.stdout.flush()
    out["paired"] = analysis
    with open(f"{SCRATCH}/ict_arms.json", "w") as fh:
        json.dump(out, fh, indent=1)
    return out


if __name__ == "__main__":
    main()
