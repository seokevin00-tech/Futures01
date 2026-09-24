"""Worker 3 audits: fill model, dollar arithmetic, costs, look-ahead, OOS, sensitivity.

Everything here is a direct check on the machinery, not a resampling of results.
D35's lesson is that resampling cannot see a bias whose sign is always
favourable; only reading the fill model can.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import Counter

sys.path.insert(0, "/home/user/Futures01")
os.chdir("/home/user/Futures01")

import workspace.strategy_research.w3_rank as W  # noqa: E402
import workspace.studies.toolkit as T  # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.config import get_contract  # noqa: E402
from futures_agents.data.bars import BarSeries  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402
from futures_agents.strategies.combinator import generate_strategies  # noqa: E402

GRAINS = ["MZC", "MZS", "MZW"]
INDEX = ["NQ", "ES"]
ALL = GRAINS + INDEX


def _cell_strats(sym, tf, budget=W.BUDGET, seed=1):
    return [s for s in generate_strategies(
        sym, FRAMES[tf], groups=T.ALL_GROUPS, max_total=99999,
        max_per_template=budget, seed=seed) if s.primary_tf == tf]


# ------------------------------------------------------------ 1. dollar audit
def dollar_audit(syms=ALL, tf=60, window=274, budget=600):
    out = {}
    for sym in syms:
        spec = get_contract(sym)
        series = T._series(sym, tf, window)
        frame = build_symbol_frame(series, FRAMES[tf])
        strats = _cell_strats(sym, tf, budget=budget)
        res = run_portfolio(frame, strats)
        trades = [t for r in res.values() for t in r.trades]
        bad_dollars = bad_r = bad_tick = bad_entry = 0
        risk_usd, pnl_usd, examples = [], [], []
        for t in trades:
            sgn = 1 if t.direction.value == "LONG" else -1
            leg_pts = sum(sgn * (l.price - t.entry_price) * l.fraction for l in t.legs)
            exp_net = leg_pts * spec.point_value - t.commission_dollars
            if abs(exp_net - t.net_dollars) > 0.01 + 1e-6 * abs(exp_net):
                bad_dollars += 1
            r_usd = t.risk_points * spec.point_value
            if r_usd > 0 and abs(t.net_r - t.net_dollars / r_usd) > 1e-3:
                bad_r += 1
            if abs(round(t.entry_price / spec.tick_size) * spec.tick_size
                   - t.entry_price) > 1e-9:
                bad_tick += 1
            if t.entry_index <= t.signal_index:
                bad_entry += 1
            risk_usd.append(r_usd)
            pnl_usd.append(t.net_dollars)
            if len(examples) < 2:
                examples.append(dict(
                    entry=t.entry_price, exit=t.exit_price, dir=t.direction.value,
                    risk_pts=round(t.risk_points, 4), risk_usd=round(r_usd, 2),
                    legs=[(round(l.price, 4), l.fraction, l.reason.value) for l in t.legs],
                    leg_points=round(leg_pts, 4), point_value=spec.point_value,
                    expected_net_usd=round(exp_net, 2), engine_net_usd=round(t.net_dollars, 2),
                    net_r=round(t.net_r, 4)))
        out[sym] = dict(
            tick_size=spec.tick_size, point_value=spec.point_value,
            tick_value=round(spec.tick_size * spec.point_value, 6),
            round_turn_fees=round(2 * (spec.commission_per_side + spec.exchange_fee_per_side), 2),
            n_trades=len(trades),
            dollar_mismatches=bad_dollars, r_mismatches=bad_r,
            offtick_entries=bad_tick, entry_not_after_signal=bad_entry,
            median_risk_usd=round(st.median(risk_usd), 2) if risk_usd else None,
            p05_risk_usd=round(sorted(risk_usd)[len(risk_usd) // 20], 2) if risk_usd else None,
            p95_risk_usd=round(sorted(risk_usd)[int(len(risk_usd) * 0.95)], 2) if risk_usd else None,
            median_abs_pnl_usd=round(st.median([abs(x) for x in pnl_usd]), 2) if pnl_usd else None,
            fees_as_pct_of_median_risk=round(
                100 * 2 * (spec.commission_per_side + spec.exchange_fee_per_side)
                / st.median(risk_usd), 2) if risk_usd else None,
            examples=examples)
        print(f"  ${sym}: n={len(trades)} dollar_mismatch={bad_dollars} "
              f"r_mismatch={bad_r} offtick={bad_tick} median_risk=${out[sym]['median_risk_usd']} "
              f"fees={out[sym]['fees_as_pct_of_median_risk']}% of R", flush=True)
    return out


# ------------------------------------------------ 2. understated exit slippage
def exit_slippage_stress(syms=ALL, tfs=(60, 240), window=274, budget=600,
                         extra_ticks=(0.5, 1.0, 2.0)):
    """Engine charges slippage on entry and stop exits only.

    Target, TIME and SESSION_CLOSE legs fill exactly at the level / close. Charge
    them ``extra_ticks`` of adverse slippage per leg analytically and see what
    survives.
    """
    out = {}
    for sym in syms:
        spec = get_contract(sym)
        for tf in tfs:
            series = T._series(sym, tf, window)
            if len(series) < 120:
                continue
            frame = build_symbol_frame(series, FRAMES[tf])
            res = run_portfolio(frame, _cell_strats(sym, tf, budget=budget))
            rows = []
            for sid, r in res.items():
                tr = [t for t in r.trades]
                if len(tr) < 20:
                    continue
                base = compute_metrics(tr).expectancy_r
                hair = {}
                for x in extra_ticks:
                    adj = []
                    for t in tr:
                        legs = sum(1 for l in t.legs
                                   if l.reason.value not in ("STOP", "BREAKEVEN"))
                        adj.append(t.net_r - legs * x * spec.tick_size / t.risk_points
                                   if t.risk_points else t.net_r)
                    hair[str(x)] = round(sum(adj) / len(adj), 4)
                rows.append((sid, round(base, 4), hair, len(tr)))
            if not rows:
                continue
            pos0 = sum(1 for _, b, _, _ in rows if b > 0)
            out[f"{sym}_{tf}"] = dict(
                n_rows=len(rows), pct_pos_base=round(pos0 / len(rows), 3),
                pct_pos_after={x: round(sum(1 for _, _, h, _ in rows if h[x] > 0)
                                        / len(rows), 3) for x in map(str, extra_ticks)},
                median_exp_base=round(st.median([b for _, b, _, _ in rows]), 4),
                median_exp_after={x: round(st.median([h[x] for _, _, h, _ in rows]), 4)
                                  for x in map(str, extra_ticks)},
                best_exp_base=round(max(b for _, b, _, _ in rows), 4),
                best_exp_after={x: round(max(h[x] for _, _, h, _ in rows), 4)
                                for x in map(str, extra_ticks)})
            print(f"  slip {sym} {tf}m: {out[f'{sym}_{tf}']['median_exp_base']} -> "
                  f"{out[f'{sym}_{tf}']['median_exp_after']}", flush=True)
    return out


# ------------------------------------------------------- 3. prefix stability
def prefix_stability(syms=ALL, tfs=(60, 240), window=274, budget=600, frac=0.7):
    """Truncate the series at ``frac`` and re-run.

    Any trade that both opened and closed before the cut must be bit-identical.
    A difference means a condition read data from after its own bar - look-ahead
    or a repainting indicator. This also catches the D35 family at the signal
    layer (it does not catch it at the fill layer, which is why the fill model is
    read directly above).
    """
    out = {}
    for sym in syms:
        for tf in tfs:
            full = T._series(sym, tf, window)
            if len(full) < 120:
                continue
            cut = int(len(full.bars) * frac)
            cut_ts = full.bars[cut - 1].ts
            strats = _cell_strats(sym, tf, budget=budget)
            a = run_portfolio(build_symbol_frame(full, FRAMES[tf]), strats)
            b = run_portfolio(build_symbol_frame(
                BarSeries(sym, tf, full.bars[:cut]), FRAMES[tf]), strats)
            comp = diff = 0
            for sid in a:
                ta = [t for t in a[sid].trades if t.exit_ts and t.exit_ts < cut_ts]
                tb = [t for t in b[sid].trades if t.exit_ts and t.exit_ts < cut_ts]
                ka = [(t.entry_ts, t.direction.value, round(t.entry_price, 6),
                       round(t.net_r, 5)) for t in ta]
                kb = [(t.entry_ts, t.direction.value, round(t.entry_price, 6),
                       round(t.net_r, 5)) for t in tb]
                comp += len(ka)
                diff += sum(1 for x, y in zip(ka, kb) if x != y) + abs(len(ka) - len(kb))
            out[f"{sym}_{tf}"] = dict(trades_compared=comp, mismatches=diff,
                                      cut_ts=str(cut_ts))
            print(f"  prefix {sym} {tf}m: {comp} trades compared, {diff} mismatches",
                  flush=True)
    return out


# ------------------------------------------------------- 4. 60/40 OOS split
def oos_split(syms=ALL, tfs=(60, 240), window=274, budget=W.BUDGET, frac=0.6,
              floor_is=12, top=10):
    """Rank on the first 60% of the 274d window, measure the winners on the last 40%."""
    out = {}
    for sym in syms:
        for tf in tfs:
            full = T._series(sym, tf, window)
            if len(full) < 200:
                continue
            cut = int(len(full.bars) * frac)
            strats = _cell_strats(sym, tf, budget=budget)
            fis = build_symbol_frame(BarSeries(sym, tf, full.bars[:cut]), FRAMES[tf])
            fos = build_symbol_frame(BarSeries(sym, tf, full.bars[cut:]), FRAMES[tf])
            ris = run_portfolio(fis, strats)
            seen, cand = set(), []
            for s in strats:
                tr = ris[s.strategy_id].trades
                if len(tr) < floor_is:
                    continue
                fp = T.fingerprint(tr)
                if fp in seen:
                    continue
                seen.add(fp)
                m = compute_metrics(tr)
                cand.append((s, m.expectancy_r, m.trades, m.t_statistic))
            cand.sort(key=lambda x: -x[1])
            win = cand[:top]
            ros = run_portfolio(fos, [w[0] for w in win]) if win else {}
            rows = []
            for s, e, n, t in win:
                m = compute_metrics(ros[s.strategy_id].trades) if ros else None
                rows.append(dict(id=s.strategy_id, group=s.group, name=s.name,
                                 is_exp=round(e, 4), is_n=n, is_t=round(t, 3),
                                 oos_exp=round(m.expectancy_r, 4) if m else None,
                                 oos_n=m.trades if m else 0,
                                 oos_t=round(m.t_statistic, 3) if m else None))
            oe = [r["oos_exp"] for r in rows if r["oos_n"] >= 5]
            med_pop = st.median([c[1] for c in cand]) if cand else None
            out[f"{sym}_{tf}"] = dict(
                is_bars=cut, oos_bars=len(full.bars) - cut,
                population=len(cand), median_is_exp=round(med_pop, 4) if med_pop else None,
                top=rows, n_oos_evaluable=len(oe),
                median_oos_exp=round(st.median(oe), 4) if oe else None,
                pct_oos_positive=round(sum(1 for x in oe if x > 0) / len(oe), 3) if oe else None,
                mean_is_minus_oos=round(
                    st.mean([r["is_exp"] for r in rows if r["oos_n"] >= 5])
                    - st.mean(oe), 4) if oe else None)
            print(f"  oos {sym} {tf}m: pop={len(cand)} IS med={out[f'{sym}_{tf}']['median_is_exp']} "
                  f"top10 OOS med={out[f'{sym}_{tf}']['median_oos_exp']} "
                  f"pos={out[f'{sym}_{tf}']['pct_oos_positive']}", flush=True)
    return out


# ------------------------------------------------- 5. seed / search sensitivity
def seed_sensitivity(syms=ALL, tfs=(60, 240), window=274, budget=W.BUDGET,
                     seeds=(1, 2, 3), floor=20, top=10):
    """Regenerate the population under different seeds; does the top 10 recur?"""
    out = {}
    for sym in syms:
        for tf in tfs:
            series = T._series(sym, tf, window)
            if len(series) < 120:
                continue
            frame = build_symbol_frame(series, FRAMES[tf])
            tops, sigs, exps = [], [], []
            for sd in seeds:
                strats = _cell_strats(sym, tf, budget=budget, seed=sd)
                res = run_portfolio(frame, strats)
                rows = W.collapse([(s, res[s.strategy_id].trades, "real", None)
                                   for s in strats], floor)
                rows.sort(key=lambda r: (-r["exp"], -r["t"]))
                tops.append([r["id"] for r in rows[:top]])
                sigs.append(Counter(x for r in rows[:top] for x in r["signals"]))
                exps.append([r["exp"] for r in rows[:top]])
            inter = set(tops[0])
            for t in tops[1:]:
                inter &= set(t)
            uni = set().union(*[set(t) for t in tops])
            common_sigs = set(sigs[0])
            for c in sigs[1:]:
                common_sigs &= set(c)
            out[f"{sym}_{tf}"] = dict(
                seeds=list(seeds), top_n=top,
                id_overlap_all_seeds=len(inter), id_union=len(uni),
                jaccard=round(len(inter) / max(1, len(uni)), 3),
                best_exp_per_seed=[round(e[0], 4) if e else None for e in exps],
                signals_in_all_seeds=sorted(common_sigs)[:20],
                n_signals_in_all_seeds=len(common_sigs))
            print(f"  seed {sym} {tf}m: top{top} id overlap {len(inter)}/{len(uni)} "
                  f"best_exp {out[f'{sym}_{tf}']['best_exp_per_seed']}", flush=True)
    return out


# ------------------------------------------------- 6. D21 clock conditions @240m
CLOCK = ("opening_drive", "power_hour", "lunch", "session", "time_of_day", "killzone",
         "kill_zone", "overnight", "prior_day", "opening_range", "first_hour",
         "close_window", "minutes_since")


def clock_audit(cells_dir=W.OUTDIR):
    out = {}
    for f in sorted(os.listdir(cells_dir)):
        if not f.startswith("rank_nq_es_grains_") or "replication" in f:
            continue
        c = json.load(open(os.path.join(cells_dir, f)))
        if c.get("skipped"):
            continue
        for tag in ("primary", "secondary"):
            rows = c.get(tag, {}).get("rows", [])[:10]
            hits = [r["name"] for r in rows
                    if any(k in x for r2 in [r] for x in
                           (r2["signals"] + r2["filters"]) for k in CLOCK)]
            out[f"{c['symbol']}_{c['tf']}_{c['window']}_{tag}"] = dict(
                top10=len(rows), with_clock_condition=len(hits),
                names=hits[:5],
                note=("240m: D21 means the session-scale guard never fires here"
                      if c["tf"] == 240 else ""))
    return out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    syms = sys.argv[2:] or ALL
    tag = which + ("" if syms == ALL else "_" + "_".join(syms))
    payload = {}
    if which in ("all", "dollar"):
        print("== dollar audit ==", flush=True)
        payload["dollar_audit"] = dollar_audit(syms=syms)
    if which in ("all", "slip"):
        print("== exit slippage stress ==", flush=True)
        payload["exit_slippage_stress"] = exit_slippage_stress(syms=syms)
    if which in ("all", "prefix"):
        print("== prefix stability ==", flush=True)
        payload["prefix_stability"] = prefix_stability(syms=syms)
    if which in ("all", "oos"):
        print("== 60/40 out-of-sample ==", flush=True)
        payload["oos_split"] = oos_split(syms=syms)
    if which in ("all", "seed"):
        print("== seed sensitivity ==", flush=True)
        payload["seed_sensitivity"] = seed_sensitivity(syms=syms)
    if which in ("all", "clock"):
        payload["clock_audit"] = clock_audit()
    os.makedirs(W.OUTDIR, exist_ok=True)
    with open(f"{W.OUTDIR}/audit_{tag}.json", "w") as fh:
        json.dump(payload, fh, indent=1, default=str)
    print("saved", f"{W.OUTDIR}/audit_{tag}.json")
