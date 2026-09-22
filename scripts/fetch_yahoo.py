#!/usr/bin/env python3
"""Pull futures bars from Yahoo Finance into the format the loader expects.

Run this where the network allows it - your own machine is fine - and copy the
CSVs into ``data/``. Every sweep then reads real bars with no code change,
because :func:`futures_agents.data.loader.load_symbol` looks for
``data/{SYMBOL}_{minutes}m.csv`` before it falls back to the generator.

    python scripts/fetch_yahoo.py                # 5m bars, the default set
    python scripts/fetch_yahoo.py --interval 1m  # finer, but only ~7 days
    python scripts/fetch_yahoo.py --symbols MGC

**On the contract mapping.** Yahoo does not list the micros. It lists the
full-size continuous front month, and the micro trades the same index at a
different multiplier - MNQ is one-twentieth of NQ, MES one-tenth of ES, MGC
one-tenth of GC. The price series is the one the micro prints, so the bars are
right and only the dollar value of a point differs. ``ContractSpec`` already
carries each micro's own ``point_value`` and cost model, so nothing downstream
needs to know where the bars came from. Volume is the full-size contract's and
is NOT rescaled: treat it as a relative measure, which is all any condition in
this system uses it for.

**What Yahoo will and will not give you.** Intraday history is capped by
interval: roughly 7 days at 1m, 60 days at 5m, 730 days at 1h. For walk-forward
to mean anything you want months, so 5m is usually the right trade-off. There
is no true tick data and therefore no real bid/ask volume - the loader will mark
delta as estimated, and an order-flow strategy validated on estimated delta has
not been validated.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

#: Micro contract -> the Yahoo continuous front-month it tracks.
YAHOO_SYMBOLS: Dict[str, str] = {
    "MNQ": "NQ=F",     # E-mini Nasdaq-100
    "MES": "ES=F",     # E-mini S&P 500
    "MGC": "GC=F",     # Gold
    "MCL": "CL=F",     # WTI crude
    "M2K": "RTY=F",    # Russell 2000
    "MYM": "YM=F",     # Dow
}

#: Interval -> the longest range Yahoo will serve for it.
MAX_RANGE: Dict[str, str] = {
    "1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d",
    "30m": "60d", "60m": "730d", "1h": "730d", "1d": "max",
}

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def fetch(ticker: str, interval: str, range_: str, *, retries: int = 3) -> dict:
    url = (CHART_URL.format(ticker=urllib.parse.quote(ticker))
           + f"?interval={interval}&range={range_}&includePrePost=true")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as fh:
                return json.loads(fh.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise SystemExit(f"{ticker}: could not fetch after {retries} tries - {last}")


def to_rows(payload: dict) -> List[dict]:
    """Yahoo's column-major chart payload -> row dicts, dropping empty bars."""
    try:
        result = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        err = (payload.get("chart") or {}).get("error")
        raise SystemExit(f"unexpected payload from Yahoo: {err or payload}")

    stamps = result.get("timestamp") or []
    q = result["indicators"]["quote"][0]
    rows: List[dict] = []
    for i, ts in enumerate(stamps):
        o, h, l, c = (q["open"][i], q["high"][i], q["low"][i], q["close"][i])
        # Yahoo emits null OHLC for halted or untraded intervals. A bar with no
        # trade is not a bar; carrying it forward would invent a flat print that
        # every indicator would then average in.
        if None in (o, h, l, c):
            continue
        v = q.get("volume", [None] * len(stamps))[i] or 0
        rows.append({
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        })
    return rows


def write_csv(rows: Sequence[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="*", default=["MNQ", "MES", "MGC"],
                    help="micro symbols to fetch (default: MNQ MES MGC)")
    ap.add_argument("--interval", default="5m", choices=sorted(MAX_RANGE),
                    help="bar interval (default: 5m - the best history/detail trade-off)")
    ap.add_argument("--range", dest="range_", default=None,
                    help="history to request; defaults to the maximum for the interval")
    ap.add_argument("--out", default="data", help="output directory (default: data)")
    args = ap.parse_args(argv)

    range_ = args.range_ or MAX_RANGE[args.interval]
    minutes = {"1h": 60}.get(args.interval,
                             int(args.interval.rstrip("mdh")) if args.interval[0].isdigit() else 1)

    failed = []
    for sym in args.symbols:
        ticker = YAHOO_SYMBOLS.get(sym.upper())
        if ticker is None:
            print(f"  {sym}: no Yahoo mapping - known: {', '.join(sorted(YAHOO_SYMBOLS))}",
                  file=sys.stderr)
            failed.append(sym)
            continue
        try:
            rows = to_rows(fetch(ticker, args.interval, range_))
        except SystemExit as exc:
            print(f"  {sym}: {exc}", file=sys.stderr)
            failed.append(sym)
            continue
        if not rows:
            print(f"  {sym}: Yahoo returned no usable bars", file=sys.stderr)
            failed.append(sym)
            continue
        path = os.path.join(args.out, f"{sym.upper()}_{minutes}m.csv")
        write_csv(rows, path)
        print(f"  {sym:4s} <- {ticker:6s}  {len(rows):>7,} bars  "
              f"{rows[0]['timestamp'][:16]} .. {rows[-1]['timestamp'][:16]}  -> {path}")

    if failed:
        print(f"\nfailed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\nDone. The loader picks these up automatically - load_symbol() checks "
          f"{args.out}/SYMBOL_{minutes}m.csv before falling back to synthetic data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
