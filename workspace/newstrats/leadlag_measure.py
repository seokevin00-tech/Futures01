"""Measurement: does the lower timeframe change structure before the higher one?

Pure measurement, no strategies.

LOOK-AHEAD GUARD (stated explicitly because this study is *about* timing):
  * Definition A reads ``TimeframeFrame._last_high/_prior_high/_last_low/
    _prior_low``. ``_build_swing_pointers`` fills those by walking swings sorted
    on ``Swing.confirmed_index`` and advancing only while ``confirmed_index<=i``,
    so a swing is never visible before the bar at which it became knowable.
    This is byte-for-byte the array the live ``structure_trend`` /
    ``structure_event`` conditions read.
  * Definition B uses ``market_structure().events``, whose event index IS
    ``swing.confirmed_index`` (see structure.py: ``st.events.append((
    s.confirmed_index, ev, s.price))``), not ``swing.index``.
  * Every event is stamped with the bar's ``end_ts`` (close), not ``ts`` (open),
    and all cross-timeframe comparison is on wall-clock closes, never on bar
    indices. A 240m bar stamped 12:00-16:00 is therefore compared at 16:00, so
    it can never appear to "know" something before a 60m bar closing 15:00.
"""
from __future__ import annotations

import statistics as st
from bisect import bisect_right
from datetime import timedelta
from typing import List, Optional, Sequence, Tuple

DIRS = ("UPTREND", "DOWNTREND")


# ------------------------------------------------------------------ defn A
def labels_from_swings(frame_tf) -> List[str]:
    """Per-bar structural label, confirmed swings only - as ``snapshot()`` does."""
    out: List[str] = []
    for i in range(len(frame_tf.series.bars)):
        lh, ph = frame_tf._last_high[i], frame_tf._prior_high[i]
        ll, pl = frame_tf._last_low[i], frame_tf._prior_low[i]
        if None in (lh, ph, ll, pl):
            out.append("UNDEFINED")
        elif lh > ph and ll > pl:
            out.append("UPTREND")
        elif lh < ph and ll < pl:
            out.append("DOWNTREND")
        else:
            out.append("RANGE")
    return out


# ------------------------------------------------------------------ defn B
def labels_from_break(frame_tf) -> List[str]:
    """Per-bar direction from the ``break_of_structure`` rule itself.

    UPTREND while the close sits beyond the last CONFIRMED swing high,
    DOWNTREND while beyond the last confirmed swing low. Same arrays, same
    confirmation guard - this is the actionable break, not the HH/HL label.

    (``market_structure().events`` was tried first and rejected: its walker
    keeps ref_high as a running MAX and ref_low as a running MIN and never
    resets them, so over 5,000 MGC 60m bars it emits 30 BOS_UP and exactly one
    CHOCH_DOWN. It cannot represent a downside break after an uptrend and is
    unusable as a lead-lag event source. Recorded as a defect.)
    """
    bars = frame_tf.series.bars
    out: List[str] = []
    for i in range(len(bars)):
        lh, ll = frame_tf._last_high[i], frame_tf._last_low[i]
        c = bars[i].close
        if lh is not None and c > lh:
            out.append("UPTREND")
        elif ll is not None and c < ll:
            out.append("DOWNTREND")
        else:
            out.append("RANGE")
    return out


def onsets_from_labels(labels: Sequence[str], bars) -> List[dict]:
    last: Optional[str] = None
    ev = []
    for i, lab in enumerate(labels):
        if lab not in DIRS:
            continue
        if lab != last:
            ev.append(dict(i=i, dir=lab, ts=bars[i].end_ts, close=bars[i].close))
            last = lab
    return ev


