"""Incremental save under the single study id ``s_geometry``.

Re-reads what is already on disk, merges one more section in and writes it
back, so the study file is complete after every step rather than only at the
end. An agent that is stopped mid-programme still leaves a usable study.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "workspace/studies")
import toolkit as T   # noqa: E402

STUDY = "s_geometry"
PATH = f"{T.OUT}/{STUDY}.json"

TITLE = "Swing geometry: leg expansion, retracement depth and swing symmetry"
QUESTION = ("Does the SHAPE of the swings - legs lengthening or contracting, "
            "pullbacks shallowing or deepening, time spent impulsing versus "
            "retracing - add anything to the plain HH/HL vs LH/LL label that "
            "structure_trend already provides?")


def put(section: str, data, headline: str = "", caveats=()) -> str:
    payload = {}
    head, cav = "", []
    if os.path.exists(PATH):
        doc = json.load(open(PATH))
        payload = doc.get("findings", {}) or {}
        head, cav = doc.get("headline", ""), list(doc.get("caveats", []))
    payload[section] = data
    if headline:
        head = headline
    for c in caveats:
        if c not in cav:
            cav.append(c)
    return T.save(STUDY, TITLE, QUESTION, payload, head, cav)
