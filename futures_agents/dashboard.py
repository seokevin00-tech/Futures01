"""The browser counterpart of :mod:`futures_agents.alerts` - a live dashboard.

The terminal renderer already solves the hard part of a live callout: making it
impossible to miss. This module does the same job for a browser, for the case
where the desk is watching a page rather than a scrollback, and where the tab is
very often *not* the foreground window.

Three constraints shape everything here:

1. **One file, no network.** The output is a single HTML document with every
   byte of CSS and JavaScript inline. No CDN, no fonts, no images, no fetch. It
   opens from ``file://`` on a machine with the network unplugged, which is the
   only way it can be trusted to work during a market event.

2. **The flash has to reach peripheral vision and a background tab.** A page
   that only changes some text is useless when the trader is looking at a chart
   on another monitor. So a new callout pulses a full-viewport colour wash,
   alternates ``document.title``, and redraws the favicon on a canvas in the
   direction colour - the last of which is the only channel that survives the
   tab being in the background.

3. **Attention-grabbing must not mean unusable.** The pulse is ~2 Hz for a
   handful of cycles and then settles; the wash is a vignette so the centre of
   the page stays readable; ``prefers-reduced-motion`` gets a static border
   instead of an animation; the beep is off until the user explicitly asks for
   it (browsers block autoplay, and unrequested audio is hostile); and the
   whole flash can be turned off permanently from the page.

Colour semantics are not re-invented here. The tones are converted from the
exact xterm-256 indices in :class:`~futures_agents.alerts.Palette`, and the
cycle counts and beep counts come from :class:`~futures_agents.alerts.Priority`,
so a LONG looks the same shade of green in both renderers and pulses the same
number of times.

Every string that reaches the document goes through :func:`html.escape`, and
everything that reaches JavaScript goes through :func:`json.dumps` with ``<``,
``>`` and ``&`` escaped as well: callout prose is model-written and must never
be able to close a tag or open a script.

``dashboard_html`` is a pure function of its arguments (pass ``now=`` to make it
fully deterministic); ``render_dashboard`` is the thin wrapper that writes it.
"""

from __future__ import annotations

import html
import json
import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .alerts import Palette, Priority
from .schema import Decision, fmt_price, fmt_prices
from .timeutil import et_label, et_stamp, et_stamp_short, now_et, to_et

__all__ = ["dashboard_html", "render_dashboard", "DEFAULT_TITLE", "DEFAULT_PATH"]

DEFAULT_TITLE = "Futures Desk - Live Callouts"
DEFAULT_PATH = "reports/dashboard.html"

#: History is capped so a long session cannot produce a multi-megabyte file that
#: a browser struggles to lay out. The overflow is reported, never hidden.
_MAX_HISTORY_ROWS = 200


# --------------------------------------------------------------------------
# Colour: the terminal palette, converted
# --------------------------------------------------------------------------

def _xterm_hex(index: int) -> str:
    """Hex for an xterm-256 colour index.

    The browser side must not guess at "about the same green". Converting the
    indices the terminal renderer already uses guarantees the two agree.
    """
    if index < 16:
        basic = (0x000000, 0x800000, 0x008000, 0x808000, 0x000080, 0x800080,
                 0x008080, 0xC0C0C0, 0x808080, 0xFF0000, 0x00FF00, 0xFFFF00,
                 0x0000FF, 0xFF00FF, 0x00FFFF, 0xFFFFFF)
        return f"#{basic[index]:06x}"
    if index < 232:
        i = index - 16
        levels = (0, 95, 135, 175, 215, 255)
        return f"#{levels[i // 36]:02x}{levels[(i // 6) % 6]:02x}{levels[i % 6]:02x}"
    grey = 8 + (index - 232) * 10
    return f"#{grey:02x}{grey:02x}{grey:02x}"


def _ansi_index(code: str) -> int:
    """``"\\x1b[48;5;46m"`` -> ``46``."""
    return int(code.rstrip("m").rsplit(";", 1)[-1])


def _rgb_triplet(hex_colour: str) -> str:
    """``"#00ff00"`` -> ``"0,255,0"`` for use inside ``rgba(var(--x), a)``."""
    h = hex_colour.lstrip("#")
    return f"{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)}"


@dataclass(frozen=True)
class _Tone:
    """One decision's visual identity, shared by the badge, wash and favicon."""

    key: str          # CSS class suffix: long | short | notrade
    label: str        # the terminal's own label, reused verbatim
    glyph: str        # the terminal's own glyph
    colour: str       # flash colour (the terminal's background)
    ink: str          # legible text on `colour` (the terminal's foreground)
    deep: str         # the terminal's inverted background, for the static ring
    rgb: str
    shape: str        # favicon mark: up | down | flat
    cycles: int       # pulses, from Priority.flash_cycles
    tones_hz: Tuple[float, ...]   # beeps, count from Priority.bells


_PRIORITY_FOR_DECISION = {
    Decision.LONG: Priority.LONG,
    Decision.SHORT: Priority.SHORT,
    Decision.NO_TRADE: Priority.NO_TRADE,
}

#: Rising pair for a buy, falling pair for a sell - direction is audible without
#: having to read anything. Sliced to Priority.bells, so NO TRADE is silent in
#: the browser exactly as it is in the terminal.
_BEEP_HZ = {
    Decision.LONG: (880.0, 1320.0),
    Decision.SHORT: (660.0, 440.0),
    Decision.NO_TRADE: (520.0, 392.0),
}

_SHAPES = {Decision.LONG: "up", Decision.SHORT: "down", Decision.NO_TRADE: "flat"}


def _tone_for(decision: Decision) -> _Tone:
    priority = _PRIORITY_FOR_DECISION[decision]
    style = Palette.style_for(priority)
    _, deep_bg = Palette.INVERTED[priority]
    colour = _xterm_hex(_ansi_index(style.bg))
    return _Tone(
        key=decision.name.lower().replace("_", ""),
        label=style.label,
        glyph=style.glyph,
        colour=colour,
        ink=_xterm_hex(_ansi_index(style.fg)),
        deep=_xterm_hex(_ansi_index(deep_bg)),
        rgb=_rgb_triplet(colour),
        shape=_SHAPES[decision],
        cycles=priority.flash_cycles,
        tones_hz=_BEEP_HZ[decision][:priority.bells],
    )


def _decision_tones() -> List[_Tone]:
    return [_tone_for(d) for d in (Decision.LONG, Decision.SHORT, Decision.NO_TRADE)]


#: Tones that are not decisions: analyst NEUTRAL and the news-risk ladder. Kept
#: in the same shape so one CSS generator covers every coloured element.
_EXTRA_TONES = (
    ("neutral", "#8a99b0", "#0b0e13", "#3a4658"),
    ("ok", "#00af5f", "#000000", "#00331c"),          # xterm 35
    ("hot", "#ff5f00", "#000000", "#5f1f00"),         # xterm 202
)


def _tone_class(value: Any) -> str:
    """CSS tone class for a direction / decision / news-risk string."""
    s = str(getattr(value, "value", value) or "").strip().upper().replace("_", " ")
    if s in ("LONG", "BUY", "UP", "BULLISH"):
        return "t-long"
    if s in ("SHORT", "SELL", "DOWN", "BEARISH"):
        return "t-short"
    if s in ("NO TRADE", "MODERATE", "WARNING"):
        return "t-notrade"
    if s in ("LOW", "APPROVED", "DONE"):
        return "t-ok"
    if s in ("HIGH",):
        return "t-hot"
    if s in ("BLACKOUT", "HALT", "FAILED", "VETO"):
        return "t-short"
    return "t-neutral"


# --------------------------------------------------------------------------
# Escaping and formatting
# --------------------------------------------------------------------------

def _e(value: Any, default: str = "-") -> str:
    """HTML-escape anything. This is the only way text enters the document."""
    value = getattr(value, "value", value)      # Enum -> its string value
    if value is None:
        return default
    text = str(value)
    if not text.strip():
        return default
    return html.escape(text, quote=True)


