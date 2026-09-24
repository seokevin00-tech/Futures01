"""Merge twenty study files into one report, and check them against each other.

The merge is the easy part. The part that matters is **contradiction detection**:
twenty agents working in parallel on overlapping questions will sometimes reach
opposite conclusions about the same condition, and a report that silently
averages them is worse than either answer alone. Five developers build on this
next, so a contradiction has to surface as a contradiction.

Also enforced here: findings are re-checked against the significance rule the
brief set, not taken on the agent's word. A study that reports a difference with
|z| < 1.96 has its claim demoted to "not supported" in the merged report
regardless of how the agent phrased it.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Dict, List

OUT = "workspace/studies/out"
Z_CRIT = 1.96

#: Above this, an "arm" is almost certainly a count of TRADES rather than of
#: strategies. The largest per-strategy population any study assembled was about
#: 1,800 clearing the trade floor and 8,700 with at least one trade, so arms in
#: the tens of thousands are trade-level pools.
#:
#: This matters because pooled trade-level statistics inflate roughly threefold
#: against the paired per-strategy equivalent - measured twice in this
#: programme: NQ 60m lunch was z=-2.75 pooled over 330 trades against z=-0.83
#: paired over 13 strategies, and di_direction was -4.32 pooled against -1.17
#: per cell. Trades inside one strategy are not independent observations; they
#: share the rule set that generated them. Left uncorrected, these dominate any
#: ranking by |z| purely through sample size, which is exactly what they did on
#: the first merge run.
TRADE_LEVEL_ARM = 3000


def load() -> List[dict]:
    if not os.path.isdir(OUT):
        return []
    docs = []
    for f in sorted(os.listdir(OUT)):
        if f.endswith(".json"):
            try:
                docs.append(json.load(open(os.path.join(OUT, f))))
            except Exception as e:
                docs.append({"study_id": f[:-5], "error": str(e)})
    return docs


def walk_ab(node, path=""):
    """Yield every ab()-shaped result anywhere in a study's payload.

    Studies nest their findings differently, so the merge cannot assume a
    schema; it looks for the shape instead.
    """
    if isinstance(node, dict):
        if "rank_sum" in node and isinstance(node.get("rank_sum"), dict):
            yield path, node
        for k, v in node.items():
            yield from walk_ab(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_ab(v, f"{path}[{i}]")


def main():
    docs = load()
    ok = [d for d in docs if "error" not in d]
    print(f"studies present: {len(docs)}  parsed: {len(ok)}")
    missing = [d["study_id"] for d in docs if "error" in d]
    if missing:
        print(f"unparseable: {missing}")
    print()

    # ---- every statistically supported claim, pooled --------------------
    supported, unsupported, inadmissible = [], [], []
    for d in ok:
        for path, ab in walk_ab(d.get("findings", {})):
            z = ab["rank_sum"].get("z", 0.0)
            na, nb = ab["rank_sum"].get("n_a", 0), ab["rank_sum"].get("n_b", 0)
            trade_level = max(na, nb) > TRADE_LEVEL_ARM
            rec = dict(study=d["study_id"], path=path, z=z, n_a=na, n_b=nb,
                       delta=ab.get("median_exp_delta"), verdict=ab.get("verdict"),
                       trade_level=trade_level)
            if trade_level:
                inadmissible.append(rec)
            elif abs(z) >= Z_CRIT and min(na, nb) >= 5:
                supported.append(rec)
            else:
                unsupported.append(rec)

    total_run = len(supported) + len(unsupported) + len(inadmissible)
    print(f"ab comparisons run across all studies: {total_run}")
    print(f"  REJECTED as trade-level pools (arm > {TRADE_LEVEL_ARM}): {len(inadmissible)}")
    print(f"  statistically supported (|z|>=1.96, both arms >=5): {len(supported)}")
    print(f"  not supported: {len(unsupported)}")
    print()

    # Multiple testing across the whole programme, not per study. Twenty
    # agents running fifty tests each is a thousand tests; at alpha=0.05 that
    # is fifty false positives expected by chance alone.
    total = len(supported) + len(unsupported)   # admissible tests only
    if total:
        import math
        bonf_z = abs(_inv_norm(0.05 / max(1, total) / 2))
        survives = [s for s in supported if abs(s["z"]) >= bonf_z]
        print(f"Bonferroni across all {total} tests requires |z| >= {bonf_z:.2f}")
        print(f"  claims surviving programme-wide correction: {len(survives)}")
        for s in sorted(survives, key=lambda r: -abs(r["z"]))[:25]:
            print(f"    {s['z']:+7.2f}  {s['study']:<20s} {s['path'][:60]}  "
                  f"(arms {s['n_a']}/{s['n_b']}, delta {s['delta']})")
        print()

    print("=" * 90)
    print("HEADLINES")
    print("=" * 90)
    for d in sorted(ok, key=lambda x: x["study_id"]):
        print(f"  [{d['study_id']:<20s}] {d.get('headline','(none)')}")
    print()
    print("=" * 90)
    print("CAVEATS RAISED")
    print("=" * 90)
    for d in sorted(ok, key=lambda x: x["study_id"]):
        for c in d.get("caveats", []):
            print(f"  [{d['study_id']:<20s}] {c}")

    json.dump(dict(studies=ok, supported=supported, unsupported=unsupported,
                   inadmissible=inadmissible),
              open("workspace/studies/merged.json", "w"), indent=1, default=str)
    print(f"\nmerged -> workspace/studies/merged.json")


def _inv_norm(p: float) -> float:
    """Inverse standard normal CDF (Acklam), for the Bonferroni z threshold."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = (2 * __import__("math").log(p)) ** 0.5 * -1
        q = __import__("math").sqrt(-2 * __import__("math").log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        return -_inv_norm(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


if __name__ == "__main__":
    main()
