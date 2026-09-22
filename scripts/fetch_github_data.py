#!/usr/bin/env python3
"""Pull real intraday bars from a public GitHub dataset into ``data/``.

**Why this source.** This container's egress policy refuses every market-data
host - Yahoo, Databento, Stooq, Alpha Vantage all answer 403 at the gateway -
but ``raw.githubusercontent.com`` is allowlisted. FutureSharks/financial-data
publishes one-minute OHLCV as plain CSV, so it is reachable where the APIs
are not.

**What this data is, precisely.** Oanda index and commodity CFD feeds, not CME
futures. NAS100_USD tracks the Nasdaq-100 index that MNQ is written on;
SPX500_USD tracks the S&P 500 that MES is written on; XAU_USD is spot gold,
which MGC settles against. The price *dynamics* - trend persistence,
volatility clustering, session structure, the shape of a pullback - are the
underlying's, and those are what every condition in this library reads.

What it is NOT, and what that costs:

* **Not the futures price.** No basis, no carry, no roll. Absolute levels
  differ from the contract and gaps at the roll are absent. Anything keyed to
  an exact futures price is not being tested here.
* **Not CME volume.** Volume is Oanda's CFD tick count, so it is a relative
  measure only. `relative_volume_high`, `volume_surge` and `volume_not_thin`
  still work as distributional tests; nothing that needs true traded size does.
* **No bid/ask split**, so delta stays estimated and the whole `orderflow`
  group remains unvalidated. That was already true of the synthetic data.
* **Different session boundaries.** A CFD trades around the clock without
  CME's daily settlement break.

It is real market structure, which is the thing the generator could not
provide. It is not a substitute for CME data when that becomes reachable.

Timestamps in the source are UTC; they are written out with an explicit UTC
offset so the loader cannot mistake them for Eastern.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

BASE = ("https://raw.githubusercontent.com/FutureSharks/financial-data/master/"
        "pyfinancialdata/data/currencies/oanda/{inst}/{year}/"
        "oanda-{inst}-{year}-{month}.csv")

#: Our contract -> the Oanda instrument whose underlying it is written on.
INSTRUMENTS: Dict[str, str] = {
    "MNQ": "NAS100_USD",     # Nasdaq-100
    "MES": "SPX500_USD",     # S&P 500
    "MGC": "XAU_USD",        # spot gold
    "MCL": "WTICO_USD",      # WTI crude
    "M2K": "US2000_USD",     # Russell 2000
}

OUT_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def fetch_month(inst: str, year: int, month: int, *, retries: int = 3) -> List[dict]:
    url = BASE.format(inst=inst, year=year, month=month)
    req = urllib.request.Request(url, headers={"User-Agent": "futures-agents/1.0"})
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as fh:
                text = fh.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []                      # month genuinely absent
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
    else:
        print(f"    {year}-{month:02d}: {last}", file=sys.stderr)
        return []

    rows: List[dict] = []
    for r in csv.DictReader(text.splitlines()):
        try:
            # Source timestamps are UTC and carry no offset. Stamp them
            # explicitly - the loader reads a naive timestamp as Eastern, which
            # would shift every bar by four or five hours and silently move
            # every session boundary the strategies depend on.
            ts = datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc)
            o, h, l, c = (float(r["open"]), float(r["high"]),
                          float(r["low"]), float(r["close"]))
        except (KeyError, ValueError):
            continue
        if not (h >= max(o, c) and l <= min(o, c)):
            continue                           # malformed bar, drop it
        rows.append({"timestamp": ts.isoformat(), "open": o, "high": h,
                     "low": l, "close": c, "volume": float(r.get("volume") or 0)})
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="*", default=["MNQ", "MES", "MGC"])
    ap.add_argument("--years", nargs="*", type=int, default=[2019, 2020])
    ap.add_argument("--out", default="data")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    failed = []
    for sym in args.symbols:
        inst = INSTRUMENTS.get(sym.upper())
        if inst is None:
            print(f"  {sym}: no mapping", file=sys.stderr)
            failed.append(sym)
            continue
        rows: List[dict] = []
        for year in args.years:
            for month in range(1, 13):
                got = fetch_month(inst, year, month)
                rows.extend(got)
                print(f"    {sym} {year}-{month:02d}: {len(got):>6,} bars", flush=True)
        if not rows:
            failed.append(sym)
            continue
        rows.sort(key=lambda r: r["timestamp"])
        path = os.path.join(args.out, f"{sym.upper()}_1m.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"  {sym} <- {inst}: {len(rows):,} bars  "
              f"{rows[0]['timestamp'][:10]} .. {rows[-1]['timestamp'][:10]}  -> {path}",
              flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
