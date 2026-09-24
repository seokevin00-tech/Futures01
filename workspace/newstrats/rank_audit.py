"""Three audits the ranking is worthless without.

**1. The fill model, read directly (D35).** The one bias resampling cannot
catch is one whose sign is always favourable: it is present in every period, so
it passes out-of-sample, disjoint slices and walk-forward alike. The only way to
find it is to open the trades and check the arithmetic. Specifically, the
failure that manufactured a +0.354R cluster at t=5.19 elsewhere in this project
was an entry filling at a bar's own extreme while that same bar's opposite
extreme was credited as a target hit. Here every trade is checked against the
bar it actually filled on.

**2. Costs, priced.** How much of a row's expectancy is the cost model, and does
the ranking survive it being wrong? Reported as cost in R per trade and as the
expectancy the same rows would have had gross.

**3. A power control - and this is the one that decides whether a negative
result means anything.** If a placebo ranks 4th of 264, there are two
explanations: nothing in the cell has an edge, or the ranking cannot tell an
edge from noise at this sample size. They are distinguished by feeding the
machinery a signal that is *guaranteed* to be real - the same entries shifted
five bars BACKWARD, so the strategy trades on information from five bars in its
own future. That is not a placebo, it is a cheat, and it must rank at the very
top. If a deliberate look-ahead cheat cannot beat the placebos, then "placebos
rank as well as strategies" says nothing about the strategies and everything
about the test.
"""

from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import Counter
from typing import Dict, List, Optional

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
os.chdir("/home/user/Futures01")

import placebo as P                                            # noqa: E402
import rank as R                                               # noqa: E402
import toolkit as T                                            # noqa: E402
from futures_agents.backtest.costs import CostModel            # noqa: E402
from futures_agents.backtest.engine import ExitReason, run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics    # noqa: E402
from futures_agents.schema import Direction                    # noqa: E402

PEEK_BARS = 5


def fill_audit(frame, results, strategies) -> dict:
    """Open every trade and check it against the bar it filled on."""
    bars = frame.base.bars
    spec = frame.spec
    out = {
        "trades": 0, "entry_not_next_bar": 0, "entry_slippage_favourable": 0,
        "entry_off_bar_open": 0, "entry_at_bar_extreme": 0,
        "entry_equals_signal_close_coincidentally": 0,
        "target_won_a_bar_the_stop_also_touched": 0,
        "target_on_entry_bar": 0,
        "target_on_entry_bar_with_stop_touched": 0,
        "exit_before_entry": 0, "zero_risk": 0, "zero_cost": 0,
        "max_entry_slip_ticks": 0.0, "cost_r": [],
    }
    for s in strategies:
        for t in results[s.strategy_id].trades:
            out["trades"] += 1
            i = t.entry_index
            if i != t.signal_index + 1:
                out["entry_not_next_bar"] += 1
            bar = bars[i]
            sig_bar = bars[t.signal_index]
            sign0 = t.direction.sign
            # Slippage must always be ADVERSE. A fill better than the bar's own
            # open is free money the backtest invented, and it is the exact
            # shape of a bias resampling cannot see, because its sign never
            # changes: it is present in every period, so every split passes.
            if sign0 * (t.entry_price - bar.open) < -spec.tick_size / 2:
                out["entry_slippage_favourable"] += 1
            # Informational, not a flag. The fill price coinciding with the
            # signal bar's close is not evidence of trading on the close: gold
            # ticks 0.1 on a 4,000 price and the fill is the NEXT bar's open
            # plus 0.5-2 ticks of adverse slippage, so the two land on the same
            # tick a few percent of the time by arithmetic alone. Measured at
            # 107 of 2,602 trades, with entry_slippage_favourable at 0 and
            # max_entry_slip_ticks at 2.0 - exactly the model's ceiling.
            if (abs(t.entry_price - sig_bar.close) < 1e-9
                    and abs(bar.open - sig_bar.close) > 1e-9):
                out["entry_equals_signal_close_coincidentally"] += 1
            slip_ticks = abs(t.entry_price - bar.open) / spec.tick_size
            out["max_entry_slip_ticks"] = max(out["max_entry_slip_ticks"], slip_ticks)
            if slip_ticks > 6:
                out["entry_off_bar_open"] += 1
            # The D35 shape: an entry sitting on the fill bar's own high or low
            # rather than its open. A limit filled at an extreme cannot also be
            # credited with that bar's other extreme.
            if (abs(t.entry_price - bar.high) < 1e-9 or
                    abs(t.entry_price - bar.low) < 1e-9):
                if abs(bar.open - bar.high) > 1e-9 and abs(bar.open - bar.low) > 1e-9:
                    out["entry_at_bar_extreme"] += 1
            if t.exit_index < t.entry_index:
                out["exit_before_entry"] += 1
            if t.risk_points <= 0:
                out["zero_risk"] += 1
            if t.commission_dollars <= 0:
                out["zero_cost"] += 1
            risk_d = t.risk_points * spec.point_value
            if risk_d > 0:
                out["cost_r"].append(t.commission_dollars / risk_d)
            sign = t.direction.sign
            for leg in t.legs:
                if leg.reason is not ExitReason.TARGET:
                    continue
                j = _bar_index_at(bars, leg.ts, t.entry_index, t.exit_index)
                if j is None:
                    continue
                b = bars[j]
                stop_touched = (b.low <= t.initial_stop if sign > 0
                                else b.high >= t.initial_stop)
                if stop_touched:
                    out["target_won_a_bar_the_stop_also_touched"] += 1
                if j == t.entry_index:
                    out["target_on_entry_bar"] += 1
                    if stop_touched:
                        out["target_on_entry_bar_with_stop_touched"] += 1
    c = out.pop("cost_r")
    out["median_cost_r_per_trade"] = round(st.median(c), 4) if c else None
    out["p90_cost_r_per_trade"] = (round(sorted(c)[int(0.9 * len(c))], 4)
                                   if c else None)
    out["max_entry_slip_ticks"] = round(out["max_entry_slip_ticks"], 2)
    out["verdict"] = (
        "clean" if not (out["entry_not_next_bar"]
                        or out["entry_slippage_favourable"]
                        or out["entry_off_bar_open"]
                        or out["target_on_entry_bar_with_stop_touched"]
                        or out["target_won_a_bar_the_stop_also_touched"]
                        or out["exit_before_entry"] or out["zero_risk"]
                        or out["zero_cost"])
        else "SUSPECT - see the non-zero counters")
    return out