def _js(obj: Any) -> str:
    """JSON for embedding in a ``<script>`` block.

    ``json.dumps`` alone is not safe inside HTML: a string containing
    ``</script>`` would end the block early. Escaping the three markup
    characters as ``\\uXXXX`` keeps the value identical to JavaScript while
    making it inert to the HTML parser.
    """
    return (json.dumps(obj, ensure_ascii=True, sort_keys=True)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _money(value: Any, *, signed: bool = False) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if (signed and v > 0) else ("-" if v < 0 else "")
    return f"{sign}${abs(v):,.2f}"


def _pct(value: Any, decimals: int = 1) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "-"


def _r(value: Any, *, signed: bool = False, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{v:+.{decimals}f}R" if signed else f"{v:.{decimals}f}R"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _d(obj: Any) -> Dict[str, Any]:
    """Dict view of a schema object, a mapping, or anything with ``to_dict()``.

    Rendering from the dict form rather than from attributes means the dashboard
    can be fed either live objects or the JSON artefacts the agents publish,
    with no second code path to keep in step.
    """
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict())
        except Exception:
            pass
    try:
        return dict(vars(obj))
    except TypeError:
        return {}


# --------------------------------------------------------------------------
# Small HTML fragments
# --------------------------------------------------------------------------

def _field(label: str, value: str, *, cls: str = "") -> str:
    klass = f" {cls}" if cls else ""
    return (f'<div class="field{klass}"><span class="k">{_e(label)}</span>'
            f'<span class="v">{value}</span></div>')


def _prose(label: str, text: Any, *, cls: str = "") -> str:
    klass = f" {cls}" if cls else ""
    return (f'<div class="prose{klass}"><span class="k">{_e(label)}</span>'
            f'<p>{_e(text)}</p></div>')


def _badge(text: Any, tone_class: str, *, big: bool = False) -> str:
    size = " big" if big else ""
    return f'<span class="badge {tone_class}{size}">{_e(text)}</span>'


def _meter(fraction: float, *, cls: str = "", label: str = "") -> str:
    pct = _clamp01(fraction) * 100.0
    aria = f' aria-label="{_e(label)}"' if label else ""
    return (f'<div class="meter {cls}" role="img"{aria}>'
            f'<div class="meter-fill" style="width:{pct:.1f}%"></div></div>')


def _gauge(fraction: float, *, cls: str = "", label: str = "") -> str:
    """A wider meter with 25/50/70% tick marks - the de-risk ladder's rungs."""
    pct = _clamp01(fraction) * 100.0
    aria = f' aria-label="{_e(label)}"' if label else ""
    ticks = "".join(f'<i style="left:{t:.0f}%"></i>' for t in (25, 50, 70, 85))
    return (f'<div class="gauge {cls}" role="img"{aria}>'
            f'<div class="gauge-fill" style="width:{pct:.1f}%"></div>{ticks}</div>')


def _severity_class(fraction: float) -> str:
    if fraction >= 0.70:
        return "sev-bad"
    if fraction >= 0.50:
        return "sev-warn"
    if fraction >= 0.25:
        return "sev-mild"
    return "sev-ok"


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

def _callout_panel(callout: Dict[str, Any], tone: Optional[_Tone]) -> str:
    """The latest callout, rendered field-for-field as the specification lists
    them. Nothing is omitted when empty - a blank Reason to Avoid is itself
    information, and a missing field would look like a rendering bug."""
    if not callout:
        return (
            '<section class="panel" id="fd-latest">'
            '<h2>Latest callout</h2>'
            '<p class="empty">No callouts yet this session. The page will pulse '
            'when one is issued.</p></section>'
        )

    decision = Decision.coerce(callout.get("decision"))
    klass = _tone_class(decision.value)
    entry = _entry_text(callout)
    stamp = _stamp_of(callout)

    header = (
        f'<div class="callout-head {klass}">'
        f'{_badge(tone.label if tone else decision.value, klass, big=True)}'
        f'<span class="sym">{_e(callout.get("symbol"))}</span>'
        f'<span class="head-entry">{_e(entry)}</span>'
        f'<span class="head-stamp">{_e(stamp)}</span>'
        f'</div>'
    )

    conf = _clamp01(_f(callout.get("confidence")))
    fields = [
        _field("Symbol", _e(callout.get("symbol"))),
        _field("Direction", _badge(decision.value, klass)),
        _field("Entry", f'<span class="num">{_e(entry)}</span>'),
        _field("Stop Loss", f'<span class="num">{_e(fmt_price(callout.get("stop_loss")))}</span>'),
        _field("Targets", f'<span class="num">{_e(fmt_prices(callout.get("targets") or []))}</span>'),
        _field("Expected R/R", f'<span class="num">{_e(_r(callout.get("expected_reward_risk")))}</span>'),
        _field("Contracts", f'<span class="num">{_e(callout.get("contracts", 0))}</span>'),
        _field("Dollar Risk", f'<span class="num risk">{_e(_money(callout.get("dollar_risk")))}</span>'),
        _field("Account Risk %", f'<span class="num">{_e(_pct(callout.get("account_risk_pct"), 2))}</span>'),
        _field("Strategy", _e(_strategy_text(callout))),
        _field("Timeframe", _e(callout.get("timeframe"))),
        _field("Market Regime", _e(callout.get("market_regime"))),
        _field("News Risk", _badge(callout.get("news_risk") or "NONE",
                                   _tone_class(callout.get("news_risk")))),
        _field("Historical Win Rate", f'<span class="num">{_e(_pct(callout.get("historical_win_rate")))}</span>'),
        _field("Historical Expectancy",
               f'<span class="num">{_e(_r(callout.get("historical_expectancy_r"), signed=True))}</span>'),
        _field("Max Historical Drawdown",
               f'<span class="num">{_e(_r(callout.get("max_historical_drawdown_r")))}</span>'),
        _field("Analyst Agreement", _e(callout.get("analyst_agreement"))),
        _field("Confidence",
               f'<span class="num">{conf:.2f}</span>'
               + _meter(conf, cls="inline", label="confidence")),
    ]

    prose = [
        _prose("Trade Invalidation", callout.get("trade_invalidation")),
        _prose("Reason for Entry", callout.get("reason_for_entry"), cls="good"),
        _prose("Reason to Avoid", callout.get("reason_to_avoid"), cls="bad"),
    ]
    if callout.get("decision_rationale"):
        prose.append(_prose("Decision Rationale", callout.get("decision_rationale")))

    # The risk layer's own numbers: dollars at risk and what a stop does to the
    # remaining buffer. The specification requires this stated explicitly.
    effect = (
        '<div class="subpanel"><h3>If this stops out</h3><div class="fields tight">'
        + _field("Account Equity", _e(_money(callout.get("account_equity"))))
        + _field("Remaining Drawdown Buffer", _e(_money(callout.get("remaining_drawdown_buffer"))))
        + _field("Buffer Consumed if Stopped", _e(_pct(callout.get("buffer_consumed_if_stopped_pct"))))
        + _field("Remaining Daily Loss Budget", _e(_money(callout.get("remaining_daily_loss_budget"))))
        + '</div></div>'
    )

    note = ""
    if (not _f(callout.get("historical_win_rate"))
            and not _f(callout.get("historical_expectancy_r"))):
        note = ('<p class="note">No measured history attached to this callout - '
                'a setup with no backtested track record has no demonstrated edge.</p>')

    veto = _veto_block(callout)
    return (
        f'<section class="panel {klass}" id="fd-latest">'
        f'<h2>Latest callout <span class="sub">{_e(callout.get("callout_id"))}</span></h2>'
        f'{header}'
        f'<div class="fields">{"".join(fields)}</div>'
        f'{note}{"".join(prose)}{veto}{effect}'
        f'</section>'
    )


def _entry_text(callout: Dict[str, Any]) -> str:
    zone = callout.get("entry_zone")
    if isinstance(zone, (list, tuple)) and len(zone) == 2 and zone[0] is not None:
        return f"{fmt_price(zone[0])} - {fmt_price(zone[1])}"
    return fmt_price(callout.get("entry"))


def _strategy_text(callout: Dict[str, Any]) -> str:
    strategy = callout.get("strategy") or ""
    group = callout.get("strategy_group") or ""
    if strategy and group:
        return f"{strategy} [{group}]"
    return strategy or group or "-"


def _veto_block(callout: Dict[str, Any]) -> str:
    """Risk-layer vetoes and warnings, when a RiskAssessment is attached."""
    risk = _d(callout.get("risk_assessment"))
    if not risk:
        return ""
    rows = []
    for label, items, cls in (("Vetoes", risk.get("vetoes") or [], "bad"),
                              ("Warnings", risk.get("warnings") or [], "warn"),
                              ("Notes", risk.get("notes") or [], "")):
        if not items:
            continue
        lis = "".join(f"<li>{_e(i)}</li>" for i in items)
        rows.append(f'<div class="risk-list {cls}"><span class="k">{_e(label)}</span>'
                    f'<ul>{lis}</ul></div>')
    approved = risk.get("approved")
    head = _badge("RISK APPROVED" if approved else "RISK NOT APPROVED",
                  "t-ok" if approved else "t-short")
    extra = "".join([
        _field("Expected Cost", _e(_money(risk.get("expected_cost")))),
        _field("R/R After Costs", _e(_r(risk.get("reward_risk_after_costs")))),
        _field("Stop Distance", _e(f'{_f(risk.get("stop_distance_points")):.2f} pts / '
                                   f'{_f(risk.get("stop_distance_ticks")):.0f} ticks')),
        _field("Risk Multiplier", _e(f'{_f(risk.get("risk_multiplier_applied"), 1.0):.2f}x')),
    ])
    return (f'<div class="subpanel"><h3>Risk layer {head}</h3>'
            f'<div class="fields tight">{extra}</div>{"".join(rows)}</div>')


def _account_panel(account: Any) -> str:
    acct = _d(account)
    if not acct:
        return ('<section class="panel"><h2>Account</h2>'
                '<p class="empty">No account state supplied.</p></section>')

    equity = _f(acct.get("equity"))
    peak = _f(acct.get("peak_equity"))
    failure = _f(acct.get("failure_equity"))
    distance = _f(acct.get("remaining_drawdown"))
    usable = _f(acct.get("usable_buffer"))
    consumed = _clamp01(_f(acct.get("buffer_consumed_pct")))
    daily = _f(acct.get("daily_pnl"))
    budget_left = _f(acct.get("remaining_daily_loss_budget"))
    limits = _account_limits(account, acct)
    day = _d(acct.get("day"))
    failed = bool(acct.get("has_failed"))

    budget_used = 0.0
    if limits["daily_loss_limit"] > 0:
        budget_used = _clamp01(1.0 - budget_left / limits["daily_loss_limit"])

    status = (_badge("ACCOUNT FAILED", "t-short") if failed
              else _badge("ACTIVE", "t-ok"))

    positions = acct.get("open_positions") or []
    pos_rows = ""
    if positions:
        cells = []
        for p in positions:
            pd = _d(p)
            cells.append(
                f'<tr><td>{_e(pd.get("symbol"))}</td>'
                f'<td>{_badge(pd.get("direction"), _tone_class(pd.get("direction")))}</td>'
                f'<td class="num">{_e(pd.get("contracts"))}</td>'
                f'<td class="num">{_e(fmt_price(pd.get("entry")))}</td>'
                f'<td class="num">{_e(fmt_price(pd.get("stop")))}</td>'
                f'<td class="num risk">{_e(_money(pd.get("dollar_risk")))}</td></tr>')
        pos_rows = (
            '<div class="subpanel"><h3>Open positions</h3>'
            '<table class="table compact"><thead><tr><th>Symbol</th><th>Dir</th>'
            '<th class="num">Qty</th><th class="num">Entry</th><th class="num">Stop</th>'
            '<th class="num">Risk</th></tr></thead><tbody>'
            + "".join(cells) + '</tbody></table></div>')

    return (
        f'<section class="panel"><h2>Account {status}</h2>'
        f'<div class="bignum {"bad" if failed else ""}">{_e(_money(equity))}'
        f'<span class="bignum-sub">equity &middot; peak {_e(_money(peak))}</span></div>'
        f'<div class="fields tight">'
        + _field("Failure Threshold", _e(_money(failure)))
        + _field("Distance to Failure", f'<span class="num">{_e(_money(distance))}</span>')
        + _field("Usable Buffer", f'<span class="num">{_e(_money(usable))}</span>')
        + _field("Drawdown", _e(f'{_money(_f(acct.get("drawdown")))} '
                                f'({_pct(acct.get("drawdown_pct"))})'))
        + _field("Risk Multiplier", _e(f'{_f(acct.get("derisk_multiplier"), 1.0):.2f}x'))
        + '</div>'
        f'<div class="gauge-block"><span class="k">Buffer consumed '
        f'<b>{_e(_pct(consumed, 0))}</b> of {_e(_money(limits["usable_full"]))}</span>'
        f'{_gauge(consumed, cls=_severity_class(consumed), label="drawdown buffer consumed")}</div>'
        f'<div class="fields tight">'
        + _field("Today P&amp;L",
                 f'<span class="num {"good" if daily > 0 else ("bad" if daily < 0 else "")}">'
                 f'{_e(_money(daily, signed=True))}</span>')
        + _field("Loss Budget Left", f'<span class="num">{_e(_money(budget_left))}</span>')
        + _field("Trades Today", _e(day.get("trades_taken", 0)))
        + _field("Consecutive Losses", _e(acct.get("consecutive_losses", 0)))
        + _field("Open Risk", f'<span class="num risk">{_e(_money(acct.get("open_risk")))}</span>')
        + '</div>'
        f'<div class="gauge-block"><span class="k">Daily loss budget used '
        f'<b>{_e(_pct(budget_used, 0))}</b> of {_e(_money(limits["daily_loss_limit"]))}</span>'
        f'{_gauge(budget_used, cls=_severity_class(budget_used), label="daily loss budget used")}</div>'
        f'{pos_rows}'
        f'</section>'
    )


def _account_limits(account: Any, acct: Dict[str, Any]) -> Dict[str, float]:
    """Config-derived denominators, recovered from the state alone when the
    config is not reachable (e.g. when rendering from a published artefact)."""
    cfg = getattr(account, "config", None)
    if cfg is None and isinstance(account, Mapping):
        cfg = account.get("config")
    cfgd = _d(cfg)

    daily_limit = cfgd.get("daily_loss_limit")
    if daily_limit is None:
        # remaining = max(0, limit + min(0, pnl))  =>  limit = remaining - min(0, pnl)
        daily_limit = _f(acct.get("remaining_daily_loss_budget")) + max(0.0, -_f(acct.get("daily_pnl")))

    max_dd = cfgd.get("max_total_drawdown")
    if max_dd is None:
        max_dd = max(0.0, _f(acct.get("peak_equity")) - _f(acct.get("failure_equity")))

    reserve_pct = _f(cfgd.get("protected_buffer_pct"), 0.0)
    usable_full = _f(max_dd) * (1.0 - reserve_pct)
    if usable_full <= 0:
        # Fall back to the live buffer so the gauge still has a sane scale.
        usable_full = max(_f(acct.get("usable_buffer")), 1.0)
    return {"daily_loss_limit": _f(daily_limit),
            "max_total_drawdown": _f(max_dd),
            "usable_full": usable_full}


def _analyst_panel(predictions: Sequence[Any]) -> str:
    """The three analysts side by side, with disagreement made loud.

    Disagreement is information the decision layer is required to keep, so the
    dashboard refuses to average it away: the split is stated in words, drawn as
    a stacked bar, and repeated in each card's edge colour.
    """
    preds = [_d(p) for p in (predictions or [])]
    preds = [p for p in preds if p]
    if not preds:
        return ('<section class="panel"><h2>Analyst predictions</h2>'
                '<p class="empty">No analyst predictions supplied.</p></section>')

    directions = [str(p.get("direction") or "NEUTRAL").upper() for p in preds]
    counts = Counter(directions)
    distinct = len(counts)
    total = len(directions)
    top, top_n = counts.most_common(1)[0]

    if distinct == 1:
        verdict = f"UNANIMOUS {top} ({top_n}/{total})"
        verdict_class, banner_class = _tone_class(top), "agree"
    else:
        parts = " / ".join(f"{n} {d}" for d, n in counts.most_common())
        verdict = f"SPLIT: {parts}"
        verdict_class, banner_class = "t-notrade", "disagree"

    confs = [_clamp01(_f(p.get("confidence"))) for p in preds]
    spread = (max(confs) - min(confs)) if confs else 0.0

    segments = "".join(
        f'<span class="seg {_tone_class(d)}" style="width:{(n / total) * 100:.1f}%">'
        f'{_e(d)} {n}</span>'
        for d, n in counts.most_common())

    cards = []
    for pred in preds:
        direction = str(pred.get("direction") or "NEUTRAL").upper()
        klass = _tone_class(direction)
        zone = pred.get("entry_zone")
        zone_text = (f"{fmt_price(zone[0])} - {fmt_price(zone[1])}"
                     if isinstance(zone, (list, tuple)) and len(zone) == 2 and zone[0] is not None
                     else "-")
        targets = [pred.get("target_1"), pred.get("target_2"), pred.get("target_3")]
        conf = _clamp01(_f(pred.get("confidence")))
        name = pred.get("analyst_name") or f'Analyst {pred.get("analyst_id") or "?"}'
        confluences = pred.get("supporting_confluences") or []
        invalidations = pred.get("invalidation_conditions") or []
        cards.append(
            f'<article class="analyst {klass}">'
            f'<header><span class="who">{_e(name)}</span>'
            f'{_badge(direction, klass)}</header>'
            f'<div class="spec">{_e(pred.get("specialisation"))}</div>'
            f'<div class="fields tight">'
            + _field("Entry Zone", f'<span class="num">{_e(zone_text)}</span>')
            + _field("Stop", f'<span class="num">{_e(fmt_price(pred.get("stop")))}</span>')
            + _field("Targets",
                     f'<span class="num">{_e(fmt_prices([t for t in targets if t is not None]))}</span>')
            + _field("Expected R/R", f'<span class="num">{_e(_r(pred.get("expected_reward_risk")))}</span>')
            + _field("Time Horizon", _e(pred.get("time_horizon")))
            + _field("Confidence",
                     f'<span class="num">{conf:.2f}</span>{_meter(conf, cls="inline")}')
            + '</div>'
            f'{_prose("Primary Reason", pred.get("primary_reason"))}'
            f'{_list_block("Confluences", confluences)}'
            f'{_list_block("Invalidation", invalidations)}'
            f'{_prose("Would change mind if", pred.get("would_change_mind_if"))}'
            f'</article>')

    return (
        f'<section class="panel"><h2>Analyst predictions</h2>'
        f'<div class="verdict {banner_class}">{_badge(verdict, verdict_class, big=True)}'
        f'<span class="spread">confidence spread {spread:.2f}</span></div>'
        f'<div class="split-bar">{segments}</div>'
        f'<div class="analysts">{"".join(cards)}</div>'
        f'</section>'
    )


def _list_block(label: str, items: Sequence[Any]) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{_e(i)}</li>" for i in items)
    return f'<div class="listy"><span class="k">{_e(label)}</span><ul>{lis}</ul></div>'


def _news_panel(news: Any) -> str:
    data = _news_dict(news)
    if not data:
        return ('<section class="panel"><h2>News risk</h2>'
                '<p class="empty">No news context supplied.</p></section>')

    risk = str(data.get("risk") or "NONE").upper()
    klass = _tone_class(risk)
    minutes = data.get("minutes_to_next_high_impact")
    minutes_text = "-" if minutes is None else f"{_f(minutes):.0f} min"

    events = []
    for ev in (data.get("upcoming_events") or [])[:8]:
        e = _d(ev)
        events.append(
            f'<tr><td>{_e(e.get("release_time_et") or e.get("timestamp_et"))}</td>'
            f'<td>{_e(e.get("title"))}</td>'
            f'<td>{_badge(e.get("impact") or "LOW", _tone_class(e.get("impact")))}</td>'
            f'<td>{_e(e.get("category"))}</td></tr>')
    events_table = ""
    if events:
        events_table = (
            '<div class="subpanel"><h3>Upcoming events</h3>'
            '<table class="table compact"><thead><tr><th>Time ET</th><th>Event</th>'
            '<th>Impact</th><th>Category</th></tr></thead><tbody>'
            + "".join(events) + '</tbody></table></div>')

    cross = data.get("cross_market") or {}
    cross_rows = "".join(_field(k, _e(v)) for k, v in list(cross.items())[:8])
    cross_block = (f'<div class="subpanel"><h3>Cross market</h3>'
                   f'<div class="fields tight">{cross_rows}</div></div>') if cross_rows else ""

    blackout = ('<p class="note bad">BLACKOUT - entries are blocked inside this '
                'event window.</p>') if risk == "BLACKOUT" else ""

    return (
        f'<section class="panel {klass}"><h2>News risk</h2>'
        f'<div class="bignum">{_badge(risk, klass, big=True)}</div>{blackout}'
        f'<div class="fields tight">'
        + _field("Macro Bias", _badge(data.get("macro_bias") or "NEUTRAL",
                                      _tone_class(data.get("macro_bias"))))
        + _field("Bias Confidence", _e(f'{_f(data.get("macro_bias_confidence")):.2f}'))
        + _field("Sentiment", _e(data.get("sentiment")))
        + _field("Next High Impact", _e(minutes_text))
        + '</div>'
        f'{_prose("Headlines", data.get("headline_summary"))}'
        f'{events_table}{cross_block}'
        f'</section>'
    )


def _news_dict(news: Any) -> Dict[str, Any]:
    """Accept a NewsContext, a dict, a NewsRisk enum, or a bare string."""
    if news is None:
        return {}
    if isinstance(news, Mapping):
        return dict(news)
    value = getattr(news, "value", None)
    if isinstance(news, str) or (value is not None and not hasattr(news, "to_dict")):
        return {"risk": value if value is not None else str(news)}
    return _d(news)


def _history_panel(callouts: Sequence[Dict[str, Any]]) -> str:
    if not callouts:
        return ""
    rows = []
    overflow = max(0, len(callouts) - _MAX_HISTORY_ROWS)
    for c in callouts[:_MAX_HISTORY_ROWS]:
        decision = Decision.coerce(c.get("decision"))
        klass = _tone_class(decision.value)
        rows.append(
            f'<tr class="{klass}"><td class="mono">{_e(_stamp_of(c, short=True))}</td>'
            f'<td>{_e(c.get("symbol"))}</td>'
            f'<td>{_badge(decision.value, klass)}</td>'
            f'<td class="num">{_e(_entry_text(c))}</td>'
            f'<td class="num">{_e(fmt_price(c.get("stop_loss")))}</td>'
            f'<td class="num">{_e(fmt_prices(c.get("targets") or []))}</td>'
            f'<td class="num">{_e(_r(c.get("expected_reward_risk")))}</td>'
            f'<td class="num">{_e(c.get("contracts", 0))}</td>'
            f'<td class="num risk">{_e(_money(c.get("dollar_risk")))}</td>'
            f'<td class="num">{_f(c.get("confidence")):.2f}</td>'
            f'<td>{_e(c.get("strategy"))}</td></tr>')
    more = (f'<tr class="more"><td colspan="11">... and {overflow} older '
            f'callouts not shown</td></tr>') if overflow else ""
    return (
        '<section class="panel"><h2>Callout history '
        f'<span class="sub">{len(callouts)} total</span></h2>'
        '<div class="scroll-x"><table class="table"><thead><tr>'
        '<th>Time ET</th><th>Symbol</th><th>Decision</th><th class="num">Entry</th>'
        '<th class="num">Stop</th><th class="num">Targets</th><th class="num">R/R</th>'
        '<th class="num">Qty</th><th class="num">Risk</th><th class="num">Conf</th>'
        '<th>Strategy</th></tr></thead><tbody>'
        + "".join(rows) + more + '</tbody></table></div></section>'
    )


def _task_panel(task_board: Any) -> str:
    tasks = _task_rows(task_board)
    if not tasks:
        return ""
    counts = Counter(str(t.get("status") or "?") for t in tasks)
    glyphs = {"PENDING": "&middot;", "ASSIGNED": "&rarr;", "IN_PROGRESS": "*",
              "BLOCKED": "!", "DONE": "&#10003;", "FAILED": "&#10007;", "SKIPPED": "-"}
    chips = "".join(
        f'<span class="chip {_tone_class(k)}">{_e(k)} {v}</span>'
        for k, v in sorted(counts.items()))
    rows = []
    for t in tasks:
        status = str(t.get("status") or "?")
        rows.append(
            f'<tr><td class="mono">{glyphs.get(status, "?")} {_e(t.get("task_id"))}</td>'
            f'<td>{_e(t.get("assigned_to") or "unassigned")}</td>'
            f'<td>{_e(t.get("title"))}</td>'
            f'<td>{_badge(status, _tone_class(status))}</td>'
            f'<td class="num">{_f(t.get("duration_s")):.1f}s</td>'
            f'<td class="err">{_e(t.get("error"), default="")}</td></tr>')
    return (
        '<section class="panel"><h2>Task board</h2>'
        f'<div class="chips">{chips}</div>'
        '<div class="scroll-x"><table class="table"><thead><tr><th>Task</th><th>Owner</th>'
        '<th>Title</th><th>Status</th><th class="num">Duration</th><th>Error</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div></section>'
    )


def _task_rows(task_board: Any) -> List[Dict[str, Any]]:
    """Accept a TaskBoard, a list of Tasks, or either one's dict form."""
    if task_board is None:
        return []
    if isinstance(task_board, (list, tuple)):
        return [_d(t) for t in task_board]
    if isinstance(task_board, Mapping):
        return [_d(t) for t in (task_board.get("tasks") or [])]
    getter = getattr(task_board, "all", None)
    if callable(getter):
        try:
            return [_d(t) for t in getter()]
        except Exception:
            pass
    return [_d(t) for t in (_d(task_board).get("tasks") or [])]


def _stamp_of(callout: Dict[str, Any], *, short: bool = False) -> str:
    """Format a callout's ET timestamp, tolerating an unparseable value."""
    raw = callout.get("timestamp_et")
    if not raw:
        return "-"
    parsed = _parse_et(raw)
    if parsed is None:
        return str(raw)
    return et_stamp_short(parsed) if short else et_stamp(parsed)


def _parse_et(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return to_et(raw)
    try:
        return to_et(datetime.fromisoformat(str(raw)))
    except (TypeError, ValueError):
        return None


def _sorted_callouts(callouts: Sequence[Any]) -> List[Dict[str, Any]]:
    """Newest first.

    The caller's ordering is not assumed: sorting on the parsed timestamp means
    a list built oldest-first and one built newest-first both render correctly,
    and a callout with no usable timestamp keeps its relative position instead
    of being dropped.
    """
    rows = [_d(c) for c in (callouts or [])]
    rows = [r for r in rows if r]
    decorated = []
    for index, row in enumerate(rows):
        parsed = _parse_et(row.get("timestamp_et"))
        decorated.append((parsed is not None, parsed.timestamp() if parsed else 0.0, index, row))
    if all(d[0] for d in decorated):
        decorated.sort(key=lambda d: (d[1], d[2]), reverse=True)
    else:
        decorated.reverse()          # no usable timestamps: trust caller order
    return [d[3] for d in decorated]


# --------------------------------------------------------------------------
# Style
# --------------------------------------------------------------------------

def _tone_css() -> str:
    blocks = []
    for tone in _decision_tones():
        blocks.append(
            f".t-{tone.key}{{--tone:{tone.colour};--tone-ink:{tone.ink};"
            f"--tone-deep:{tone.deep};--tone-rgb:{tone.rgb};}}")
    for key, colour, ink, deep in _EXTRA_TONES:
        blocks.append(
            f".t-{key}{{--tone:{colour};--tone-ink:{ink};"
            f"--tone-deep:{deep};--tone-rgb:{_rgb_triplet(colour)};}}")
    return "\n".join(blocks)


_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  color-scheme:dark light;
  --bg:#0a0d13; --panel-bg:#121720; --panel-2:#1a2130;
  --line:#26304a; --line-2:#1c2436;
  --ink:#e8eef7; --ink-2:#a3b3cb; --ink-3:#6f8098;
  --good:#00d47e; --bad:#ff5b5b; --warn:#ffaf00;
  --radius:10px;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --shadow:0 1px 0 rgba(255,255,255,.03),0 10px 28px rgba(0,0,0,.45);
  --ribbon-h:46px;
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#eef1f6; --panel-bg:#ffffff; --panel-2:#f4f6fa;
    --line:#ccd4e2; --line-2:#e2e7f0;
    --ink:#0f1725; --ink-2:#44536b; --ink-3:#6b7a91;
    --good:#008a52; --bad:#c62828; --warn:#a96b00;
    --shadow:0 1px 2px rgba(16,24,40,.06),0 8px 20px rgba(16,24,40,.08);
  }
}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--ink);
  font-family:var(--sans); font-size:15px; line-height:1.45;
  -webkit-text-size-adjust:100%;
}
body.ribbon-on{padding-top:var(--ribbon-h)}
h1,h2,h3{margin:0; font-weight:650; letter-spacing:.01em}
h2{font-size:1.02rem; padding:12px 14px; border-bottom:1px solid var(--line-2);
   display:flex; align-items:center; gap:10px; flex-wrap:wrap}
