"""ICT's *sequence*: liquidity sweep -> market structure shift -> retrace into the imbalance.

The library has already measured the three ingredients separately.  ``prior_day_sweep``,
``overnight_sweep`` and ``session_extreme_sweep`` never once produced a 20-trade rule set;
``break_of_structure`` scored +4.4..+5.0 in sample at 240m and was retracted after failing out
of sample; ``fvg_nearby`` has never been isolated.  None of that tests the claim ICT actually
makes, which is not about any one of those conditions but about the **order** they occur in.

So this module builds the chain explicitly rather than as a conjunction of independently
sampled signals.  A conjunction asks "are all three true right now"; the chain asks "did A
happen, then B, then C, in that order, inside a stated number of bars" - and those are not the
same question.  The library's combinator can only express the first, which is a large part of
why the sweep family starved: ``min_signals=2`` demanded two independent conditions coincide
on one bar, and a sweep and its consequence never do.

Causality
---------
Every stage is built forward in confirmation order and nothing consults a bar later than the
one it is stored against:

* swings come from :func:`find_swings` and are filtered on ``confirmed_index``, never on
  ``index`` - a 3-bar fractal at bar *i* is not knowable until *i+3*;
* the reference level a sweep runs is built only from bars strictly before the sweep bar;
* the swing the MSS breaks is chosen among swings that both *formed before the sweep* and are
  *confirmed by the bar the break is tested on*;
* a three-bar FVG spanning *k-1..k+1* is treated as knowable at *k+1*;
* ``market_structure().events`` is NOT used (defect D27: its ``ref_high``/``ref_low`` are
  running extremes that never reset, so 5,000 MGC 60m bars yield 30 events).  Everything here
  works from the confirmed-swing arrays, and each chain resets its own reference - which is
  precisely the bug D27 describes, fixed by construction.

Entries are signalled at the **close** of the retrace bar and the engine fills at the next
bar's open.  That is deliberately worse than the ICT prescription (a resting limit at the gap
edge); an unfilled-limit backtest is the single easiest way to manufacture this result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from futures_agents.indicators.structure import Swing, find_swings
from futures_agents.schema import Direction
from futures_agents.strategies.base import Condition, ConditionKind, ConditionResult
from futures_agents.timeutil import is_rth, trading_day

LONG, SHORT = Direction.LONG, Direction.SHORT

#: Our own registry.  Deliberately NOT ``library.CONDITIONS`` - six agents sharing one dict is
#: how name collisions and cross-study contamination happen.
CONDITIONS: Dict[str, Condition] = {}

#: ``name -> [evaluated, fired]``.  Firing rates are reported before any performance number.
STATS: Dict[str, List[int]] = {}


def condition(name: str, group: str = "ict_sequence", *,
              kind: ConditionKind = ConditionKind.SIGNAL,
              description: str = "", warmup: int = 60):
    def deco(fn):
        CONDITIONS[name] = Condition(name=name, group=group, fn=fn, kind=kind,
                                     description=description or (fn.__doc__ or "").strip(),
                                     warmup_bars=warmup)
        return fn
    return deco


def get(name: str) -> Condition:
    return CONDITIONS[name]


def reset_stats() -> None:
    STATS.clear()


def firing_rates() -> Dict[str, dict]:
    return {k: {"evaluated": v[0], "fired": v[1],
                "rate": round(v[1] / v[0], 6) if v[0] else None}
            for k, v in sorted(STATS.items())}


def _tally(name: str, fired: bool) -> None:
    v = STATS.setdefault(name, [0, 0])
    v[0] += 1
    v[1] += int(fired)


# ==========================================================================
# Configuration
# ==========================================================================

@dataclass(frozen=True)
class Cfg:
    """Every knob that changes what counts as the sequence.

    Parameterised rather than hard-coded because an effect that exists only at one setting of
    ``max_gap`` is an artefact, and the only way to show that is to sweep it.
    """
    max_gap: int = 5            # bars allowed between the sweep and the MSS
    retrace_window: int = 10    # bars allowed between the MSS and the retrace touch
    min_pen_atr: float = 0.0    # how far past the level price must trade, in ATR
    reclaim_bars: int = 0       # 0 = the sweep bar must itself close back inside
    require_fvg: bool = True    # the MSS leg must leave a three-bar imbalance
    fvg_min_atr: float = 0.0    # minimum gap height, in ATR
    swing_left: int = 3
    swing_right: int = 3
    pools: Tuple[str, ...] = ("PDH", "PDL", "ONH", "ONL", "SESH", "SESL",
                              "SWH", "SWL")
    label: str = "base"


# ==========================================================================
# Causal reference levels
# ==========================================================================

def _atr(bars, n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(bars)
    if not bars:
        return out
    trs: List[float] = []
    prev_close = bars[0].close
    run = None
    for i, b in enumerate(bars):
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        prev_close = b.close
        trs.append(tr)
        if i == n - 1:
            run = sum(trs) / n
        elif i >= n:
            run = (run * (n - 1) + tr) / n
        out[i] = run
    return out


def _levels(bars) -> List[Dict[str, Optional[float]]]:
    """Per-bar liquidity pools, each built only from bars STRICTLY BEFORE that bar.

    Session state is carried incrementally rather than recomputed, both for speed and because
    a recompute-from-scratch helper is the classic place a future bar sneaks in.
    """
    out: List[Dict[str, Optional[float]]] = []
    cur_day = None
    prev_rth_h = prev_rth_l = None      # previous trading day's RTH extremes
    rth_h = rth_l = None                # today's RTH extremes so far
    on_h = on_l = None                  # today's pre-RTH extremes
    for b in bars:
        d = trading_day(b.ts)
        if d != cur_day:
            if rth_h is not None:
                prev_rth_h, prev_rth_l = rth_h, rth_l
            cur_day, rth_h, rth_l, on_h, on_l = d, None, None, None, None
        out.append({"PDH": prev_rth_h, "PDL": prev_rth_l,
                    "ONH": on_h, "ONL": on_l,
                    "SESH": rth_h, "SESL": rth_l})
        if is_rth(b.ts):
            rth_h = b.high if rth_h is None else max(rth_h, b.high)
            rth_l = b.low if rth_l is None else min(rth_l, b.low)
        else:
            on_h = b.high if on_h is None else max(on_h, b.high)
            on_l = b.low if on_l is None else min(on_l, b.low)
    return out


def _confirmed_swings(bars, cfg: Cfg) -> Tuple[List[Swing], List[Swing]]:
    sw = find_swings(bars, cfg.swing_left, cfg.swing_right)
    return ([s for s in sw if s.is_high], [s for s in sw if not s.is_high])


def _latest_by_index(swings: List[Swing], n: int) -> List[Optional[Swing]]:
    """``tbl[k]`` = the most recent swing whose FORMATION index is ``<= k``.

    A fractal confirms at ``index + right``, so "confirmed by bar ``at``" is exactly
    "formation index ``<= at - right``" - which turns every visibility question in this module
    into one array lookup, and makes the confirmation lag impossible to forget.
    """
    tbl: List[Optional[Swing]] = [None] * max(1, n)
    cur = None
    j = 0
    sw = sorted(swings, key=lambda s: s.index)
    for k in range(n):
        while j < len(sw) and sw[j].index <= k:
            cur = sw[j]
            j += 1
        tbl[k] = cur
    return tbl


# ==========================================================================
# The chain
# ==========================================================================

@dataclass
class Chain:
    """One attempt at the sequence, with the bar each stage became knowable."""
    side: Direction
    sweep_i: int          # bar at which the sweep became KNOWABLE (the reclaim close)
    pool: str
    level: float
    penetration_atr: float
    ext_i: int = -1       # bar that made the extreme; == sweep_i when reclaim is same-bar
    mss_i: Optional[int] = None
    mss_ref: Optional[float] = None
    fvg_i: Optional[int] = None
    fvg_top: Optional[float] = None
    fvg_bot: Optional[float] = None
    entry_i: Optional[int] = None
    dead_i: Optional[int] = None
    gap: Optional[int] = None        # mss_i - sweep_i
    lag: Optional[int] = None        # entry_i - mss_i


@dataclass
class Built:
    """Everything one (symbol, timeframe, config) produces."""
    symbol: str
    tf: int
    n_bars: int
    ts: List[object]
    sweeps: List[Chain]
    chains: List[Chain]                     # sweeps that reached at least the MSS
    wrong: List[Chain]                      # MSS-then-sweep control
    mss_only: List[Tuple[int, Direction]]   # fresh structure breaks, no sweep required
    mss_chain: List[Chain]                  # MSS -> FVG -> retrace, no sweep required
    census: dict


def _reclaim(bars, i: int, L: float, high_side: bool, extra: int,
             n: int) -> Optional[int]:
    """Bar at which price closed back through ``L`` - the bar the sweep became knowable.

    A run that never closes back inside is a breakout, not a sweep, and returning ``None``
    for it is the whole distinction.
    """
    for j in range(i, min(i + 1 + extra, n)):
        if (bars[j].close < L) if high_side else (bars[j].close > L):
            return j
    return None


def _find_fvg(bars, lo: int, hi: int, side: Direction, atr: List[Optional[float]],
              cfg: Cfg) -> Optional[Tuple[int, float, float]]:
    """Most recent three-bar imbalance inside ``[lo, hi]`` that is knowable by ``hi``.

    Bearish (for a SHORT): ``bars[k-1].low > bars[k+1].high`` - price displaced down so fast
    that nothing traded in between.  The gap is knowable at ``k+1``, so ``k+1 <= hi``.
    """
    best = None
    for k in range(max(1, lo), min(hi, len(bars) - 1)):
        if k + 1 > hi:
            break
        p, nx = bars[k - 1], bars[k + 1]
        a = atr[k] or 0.0
        if side is SHORT and p.low > nx.high:
            if a and (p.low - nx.high) < cfg.fvg_min_atr * a:
                continue
            best = (k, p.low, nx.high)           # top, bottom
        elif side is LONG and nx.low > p.high:
            if a and (nx.low - p.high) < cfg.fvg_min_atr * a:
                continue
            best = (k, nx.low, p.high)
    return best


def build(symbol: str, tf: int, bars, cfg: Cfg = Cfg()) -> Built:
    """Detect every stage of the sequence over one bar series."""
    bars = list(bars)
    n = len(bars)
    atr = _atr(bars)
    lv = _levels(bars)
    highs, lows = _confirmed_swings(bars, cfg)
    HI, LO = _latest_by_index(highs, n), _latest_by_index(lows, n)
    right = cfg.swing_right

    def last_swing(tbl, at: int, before: Optional[int] = None) -> Optional[Swing]:
        """Most recent swing CONFIRMED by ``at`` that FORMED before ``before``."""
        k = at - right
        if before is not None:
            k = min(k, before - 1)
        return tbl[k] if 0 <= k < n else None

    # ---- stage 1: sweeps -------------------------------------------------
    sweeps: List[Chain] = []
    for i in range(max(cfg.swing_left + cfg.swing_right + 1, 20), n):
        b = bars[i]
        a = atr[i - 1] or 0.0
        pool_prices: List[Tuple[str, float, bool]] = []      # name, price, is_high_side
        for name in cfg.pools:
            if name in ("SWH", "SWL"):
                continue
            p = lv[i].get(name)
            if p is not None:
                pool_prices.append((name, p, name.endswith("H")))
        if "SWH" in cfg.pools:
            s = last_swing(HI, i, before=i)
            if s is not None:
                pool_prices.append(("SWH", s.price, True))
        if "SWL" in cfg.pools:
            s = last_swing(LO, i, before=i)
            if s is not None:
                pool_prices.append(("SWL", s.price, False))

        for name, L, is_high in pool_prices:
            pen = cfg.min_pen_atr * a
            if is_high:
                if b.high <= L + pen:
                    continue
                r = _reclaim(bars, i, L, True, cfg.reclaim_bars, n)
                if r is not None:
                    sweeps.append(Chain(SHORT, r, name, L,
                                        round((b.high - L) / a, 3) if a else 0.0,
                                        ext_i=i))
            else:
                if b.low >= L - pen:
                    continue
                r = _reclaim(bars, i, L, False, cfg.reclaim_bars, n)
                if r is not None:
                    sweeps.append(Chain(LONG, r, name, L,
                                        round((L - b.low) / a, 3) if a else 0.0,
                                        ext_i=i))

    # Several pools can be run by one bar.  Keep one chain per (bar, side): the deepest
    # penetration, so the census counts *events* and not *levels*.
    dedup: Dict[Tuple[int, Direction], Chain] = {}
    for c in sweeps:
        k = (c.sweep_i, c.side)
        if k not in dedup or c.penetration_atr > dedup[k].penetration_atr:
            dedup[k] = c
    sweeps = sorted(dedup.values(), key=lambda c: c.sweep_i)

    # ---- stage 2 + 3 + 4: MSS, imbalance, retrace ------------------------
    chains: List[Chain] = []
    for c in sweeps:
        i = c.sweep_i
        e = c.ext_i if c.ext_i >= 0 else i
        ext = bars[e].high if c.side is SHORT else bars[e].low
        for j in range(i + 1, min(i + 1 + cfg.max_gap, n)):
            # the setup dies if price simply carries on through the swept level
            if c.side is SHORT and bars[j].close > ext:
                c.dead_i = j
                break
            if c.side is LONG and bars[j].close < ext:
                c.dead_i = j
                break
            ref = (last_swing(LO, j, before=i) if c.side is SHORT
                   else last_swing(HI, j, before=i))
            if ref is None:
                continue
            broke = (bars[j].close < ref.price if c.side is SHORT
                     else bars[j].close > ref.price)
            if broke:
                c.mss_i, c.mss_ref, c.gap = j, ref.price, j - i
                break
        if c.mss_i is None:
            continue
        chains.append(c)
        j = c.mss_i
        fv = _find_fvg(bars, e, j, c.side, atr, cfg)
        if fv is not None:
            c.fvg_i, c.fvg_top, c.fvg_bot = fv
        if cfg.require_fvg and fv is None:
            continue
        # ---- stage 4: retrace back into the imbalance --------------------
        if fv is not None:
            touch = c.fvg_bot if c.side is SHORT else c.fvg_top
            for m in range(j + 1, min(j + 1 + cfg.retrace_window, n)):
                if c.side is SHORT and bars[m].close > ext:
                    c.dead_i = m
                    break
                if c.side is LONG and bars[m].close < ext:
                    c.dead_i = m
                    break
                hit = (bars[m].high >= touch if c.side is SHORT
                       else bars[m].low <= touch)
                if hit:
                    c.entry_i, c.lag = m, m - j
                    break

    # ---- the MSS-alone arm ----------------------------------------------
    mss_only: List[Tuple[int, Direction]] = []
    for j in range(max(cfg.swing_left + cfg.swing_right + 1, 20), n):
        lo = last_swing(LO, j, before=j)
        hi = last_swing(HI, j, before=j)
        if lo is not None and bars[j].close < lo.price <= bars[j - 1].close:
            mss_only.append((j, SHORT))
        elif hi is not None and bars[j].close > hi.price >= bars[j - 1].close:
            mss_only.append((j, LONG))

    # ---- MSS -> imbalance -> retrace, with NO sweep required -------------
    # Same window lengths as the full chain so the ablation is matched: the FVG is looked for
    # over ``max_gap`` bars ending at the MSS, exactly the span the full chain searches.
    mss_chain: List[Chain] = []
    for j, side in mss_only:
        c = Chain(side, j, "NONE", bars[j].close, 0.0, mss_i=j, gap=0)
        fv = _find_fvg(bars, j - cfg.max_gap, j, side, atr, cfg)
        if fv is None:
            mss_chain.append(c)
            continue
        c.fvg_i, c.fvg_top, c.fvg_bot = fv
        touch = c.fvg_bot if side is SHORT else c.fvg_top
        for m in range(j + 1, min(j + 1 + cfg.retrace_window, n)):
            hit = (bars[m].high >= touch if side is SHORT else bars[m].low <= touch)
            if hit:
                c.entry_i, c.lag = m, m - j
                break
        mss_chain.append(c)

    # ---- the WRONG-ORDER control ----------------------------------------
    # Identical ingredients, identical window lengths, the first two swapped: structure shifts
    # FIRST and the sweep comes after.  If this performs the same, "the sequence" is not a
    # mechanism, it is two conditions that happen to co-occur.
    sweep_at: Dict[Tuple[int, Direction], Chain] = {(c.sweep_i, c.side): c for c in sweeps}
    wrong: List[Chain] = []
    for j, side in mss_only:
        for k in range(j + 1, min(j + 1 + cfg.max_gap, n)):
            sc = sweep_at.get((k, side))
            if sc is None:
                continue
            c = Chain(side, k, sc.pool, sc.level, sc.penetration_atr,
                      mss_i=j, mss_ref=None, gap=k - j)
            fv = _find_fvg(bars, j, k, side, atr, cfg)
            if fv is not None:
                c.fvg_i, c.fvg_top, c.fvg_bot = fv
            if cfg.require_fvg and fv is None:
                wrong.append(c)
                break
            if fv is not None:
                touch = c.fvg_bot if side is SHORT else c.fvg_top
                for m in range(k + 1, min(k + 1 + cfg.retrace_window, n)):
                    hit = (bars[m].high >= touch if side is SHORT
                           else bars[m].low <= touch)
                    if hit:
                        c.entry_i, c.lag = m, m - k
                        break
            wrong.append(c)
            break

    census = {
        "bars": n,
        "s1_sweeps": len(sweeps),
        "s2_sweep_then_mss": len(chains),
        "s3_with_imbalance": sum(1 for c in chains if c.fvg_i is not None),
        "s4_retraced_entry": sum(1 for c in chains if c.entry_i is not None),
        "died_before_mss": sum(1 for c in sweeps if c.mss_i is None and c.dead_i is not None),
        "mss_only": len(mss_only),
        "mss_chain_entries": sum(1 for c in mss_chain if c.entry_i is not None),
        "wrong_order_pairs": len(wrong),
        "wrong_order_entries": sum(1 for c in wrong if c.entry_i is not None),
        "rate_s1": round(len(sweeps) / n, 5) if n else None,
        "rate_s2": round(len(chains) / n, 5) if n else None,
        "rate_s4": round(sum(1 for c in chains if c.entry_i is not None) / n, 5) if n else None,
        "gaps": _hist([c.gap for c in chains if c.gap is not None]),
        "lags": _hist([c.lag for c in chains if c.lag is not None]),
        "by_pool": _tally_pool(sweeps, chains),
    }
    return Built(symbol.upper(), int(tf), n, [b.ts for b in bars], sweeps, chains,
                 wrong, mss_only, mss_chain, census)


def _hist(vals: Sequence[int]) -> dict:
    h: Dict[int, int] = {}
    for v in vals:
        h[v] = h.get(v, 0) + 1
    return {str(k): h[k] for k in sorted(h)}


def _tally_pool(sweeps, chains) -> dict:
    out: Dict[str, Dict[str, int]] = {}
    for c in sweeps:
        out.setdefault(c.pool, {"sweeps": 0, "mss": 0, "entries": 0})["sweeps"] += 1
    for c in chains:
        d = out.setdefault(c.pool, {"sweeps": 0, "mss": 0, "entries": 0})
        d["mss"] += 1
        d["entries"] += int(c.entry_i is not None)
    return out


# ==========================================================================
# Arms - one registered condition per ablation stage
# ==========================================================================
#
# Each arm is a bar -> direction map looked up by TIMESTAMP, never by index, so a slice, a
# window and the full series can never silently read one another's indices.

_MAPS: Dict[Tuple[str, int, str], Dict[object, Direction]] = {}

#: The six ablation arms.  ``full`` is the claim; the rest exist to take it apart.
ARMS = ("sweep_only", "mss_only", "sweep_mss", "mss_fvg_retrace", "full", "wrong_order")


def register(built: Built, suffix: str = "") -> Dict[str, int]:
    """Install the arm maps for one (symbol, timeframe) and report each arm's bar count."""
    ts = built.ts
    m: Dict[str, Dict[object, Direction]] = {a: {} for a in ARMS}

    def put(arm: str, i: Optional[int], side: Direction) -> None:
        if i is None or not (0 <= i < len(ts)):
            return
        k = ts[i]
        if k in m[arm] and m[arm][k] is not side:
            m[arm][k] = Direction.NEUTRAL      # two live chains disagree -> no trade
        else:
            m[arm].setdefault(k, side)

    for c in built.sweeps:
        put("sweep_only", c.sweep_i, c.side)
    for i, side in built.mss_only:
        put("mss_only", i, side)
    for c in built.chains:
        put("sweep_mss", c.mss_i, c.side)
        put("full", c.entry_i, c.side)
    for c in built.mss_chain:
        put("mss_fvg_retrace", c.entry_i, c.side)
    for c in built.wrong:
        put("wrong_order", c.entry_i, c.side)

    for a in ARMS:
        _MAPS[(built.symbol, built.tf, a + suffix)] = {
            k: v for k, v in m[a].items() if v is not Direction.NEUTRAL}
    return {a: len(_MAPS[(built.symbol, built.tf, a + suffix)]) for a in ARMS}


