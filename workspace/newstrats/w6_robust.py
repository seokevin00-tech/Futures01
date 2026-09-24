"""Robustness report: the anti-overfitting audit, stated as what was checked and found."""
from __future__ import annotations
import json, math, os, statistics as st, sys
from collections import defaultdict
sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
import toolkit as T, w6_analyse as W, w6_arms as A      # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research"
SC = f"{OUT}/scratch/ict"
PREFIX = "ict_killzones_ote"
rank = json.load(open(f"{OUT}/{PREFIX}_strategy_rankings.json"))
hour = W.combine(f"{SC}/hour_arms.json", tf=60)
plac = W.combine(f"{SC}/placebo_arms.json", tf=60)
nc = W.combine(f"{SC}/hour_arms_nocostasym.json", tf=60)
fr = json.load(open(f"{SC}/firing_rates.json"))
wfdb = json.load(open(f"{SC}/walkforward_db.json"))

hz = {v: x["all_cells"]["stouffer_z"] for v, x in hour.items()}
pz = {v: x["all_cells"]["stouffer_z"] for v, x in plac.items() if v.startswith("stride")}

doc = {
 "study": PREFIX,
 "scope": "ICT kill zones (time of day) and Optimal Trade Entry, on MNQ/MES/MGC/MCL, "
          "60m primary with 15m and 5m corroboration, 240m excluded by arithmetic.",
 "headline": "Nothing here is live-eligible. 0 of 206 ranked rule sets pass all six gates; "
             "the best durability score is -0.80 (deflated t), i.e. every rule set is below "
             "what searching 206 of them buys for free (free_t = 3.311).",

 "checks_performed": {
  "look_ahead_bias": {
    "checked": "Traced entry timestamps against signal-bar timestamps on a live backtest.",
    "found": "CLEAN. A kz_silver_bullet strategy signalling on the 10:00 ET bar filled at "
             "11:00 ET on 161 of 162 trades (the exception is a session gap); kz_london_open "
             "signals at 02/03/04 filled at 03/04/05. Entries fill at the NEXT bar's open, "
             "so the ET-window conditions read only the signal bar's own start stamp."},
  "repainting": {
    "checked": "Whether the retracement bands are drawn from swings that were knowable.",
    "found": "CLEAN. features.py filters swings with `confirmed_index <= i`, and "
             "indicators/structure.py carries confirmed_index as 'the first bar at which "
             "this swing was knowable'. The fib zone therefore cannot repaint. Bar "
             "timestamps do not repaint either."},
  "future_data_leakage": {
    "checked": "Every input used by a tradeable condition.",
    "found": "CLEAN for the strategy tests. ONE DELIBERATE EXCEPTION, in the descriptive "
             "Part A hour profile only: each bar's range/volume is divided by its own "
             "TRADING DAY's mean, which uses bars after the bar being described. That "
             "normaliser is a per-day constant applied identically to all 24 hours, so it "
             "cannot create or reorder an hour-of-day ranking, but the hour profile is a "
             "description and is NOT usable as a live feature. Stated rather than hidden."},
  "data_mining_bias_and_deflation": {
    "checked": "free_t arithmetic at every search size used.",
    "found": "206 rule sets ranked gives free_t = 3.311 t-units; no rule set clears it "
             "(best deflated t = -0.80). For the 24-hour census free_t = 2.52 and the "
             "Silver Bullet's mean z against other hours is +2.54 - it barely matches what "
             "search alone buys. For the ~38 fib/kill-zone comparisons screened, free_t = 2.70."},
  "insufficient_sample_size": {
    "checked": "Matched-pair counts at every trade floor, and bar counts per timeframe.",
    "found": "A REAL LIMIT. At 240m, ZERO matched fib pairs reach 20 trades in any disjoint "
             "slice, so the 240m fib claim cannot be evaluated at the library's own floor. "
             "The 15m CSVs cover 41 trading days and the 5m 20, so every sub-60m result is "
             "single-regime. 1m covers ~5 days and was not used at all."},
  "unrealistic_fills": {
    "checked": "Fill convention and gap handling in the engine.",
    "found": "Entries fill at the next bar's open with adverse slippage; a gap through a "
             "stop fills at the open, worse than the stop. This is conservative. Not "
             "verified: whether the tick-based slippage magnitudes are calibrated to real "
             "CME books, and there is no bid/ask data in these CSVs to calibrate against."},
  "understated_costs_and_time_asymmetry": {
    "checked": "Re-ran the entire 24-hour census with thin_book_extra_ticks set to 0.",
    "found": "MATERIAL. The engine charges an extra tick when `not is_rth(fill_ts)`, so "
             "overnight fills pay 1.5 ticks and RTH fills 0.5. Removing it lifts overnight "
             "hours by +0.38 z on average and lowers RTH hours by -0.15, and moves the "
             "Silver Bullet from rank 1 of 23 hours to rank 3, behind 21:00 and 20:00 ET. "
             "Any ET-clock study in this system is partly a study of its cost model."},
  "survivorship_bias": {
    "checked": "Instrument selection and series construction.",
    "found": "No universe selection: four named contracts, all bars, no filtering on "
             "outcome. NOT CHECKED, and a real gap: these are continuous series with no "
             "roll dates supplied, so contract-roll artefacts are unquantified."},
  "parameter_sensitivity": {
    "checked": "Shifted the Silver Bullet window by one hour either way, and varied the "
               "OTE band edges.",
    "found": "THE KILL ZONE IS A SPIKE, NOT A PLATEAU. hour_09 scores +1.43, hour_10 +5.15, "
             "hour_11 +2.41 against the same controls - a 3.7-z swing for a one-hour shift, "
             "which is the signature of a fitted parameter rather than a robust one. The OTE "
             "band is the opposite: 0.62-0.79 and 0.618-0.786 overlap at Jaccard 0.97 and "
             "differ by nothing measurable, and narrowing to 0.68-0.73 (Jaccard 0.28) also "
             "changes nothing - insensitivity here means the band is inert, not robust."},
  "degenerate_or_duplicate_conditions": {
    "checked": "Firing rate of every condition, and Jaccard against existing library ones.",
    "found": "ote_zone is fib_golden_pocket: Jaccard 0.938-1.000 over 8 cells. The kz_* "
             "conditions are genuinely new (Jaccard 0.000-0.053 against the shipped time "
             "filters, except kz_ny_open vs opening_drive_window at 0.334 on MGC and 0.243 "
             "on MCL, whose RTH opens fall inside the 07:00-10:00 window). All firing rates "
             "are inside 1-95% at 60m."},
  "clone_and_correlation_inflation_D28": {
    "checked": "Whether T.ab was used anywhere.",
    "found": "It was not. Every comparison is a matched arm with a per-cell paired sign test "
             "combined by Stouffer, per D28."},
  "out_of_sample": {
    "checked": "Disjoint thirds, a 60/40 temporal split, and a 6-fold walk-forward.",
    "found": "Applied to every headline claim. The Silver Bullet's edge over other RTH hours "
             "goes from +3.02 in sample to +0.63 out of sample. The golden-pocket-over-"
             "shallow effect goes from +2.62 IS to +1.0-1.5 OOS at 60m and is absent at "
             "240m. Walk-forward: 0 of 206 rule sets clear the efficiency gate together "
             "with the others."}},

 "walk_forward": {
   "design": "Six consecutive, non-overlapping sixths of each symbol's 60m series "
             "(~37 trading days each), 4 symbols, anchored: folds 1..k-1 inform fold k.",
   "n_rule_sets": rank["n_rule_sets_ranked"], "n_live_eligible": rank["n_live_eligible"],
   "free_t": rank["free_t"],
   "best_durability_score": rank["rankings"][0]["durability_score"],
   "by_variant": json.load(open(f"{SC}/variant_summary.json")),
   "caveat": "walk_forward_efficiency is a ratio of small means and is unstable when the "
             "in-sample mean is near zero; several values exceed +-3 for that reason and "
             "should be read as 'unstable', not as large."},

 "placebo_controls": {
   "count_matched_1_in_24": {"stouffer_z_by_phase": pz,
     "mean": round(st.mean(pz.values()) if hasattr(pz, "values") else st.mean(list(pz.values())), 3),
     "max": round(max(pz.values()), 3),
     "silver_bullet_for_comparison": hz.get("hour_10"),
     "read": "A filter with the same firing rate as a one-hour window and no clock content "
             "scores a mean of +2.25 and a maximum of +4.77 against the unfiltered control. "
             "The Silver Bullet's +5.15 is at the top of that distribution but not outside "
             "it, and 21:00 ET - which ICT says not to trade - scores +4.52 with 11 of 12 "
             "cells positive."},
   "hour_census": {"stouffer_z_by_hour": hz,
     "mean_over_24_hours": round(st.mean(list(hz.values())), 3),
     "hours_positive": sum(1 for z in hz.values() if z > 0),
     "read": "18 of 24 single-hour filters beat no filter, mean +1.58. The effect is "
             "restricting trade frequency, not choosing the right hour."},
   "cost_symmetric_rerun": {"stouffer_z_by_hour": {v: x["all_cells"]["stouffer_z"]
                                                   for v, x in nc.items()}}},

 "firing_rates": {k: v["firing_rate"] for k, v in fr.items()},

 "what_would_change_the_verdict": [
   "A 60m series of several years rather than 224 trading days, so the 4 out-of-sample "
   "cells become 20 and MNQ/MES stop being one observation counted twice.",
   "15m and 5m histories long enough to test the kill zones where they are best resolved; "
   "at 41 and 20 trading days the sub-60m results here are single-regime.",
   "A calibrated slippage model, or bid/ask data to calibrate one, since the RTH/overnight "
   "cost asymmetry is large enough to reorder the hour ranking.",
   "Contract roll dates, so roll artefacts in the continuous series can be excluded."],
}
json.dump(doc, open(f"{OUT}/{PREFIX}_robustness_report.json", "w"), indent=1, default=str)
print("written", f"{OUT}/{PREFIX}_robustness_report.json")
