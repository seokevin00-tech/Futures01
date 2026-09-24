"""Incremental save for the ICT study.

``T.save`` writes a whole document, so a study built in six passes either saves
six times and keeps the last, or saves once at the end and loses everything if
the run is stopped.  This merges one section into the existing document and
rewrites it, so the file on disk is always a valid, complete-so-far study.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
import toolkit as T                                    # noqa: E402

STUDY = "ict_killzones_ote"
PATH = f"/home/user/Futures01/{T.OUT}/{STUDY}.json"

TITLE = ("ICT kill zones and Optimal Trade Entry: the windows are louder, not more "
         "directional, and OTE is fib_golden_pocket under another name")
QUESTION = ("Do ICT's time-of-day kill zones (London open, NY open, Silver Bullet, "
            "London close, Asian range) contain distinguishable market behaviour, do "
            "they improve expectancy as strategy filters, and is Optimal Trade Entry "
            "anything other than the already-refuted fib_golden_pocket?")


def load() -> dict:
    if os.path.exists(PATH):
        return json.load(open(PATH))
    return {"findings": {}, "headline": "(in progress)", "caveats": []}


def put(section: str, payload, *, headline: str = None, caveats=None) -> str:
    doc = load()
    f = doc.get("findings", {})
    f[section] = payload
    if headline:
        doc["headline"] = headline
    cav = list(doc.get("caveats", []))
    for c in (caveats or []):
        if c not in cav:
            cav.append(c)
    return T.save(STUDY, TITLE, QUESTION, f, doc.get("headline", "(in progress)"), cav)
