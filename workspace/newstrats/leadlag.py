"""Lead-lag conditions: trade the lower timeframe's break before the higher one confirms.

Measured first (study ``s_leadlag``): on 60m->240m the lower timeframe breaks
structure a median 6-17 60m bars before the 240m confirms the same direction,
0-6% of 240m breaks have no 60m precursor at all, and ~80% of genuinely-early
60m breaks are never followed by a 240m break within 24h. These conditions turn
that measurement into rules and let the backtest price the trade-off.

LOOK-AHEAD GUARD - this study is ABOUT timing, so the guard is the whole result:

* Break direction is derived from ``TFSnapshot.last_swing_high/last_swing_low``,
  which ``TimeframeFrame._build_swing_pointers`` fills by walking swings sorted
  on ``Swing.confirmed_index`` and advancing the pointer only while
  ``confirmed_index <= i``. A swing at bar i is confirmed at i+right, so the
  value at bar i can only reflect swings whose right-hand fractal bars have
  already closed.
* ``bars_since_onset`` needs history that a TFSnapshot does not carry, so it
  comes from ``_history()``. That function recomputes the SAME confirmed arrays
  and then does a single strictly-forward pass: the value stored against bar i
  is a function of labels[0..i] only. ``_audit_causality`` re-derives the array
  from ``bars[:i+1]`` and asserts equality with the full-series value - run it,
  it is the proof, not a comment.
* Cross-timeframe reads go through ``snap.tf(htf)``, and ``SymbolFrame.
  _build_alignment`` maps a base bar to the last COMPLETED bar on each higher
  timeframe. The 240m bar spanning 12:00-16:00 is not visible to the 60m bar
  closing 13:00.
* The engine fills entries at the NEXT bar's open, so even a correctly-timed
  signal is never filled at the close that produced it.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from futures_agents.features import FeatureSnapshot, TFSnapshot
from futures_agents.schema import Direction
from futures_agents.strategies.base import ConditionKind, ConditionResult
from futures_agents.strategies.library import condition

LONG, SHORT = Direction.LONG, Direction.SHORT

#: Which timeframe confirms which. The higher member of each pair must be
#: present in the SymbolFrame or the condition declines rather than guessing.
HTF_OF: Dict[int, int] = {5: 15, 15: 60, 30: 240, 60: 240, 240: 1440}


# ---------------------------------------------------------------- primitives
def break_dir(s: Optional[TFSnapshot]) -> Optional[str]:
    """Direction of the CONFIRMED-swing break at this bar, or None.

    Identical predicate to the library's ``break_of_structure``; kept here so
    the early/late arms and the incumbent control are provably reading the same
    definition of "broken".
    """
    if s is None:
        return None
    if s.last_swing_high is not None and s.close > s.last_swing_high:
        return "UP"
    if s.last_swing_low is not None and s.close < s.last_swing_low:
        return "DOWN"
    return None


_HIST: Dict[Tuple[str, int], Dict] = {}


def _labels(frame_tf) -> List[Optional[str]]:
    bars = frame_tf.series.bars
    out: List[Optional[str]] = []
    for i in range(len(bars)):
        lh, ll = frame_tf._last_high[i], frame_tf._last_low[i]
        c = bars[i].close
        out.append("UP" if (lh is not None and c > lh)
                   else "DOWN" if (ll is not None and c < ll) else None)
    return out


def _bars_since_onset(labels) -> List[Optional[int]]:
    """Strictly forward pass: value at i depends on labels[0..i] only."""
    out: List[Optional[int]] = []
    cur, start = None, None
    for i, lab in enumerate(labels):
        if lab is None:
            cur, start = None, None
            out.append(None)
            continue
        if lab != cur:
            cur, start = lab, i
        out.append(i - start)
    return out


def _history(symbol: str, tf: int) -> Dict:
    """``end_ts -> (dir, bars_since_onset)`` for one symbol/timeframe.

    Built once from the full series. Causal by construction (see
    ``_audit_causality``); the only difference between this and a slice-local
    computation is that a slice would have LESS warm-up history, never more.
    """
    key = (symbol.upper(), int(tf))
    if key in _HIST:
        return _HIST[key]
    import sys
    sys.path.insert(0, "/home/user/Futures01/workspace/studies")
    import toolkit as T
    from futures_agents.features import build_symbol_frame
    base = 60 if tf >= 60 else tf
    series = T._series(symbol, base, None)
    tfs = sorted({base, tf})
    f = build_symbol_frame(series, tfs)
    ft = f.frames[tf]
    labs = _labels(ft)
    since = _bars_since_onset(labs)
    out = {b.end_ts: (labs[i], since[i]) for i, b in enumerate(ft.series.bars)}
    _HIST[key] = out
    return out


def _audit_causality(symbol: str, tf: int, n_probe: int = 25) -> dict:
    """Re-derive the arrays from ``bars[:i+1]`` and assert they match.

    If any probe disagrees, the history cache is reading the future and every
    number in this study is void.
    """
    import sys
    sys.path.insert(0, "/home/user/Futures01/workspace/studies")
    import toolkit as T
    from futures_agents.data.bars import BarSeries
    from futures_agents.features import build_symbol_frame
    base = 60 if tf >= 60 else tf
    series = T._series(symbol, base, None)
    full = build_symbol_frame(series, sorted({base, tf})).frames[tf]
    labs_full, since_full = _labels(full), _bars_since_onset(_labels(full))
    n = len(full.series.bars)
    idx = [int(n * k / (n_probe + 1)) for k in range(1, n_probe + 1)]
    bad = []
    for i in idx:
        ts = full.series.bars[i].end_ts
        cut = [b for b in series.bars if b.end_ts <= ts]
        trunc = build_symbol_frame(BarSeries(symbol, base, cut),
                                   sorted({base, tf})).frames[tf]
        j = len(trunc.series.bars) - 1
        if trunc.series.bars[j].end_ts != ts:
            continue
        lt, st_ = _labels(trunc), None
        st_ = _bars_since_onset(lt)
        if lt[j] != labs_full[i] or st_[j] != since_full[i]:
            bad.append(dict(i=i, ts=str(ts), full=(labs_full[i], since_full[i]),
                            truncated=(lt[j], st_[j])))
    return dict(symbol=symbol, tf=tf, probes=len(idx), mismatches=len(bad),
                detail=bad[:5],
                verdict="CAUSAL" if not bad else "LOOK-AHEAD DETECTED")


def _dirn(d: str) -> Direction:
    return LONG if d == "UP" else SHORT


# ---------------------------------------------------------------- conditions
@condition("ltf_break_first", "leadlag",
           description="Lower timeframe has broken structure in a direction the "
                       "higher timeframe has not yet confirmed")
def _ltf_break_first(snap: FeatureSnapshot, tf: int) -> ConditionResult:
    lo = snap.tf(tf)
    htf = HTF_OF.get(tf)
    hi = snap.tf(htf) if htf else None
    if lo is None or hi is None:
        return ConditionResult.no()
    d_lo, d_hi = break_dir(lo), break_dir(hi)
    if d_lo is None or d_hi == d_lo:
        return ConditionResult.no()
    return ConditionResult.yes(_dirn(d_lo),
                               f"{tf}m broke {d_lo}, {htf}m not yet", d_lo)


def _make_fresh(k: int):
    def fn(snap: FeatureSnapshot, tf: int) -> ConditionResult:
        base = _ltf_break_first(snap, tf)
        if not base.triggered:
            return ConditionResult.no()
        lo = snap.tf(tf)
        h = _history(snap.symbol, tf).get(lo.bar.end_ts)
        if h is None or h[1] is None or h[1] > k:
            return ConditionResult.no()
        return ConditionResult.yes(base.direction,
                                   f"{base.detail}, break is {h[1]} bars old", h[1])
    return fn


for _k in (0, 1, 3, 6):
    condition(f"ltf_break_first_fresh{_k}", "leadlag",
              description=f"ltf_break_first, and the lower-TF break is at most "
                          f"{_k} bars old")(_make_fresh(_k))


def _make_late(n: int, fresh: int = 4):
    """HTF has NOW confirmed a break the LTF made at least ``n`` LTF bars ago.

    ``fresh`` bounds how stale the HTF confirmation may be, in LTF bars; without
    it the condition is "HTF is in a break", which is the incumbent, not a
    late entry.
    """
    def fn(snap: FeatureSnapshot, tf: int) -> ConditionResult:
        lo, htf = snap.tf(tf), HTF_OF.get(tf)
        hi = snap.tf(htf) if htf else None
        if lo is None or hi is None:
            return ConditionResult.no()
        d_hi = break_dir(hi)
        if d_hi is None:
            return ConditionResult.no()
        hh = _history(snap.symbol, htf).get(hi.bar.end_ts)
        if hh is None or hh[0] != d_hi or hh[1] is None:
            return ConditionResult.no()
        if hh[1] * htf > fresh * tf:                 # HTF confirmation gone stale
            return ConditionResult.no()
        lh = _history(snap.symbol, tf).get(lo.bar.end_ts)
        if lh is None or lh[0] != d_hi or lh[1] is None:
            return ConditionResult.no()              # LTF is not in that break
        if lh[1] < n:                                # LTF did not lead by enough
            return ConditionResult.no()
        return ConditionResult.yes(_dirn(d_hi),
                                   f"{htf}m confirmed {d_hi}; {tf}m led by {lh[1]} bars",
                                   lh[1])
    return fn


for _n in (0, 2, 4, 8, 16):
    condition(f"htf_confirms_late{_n}", "leadlag",
              description=f"Higher timeframe has just confirmed a break the lower "
                          f"timeframe made at least {_n} bars ago")(_make_late(_n))


@condition("htf_broken_now", "leadlag",
           description="Higher timeframe is itself in a confirmed break "
                       "(confirmation-entry control, no lead requirement)")
def _htf_broken(snap: FeatureSnapshot, tf: int) -> ConditionResult:
    htf = HTF_OF.get(tf)
    hi = snap.tf(htf) if htf else None
    d = break_dir(hi)
    if d is None:
        return ConditionResult.no()
    return ConditionResult.yes(_dirn(d), f"{htf}m broken {d}", d)


@condition("ltf_and_htf_both_broken", "leadlag",
           description="Both timeframes broken the same way - full alignment control")
def _both(snap: FeatureSnapshot, tf: int) -> ConditionResult:
    lo, htf = snap.tf(tf), HTF_OF.get(tf)
    hi = snap.tf(htf) if htf else None
    d_lo, d_hi = break_dir(lo), break_dir(hi)
    if d_lo is None or d_lo != d_hi:
        return ConditionResult.no()
    return ConditionResult.yes(_dirn(d_lo), f"{tf}m and {htf}m both broken {d_lo}", d_lo)
