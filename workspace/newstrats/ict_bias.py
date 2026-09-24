"""Bias audit: look-ahead, repainting, cost understatement, fill realism.

Two of these are actually run rather than asserted.

**Repaint test.**  Rebuild the whole detector on truncated prefixes of the series and compare
the entry set it produces against the entry set produced on the full series.  A detector that
uses future information - a swing read at its formation index, an FVG marked before its third
bar, a "most recent swing" that drifts as later swings confirm - will disagree.  Entries within
``max_gap + retrace_window + swing_right + 2`` bars of the truncation point are excluded,
because those chains were genuinely still in flight at the cut.

**Cost stress.**  Re-run at 2x and 4x the slippage model.  D13 records that scale-out legs are
charged neither commission nor slippage, so the default run already understates costs on a
two-target exit; this bounds how much that matters.
"""
import json, os, statistics as st, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I
from ict_perf import EXITS, row
from futures_agents.backtest.costs import CostModel, SlippageModel
from futures_agents.backtest.engine import run_portfolio
from futures_agents.config import get_contract
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES

CFG = I.Cfg()
MARGIN = CFG.max_gap + CFG.retrace_window + CFG.swing_right + 2

# ---- repaint -------------------------------------------------------------
rep = {}
for sym in ['MES', 'MGC', 'MCL', 'MNQ']:
    for tf in [60, 240]:
        bars = list(T._series(sym, tf, None).bars)
        n = len(bars)
        full = I.build(sym, tf, bars, CFG)
        ref = {(c.entry_i, c.side.value) for c in full.chains if c.entry_i is not None}
        bad = tot = 0
        for frac in (0.55, 0.65, 0.75, 0.85, 0.95):
            k = int(n * frac)
            pre = I.build(sym, tf, bars[:k], CFG)
            a = {(c.entry_i, c.side.value) for c in pre.chains
                 if c.entry_i is not None and c.entry_i <= k - MARGIN}
            b = {e for e in ref if e[0] <= k - MARGIN}
            bad += len(a ^ b)
            tot += len(b)
        rep[f"{sym}|{tf}"] = {"mismatched": bad, "compared": tot,
                              "mismatch_rate": round(bad / tot, 6) if tot else None}
        print("repaint", sym, tf, rep[f"{sym}|{tf}"], flush=True)

# ---- cost stress ---------------------------------------------------------
cost = {}
for sym in ['MES', 'MGC', 'MCL', 'MNQ']:
    for tf in [60, 240]:
        series = T._series(sym, tf, None)
        frame = build_symbol_frame(series, FRAMES[tf])
        n = len(series.bars)
        I.clear()
        I.register(I.build(sym, tf, series.bars, CFG))
        strats = [T.make_strategy(sym, tf, [I.get(a)], group="ICT", name=a,
                                  exit_model=EXITS["anchored"])
                  for a in ("full", "sweep_mss", "mss_only")]
        spec = get_contract(sym)
        models = {"x1": None,
                  "x2": CostModel(spec, SlippageModel(1.0, 2.0, 1.2, 2.0, 4.0)),
                  "x4": CostModel(spec, SlippageModel(2.0, 4.0, 2.4, 4.0, 8.0)),
                  "zero": CostModel(spec, SlippageModel(0, 0, 0, 0, 0),
                                    commission_override=0.0)}
        for lab, cm in models.items():
            res = run_portfolio(frame, strats, cost_model=cm, start=250, end=n)
            for s in strats:
                r = row(res[s.strategy_id].trades)
                r.pop('rs')
                cost[f"{sym}|{tf}|{s.name}|{lab}"] = r
        print("cost", sym, tf, {lab: cost[f'{sym}|{tf}|full|{lab}']['exp'] for lab in models},
              flush=True)

json.dump({"repaint": rep, "cost": cost},
          open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/bias.json', 'w'), indent=1, default=str)
for a in ("full", "sweep_mss", "mss_only"):
    print(f"\n{a}: median expectancy across 8 cells by cost level")
    for lab in ("zero", "x1", "x2", "x4"):
        v = [cost[k]['exp'] for k in cost if k.endswith(f"|{a}|{lab}")]
        print(f"   {lab:5s} {st.median(v):+.4f}   (cells {len(v)})")