def _bar_index_at(bars, ts, lo, hi) -> Optional[int]:
    for j in range(lo, min(hi + 1, len(bars))):
        if bars[j].ts == ts:
            return j
    return None


def power_control(symbol: str, tf: int, window_days: int, *, budget: int = 1200,
                  seed: int = 1, floor: int = R.FLOOR) -> dict:
    """Where does a deliberate look-ahead cheat rank?

    Built exactly like ``placebo_shift`` but displaced **backwards**: the
    strategy enters five bars before the signal it is reacting to, which is to
    say it reads five bars of its own future. It is not a control and it is
    never published as one; it is the calibration that tells you whether a
    ranking in which controls do well is evidence of no edge or evidence of no
    power.
    """
    frame, series = R._frame(symbol, tf, window_days, None)
    if frame is None:
        return {"skipped": "too few bars"}
    reals, popdiag = R._population(symbol, tf, budget=budget, seed=seed,
                                   groups=None, rth_only=False,
                                   drop_session_close_exits=tf >= 240)
    res = run_portfolio(frame, reals)
    realised = {s.strategy_id: len(res[s.strategy_id].trades) for s in reals}
    cleared = [s for s in reals if realised[s.strategy_id] >= floor]
    if len(cleared) < 8:
        return {"skipped": f"only {len(cleared)} cleared the floor"}
    bases = R._stratified(cleared, min(20, len(cleared)), seed, realised)

    sched = P.extract_signals(frame, bases)
    ts_of = [b.ts for b in frame.base.bars]
    n = len(ts_of)
    peeks, peek_of = [], {}
    for s in bases:
        real = sched.get(s.strategy_id, {})
        back = {i - PEEK_BARS: d for i, d in real.items() if 0 <= i - PEEK_BARS < n}
        if len(back) < 2:
            continue
        p = P.make_placebo(s, "peek", back, ts_of)
        peeks.append(p)
        peek_of[p.strategy_id] = s.strategy_id
    res_peek = run_portfolio(frame, peeks)

    rows = []
    for s in reals:
        tr = res[s.strategy_id].trades
        if len(tr) >= floor:
            r = R._row(s, tr)
            r.update(is_placebo=False, placebo_kind=None, base_id=None,
                     base_name=None)
            rows.append(r)
    peek_rows = 0
    for s in peeks:
        tr = res_peek[s.strategy_id].trades
        if len(tr) >= floor:
            r = R._row(s, tr)
            r.update(is_placebo=True, placebo_kind="peek",
                     base_id=peek_of[s.strategy_id], base_name=None)
            rows.append(r)
            peek_rows += 1
    coll, _ = R._collapse(rows)
    order = R._order(coll)
    ranked = [dict(coll[i], rank=pos) for pos, i in enumerate(order, 1)]
    pr = [r["rank"] for r in ranked if r["placebo_kind"] == "peek"]
    peek_exp = [r["exp"] for r in ranked if r["placebo_kind"] == "peek"]
    real_exp = [r["exp"] for r in ranked if not r["is_placebo"]]

    # Paired: each peek against its own base, same exits, same filters, same
    # bars - the only difference is five bars of hindsight.
    base_exp = {r["id"]: r["exp"] for r in ranked if not r["is_placebo"]}
    pairs = [(r["exp"], base_exp[r["base_id"]]) for r in ranked
             if r["placebo_kind"] == "peek" and r["base_id"] in base_exp]
    wins = sum(1 for a, b in pairs if a > b)
    return {
        "symbol": symbol, "tf": tf, "window": window_days,
        "n_reals_run": len(reals), "n_peeks_run": len(peeks),
        "n_rows": len(ranked), "n_peek_rows": peek_rows,
        "best_peek_rank": min(pr) if pr else None,
        "peek_ranks": sorted(pr),
        "null_expected_best_rank": P.null_rank_distribution(
            len(ranked), peek_rows).get("expected_best_rank"),
        "median_exp_peek": round(st.median(peek_exp), 4) if peek_exp else None,
        "median_exp_real": round(st.median(real_exp), 4) if real_exp else None,
        "paired_peek_beats_its_base": f"{wins}/{len(pairs)}",
        "rank_sum_peek_vs_real": (T.mann_whitney_u(peek_exp, real_exp)
                                  if peek_exp and real_exp else {}),
        "reading": ("A cheat that reads 5 bars ahead must dominate. If it does "
                    "not, the cell has no power and a good placebo rank there "
                    "is uninformative rather than damning."),
        "population": popdiag,
    }


