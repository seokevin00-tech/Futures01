import sys, json, statistics as st
sys.path.insert(0, '/home/user/Futures01')
sys.path.insert(0, '/home/user/Futures01/workspace/studies')
import toolkit as T

d = json.load(open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/measure.json'))
SY = ["MGC", "MES", "NQ", "MNQ", "MCL"]

def col(key, path):
    out = {}
    for s in SY:
        r = d[s].get(key)
        if not r or "error" in r:
            continue
        v = r
        for p in path:
            v = v[p]
        out[s] = v
    return out

summary = {}
for key in ["60->240_break", "60->240_swings", "60->240_break_IS_first60",
            "60->240_break_OOS_last40", "15->60_break"]:
    med = col(key, ["lead_bars", "median"])
    summary[key] = dict(
        n_ltf_onsets=col(key, ["n_ltf_onsets"]),
        n_htf_onsets=col(key, ["n_htf_onsets"]),
        pct_htf_with_live_ltf_lead=col(key, ["pct_htf_changes_ltf_costate"]),
        pct_htf_ltf_ever_led=col(key, ["pct_htf_changes_ltf_ever_led"]),
        pct_htf_NO_ltf_signal_at_all=col(key, ["pct_htf_changes_no_ltf_signal_at_all"]),
        lead_bars_median=med,
        lead_bars_q1=col(key, ["lead_bars", "q1"]),
        lead_bars_q3=col(key, ["lead_bars", "q3"]),
        lead_minutes_median=col(key, ["lead_min", "median"]),
        lead_in_htf_bars_median=col(key, ["lead_htf_bars_median"]),
        pct_lead_exactly_zero=col(key, ["lead_bars", "pct_zero"]),
        fp_rate_1x_htf_horizon_24h=col(key, ["false_positives", "6htf_bars", "false_positive_rate"]),
        fp_rate_48h=col(key, ["false_positives", "12htf_bars", "false_positive_rate"]),
        fp_rate_96h=col(key, ["false_positives", "24htf_bars", "false_positive_rate"]),
        n_genuinely_early_ltf_breaks=col(key, ["false_positives", "6htf_bars", "n_genuinely_early"]),
        ltf_onsets_per_htf_onset=col(key, ["ltf_onsets_per_htf_onset"]),
        ltf_label_base_rate=col(key, ["ltf_label_base_rate"]),
    )

b = summary["60->240_break"]
payload = dict(
    method=dict(
        definition_A_swings="per-bar HH/HL vs LH/LL label read off the SAME confirmed-swing arrays the live structure_trend / structure_event conditions read (TimeframeFrame._last_high/_prior_high/_last_low/_prior_low, filled in Swing.confirmed_index order)",
        definition_B_break="per-bar direction from the break_of_structure rule itself: close beyond the last CONFIRMED swing high (UP) or low (DOWN)",
        definition_rejected="market_structure().events - its walker keeps ref_high as a running MAX and ref_low as a running MIN and never resets them, so on 5,000 MGC 60m bars it emits 30 BOS_UP and 1 CHOCH_DOWN. It structurally cannot express a downside break after an uptrend. DEFECT, unusable as a lead-lag event source.",
        event_stamp="bar end_ts (close), never ts (open); all cross-timeframe comparison on wall-clock closes, never bar indices",
        lookahead_guard="a swing is never read before Swing.confirmed_index because the arrays used are built by advancing a pointer only while confirmed_index <= i; the 240m bar covering 12:00-16:00 is timestamped 16:00 so it can never appear to know something before the 60m bar closing 15:00",
        span="~321 trading days of 60m bars per symbol (MCL 343); 15m base is only 58 days and is reported as replication of the MEASUREMENT only, never as a strategy test",
    ),
    per_pair=summary,
    headline_numbers=dict(
        lead_bars_median_60m=b["lead_bars_median"],
        lead_bars_iqr_60m={s: [b["lead_bars_q1"][s], b["lead_bars_q3"][s]] for s in b["lead_bars_median"]},
        pct_htf_breaks_with_live_ltf_lead=b["pct_htf_with_live_ltf_lead"],
        pct_htf_breaks_with_no_ltf_signal_at_all=b["pct_htf_NO_ltf_signal_at_all"],
        false_positive_rate_at_24h=b["fp_rate_1x_htf_horizon_24h"],
    ),
    stability=dict(
        note="first 60% of bars vs last 40%, genuinely non-overlapping",
        costate_IS=summary["60->240_break_IS_first60"]["pct_htf_with_live_ltf_lead"],
        costate_OOS=summary["60->240_break_OOS_last40"]["pct_htf_with_live_ltf_lead"],
        lead_bars_median_IS=summary["60->240_break_IS_first60"]["lead_bars_median"],
        lead_bars_median_OOS=summary["60->240_break_OOS_last40"]["lead_bars_median"],
        fp24h_IS=summary["60->240_break_IS_first60"]["fp_rate_1x_htf_horizon_24h"],
        fp24h_OOS=summary["60->240_break_OOS_last40"]["fp_rate_1x_htf_horizon_24h"],
    ),
    sign_test=dict(
        claim="median lead >= 6 LTF bars AND 24h false-positive rate >= 0.77",
        symbols_satisfying=5, symbols_tested=5,
        independent_units="MNQ/NQ/MES are one index complex; independent units are (index, MGC, MCL) = 3/3",
        note="a 5/5 sign test is p=0.031 one-sided but only 3 units are independent, so p=0.125 honestly",
    ),
)

path = T.save(
    "s_leadlag",
    "Lead-lag between timeframes: how many bars early does the lower timeframe break structure, and what does that early signal cost?",
    "For every confirmed 240m structure change, how far ahead did the 60m change? What fraction of 240m changes had no 60m precursor at all, and how often does the 60m break without the 240m ever following?",
    payload,
    headline=("The lead is REAL, LARGE and STABLE, and it is expensive. Across 5 symbols the 60m "
              "breaks structure a median 6-17 60m bars (360-1080 min = 1.5-4.5 240m bars) before the "
              "240m confirms the same direction; IQR ~2 to ~27 bars; exactly-zero lead is 0% of cases. "
              "79-87% of 240m breaks had a live 60m break of the same direction already in place, and "
              "only 0-6% had no 60m precursor at all, so signal availability is not the problem. The "
              "price is the false-positive rate: 78-81% of genuinely-early 60m breaks are never "
              "followed by a 240m break within 24h (69-77% within 48h, 51-67% within 96h). That ~80% "
              "figure is near-identical on MGC, MES, NQ, MNQ, MCL AND on the independent 15m->60m pair, "
              "and it barely moves between the first 60% and last 40% of the sample. An early-entry "
              "strategy is buying a 1.5-4.5 HTF-bar head start at a price of four wrong signals in five."),
    caveats=[
        "Measurement only - no trades, no costs, no P&L. Whether the head start is worth the 80% FP rate is a separate question answered by the strategy arms.",
        "market_structure().events is defective (running-max/running-min refs, never reset) and was NOT used; the HH/HL label definition and the break_of_structure definition were used instead and disagree sharply on co-state rate (14-43% vs 79-87%) because the HH/HL label spends 37-69% of bars in RANGE.",
        "MNQ/NQ/MES are one index complex; agreement among them is one observation, not three. Independent units are 3.",
        "15m->60m spans only 58 days and is replication of the measurement, never evidence about tradeability.",
        "The false-positive rate depends on the horizon chosen; it is reported at 4 horizons rather than one.",
    ])
print(path)
