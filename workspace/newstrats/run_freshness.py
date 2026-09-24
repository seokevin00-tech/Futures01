"""Driver for the structure-freshness / distance-to-invalidation study."""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace")

import toolkit as T
from newstrats import freshness as F
from futures_agents.backtest.costs import CostModel
from futures_agents.config import get_contract
from futures_agents.data.bars import BarSeries

SYMBOLS = ["MGC", "MES", "NQ", "MNQ", "MCL"]
TFS = [60, 240]
NSLICE = 3
SCRATCH = "/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad"


def cost_fn_for(symbol: str):
    spec = get_contract(symbol)
    cm = CostModel(spec)

    def f(risk_points: float) -> float:
        return cm.cost_in_r(risk_points)
    return f


_TAPE_CACHE = {}


def tapes(symbol: str, tf: int):
    """(tape, slices) where slices are lists of (label, StructureTape)."""
    key = (symbol, tf)
    if key in _TAPE_CACHE:
        return _TAPE_CACHE[key]
    full = T._series(symbol, tf, None)
    bars = full.bars
    n = len(bars)
    edge = n // NSLICE
    out = []
    for s in range(NSLICE):
        lo = s * edge
        hi = n if s == NSLICE - 1 else (s + 1) * edge
        sl = bars[lo:hi]
        if len(sl) < 200:
            continue
        out.append((f"s{s}", F.StructureTape(sl)))
    _TAPE_CACHE[key] = out
    return out


def stouffer(zs):
    zs = [z for z in zs if z is not None]
    if not zs:
        return 0.0
    return round(sum(zs) / math.sqrt(len(zs)), 3)


def sign_test(diffs):
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    if n == 0:
        return dict(pos=0, neg=0, z=0.0, p=1.0)
    z = (pos - n / 2) / math.sqrt(n / 4)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return dict(pos=pos, neg=neg, n=n, z=round(z, 3), p=round(p, 4))


def welch(a, b):
    """z on the difference in mean R between two independent trade lists."""
    if len(a) < 8 or len(b) < 8:
        return None
    ma, mb = st.mean(a), st.mean(b)
    va, vb = st.pvariance(a), st.pvariance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return round((ma - mb) / se, 3) if se else None


def prop_z(w1, n1, w2, n2):
    if min(n1, n2) < 8:
        return None
    p1, p2 = w1 / n1, w2 / n2
    p = (w1 + w2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return round((p1 - p2) / se, 3) if se else None