def gross_vs_net(symbol: str, tf: int, window_days: int, *, budget: int = 1200,
                 seed: int = 1, floor: int = R.FLOOR) -> dict:
    """How much of the table is the cost model?"""
    frame, series = R._frame(symbol, tf, window_days, None)
    if frame is None:
        return {"skipped": "too few bars"}
    reals, _ = R._population(symbol, tf, budget=budget, seed=seed, groups=None,
                             rth_only=False, drop_session_close_exits=tf >= 240)
    res = run_portfolio(frame, reals)
    rows = [(s, res[s.strategy_id].trades) for s in reals
            if len(res[s.strategy_id].trades) >= floor]
    if not rows:
        return {"skipped": "nothing cleared the floor"}
    net = [compute_metrics(t).expectancy_r for _, t in rows]
    gross = [st.fmean([x.gross_r for x in t]) for _, t in rows]
    return {"symbol": symbol, "tf": tf, "window": window_days,
            "n_rows": len(rows),
            "median_exp_net": round(st.median(net), 4),
            "median_exp_gross": round(st.median(gross), 4),
            "median_cost_r": round(st.median([g - n for g, n in zip(gross, net)]), 4),
            "pct_positive_net": round(sum(1 for x in net if x > 0) / len(net), 3),
            "pct_positive_gross": round(sum(1 for x in gross if x > 0) / len(gross), 3),
            "n_flipped_by_costs": sum(1 for g, n in zip(gross, net) if g > 0 >= n)}


def main(cells=(("MGC", 60, 274), ("MCL", 60, 274),
                ("MGC", 240, 274), ("MCL", 240, 274))) -> dict:
    out = {"fill_audit": {}, "power_control": {}, "gross_vs_net": {}}
    for sym, tf, w in cells:
        frame, series = R._frame(sym, tf, w, None)
        reals, _ = R._population(sym, tf, budget=1200, seed=1, groups=None,
                                 rth_only=False, drop_session_close_exits=tf >= 240)
        res = run_portfolio(frame, reals)
        key = f"{sym}_{tf}_{w}"
        out["fill_audit"][key] = fill_audit(frame, res, reals)
        print("fill", key, out["fill_audit"][key]["verdict"],
              out["fill_audit"][key]["trades"], "trades", flush=True)
        out["gross_vs_net"][key] = gross_vs_net(sym, tf, w)
        print("cost", key, out["gross_vs_net"][key], flush=True)
        out["power_control"][key] = power_control(sym, tf, w)
        print("power", key,
              {k: out["power_control"][key].get(k) for k in
               ("best_peek_rank", "n_rows", "n_peek_rows",
                "null_expected_best_rank", "median_exp_peek", "median_exp_real",
                "paired_peek_beats_its_base")}, flush=True)
    T.save("rank_mgc_mcl_audit",
           "Fill model, costs and statistical power for the MGC/MCL ranking",
           "Is the fill model one-sided, how much of the table is costs, and "
           "can this ranking detect a signal that is definitely there?",
           out,
           "; ".join(f"{k}: fill {v['verdict']}, peek rank "
                     f"{out['power_control'][k].get('best_peek_rank')} of "
                     f"{out['power_control'][k].get('n_rows')}"
                     for k, v in out["fill_audit"].items()),
           ["The peek arm is a deliberate look-ahead cheat used only to "
            "calibrate power. It is never reported as a strategy or a control.",
            "D35: a bias whose sign is always favourable passes every "
            "resampling test, so the fill audit is a direct read of the trades "
            "rather than another split."])
    with open("/home/user/Futures01/workspace/strategy_research/"
              "rank_mgc_mcl_robustness_report.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    return out


if __name__ == "__main__":
    main()