def _q(v, p):
    if not v:
        return None
    s = sorted(v)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def lead_lag(ltf_frame, htf_frame, ltf_min: int, htf_min: int, *,
             defn: str = "swings",
             fp_horizons_htf_bars: Sequence[int] = (3, 6, 12, 24)) -> dict:
    lab_fn = labels_from_swings if defn == "swings" else labels_from_break
    lo_lab, hi_lab = lab_fn(ltf_frame), lab_fn(htf_frame)
    lo_bars, hi_bars = ltf_frame.series.bars, htf_frame.series.bars
    lo_ev = onsets_from_labels(lo_lab, lo_bars)
    hi_ev = onsets_from_labels(hi_lab, hi_bars)
    lo_ends = [b.end_ts for b in lo_bars]
    hi_ends = [b.end_ts for b in hi_bars]

    def state_at(ends, labs, ts):
        k = bisect_right(ends, ts) - 1
        return labs[k] if k >= 0 else None

    def ltf_onset_of_current_run(ts):
        for e in reversed(lo_ev):
            if e["ts"] <= ts:
                return e
        return None

    # ---- A. every HTF structure change: what had the LTF already done? -----
    leads_min, leads_bars = [], []
    ever_leads_min = []
    cls = {"co_state_led": 0, "ltf_opposite": 0, "ltf_range": 0, "ltf_none": 0}
    ever_led = 0
    per_event = []
    prev_h = None
    for h in hi_ev:
        lab = state_at(lo_ends, lo_lab, h["ts"])
        run = ltf_onset_of_current_run(h["ts"])
        rec = dict(ts=str(h["ts"]), dir=h["dir"], ltf_label=lab)
        if lab == h["dir"] and run is not None and run["dir"] == h["dir"]:
            dmin = (h["ts"] - run["ts"]).total_seconds() / 60.0
            dbar = bisect_right(lo_ends, h["ts"]) - bisect_right(lo_ends, run["ts"])
            leads_min.append(dmin); leads_bars.append(dbar)
            cls["co_state_led"] += 1
            rec.update(lead_min=dmin, lead_bars=dbar)
        else:
            cls["ltf_opposite" if lab in DIRS else
                "ltf_range" if lab == "RANGE" else "ltf_none"] += 1
        # generous test: did the LTF EVER flip that way since the last HTF flip?
        lo_ts = prev_h["ts"] if prev_h else lo_ends[0]
        cand = [e for e in lo_ev if e["dir"] == h["dir"] and lo_ts < e["ts"] <= h["ts"]]
        if cand:
            ever_led += 1
            ever_leads_min.append((h["ts"] - cand[0]["ts"]).total_seconds() / 60.0)
            rec["ever_lead_min"] = ever_leads_min[-1]
        per_event.append(rec)
        prev_h = h
    nh = max(1, len(hi_ev))

    # ---- B. every LTF structure change: did the HTF follow? ---------------
    fp = {}
    for H in fp_horizons_htf_bars:
        span = timedelta(minutes=H * htf_min)
        tot = already = followed = 0
        delays = []
        for l in lo_ev:
            tot += 1
            if state_at(hi_ends, hi_lab, l["ts"]) == l["dir"]:
                already += 1
                continue
            nxt = [h for h in hi_ev if h["dir"] == l["dir"]
                   and l["ts"] < h["ts"] <= l["ts"] + span]
            if nxt:
                followed += 1
                delays.append((nxt[0]["ts"] - l["ts"]).total_seconds() / 60.0)
        early = tot - already
        fp[f"{H}htf_bars"] = dict(
            horizon_min=H * htf_min, n_ltf_onsets=tot,
            n_htf_already_aligned=already, n_genuinely_early=early,
            n_htf_followed=followed,
            false_positive_rate=round(1 - followed / max(1, early), 4),
            follow_delay_median_min=_q(delays, .5))

    def rate(ls):
        n = max(1, len(ls))
        return {k: round(sum(1 for x in ls if x == k) / n, 4)
                for k in ("UPTREND", "DOWNTREND", "RANGE", "UNDEFINED")}

    return dict(
        defn=defn, ltf=ltf_min, htf=htf_min,
        n_ltf_bars=len(lo_ends), n_htf_bars=len(hi_ends),
        n_ltf_onsets=len(lo_ev), n_htf_onsets=len(hi_ev),
        ltf_onsets_per_htf_onset=round(len(lo_ev) / nh, 2),
        htf_change_classification={k: dict(n=v, pct=round(v / nh, 4))
                                   for k, v in cls.items()},
        pct_htf_changes_ltf_costate=round(cls["co_state_led"] / nh, 4),
        pct_htf_changes_ltf_ever_led=round(ever_led / nh, 4),
        pct_htf_changes_no_ltf_signal_at_all=round(1 - ever_led / nh, 4),
        lead_min=dict(median=_q(leads_min, .5), q1=_q(leads_min, .25),
                      q3=_q(leads_min, .75), p10=_q(leads_min, .10),
                      p90=_q(leads_min, .90),
                      mean=round(st.mean(leads_min), 1) if leads_min else None,
                      n=len(leads_min)),
        ever_lead_min=dict(median=_q(ever_leads_min, .5), q1=_q(ever_leads_min, .25),
                           q3=_q(ever_leads_min, .75), n=len(ever_leads_min)),
        lead_bars=dict(median=_q(leads_bars, .5), q1=_q(leads_bars, .25),
                       q3=_q(leads_bars, .75),
                       pct_zero=round(sum(1 for b in leads_bars if b == 0) / max(1, len(leads_bars)), 4)),
        lead_htf_bars_median=(round(_q(leads_min, .5) / htf_min, 2) if leads_min else None),
        ever_lead_htf_bars_median=(round(_q(ever_leads_min, .5) / htf_min, 2) if ever_leads_min else None),
        false_positives=fp,
        ltf_label_base_rate=rate(lo_lab), htf_label_base_rate=rate(hi_lab),
        events=per_event)
