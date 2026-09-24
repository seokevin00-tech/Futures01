import sys, json
sys.path.insert(0, '/home/user/Futures01'); sys.path.insert(0, '/home/user/Futures01/workspace/studies')
import toolkit as T
SC = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'
d = json.load(open(f'{SC}/wf_analysis.json'))

T.save("s_leadlag_walkforward",
  "Walk-forward: six sequential disjoint blocks, does the early-entry ranking hold anywhere?",
  "Do the lead-lag arm rankings replicate when the sample is cut into six sequential non-overlapping blocks instead of three?",
  dict(design="6 genuinely disjoint ~53-day blocks per symbol x 5 symbols = 30 cells, floor 20, per-cell rank-sum z combined by Stouffer, never pooled",
       comparisons=d["walk_forward"],
       free_t_84_screened=round(T.free_t(84), 3),
       interpretation={
         "EARLY vs INCUMBENT break_of_structure@60": "stouffer +0.112 over 30 cells, 15 positive / 15 negative, sign alternates block to block. Dead null. The 'higher timeframe has not yet confirmed' gate contributes nothing.",
         "EARLY vs ALIGNED both_broken": "+1.231, 14+/15-. The +3.83 in-sample / -4.44 out-of-sample flip seen in the 3-slice design does NOT survive finer slicing - it was noise in both directions, not a regime effect.",
         "EARLY vs CONFIRM htf_broken_now": "-0.903, 15+/15-. The -4.61 OOS result from the 3-slice design does not replicate. Reported as a null, and the 3-slice figure is downgraded.",
         "EARLY_fresh3 vs CONFIRM_late0": "+2.678 over 22 cells, 14+/8-. The only comparison that survives both designs, but free_t(84)=2.98 so it does not clear deflation.",
         "CONFIRM_late0 vs INCUMBENT": "-2.221, 10+/12-. Waiting for the higher timeframe to confirm a break the lower made N bars ago is WORSE than just trading break_of_structure on the lower timeframe."}),
  headline=("Nothing about early entry survives a six-block walk-forward. ltf_break_first vs "
            "break_of_structure@60 is +0.112 over 30 cells at 15 positive / 15 negative - a coin "
            "flip that changes sign from block to block. The two results that looked significant in "
            "the coarser 3-slice design (early beats aligned in sample at +3.83, aligned beats early "
            "out of sample at -4.44) both collapse to +1.231 and 14+/15- here, which means the "
            "3-slice 'sign flip' was sampling noise at both ends rather than a regime story. Only "
            "ltf_break_first_fresh3 vs htf_confirms_late0 replicates (+2.678, 14+/8-) and it sits "
            "below free_t(84)=2.98."),
  caveats=["30 cells but only ~18 independent (unit x block) observations: MNQ/NQ/MES are one index complex.",
           "Blocks are ~53 days; median strategy trade counts drop to 20-45 per cell, so per-cell z is itself noisy. That noisiness is the point - it is what the 3-slice design hid.",
           "htf_confirms_late0 clears the floor in only 22 of 30 cells; the comparison is on the cells where both arms trade enough."])

T.save("s_leadlag_bos240_oos",
  "break_of_structure at 240m: the out-of-sample test nobody had run",
  "The previous programme scored break_of_structure@240 at +4.4 to +5.0 versus other structure signals on all three symbols and it strengthened with the trade floor. Does it survive disjoint out-of-sample slices?",
  dict(design="5 symbols x 3 genuinely disjoint ~107-day 240m slices; 5 structure signals x 14 strategies each (bare + 13 partners from 13 diversity groups); identical exit and costs; per-cell rank-sum z, Stouffer combined; slice0 in sample, slices 1+2 out of sample",
       shootout=d["shootout240"], census=d["census240"], bos_per_slice=d["bos240_per_slice"],
       sign_test_oos=dict(
         slice1="MGC -0.067, MES +0.097, NQ +0.350, MNQ +0.283, MCL +0.368 -> 4 of 5 positive",
         slice2="MGC -0.100, MES -0.001, NQ -0.178, MNQ -0.065, MCL +0.183 -> 1 of 5 positive",
         combined_oos="5 positive / 5 negative across 10 OOS cells; correcting for the index complex being one unit, 3 of 6 independent observations. A coin flip.",
         in_sample_slice0="negative on all 5 symbols, 0-10% of strategies profitable")),
  headline=("break_of_structure at 240m FAILS out of sample. Its absolute level is negative - median "
            "expectancy -0.0364R over 142 strategies clearing the 20-trade floor, 37% profitable, "
            "median 40 trades. Its relative advantage does not replicate: versus structure_trend@240 "
            "it is +1.061 over 15 cells (8+/7-), in sample -0.876 and out of sample +1.919, none "
            "significant - the previous +4.4 to +5.0 does not reproduce on disjoint slices. It beats "
            "range_position_extreme (+3.860 all, +3.529 OOS, 8+/2-) and LOSES to pullback_to_support "
            "(-5.106 over the 5 cells where both clear the floor). Per slice it is negative on 5 of 5 "
            "symbols in the oldest slice, positive on 4 of 5 in the middle slice and negative on 4 of "
            "5 in the newest: 5 positive / 5 negative across the 10 out-of-sample cells, or 3 of 6 "
            "once the index complex is counted as one unit. The earlier score was a comparator "
            "artefact plus one favourable period."),
  caveats=["The previous programme's +4.4 to +5.0 was measured on nested trailing windows against a different comparator set; this is not a like-for-like replication, it is the OOS test that was missing.",
           "pullback_to_support and fvg_nearby clear the 20-trade floor in very few 240m cells (5 and 1), so those two comparisons are weak - the honest reading is that bos@240 is not distinguishable from structure_trend@240, not that it is worse than everything.",
           "240m slices hold ~430-490 bars each; this is the smallest sample in the study and the per-cell z are correspondingly noisy.",
           "Level and ranking are separate claims. Even the comparisons bos@240 wins are wins among negative-expectancy arms."])
print("saved")
