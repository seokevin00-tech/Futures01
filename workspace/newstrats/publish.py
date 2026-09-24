"""Publish the three deliverables, namespaced so a peer worker's files are not clobbered."""
import sys, json, statistics as st
from collections import defaultdict
sys.path.insert(0, '/home/user/Futures01'); sys.path.insert(0, '/home/user/Futures01/workspace/studies')
import toolkit as T

SC = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'
OUT = '/home/user/Futures01/workspace/strategy_research'
bt = json.load(open(f'{SC}/bt.json')); wf = json.load(open(f'{SC}/wf.json'))
s240 = json.load(open(f'{SC}/s240.json')); stress = json.load(open(f'{SC}/stress.json'))
rates = json.load(open(f'{SC}/rates.json')); wfa = json.load(open(f'{SC}/wf_analysis.json'))
meas = json.load(open('/home/user/Futures01/workspace/studies/out/s_leadlag.json'))

FORMULA = ("durability = expectancy_R * n/(n+40) + 0.15*min(t,4) - 0.05*maxDD_R "
           "- 0.02*max_consecutive_losses. Never total or historical profit: the n/(n+40) shrink "
           "kills a 22-trade wonder, the t term rewards a distinguishable R series and the drawdown "
           "and losing-run terms charge for the path rather than the endpoint.")

def durability(r, maxcl=0):
    n = r["n"] or 0
    if not n or r["exp"] is None: return None
    return round(r["exp"] * n / (n + 40) + 0.15 * min(r.get("t") or 0, 4)
                 - 0.05 * abs(r.get("maxdd") or 0) - 0.02 * maxcl, 4)

# ---- performance_db: per symbol x timeframe x arm, aggregated over disjoint cells
db = defaultdict(lambda: defaultdict(dict))
allrows = bt + s240
for sym in ["MGC", "MES", "NQ", "MNQ", "MCL"]:
    for tf in (60, 240):
        for arm in sorted({r["arm"] for r in allrows if r["tf"] == tf}):
            v = [r for r in allrows if r["symbol"] == sym and r["tf"] == tf
                 and r["arm"] == arm and r["n"] and r["n"] >= 20 and r["exp"] is not None]
            if not v: continue
            by_cell = defaultdict(list)
            for r in v: by_cell[r["cell"]].append(r)
            db[sym][str(tf)][arm] = dict(
                n_strategies=len(v), total_trades=sum(r["n"] for r in v),
                median_trades=st.median([r["n"] for r in v]),
                win_rate=round(st.median([r["win"] for r in v]), 4),
                avg_win_R=round(st.median([r["avg_win"] for r in v if r.get("avg_win")]), 4),
                avg_loss_R=round(st.median([r["avg_loss"] for r in v if r.get("avg_loss")]), 4),
                payoff_ratio=round(st.median([r["rr"] for r in v]), 3),
                profit_factor=round(st.median([r["pf"] for r in v]), 3),
                expectancy_R=round(st.median([r["exp"] for r in v]), 4),
                max_drawdown_R=round(max(r["maxdd"] for r in v), 3),
                avg_drawdown_R=round(st.median([r["maxdd"] for r in v]), 3),
                sortino=round(st.median([r["sortino"] for r in v if r.get("sortino") is not None]), 3),
                t_stat=round(st.median([r["t"] for r in v]), 3),
                avg_MAE_R=round(st.median([r["mae"] for r in v if r.get("mae")]), 4),
                avg_MFE_R=round(st.median([r["mfe"] for r in v if r.get("mfe")]), 4),
                edge_ratio_MFE_over_MAE=round(st.median([r["mfe"] for r in v if r.get("mfe")])
                                              / st.median([r["mae"] for r in v if r.get("mae")]), 3),
                avg_duration_min=round(st.median([r["hold_min"] for r in v if r.get("hold_min")]), 1),
                pct_profitable=round(sum(1 for r in v if r["exp"] > 0) / len(v), 3),
                per_disjoint_slice={c: dict(n_strategies=len(g),
                                            expectancy_R=round(st.median([x["exp"] for x in g]), 4),
                                            pct_profitable=round(sum(1 for x in g if x["exp"] > 0) / len(g), 3))
                                    for c, g in sorted(by_cell.items())})

perf = dict(
    generated_by="strategy research worker - lead-lag study (s_leadlag, s_leadlag_arms, s_leadlag_walkforward, s_leadlag_bos240_oos, s_leadlag_robustness)",
    independence="Every symbol is its own universe. Nothing measured on MNQ is claimed for MES, MGC or MCL. MNQ/NQ/MES are one index complex and are counted as ONE independent unit alongside MGC and MCL.",
    timeframes_tested=dict(individually=[60, 240], as_a_pair="60m lower / 240m higher on one frame, plus a 15m/60m replication of the MEASUREMENT only (58-day span, never used for a tradeability claim)"),
    metric_note="Values are medians across the 14-strategy population per arm (bare entry + 13 partner conditions), aggregated over 3 genuinely disjoint ~107-day slices. max_drawdown_R is the worst across the population; avg_drawdown_R is its median.",
    max_consecutive=stress,
    by_symbol={k: dict(v) for k, v in db.items()})
json.dump(perf, open(f'{OUT}/leadlag_performance_db.json', 'w'), indent=1, default=str)

