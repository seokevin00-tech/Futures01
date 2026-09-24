import sys, json, statistics as st
sys.path.insert(0, '/home/user/Futures01'); sys.path.insert(0, '/home/user/Futures01/workspace/studies')
import toolkit as T
SC = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'
rows = json.load(open(f'{SC}/bt.json'))
geo = {}
for a in sorted({r["arm"] for r in rows}):
    v = [r for r in rows if r["arm"] == a and r["n"] and r["n"] >= 20 and r.get("mae")]
    if not v: continue
    mae = st.median([r["mae"] for r in v]); mfe = st.median([r["mfe"] for r in v])
    geo[a] = dict(n_strategies=len(v), avg_MAE_R=round(mae, 4), avg_MFE_R=round(mfe, 4),
                  edge_ratio=round(mfe / mae, 4),
                  payoff_ratio=round(st.median([r["rr"] for r in v]), 3),
                  avg_win_R=round(st.median([r["avg_win"] for r in v]), 4),
                  avg_loss_R=round(st.median([r["avg_loss"] for r in v]), 4),
                  avg_duration_min=round(st.median([r["hold_min"] for r in v]), 1),
                  win_rate=round(st.median([r["win"] for r in v]), 4))
T.save("s_leadlag_mechanism",
  "Why the head start does not pay: trade geometry of early vs confirmed entries",
  "The early entry does get in a median 6-17 bars sooner. Does that show up as a better excursion profile?",
  dict(trade_geometry=geo, floor=20, cells="5 symbols x 3 disjoint slices",
       reading=("The early arms DO get in earlier - median hold 405-431 minutes against 570-633 for "
                "the confirmation arms and 1,846 for break_of_structure@240, so the timing effect is "
                "real in the trades themselves and not just in the measurement. But it buys nothing: "
                "average MFE is LOWER for the early arms (1.371-1.409R) than for entering on the "
                "higher timeframe's own break (1.425R) or on both-broken alignment (1.427R), while "
                "average MAE is HIGHER (1.173-1.209R vs 1.157-1.167R). Edge ratio MFE/MAE: "
                "ltf_break_first 1.169, fresh3 1.194, htf_broken_now 1.231, both_broken 1.223, "
                "late0 1.127. Entering a median 6-17 bars earlier produces a WORSE excursion "
                "profile, which is exactly what an 80% false-positive rate predicts - the extra "
                "trades you get for being early are the ones the higher timeframe never confirms.")),
  headline=("The head start is real in the trades and still does not pay. Early entries hold 405-431 "
            "minutes against 570-633 for confirmation entries, so they genuinely are earlier - but "
            "their MFE is lower (1.371-1.409R vs 1.425R) and their MAE is higher (1.173-1.209R vs "
            "1.157R), giving an edge ratio of 1.169 against 1.231 for entering on the higher "
            "timeframe's own break. Being early buys more adverse excursion and less favourable "
            "excursion. That is the measured 80% false-positive rate showing up in the P&L."),
  caveats=["MFE/MAE are engine-reported per trade and medianed across the 14-strategy arm population, not pooled across trades.",
           "The differences are small in absolute terms (edge ratio 1.13 to 1.23 across all arms) and no per-cell significance test was run on them - this is a mechanism description consistent with the arm results, not an independent significance claim."])
print("saved")
