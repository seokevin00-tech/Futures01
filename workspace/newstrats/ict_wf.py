"""Walk-forward on the one thing a user of this idea would actually tune: the timing tolerance.

There is no fitted parameter inside a single arm, so a naive walk-forward would be theatre.
The realistic failure mode is that someone sweeps ``max_gap``, sees a winner, and trades it.
So that is what is walked forward: on each fold, pick the ``max_gap`` that maximised expectancy
on everything seen so far, and score that choice on the NEXT fold only.  Three benchmarks:

* ``selected``  - what the honest tuner gets;
* ``fixed5``    - never tuning at all;
* ``oracle``    - the best gap chosen with hindsight inside the test fold.  The gap between
  ``oracle`` and ``selected`` is the size of the data-mining bias being paid.

A placebo arm is run alongside: the identical entries displaced by +1 and +5 bars.  If the
real arm cannot beat its own displaced copy, whatever it is reading is not timing-specific;
if it hugely beats a +1-bar displacement, that is a look-ahead signature rather than an edge.
"""
import json, math, os, statistics as st, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I
from ict_perf import EXITS, row
from futures_agents.backtest.engine import run_portfolio
from futures_agents.backtest.metrics import compute_metrics
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES

ALL8 = ("PDH", "PDL", "ONH", "ONL", "SESH", "SESL", "SWH", "SWL")
GAPS = (1, 2, 3, 5, 8, 12, 20)
NFOLD = 6


def shift_map(built, k):
    """Displace every full-chain entry k bars later - a placebo with the same trade count."""
    out = []
    for c in built.chains:
        if c.entry_i is not None and c.entry_i + k < built.n_bars:
            out.append((c.entry_i + k, c.side))
    return out


res, wf = {}, {}
for sym in ['MES', 'MNQ', 'MGC', 'MCL']:
    for tf in [60, 240]:
        series = T._series(sym, tf, None)
        frame = build_symbol_frame(series, FRAMES[tf])
        n = len(series.bars)
        I.clear()
        strats, meta = [], {}
        for g in GAPS:
            b = I.build(sym, tf, series.bars, I.Cfg(max_gap=g, pools=ALL8))
            I.register_variant(b, f"@g{g}")
            s = T.make_strategy(sym, tf, [I.get(f"full@g{g}")], group="ICT",
                                name=f"full@g{g}", exit_model=EXITS["anchored"])
            strats.append(s); meta[s.strategy_id] = f"g{g}"
        # placebos, built off the g5 chain
        b5 = I.build(sym, tf, series.bars, I.Cfg(max_gap=5, pools=ALL8))
        ts = [x.ts for x in series.bars]
        for k in (1, 5):
            key = (sym.upper(), tf, f"placebo{k}")
            I._MAPS[key] = {}
            for i, side in shift_map(b5, k):
                I._MAPS[key].setdefault(ts[i], side)
            if f"placebo{k}" not in I.CONDITIONS:
                I._make(f"placebo{k}", f"full-chain entries displaced {k} bars")
            s = T.make_strategy(sym, tf, [I.get(f"placebo{k}")], group="ICT",
                                name=f"placebo{k}", exit_model=EXITS["anchored"])
            strats.append(s); meta[s.strategy_id] = f"placebo{k}"

        edge = (n - 250) // NFOLD
        folds = [(250 + k * edge, 250 + (k + 1) * edge) for k in range(NFOLD)]
        per = {}
        for fi, (s0, s1) in enumerate(folds):
            r = run_portfolio(frame, strats, start=s0, end=s1)
            for sid, lab in meta.items():
                m = compute_metrics(r[sid].trades)
                per[(lab, fi)] = (m.trades, round(m.expectancy_r, 4), round(m.total_r, 3))
        res[f"{sym}|{tf}"] = {f"{k[0]}|{k[1]}": v for k, v in per.items()}

        sel, fix, orc = [], [], []
        for fi in range(1, NFOLD):
            hist = {g: sum(per[(f"g{g}", j)][2] for j in range(fi)) for g in GAPS}
            best = max(GAPS, key=lambda g: hist[g])
            test = {g: per[(f"g{g}", fi)] for g in GAPS}
            sel.append(test[best][1]); fix.append(test[5][1])
            orc.append(max(test[g][1] for g in GAPS if test[g][0] >= 3) if any(
                test[g][0] >= 3 for g in GAPS) else 0.0)
        wf[f"{sym}|{tf}"] = {
            "selected_exp_by_fold": sel, "fixed5_exp_by_fold": fix,
            "oracle_exp_by_fold": orc,
            "mean_selected": round(st.mean(sel), 4), "mean_fixed5": round(st.mean(fix), 4),
            "mean_oracle": round(st.mean(orc), 4),
            "datamining_bias_R": round(st.mean(orc) - st.mean(sel), 4),
            "placebo1": [per[("placebo1", f)][1] for f in range(NFOLD)],
            "placebo5": [per[("placebo5", f)][1] for f in range(NFOLD)]}
        print(sym, tf, wf[f"{sym}|{tf}"]["mean_selected"], wf[f"{sym}|{tf}"]["mean_fixed5"],
              wf[f"{sym}|{tf}"]["mean_oracle"], flush=True)

json.dump({"folds": res, "wf": wf},
          open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/wf.json', 'w'), indent=1, default=str)

print(f"\n{'cell':10s}{'selected':>10s}{'fixed5':>9s}{'oracle':>9s}{'bias':>8s}{'plcb1':>9s}{'plcb5':>9s}")
for k, v in sorted(wf.items()):
    print(f"{k:10s}{v['mean_selected']:>+10.3f}{v['mean_fixed5']:>+9.3f}{v['mean_oracle']:>+9.3f}"
          f"{v['datamining_bias_R']:>8.3f}{st.mean(v['placebo1']):>+9.3f}{st.mean(v['placebo5']):>+9.3f}")
sel = [v['mean_selected'] for v in wf.values()]; fix = [v['mean_fixed5'] for v in wf.values()]
orc = [v['mean_oracle'] for v in wf.values()]
p1 = [st.mean(v['placebo1']) for v in wf.values()]; p5 = [st.mean(v['placebo5']) for v in wf.values()]
print(f"\nmedian across 8 cells  selected {st.median(sel):+.4f}  fixed5 {st.median(fix):+.4f} "
      f" oracle {st.median(orc):+.4f}  placebo+1 {st.median(p1):+.4f}  placebo+5 {st.median(p5):+.4f}")