# ---- strategy_rankings
rank = []
for sym in db:
    for tf in db[sym]:
        for arm, m in db[sym][tf].items():
            d = durability(dict(n=m["total_trades"], exp=m["expectancy_R"],
                                t=m["t_stat"], maxdd=m["avg_drawdown_R"]),
                           maxcl=7)
            rank.append(dict(symbol=sym, timeframe=int(tf), arm=arm, durability=d,
                             expectancy_R=m["expectancy_R"], t_stat=m["t_stat"],
                             trades=m["total_trades"], win_rate=m["win_rate"],
                             profit_factor=m["profit_factor"],
                             pct_profitable=m["pct_profitable"]))
rank.sort(key=lambda r: -(r["durability"] or -9))
rankings = dict(
    generated_by="strategy research worker - lead-lag study",
    ranked_on=FORMULA,
    live_eligible=[],
    live_eligible_reason=(
        "NOTHING from this study is live-eligible. Two independent bars are failed. (1) LEVEL: every "
        "lead-lag arm and the break_of_structure@240 incumbent have NEGATIVE median expectancy at "
        "floor 0, at floor 20 and under tripled slippage. (2) DURABILITY: the only relative result "
        "that survives both a 3-slice and a 6-block disjoint partition - ltf_break_first_fresh3 over "
        "htf_confirms_late0 - is +2.678 Stouffer over 22 six-block cells against free_t(84)=2.977, "
        "and it FAILS parameter sensitivity: median expectancy across the freshness parameter is "
        "-0.036, -0.046, -0.012, -0.043 for K=0,1,3,unbounded, so K=3 is an isolated bump."),
    headline_finding=meas["headline"],
    ranked=rank[:40],
    note_on_ranking=("Every entry in this table has negative or near-zero durability. The table is "
                     "published so the ordering is inspectable, not because anything in it is a "
                     "candidate. Ranking among losers is still ranking among losers."))
json.dump(rankings, open(f'{OUT}/leadlag_strategy_rankings.json', 'w'), indent=1, default=str)

# ---- robustness_report
rob = json.load(open('/home/user/Futures01/workspace/studies/out/s_leadlag_robustness.json'))
rob_out = dict(
    scope=("Anti-overfitting audit for the lead-lag study: 5 symbols (MGC, MES, NQ, MNQ, MCL), "
           "timeframes 60m and 240m individually and as a 60m/240m pair, 3 disjoint ~107-day slices "
           "and a separate 6-block ~53-day partition, 2,520 + 1,260 + 1,050 measured rule-set runs."),
    bottom_line=rob["headline"],
    checks=rob["findings"]["checks"],
    out_of_sample=dict(
        design_1="slice0 (oldest) in sample, slices 1+2 out of sample, T.disjoint_slices n=3",
        design_2="6 sequential disjoint blocks, every comparison recomputed per block",
        headline_claims_and_their_oos_result={
          "the lower timeframe leads the higher one": "HOLDS. Median lead 6-17 60m bars in the first 60% of the sample and 7-21 bars in the last 40%; false-positive rate 0.73-0.83 then 0.78-0.93. Stable.",
          "early entry beats the incumbent break_of_structure@60": "FAILS. z=-1.17 over 10 OOS cells (3-slice), +0.112 over 30 cells (6-block), 15+/15-. Null.",
          "early entry beats entering at the higher timeframe's own break": "FAILS. -4.61 over 10 OOS cells in the 3-slice design does NOT replicate: -0.903 over 30 cells, 15+/15-.",
          "early beats aligned (the two halves of break_of_structure@60)": "FAILS. +3.83 in sample, -4.44 out of sample in the 3-slice design; +1.231 and 14+/15- over 6 blocks. Sign flip was noise at both ends.",
          "ltf_break_first_fresh3 beats htf_confirms_late0": "SURVIVES both designs (+4.20 OOS 3-slice, +2.678 6-block) but is below free_t(84)=2.977 and fails parameter sensitivity.",
          "break_of_structure@240 is the most promising untested thread": "FAILS. Median expectancy -0.0364R over 142 strategies, 37% profitable. Versus structure_trend@240: +1.061 over 15 cells (8+/7-), IS -0.876, OOS +1.919 - the previous +4.4 to +5.0 does not reproduce. Per slice: negative on 5/5 symbols in slice0, positive on 4/5 in slice1, negative on 4/5 in slice2. 5+/5- across OOS cells, 3/6 on independent units."}),
    walk_forward=wfa["walk_forward"],
    firing_rates=rates["rates"],
    causality_audit=rates["causality_audit"],
    live_eligible=[],
    what_would_change_this=("A longer history. 321 days gives 34-57 confirmed 240m structure changes "
                            "per symbol; the lead measurement is comfortable at that size but the "
                            "strategy comparisons are not. The measurement finding does not need "
                            "more data - it is stable across symbols, across the 15m/60m pair and "
                            "across the temporal split."))
json.dump(rob_out, open(f'{OUT}/leadlag_robustness_report.json', 'w'), indent=1, default=str)
print("published 3 files to", OUT)
for r in rank[:8]:
    print(f"  {r['symbol']:5} {r['timeframe']:4} {r['arm']:30} dur={r['durability']:+.4f} exp={r['expectancy_R']:+.4f} n={r['trades']}")