h3{font-size:.82rem; text-transform:uppercase; letter-spacing:.09em;
   color:var(--ink-2); margin:0 0 8px; display:flex; align-items:center; gap:8px}
.sub{font-weight:400; font-size:.76rem; color:var(--ink-3); font-family:var(--mono)}
p{margin:.35em 0}
a{color:inherit}

/* ---------- header ---------- */
.topbar{
  position:sticky; top:0; z-index:50;
  display:flex; align-items:center; gap:14px; flex-wrap:wrap;
  padding:10px 16px; background:var(--panel-bg);
  border-bottom:1px solid var(--line); box-shadow:var(--shadow);
}
body.ribbon-on .topbar{top:var(--ribbon-h)}
.brand{font-size:1.06rem; font-weight:700; letter-spacing:.01em; margin-right:auto}
.brand small{display:block; font-weight:400; font-size:.72rem; color:var(--ink-3)}
.clock{
  display:flex; align-items:baseline; gap:8px;
  font-family:var(--mono); padding:6px 10px; border-radius:var(--radius);
  background:var(--panel-2); border:1px solid var(--line-2);
}
.clock .time{font-size:1.24rem; font-weight:700; letter-spacing:.02em}
.clock .zone{font-size:.78rem; color:var(--warn); font-weight:700}
.clock .date{font-size:.74rem; color:var(--ink-3)}
.age{font-family:var(--mono); font-size:.72rem; color:var(--ink-3);
     padding:3px 7px; border-radius:6px; border:1px solid var(--line-2)}
