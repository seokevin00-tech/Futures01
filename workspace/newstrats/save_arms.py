import sys, json, statistics as st
sys.path.insert(0, '/home/user/Futures01')
sys.path.insert(0, '/home/user/Futures01/workspace/studies')
import toolkit as T

SC = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'
a0 = json.load(open(f'{SC}/analysis_f0.json'))
a20 = json.load(open(f'{SC}/analysis_f20.json'))
rates = json.load(open(f'{SC}/rates.json'))
rows = json.load(open(f'{SC}/bt.json'))

def per_slice(arm_a, arm_b, floor=20):
    out = {}
    for sl in ["slice0", "slice1", "slice2"]:
        d = {}
        for sym in ["MGC", "MES", "NQ", "MNQ", "MCL"]:
            A = [r["exp"] for r in rows if r["arm"] == arm_a and r["cell"] == sl
                 and r["symbol"] == sym and r["n"] and r["n"] >= floor and r["exp"] is not None]
            B = [r["exp"] for r in rows if r["arm"] == arm_b and r["cell"] == sl
                 and r["symbol"] == sym and r["n"] and r["n"] >= floor and r["exp"] is not None]
            if len(A) < 5 or len(B) < 5:
                continue
            u = T.mann_whitney_u(A, B)
            d[sym] = dict(z=u["z"], n_a=len(A), n_b=len(B),
                          med_a=round(st.median(A), 4), med_b=round(st.median(B), 4))
        out[sl] = d
    return out

payload = dict(
    design=dict(
        arms="11 entry rules on 60m + 1 on 240m; each arm is the SAME 14-strategy population (bare entry + 13 partners from 13 different diversity groups), only the entry rule swapped - so a between-arm difference is the entry rule and nothing else",
        partners=["adx_trending", "rsi_directional", "above_vwap", "volume_not_thin",
                  "cvd_directional", "volatility_normal", "regime_trending", "avoid_lunch",
                  "candle_decisive_close", "bollinger_mean_pull", "fvg_nearby",
                  "efficiency_high", "outside_news_blackout"],
        cells="5 symbols x 3 GENUINELY DISJOINT ~107-day slices (T.disjoint_slices), never nested windows",
        oos="slice0 (oldest) = in sample; slice1 + slice2 = out of sample; per-cell z combined by Stouffer, never pooled trades",
        exit="toolkit default (ATRx1 stop, anchor-ATR targets 1.0/2.5, scale 50/50, BE at 1.5R, 40-bar time stop, exit_at_session_close=False), rth_only=False",
        costs="engine CostModel: commission in dollars + slippage on fill price, entry at NEXT bar's open, stop wins stop/target ties, gaps fill at open",
    ),
    firing_rates=rates["rates"],
    causality_audit=rates["causality_audit"],
    census_floor0=a0["arm_summary"], census_floor20=a20["arm_summary"],
    pairs_floor0=a0["pairs"], pairs_floor20=a20["pairs"],
    per_slice_EARLY_vs_ALIGNED=per_slice("EARLY_ltf_break_first", "ALIGNED_both_broken"),
    per_slice_EARLY_vs_CONFIRM_late0=per_slice("EARLY_ltf_break_first", "CONFIRM_late0"),
    per_slice_EARLYfresh3_vs_CONFIRM_late0=per_slice("EARLY_fresh3", "CONFIRM_late0"),
    per_slice_EARLY_vs_INCUMBENT=per_slice("EARLY_ltf_break_first", "INCUMBENT_bos_ltf"),
    deflation=dict(
        strategies_screened_per_cell=12 * 14,
        free_t_168=round(T.free_t(168), 3),
        arm_comparisons_run=11 * 3,
        free_t_33=round(T.free_t(33), 3),
        note="the ONLY OOS result above free_t(168)=3.20 is EARLY_fresh3 vs CONFIRM_late0 at +4.202 (floor 20). Everything else, including every claim about the early signal beating the incumbent, is below the deflation floor.",
    ),
    duplicate_check=dict(
        finding="htf_broken_now and break_of_structure.bind(240) produce IDENTICAL trade sets (15,698 trades each at floor 20, identical arm summaries). This is not a defect - it is the intended validation that leadlag.break_dir() reproduces the library's break_of_structure predicate exactly.",
        other_near_duplicates={"ltf_break_first ~ ltf_break_first_fresh6": "98.6-99.0% identical verdicts - fresh6 dropped",
                               "htf_confirms_late0 ~ htf_confirms_late2": "98.1-98.4% identical - late2 dropped",
                               "htf_confirms_late8 ~ htf_confirms_late16": "98.8-99.4% identical; late16 fires on 0.0-0.04% of bars, DEGENERATE, dropped"},
        vs_break_of_structure="no lead-lag condition exceeds 86.7% verdict agreement with break_of_structure@60 - none is a copy of it",
    ),
)

path = T.save(
    "s_leadlag_arms",
    "Early entry vs confirmation entry vs the break_of_structure incumbent, on matched arms over disjoint slices",
    "Does entering on the lower timeframe's break while the higher timeframe is still unconfirmed beat entering at confirmation, and does either beat plain break_of_structure?",
    payload,
    headline=(
        "Early entry is NOT an edge over the incumbent, and the one place it looks like an edge "
        "flips sign out of sample. Matched arms, 15 disjoint cells, per-cell z Stouffer-combined, "
        "floor 20: (1) ltf_break_first vs break_of_structure@60 is a NULL - z=-0.60 over 15 cells "
        "(174 vs 180 strategies), z=-1.17 out of sample, 5-5 on cells. The 'not yet confirmed' gate "
        "adds nothing. (2) ltf_break_first vs ltf_and_htf_both_broken - the two halves that exactly "
        "partition break_of_structure@60 - is z=+3.83 IN SAMPLE and z=-4.44 OUT OF SAMPLE, 4-1 then "
        "2-8. A textbook sign flip; the early half wins only in the oldest slice. (3) Entering on the "
        "higher timeframe's own break beats entering early out of sample, z=-4.61 over 10 OOS cells "
        "(174 vs 174 strategies), 2-8. (4) The one survivor: ltf_break_first_fresh3 (early break at "
        "most 3 bars old) beats htf_confirms_late0 at z=+5.98 all cells and z=+4.20 out of sample, "
        "12-3 and 8-2 - and it STRENGTHENS with the trade floor (+3.93/+2.50 at floor 0). But that is "
        "early-vs-late-confirmation, not early-vs-incumbent, and the LEVEL is still negative: every "
        "arm's median expectancy is between -0.08R and -0.01R. This is a ranking among losers."),
    caveats=[
        "Every arm has NEGATIVE median expectancy at both floors. The comparisons are relative; nothing here is profitable.",
        "free_t(168 screened) = 3.20. Only EARLY_fresh3 vs CONFIRM_late0 OOS (+4.20) clears it. Every claim about the early signal beating the incumbent is below the deflation floor and is reported as a null, not as a negative finding.",
        "MNQ/NQ/MES are one index complex - 15 cells are really about 9 independent (unit x slice) observations, so Stouffer over 15 overstates by roughly sqrt(15/9)=1.29x.",
        "htf_confirms_late8 fires on 0.6-1.2% of bars and never clears the 20-trade floor; htf_confirms_late16 fires on ~0.0% and is degenerate. The 'enter very late' end of the parameter is untestable, not tested-and-null.",
        "Slippage and commission are the engine's standard model; they were not stressed. A 1-bar-earlier entry is also a worse-liquidity entry in reality and that is not modelled.",
    ])
print(path)