def clear() -> None:
    _MAPS.clear()


def _lookup(snap, tf, key: str) -> Optional[Direction]:
    s = snap.tf(tf)
    if s is None:
        return None
    return _MAPS.get((snap.symbol.upper(), int(tf), key), {}).get(s.bar.ts)


def _make(key: str, desc: str):
    @condition(key, description=desc)
    def _fn(snap, tf, _k=key):
        d = _lookup(snap, tf, _k)
        if d is None:
            _tally(_k, False)
            return ConditionResult.no()
        _tally(_k, True)
        return ConditionResult.yes(d, _k, None, 1.0)
    return _fn


_make("sweep_only", "Ran an obvious high/low and closed back inside it")
_make("mss_only", "First close beyond the last confirmed swing - structure shift, no sweep")
_make("sweep_mss", "Sweep, then a structure shift against it within N bars - entry at the break")
_make("mss_fvg_retrace", "Structure shift, then retrace into its imbalance - NO sweep required")
_make("full", "Sweep -> structure shift -> retrace into the imbalance (the ICT sequence)")
_make("wrong_order", "Structure shift FIRST, then the sweep, then the retrace (control)")


def register_variant(built: Built, suffix: str) -> Dict[str, int]:
    """Install a parameter variant under its own condition names (``full@gap8`` etc.)."""
    counts = register(built, suffix)
    for a in ARMS:
        nm = a + suffix
        if nm not in CONDITIONS:
            _make(nm, f"{a} variant {suffix}")
    return counts