.age.stale{color:#000; background:var(--warn); border-color:var(--warn); font-weight:700}
.controls{display:flex; gap:8px; flex-wrap:wrap}
button{font:inherit; color:inherit}
.toggle{
  font-family:var(--mono); font-size:.78rem; cursor:pointer;
  padding:7px 11px; border-radius:var(--radius);
  background:var(--panel-2); border:1px solid var(--line); color:var(--ink-2);
}
.toggle:hover{border-color:var(--ink-3); color:var(--ink)}
.toggle:focus-visible{outline:2px solid var(--warn); outline-offset:2px}
.toggle.on{background:var(--ink); color:var(--panel-bg); border-color:var(--ink);
           font-weight:700}

/* ---------- layout ---------- */
.wrap{max-width:1500px; margin:0 auto; padding:16px}
.grid{display:grid; gap:14px; grid-template-columns:repeat(12,minmax(0,1fr))}
.col-4{grid-column:span 4} .col-5{grid-column:span 5}
.col-7{grid-column:span 7} .col-8{grid-column:span 8}
.col-12{grid-column:1/-1}
@media (max-width:1080px){ .col-4,.col-5,.col-7,.col-8{grid-column:1/-1} }
.panel{
  background:var(--panel-bg); border:1px solid var(--line);
  border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden;
  min-width:0;
}
.panel > *:not(h2){margin-left:14px; margin-right:14px}
.panel > *:last-child{margin-bottom:14px}
.empty{color:var(--ink-3); font-style:italic; padding:14px 0}
.note{font-size:.82rem; color:var(--ink-2); border-left:3px solid var(--line);
      padding:6px 10px; background:var(--panel-2); border-radius:0 6px 6px 0}
.note.bad{color:var(--bad); border-left-color:var(--bad); font-weight:600}

/* ---------- fields ---------- */
.fields{display:grid; grid-template-columns:repeat(auto-fit,minmax(255px,1fr));
        gap:2px 18px; margin-top:12px}
.fields.tight{grid-template-columns:1fr; gap:0}
.field{display:flex; align-items:baseline; gap:10px; padding:5px 0;
       border-bottom:1px dotted var(--line-2); min-width:0}
.field .k{flex:0 0 auto; font-size:.73rem; text-transform:uppercase;
          letter-spacing:.07em; color:var(--ink-3); white-space:nowrap}
.field .v{margin-left:auto; text-align:right; min-width:0; overflow-wrap:anywhere}
.num{font-family:var(--mono); font-variant-numeric:tabular-nums; font-weight:600}
.num.risk{color:var(--warn)}
.good{color:var(--good)} .bad{color:var(--bad)} .warn{color:var(--warn)}
.mono{font-family:var(--mono); font-size:.8rem}
.prose{margin-top:10px}
.prose .k{display:block; font-size:.73rem; text-transform:uppercase;
          letter-spacing:.07em; color:var(--ink-3); margin-bottom:2px}
.prose p{margin:0; overflow-wrap:anywhere}
.prose.good p{color:var(--good)} .prose.bad p{color:var(--bad)}
.listy{margin-top:8px}
.listy .k{font-size:.73rem; text-transform:uppercase; letter-spacing:.07em;
          color:var(--ink-3)}
.listy ul,.risk-list ul{margin:2px 0 0; padding-left:18px}
.listy li,.risk-list li{font-size:.86rem; overflow-wrap:anywhere}
.risk-list{margin-top:8px}
.risk-list.bad li{color:var(--bad)} .risk-list.warn li{color:var(--warn)}
.subpanel{margin-top:14px; padding:12px; background:var(--panel-2);
          border:1px solid var(--line-2); border-radius:var(--radius)}
.subpanel .fields{margin-top:0}

/* ---------- badges, meters ---------- */
.badge{
  display:inline-block; padding:3px 9px; border-radius:6px;
  background:var(--tone,var(--panel-2)); color:var(--tone-ink,var(--ink));
  font-family:var(--mono); font-size:.74rem; font-weight:700;
  letter-spacing:.06em; white-space:nowrap;
}
.badge.big{font-size:1rem; padding:6px 14px; letter-spacing:.09em}
.chips{display:flex; gap:7px; flex-wrap:wrap; margin-top:12px}
.chip{display:inline-block; padding:3px 9px; border-radius:999px;
      background:var(--tone,var(--panel-2)); color:var(--tone-ink,var(--ink));
      font-family:var(--mono); font-size:.72rem; font-weight:700}
.meter{height:7px; border-radius:4px; background:var(--panel-2);
       border:1px solid var(--line-2); overflow:hidden; min-width:60px}
.meter.inline{display:inline-block; width:74px; vertical-align:middle;
              margin-left:8px}
.meter-fill{height:100%; background:var(--ink-2)}
.gauge{position:relative; height:13px; border-radius:7px; background:var(--panel-2);
       border:1px solid var(--line-2); overflow:hidden; margin-top:5px}
.gauge-fill{height:100%; background:var(--good); transition:width .3s ease}
.gauge i{position:absolute; top:0; bottom:0; width:1px; background:var(--line);
         opacity:.85}
.gauge.sev-ok .gauge-fill{background:var(--good)}
.gauge.sev-mild .gauge-fill{background:#9acd32}
.gauge.sev-warn .gauge-fill{background:var(--warn)}
.gauge.sev-bad .gauge-fill{background:var(--bad)}
.gauge-block{margin-top:12px}
.gauge-block .k{font-size:.73rem; text-transform:uppercase; letter-spacing:.07em;
                color:var(--ink-3)}
.gauge-block b{color:var(--ink); font-family:var(--mono)}
.bignum{font-family:var(--mono); font-size:1.9rem; font-weight:700;
        letter-spacing:-.01em; margin-top:12px; line-height:1.15}
.bignum.bad{color:var(--bad)}
.bignum-sub{display:block; font-family:var(--sans); font-size:.76rem;
            font-weight:400; color:var(--ink-3); letter-spacing:0}

/* ---------- callout ---------- */
.callout-head{
  display:flex; align-items:center; gap:12px; flex-wrap:wrap;
  margin-top:12px; padding:12px 14px; border-radius:var(--radius);
  background:rgba(var(--tone-rgb),.10);
  border:1px solid rgba(var(--tone-rgb),.45);
  border-left:6px solid var(--tone);
}
.callout-head .sym{font-family:var(--mono); font-size:1.5rem; font-weight:700}
.callout-head .head-entry{font-family:var(--mono); font-size:1.1rem;
                          color:var(--ink-2)}
.callout-head .head-stamp{margin-left:auto; font-family:var(--mono);
                          font-size:.76rem; color:var(--ink-3)}

/* ---------- analysts ---------- */
.verdict{display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-top:12px}
.verdict .spread{font-family:var(--mono); font-size:.76rem; color:var(--ink-3)}
.verdict.disagree{
  padding:10px 12px; border-radius:var(--radius);
  border:1px dashed var(--warn); background:rgba(255,175,0,.10);
}
.split-bar{display:flex; height:22px; border-radius:6px; overflow:hidden;
           margin-top:10px; border:1px solid var(--line-2)}
.split-bar .seg{display:flex; align-items:center; justify-content:center;
  background:var(--tone); color:var(--tone-ink); font-family:var(--mono);
  font-size:.7rem; font-weight:700; letter-spacing:.05em; overflow:hidden;
  white-space:nowrap}
.analysts{display:grid; gap:12px; margin-top:12px;
          grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.analyst{
  background:var(--panel-2); border:1px solid var(--line-2);
  border-left:5px solid var(--tone); border-radius:var(--radius); padding:12px;
  min-width:0;
}
.analyst header{display:flex; align-items:center; gap:8px; flex-wrap:wrap;
                justify-content:space-between}
.analyst .who{font-weight:700}
.analyst .spec{font-size:.76rem; color:var(--ink-3); margin-top:2px}

/* ---------- tables ---------- */
.scroll-x{overflow-x:auto; margin-top:12px; -webkit-overflow-scrolling:touch}
.table{width:100%; border-collapse:collapse; font-size:.86rem}
.table th,.table td{padding:7px 9px; text-align:left; white-space:nowrap;
                    border-bottom:1px solid var(--line-2)}
.table th{font-size:.7rem; text-transform:uppercase; letter-spacing:.07em;
          color:var(--ink-3); font-weight:600; position:sticky; top:0;
          background:var(--panel-bg)}
.table td.num,.table th.num{text-align:right; font-family:var(--mono);
                            font-variant-numeric:tabular-nums}
.table tbody tr:hover{background:var(--panel-2)}
.table td.err{color:var(--bad); white-space:normal; max-width:320px}
.table tr.more td{color:var(--ink-3); font-style:italic; text-align:center}
.table.compact th,.table.compact td{padding:5px 8px; font-size:.8rem}

footer{max-width:1500px; margin:0 auto; padding:10px 16px 28px;
       color:var(--ink-3); font-size:.74rem; font-family:var(--mono)}

/* ---------- the flash ---------- */
.flash-overlay{
  position:fixed; top:0; left:0; right:0; bottom:0; inset:0;
  z-index:9000; pointer-events:none; display:none; opacity:0;
}
.flash-overlay.on{
  display:block;
  background:radial-gradient(ellipse at center,
    rgba(var(--tone-rgb),.04) 0%,
    rgba(var(--tone-rgb),.20) 52%,
    rgba(var(--tone-rgb),.66) 100%);
  box-shadow:inset 0 0 140px 40px rgba(var(--tone-rgb),.85);
  animation:fd-wash .5s ease-in-out 6 both;   /* ~2 Hz; count set from Priority */
}
@keyframes fd-wash{ 0%{opacity:0} 45%{opacity:1} 100%{opacity:0} }
.panel.flashing{animation:fd-ring .5s ease-in-out 6 both}
@keyframes fd-ring{
  0%,100%{box-shadow:var(--shadow),0 0 0 0 rgba(var(--tone-rgb),0)}
  45%{box-shadow:var(--shadow),0 0 0 7px rgba(var(--tone-rgb),.70)}
}
.ribbon{
  position:fixed; top:0; left:0; right:0; z-index:9500; display:none;
  align-items:center; gap:12px; height:var(--ribbon-h); padding:0 14px;
  background:var(--tone,var(--panel-2)); color:var(--tone-ink,var(--ink));
  font-family:var(--mono); font-weight:700; letter-spacing:.05em;
  box-shadow:0 4px 18px rgba(0,0,0,.4);
}
.ribbon.on{display:flex}
.ribbon .txt{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.ribbon button{
  margin-left:auto; cursor:pointer; background:transparent; font-weight:700;
  border:1px solid currentColor; border-radius:6px; padding:3px 10px;
  color:inherit; font-family:var(--mono); font-size:.78rem; flex:0 0 auto;
}
/* Users who ask for reduced motion get a static, high-contrast border instead
   of a pulse - the alert is just as impossible to miss, and nothing blinks. */
@media (prefers-reduced-motion: reduce){
  .flash-overlay.on{
    animation:none; opacity:1; background:none;
    box-shadow:inset 0 0 0 14px var(--tone), inset 0 0 0 22px var(--tone-deep);
  }
  .panel.flashing{
    animation:none;
    box-shadow:var(--shadow),0 0 0 4px var(--tone);
  }
  .gauge-fill{transition:none}
  *{scroll-behavior:auto !important}
}
@media (max-width:560px){
  .wrap{padding:10px}
  .fields{grid-template-columns:1fr}
  .field{flex-direction:column; align-items:flex-start; gap:1px}
  .field .v{margin-left:0; text-align:left}
  .brand{font-size:.96rem}
  .clock .time{font-size:1.06rem}
  .callout-head .sym{font-size:1.2rem}
  .callout-head .head-stamp{margin-left:0; width:100%}
  .bignum{font-size:1.5rem}
}
"""


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------

_JS = r"""
(function () {
  "use strict";

  var D = window.DASHBOARD || {};
  var K_SEEN = "futures_dashboard.seen";
  var K_FLASH = "futures_dashboard.flash";
  var K_SOUND = "futures_dashboard.sound";
  var MEM = {};

  /* ---- storage -------------------------------------------------------
     Every Storage access can throw: Safari private mode, blocked third-party
     data, and Chrome's file:// policy all raise on the *getter*, not just on
     the call. So each one is wrapped, and the chain degrades
     localStorage -> sessionStorage -> window.name -> memory. window.name
     matters because it survives a meta-refresh in the same tab, which is
     exactly the live-loop case, even when both Storage APIs are unavailable. */
  function nameBag() {
    try {
      var b = JSON.parse(window.name || "{}");
      return (b && typeof b === "object") ? b : {};
    } catch (e) { return {}; }
  }
  function readKey(key) {
    try { var a = window.localStorage.getItem(key); if (a !== null) return a; } catch (e) {}
    try { var b = window.sessionStorage.getItem(key); if (b !== null) return b; } catch (e) {}
    var bag = nameBag();
    if (Object.prototype.hasOwnProperty.call(bag, key)) return String(bag[key]);
    return Object.prototype.hasOwnProperty.call(MEM, key) ? MEM[key] : null;
  }
  function writeKey(key, value) {
    var v = String(value);
    MEM[key] = v;
    try { window.localStorage.setItem(key, v); } catch (e) {}
    try { window.sessionStorage.setItem(key, v); } catch (e) {}
    try { var bag = nameBag(); bag[key] = v; window.name = JSON.stringify(bag); } catch (e) {}
  }

  function byId(id) { try { return document.getElementById(id); } catch (e) { return null; } }
  function setText(id, text) { var el = byId(id); if (el) el.textContent = text; }

  var reduceMotion = false;
  try {
    reduceMotion = !!(window.matchMedia
                      && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  } catch (e) { reduceMotion = false; }

  var flashOn = readKey(K_FLASH) !== "off";   /* flashing defaults ON  */
  var soundOn = readKey(K_SOUND) === "on";    /* audio defaults OFF    */

  /* ---- Eastern Time clock --------------------------------------------
     The wall-clock digits come from the IANA zone, so the DST switch is
     handled by the browser's own tz database rather than by arithmetic here.
     The abbreviation shown is the one Python resolved at render time. */
  var etFmt = null;
  try {
    etFmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", hour12: false,
      weekday: "short", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
  } catch (e) { etFmt = null; }

  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function easternParts(d) {
    if (etFmt) {
      try {
        var parts = etFmt.formatToParts(d), p = {}, i;
        for (i = 0; i < parts.length; i++) { p[parts[i].type] = parts[i].value; }
        if (p.year && p.hour) {
          var hh = (p.hour === "24") ? "00" : p.hour;   /* hour12:false quirk */
          return { date: p.year + "-" + p.month + "-" + p.day,
                   time: hh + ":" + p.minute + ":" + p.second,
                   day: p.weekday };
        }
      } catch (e) {}
    }
    /* Fallback for engines without a tz database: shift by the offset Python
       measured at render time. Correct until the next DST transition, and the
       next server render corrects it. Never a hard-coded -05:00. */
    var s = new Date(d.getTime() + (D.et_offset_seconds || 0) * 1000);
    return {
      date: s.getUTCFullYear() + "-" + pad(s.getUTCMonth() + 1) + "-" + pad(s.getUTCDate()),
      time: pad(s.getUTCHours()) + ":" + pad(s.getUTCMinutes()) + ":" + pad(s.getUTCSeconds()),
      day: DAYS[s.getUTCDay()]
    };
  }

  function tickClock() {
    var now = new Date(), p = easternParts(now);
    setText("fd-time", p.time);
    setText("fd-date", p.date + " (" + p.day + ")");
    setText("fd-zone", D.et_label || "ET");
    var el = byId("fd-age");
    if (el && D.rendered_epoch_ms) {
      var age = Math.max(0, Math.round((now.getTime() - D.rendered_epoch_ms) / 1000));
      var text = age < 60 ? (age + "s old")
               : (age < 3600 ? (Math.floor(age / 60) + "m old")
                             : (Math.floor(age / 3600) + "h old"));
      el.textContent = "data " + text;
      var limit = D.refresh_seconds ? (D.refresh_seconds * 3 + 5) : 600;
      el.className = "age" + (age > limit ? " stale" : "");
    }
  }

  /* ---- favicon --------------------------------------------------------
     A background tab shows nothing but its title and its icon, so the icon is
     drawn here rather than shipped as a file: canvas -> data URL keeps the
     document self-contained and lets the colour follow the direction. */
  function drawIcon(item, dim) {
    try {
      var c = document.createElement("canvas");
      c.width = 64; c.height = 64;
      var g = c.getContext("2d");
      if (!g) return null;
      var body = dim ? "#0d1016" : item.colour;
      var mark = dim ? item.colour : item.ink;
      g.clearRect(0, 0, 64, 64);
      g.fillStyle = body;
      g.beginPath();
      var r = 14;
      g.moveTo(r, 0); g.lineTo(64 - r, 0); g.quadraticCurveTo(64, 0, 64, r);
      g.lineTo(64, 64 - r); g.quadraticCurveTo(64, 64, 64 - r, 64);
      g.lineTo(r, 64); g.quadraticCurveTo(0, 64, 0, 64 - r);
      g.lineTo(0, r); g.quadraticCurveTo(0, 0, r, 0);
      g.closePath(); g.fill();
      if (dim) { g.strokeStyle = item.colour; g.lineWidth = 7; g.stroke(); }
      g.fillStyle = mark;
      g.beginPath();
      if (item.shape === "up") {
        g.moveTo(32, 12); g.lineTo(54, 50); g.lineTo(10, 50);
      } else if (item.shape === "down") {
        g.moveTo(32, 52); g.lineTo(54, 14); g.lineTo(10, 14);
      } else {
        g.rect(10, 26, 44, 12);
      }
      g.closePath(); g.fill();
      return c.toDataURL("image/png");
    } catch (e) { return null; }
  }

  function setFavicon(href) {
    if (!href) return;
    try {
      var old = document.querySelectorAll("link[rel~='icon']"), i;
      for (i = 0; i < old.length; i++) { old[i].parentNode.removeChild(old[i]); }
      var link = document.createElement("link");
      link.rel = "icon"; link.type = "image/png"; link.href = href;
      document.head.appendChild(link);          /* replaced, not mutated: some
                                                   engines ignore an href change */
    } catch (e) {}
  }

  /* ---- audio ----------------------------------------------------------
     Created only inside the unmute click, because that click is the user
     gesture browsers require before an AudioContext may make sound. */
  var actx = null;
  function ensureAudio() {
    try {
      var Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return null;
      if (!actx) actx = new Ctor();
      if (actx.state === "suspended" && actx.resume) actx.resume();
    } catch (e) { actx = null; }
    return actx;
  }
  function beep(item) {
    if (!soundOn || !actx || !item.tones || !item.tones.length) return;
    try {
      var start = actx.currentTime + 0.02, i;
      for (i = 0; i < item.tones.length; i++) {
        var osc = actx.createOscillator(), gain = actx.createGain();
        var at = start + i * 0.17;
        osc.type = "square";
        osc.frequency.setValueAtTime(item.tones[i], at);
        gain.gain.setValueAtTime(0.0001, at);
        gain.gain.exponentialRampToValueAtTime(0.16, at + 0.012);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.15);
        osc.connect(gain); gain.connect(actx.destination);
        osc.start(at); osc.stop(at + 0.18);
      }
    } catch (e) {}
  }

  /* ---- the flash ------------------------------------------------------ */
  var timer = null, idleIcon = null;

  function settle() {
    if (timer) { window.clearInterval(timer); timer = null; }
    try { if (D.title) document.title = D.title; } catch (e) {}
    var ov = byId("fd-overlay");
    if (ov) { ov.className = "flash-overlay"; ov.style.animationIterationCount = ""; }
    var panel = byId("fd-latest");
    if (panel) {
      panel.classList.remove("flashing");
      panel.style.animationIterationCount = "";
    }
    if (idleIcon) setFavicon(idleIcon);
  }

  function marker(item) {
    return item.glyph + item.glyph + " " + item.label + " " + item.headline;
  }

  function startFlash(item) {
    settle();
    var cycles = Math.max(1, Math.min(10, item.cycles || 4));
    var ov = byId("fd-overlay");
    if (ov) {
      ov.className = "flash-overlay t-" + item.key + " on";
      ov.style.animationIterationCount = String(cycles);
      void ov.offsetWidth;                      /* restart the keyframes */
    }
    var panel = byId("fd-latest");
    if (panel) {
      panel.style.animationIterationCount = String(cycles);
      panel.classList.add("flashing");
      void panel.offsetWidth;
    }

    var onIcon = drawIcon(item, false), offIcon = drawIcon(item, true);
    var text = marker(item);

    if (reduceMotion) {
      /* No alternation at all: set the title and icon once and hold them. */
      try { document.title = text; } catch (e) {}
      setFavicon(onIcon);
      window.setTimeout(settle, cycles * 500 + 1500);
      return;
    }

    var ticks = cycles * 2, i = 0;
    function step() {
      var lit = (i % 2) === 0;
      try { document.title = lit ? text : (D.title || ""); } catch (e) {}
      setFavicon(lit ? onIcon : offIcon);
      i += 1;
      if (i > ticks) settle();
    }
    step();                                     /* land the first frame at once */
    timer = window.setInterval(step, 250);      /* 250 ms half-cycle = ~2 Hz */
  }

  function showRibbon(item) {
    var r = byId("fd-ribbon"), t = byId("fd-ribbon-text");
    if (!r || !t) return;
    t.textContent = marker(item);               /* textContent: never parsed */
    r.className = "ribbon t-" + item.key + " on";
    try { document.body.classList.add("ribbon-on"); } catch (e) {}
  }
  function hideRibbon() {
    var r = byId("fd-ribbon");
    if (r) r.className = "ribbon";
    try { document.body.classList.remove("ribbon-on"); } catch (e) {}
  }

  function announce(item) {
    if (!item) return;
    showRibbon(item);          /* the banner shows even with flashing disabled */
    if (flashOn) startFlash(item);
    beep(item);
  }

  /* ---- controls ------------------------------------------------------- */
  function paintToggle(el, on, labelOn, labelOff) {
    if (!el) return;
    el.className = "toggle" + (on ? " on" : "");
    el.textContent = on ? labelOn : labelOff;
    el.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function wireControls() {
    var flashBtn = byId("fd-flash-toggle");
    var soundBtn = byId("fd-sound-toggle");
    var testBtn = byId("fd-test");
    var closeBtn = byId("fd-ribbon-close");

    paintToggle(flashBtn, flashOn, "Flash: ON", "Flash: OFF");
    paintToggle(soundBtn, soundOn, "Sound: ON", "Sound: OFF");

    if (flashBtn) flashBtn.addEventListener("click", function () {
      flashOn = !flashOn;
      writeKey(K_FLASH, flashOn ? "on" : "off");
      paintToggle(flashBtn, flashOn, "Flash: ON", "Flash: OFF");
      if (!flashOn) settle();
    });

    if (soundBtn) soundBtn.addEventListener("click", function () {
      soundOn = !soundOn;
      writeKey(K_SOUND, soundOn ? "on" : "off");
      paintToggle(soundBtn, soundOn, "Sound: ON", "Sound: OFF");
      if (soundOn) { ensureAudio(); beep(D.latest || D.demo); }
    });

    if (testBtn) testBtn.addEventListener("click", function () {
      announce(D.latest || D.demo);
    });

    if (closeBtn) closeBtn.addEventListener("click", function () {
      hideRibbon(); settle();
    });

    try {
      document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") { hideRibbon(); settle(); }
      });
    } catch (e) {}
  }

  /* ---- boot ----------------------------------------------------------- */
  function boot() {
    wireControls();
    tickClock();
    window.setInterval(tickClock, 1000);

    if (D.latest) {
      idleIcon = drawIcon(D.latest, false);     /* a background tab keeps showing
                                                   the current direction colour */
      setFavicon(idleIcon);
      var seen = readKey(K_SEEN);
      if (D.latest.id && D.latest.id !== seen) {
        writeKey(K_SEEN, D.latest.id);
        if (D.latest.cycles > 0) announce(D.latest);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
"""


# --------------------------------------------------------------------------
# Document assembly
# --------------------------------------------------------------------------

def _flash_item(callout: Dict[str, Any], tone: _Tone) -> Dict[str, Any]:
    """The compact record the page's JavaScript flashes on.

    Only what the flash needs: identity (so a refresh that brings no new callout
    does not re-pulse), the tone, and the headline.
    """
    entry = _entry_text(callout)
    symbol = str(callout.get("symbol") or "")
    headline = f"{symbol} {entry}".strip()
    if callout.get("contracts"):
        headline += f"  x{callout.get('contracts')}"
    return {
        "id": str(callout.get("callout_id") or callout.get("timestamp_et") or headline),
        "key": tone.key,
        "label": tone.label,
        "glyph": tone.glyph,
        "colour": tone.colour,
        "ink": tone.ink,
        "shape": tone.shape,
        "cycles": tone.cycles,
        "tones": list(tone.tones_hz),
        "symbol": symbol,
        "headline": headline or tone.label,
    }


def _header(title: str, stamp: str, label: str, refresh_seconds: Optional[int]) -> str:
    refresh = (f'<span class="age">auto-refresh {int(refresh_seconds)}s</span>'
               if refresh_seconds else "")
    return (
        '<header class="topbar">'
        f'<div class="brand">{_e(title)}<small>rendered {_e(stamp)}</small></div>'
        '<div class="clock">'
        '<span class="time" id="fd-time">--:--:--</span>'
        f'<span class="zone" id="fd-zone">{_e(label)}</span>'
        '<span class="date" id="fd-date"></span>'
        '</div>'
        '<span class="age" id="fd-age">data 0s old</span>'
        f'{refresh}'
        '<div class="controls">'
        '<button type="button" class="toggle" id="fd-flash-toggle" aria-pressed="true">'
        'Flash: ON</button>'
        '<button type="button" class="toggle" id="fd-sound-toggle" aria-pressed="false" '
        'title="Browsers block audio until you ask for it">Sound: OFF</button>'
        '<button type="button" class="toggle" id="fd-test">Test flash</button>'
        '</div>'
        '</header>'
    )


def dashboard_html(
    callouts: Sequence[Any],
    account: Any,
    *,
    predictions: Optional[Sequence[Any]] = None,
    news: Any = None,
    task_board: Any = None,
    title: str = DEFAULT_TITLE,
    refresh_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
) -> str:
    """Render the whole dashboard as one self-contained HTML document.

    Pure: nothing is read from or written to disk, and no module state is
    touched. Pass ``now`` to pin the render stamp and make the output
    byte-stable for tests.
    """
    moment = to_et(now) if now is not None else now_et()
    stamp = et_stamp(moment)
    label = et_label(moment)
    offset = moment.utcoffset()
    offset_seconds = int(offset.total_seconds()) if offset else 0

    rows = _sorted_callouts(callouts)
    latest = rows[0] if rows else {}
    tone = _tone_for(Decision.coerce(latest.get("decision"))) if latest else None

    payload = {
        "title": title,
        "et_label": label,
        "et_offset_seconds": offset_seconds,
        "rendered_epoch_ms": int(moment.timestamp() * 1000),
        "refresh_seconds": int(refresh_seconds) if refresh_seconds else None,
        "latest": _flash_item(latest, tone) if (latest and tone) else None,
        # A synthetic LONG so "Test flash" works on an empty desk, built from
        # the same palette so the test looks exactly like the real thing.
        "demo": _flash_item({"callout_id": "demo", "symbol": "TEST",
                             "entry": None, "contracts": 0},
                            _tone_for(Decision.LONG)),
    }

    meta_refresh = (f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
                    if refresh_seconds else "")

    panels = [
        f'<div class="col-8">{_callout_panel(latest, tone)}</div>',
        f'<div class="col-4">{_account_panel(account)}</div>',
        f'<div class="col-8">{_analyst_panel(predictions or [])}</div>',
        f'<div class="col-4">{_news_panel(news)}</div>',
    ]
    history = _history_panel(rows)
    if history:
        panels.append(f'<div class="col-12">{history}</div>')
    tasks = _task_panel(task_board)
    if tasks:
        panels.append(f'<div class="col-12">{tasks}</div>')

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="dark light">\n'
        f'{meta_refresh}\n'
        f'<title>{_e(title)}</title>\n'
        f'<style>\n{_CSS}\n{_tone_css()}\n</style>\n'
        '</head>\n<body>\n'
        '<div class="flash-overlay" id="fd-overlay" aria-hidden="true"></div>\n'
        '<div class="ribbon" id="fd-ribbon" role="status" aria-live="assertive">'
        '<span class="txt" id="fd-ribbon-text"></span>'
        '<button type="button" id="fd-ribbon-close" aria-label="Dismiss alert">'
        'dismiss</button></div>\n'
        f'{_header(title, stamp, label, refresh_seconds)}\n'
        '<main class="wrap">\n<div class="grid">\n'
        + "\n".join(panels)
        + '\n</div>\n</main>\n'
        f'<footer>Rendered {_e(stamp)} &middot; all times Eastern &middot; '
        'self-contained, no network. Colours and pulse counts follow '
        'futures_agents.alerts.</footer>\n'
        f'<script>\nwindow.DASHBOARD = {_js(payload)};\n{_JS}\n</script>\n'
        '</body>\n</html>\n'
    )


def render_dashboard(
    callouts: Sequence[Any],
    account: Any,
    *,
    predictions: Optional[Sequence[Any]] = None,
    news: Any = None,
    task_board: Any = None,
    path: str = DEFAULT_PATH,
    title: str = DEFAULT_TITLE,
    refresh_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
) -> str:
    """Write the dashboard to ``path`` and return the path written.

    The only I/O in this module. Parent directories are created; the file is
    written whole (never appended) so a reader that opens it mid-refresh sees
    the previous complete document rather than a truncated one.
    """
    document = dashboard_html(
        callouts, account, predictions=predictions, news=news,
        task_board=task_board, title=title, refresh_seconds=refresh_seconds, now=now,
    )
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(document)
    return path
