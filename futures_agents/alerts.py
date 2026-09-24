"""Terminal alert rendering: Eastern-Time stamping and the visual flash.

Two requirements drive this module:

1. **Every alert leads with the time in Eastern Time.** The stamp is printed
   before the body, never after, so a callout read mid-scroll is never
   ambiguous about when it was valid.

2. **A live callout has to be impossible to miss.** ``flash()`` pulses a
   full-width colour banner in place and, for high-priority alerts, inverts the
   whole terminal (the classic visual bell) so a BUY/SELL lands in peripheral
   vision.

Everything degrades safely: when stdout is not a TTY, when ``NO_COLOR`` is set,
when ``TERM=dumb``, or when flashing is disabled in config, the same content is
emitted as plain text with ``***`` markers so logs stay readable and greppable.
"""

from __future__ import annotations

import os
import shutil
import sys
import time as _time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, List, Optional, Sequence, TextIO

from .timeutil import et_stamp, now_et

__all__ = [
    "Priority", "Palette", "AlertRenderer", "alert", "flash", "banner",
    "supports_color", "strip_ansi", "console",
]


# --------------------------------------------------------------------------
# Capability detection
# --------------------------------------------------------------------------

def supports_color(stream: Optional[TextIO] = None) -> bool:
    """Whether ANSI styling should be emitted to ``stream``."""
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM", "").lower() in ("dumb", ""):
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def terminal_width(default: int = 100) -> int:
    try:
        return max(60, min(160, shutil.get_terminal_size((default, 24)).columns))
    except Exception:
        return default


def strip_ansi(text: str) -> str:
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "\x1b":
            j = i + 1
            if j < n and text[j] == "[":
                j += 1
                while j < n and not text[j].isalpha():
                    j += 1
                i = j + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# Styling
# --------------------------------------------------------------------------

class Priority(str, Enum):
    """How loudly an alert announces itself."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    RESEARCH = "RESEARCH"
    SIGNAL = "SIGNAL"          # an analyst published a prediction
    LONG = "LONG"              # executable buy callout
    SHORT = "SHORT"            # executable sell callout
    NO_TRADE = "NO_TRADE"      # the legitimate "stand down" outcome
    WARNING = "WARNING"
    RISK = "RISK"              # risk layer intervened
    HALT = "HALT"              # daily limit hit / trading disabled

    @property
    def flash_cycles(self) -> int:
        return {
            Priority.DEBUG: 0, Priority.INFO: 0, Priority.RESEARCH: 0,
            Priority.SIGNAL: 2, Priority.NO_TRADE: 2, Priority.WARNING: 3,
            Priority.LONG: 6, Priority.SHORT: 6, Priority.RISK: 4, Priority.HALT: 8,
        }[self]

    @property
    def screen_flash(self) -> bool:
        """Invert the entire terminal - reserved for things you must not miss."""
        return self in (Priority.LONG, Priority.SHORT, Priority.HALT)

    @property
    def bells(self) -> int:
        return {
            Priority.DEBUG: 0, Priority.INFO: 0, Priority.RESEARCH: 0,
            Priority.SIGNAL: 1, Priority.NO_TRADE: 0, Priority.WARNING: 1,
            Priority.LONG: 2, Priority.SHORT: 2, Priority.RISK: 1, Priority.HALT: 3,
        }[self]


@dataclass(frozen=True)
class Style:
    fg: str
    bg: str
    label: str
    glyph: str


class Palette:
    """256-colour ANSI palette, chosen to stay legible on light and dark
    terminals (all backgrounds are saturated, all foregrounds are near-white or
    near-black against them)."""

    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    ITALIC = "\x1b[3m"
    UNDERLINE = "\x1b[4m"
    REVERSE = "\x1b[7m"

    # Screen-level reverse video (DECSCNM): the visual-bell full-screen flash.
    SCREEN_REVERSE_ON = "\x1b[?5h"
    SCREEN_REVERSE_OFF = "\x1b[?5l"

    STYLES = {
        Priority.DEBUG:    Style("\x1b[38;5;245m", "\x1b[48;5;236m", "DEBUG",    "·"),
        Priority.INFO:     Style("\x1b[38;5;252m", "\x1b[48;5;238m", "INFO",     "i"),
        Priority.RESEARCH: Style("\x1b[38;5;231m", "\x1b[48;5;24m",  "RESEARCH", "*"),
        # SIGNAL steps back to a muted slate so it cannot be mistaken for a
        # BUY callout at a glance - they are both blue, but only one is a
        # direction.
        Priority.SIGNAL:   Style("\x1b[38;5;231m", "\x1b[48;5;61m",  "SIGNAL",   ">"),
        # Direction colours are BLUE for buy and ORANGE for sell, by request.
        # Foregrounds are chosen for contrast against each: white on the deep
        # blue, black on the orange.
        Priority.LONG:     Style("\x1b[38;5;231m", "\x1b[48;5;27m",  "BUY / LONG",  "^"),
        Priority.SHORT:    Style("\x1b[38;5;16m",  "\x1b[48;5;208m", "SELL / SHORT", "v"),
        # NO_TRADE moves off orange for the same reason SIGNAL moved off blue:
        # it previously sat at 214, a near neighbour of the new SELL colour,
        # and "no trade" must never read as "sell" in peripheral vision.
        Priority.NO_TRADE: Style("\x1b[38;5;16m",  "\x1b[48;5;250m", "NO TRADE", "="),
        Priority.WARNING:  Style("\x1b[38;5;16m",  "\x1b[48;5;220m", "WARNING",  "!"),
        Priority.RISK:     Style("\x1b[38;5;231m", "\x1b[48;5;129m", "RISK",     "#"),
        Priority.HALT:     Style("\x1b[38;5;231m", "\x1b[48;5;160m", "HALT",     "X"),
    }

    #: Alternate background used on the "off" half of each flash cycle.
    INVERTED = {
        Priority.LONG:     ("\x1b[38;5;27m",  "\x1b[48;5;17m"),
        Priority.SHORT:    ("\x1b[38;5;208m", "\x1b[48;5;94m"),
        Priority.NO_TRADE: ("\x1b[38;5;250m", "\x1b[48;5;240m"),
        Priority.WARNING:  ("\x1b[38;5;220m", "\x1b[48;5;58m"),
        Priority.RISK:     ("\x1b[38;5;129m", "\x1b[48;5;53m"),
        Priority.HALT:     ("\x1b[38;5;196m", "\x1b[48;5;52m"),
        Priority.SIGNAL:   ("\x1b[38;5;61m",  "\x1b[48;5;60m"),
    }

    @classmethod
    def style_for(cls, p: Priority) -> Style:
        return cls.STYLES[p]


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------

class AlertRenderer:
    """Stateful renderer so flash behaviour can be configured once and reused."""

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        *,
        flash_enabled: bool = True,
        bell_enabled: bool = True,
        color: Optional[bool] = None,
        flash_interval: float = 0.11,
        max_flash_cycles: int = 10,
        screen_flash_enabled: bool = True,
        width: Optional[int] = None,
    ) -> None:
        self.stream = stream or sys.stdout
        self.flash_enabled = flash_enabled
        self.bell_enabled = bell_enabled
        self.color = supports_color(self.stream) if color is None else bool(color)
        self.flash_interval = max(0.02, float(flash_interval))
        self.max_flash_cycles = int(max_flash_cycles)
        self.screen_flash_enabled = screen_flash_enabled
        self._width = width

    # ---- low-level -----------------------------------------------------
    @property
    def width(self) -> int:
        return self._width or terminal_width()

    def _w(self, text: str = "") -> None:
        self.stream.write(text)

    def _flush(self) -> None:
        try:
            self.stream.flush()
        except Exception:
            pass

    def _paint(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return "".join(codes) + text + Palette.RESET

    # ---- banners -------------------------------------------------------
    def _banner_lines(self, priority: Priority, headline: str,
                      subline: str = "") -> List[str]:
        """Three fixed-width lines: pad, headline, pad. Fixed height matters -
        the flash rewrites exactly this many lines in place."""
        st = Palette.style_for(priority)
        w = self.width
        tag = f" {st.glyph}{st.glyph} {st.label} {st.glyph}{st.glyph} "
        head = f"{tag}| {headline}"
        if len(head) > w - 2:
            head = head[: w - 5] + "..."
        sub = subline[: w - 4] if subline else ""
        return [
            " " * w,
            " " + head.ljust(w - 2) + " ",
            (" " + sub.ljust(w - 2) + " ") if sub else " " * w,
        ]

    def _render_banner(self, priority: Priority, lines: Sequence[str],
                       inverted: bool = False) -> str:
        if not self.color:
            return "\n".join(line.rstrip() for line in lines if line.strip())
        st = Palette.style_for(priority)
        if inverted and priority in Palette.INVERTED:
            fg, bg = Palette.INVERTED[priority]
        else:
            fg, bg = st.fg, st.bg
        return "\n".join(f"{bg}{fg}{Palette.BOLD}{line}{Palette.RESET}" for line in lines)

    def banner(self, priority: Priority, headline: str, subline: str = "") -> str:
        """Return a rendered (non-animated) banner string."""
        return self._render_banner(priority, self._banner_lines(priority, headline, subline))

    # ---- the flash -----------------------------------------------------
    def flash(
        self,
        priority: Priority,
        headline: str,
        subline: str = "",
        *,
        cycles: Optional[int] = None,
        screen: Optional[bool] = None,
    ) -> None:
        """Pulse a banner in place, then leave it on screen.

        The animation rewrites the same three lines using cursor-up, so the
        scrollback is not filled with duplicate banners. When colour is
        unavailable the banner is printed once, plainly.
        """
        lines = self._banner_lines(priority, headline, subline)
        n = len(lines)

        if not self.color or not self.flash_enabled:
            self._w(self._render_banner(priority, lines) + "\n")
            self._flush()
            self._ring(priority)
            return

        want = priority.flash_cycles if cycles is None else int(cycles)
        want = max(0, min(self.max_flash_cycles, want))

        do_screen = (self.screen_flash_enabled
                     and (priority.screen_flash if screen is None else bool(screen)))

        self._w(self._render_banner(priority, lines) + "\n")
        self._flush()

        try:
            if do_screen:
                self._screen_flash(2)
            for i in range(want):
                self._w(f"\x1b[{n}A")           # cursor up to the banner's first line
                self._w("\r")
                self._w(self._render_banner(priority, lines, inverted=(i % 2 == 0)) + "\n")
                self._flush()
                _time.sleep(self.flash_interval)
            if want:
                self._w(f"\x1b[{n}A\r")
                self._w(self._render_banner(priority, lines) + "\n")
                self._flush()
        except Exception:
            # An animation must never be able to break the alert itself.
            self._flush()

        self._ring(priority)

    def _screen_flash(self, times: int = 2, interval: float = 0.06) -> None:
        """Invert the whole terminal briefly - the visual bell."""
        try:
            for _ in range(max(0, times)):
                self._w(Palette.SCREEN_REVERSE_ON)
                self._flush()
                _time.sleep(interval)
                self._w(Palette.SCREEN_REVERSE_OFF)
                self._flush()
                _time.sleep(interval)
        except Exception:
            self._w(Palette.SCREEN_REVERSE_OFF)
            self._flush()

    def _ring(self, priority: Priority) -> None:
        if not self.bell_enabled:
            return
        try:
            for _ in range(priority.bells):
                self._w("\a")
                self._flush()
                _time.sleep(0.08)
        except Exception:
            pass

    # ---- full alert ----------------------------------------------------
    def alert(
        self,
        priority: Priority,
        headline: str,
        body: str = "",
        *,
        subline: str = "",
        timestamp: Optional[datetime] = None,
        cycles: Optional[int] = None,
        rule: bool = True,
    ) -> str:
        """Print a complete alert: ET stamp, flashing banner, then the body.

        Returns the plain-text form of what was printed so callers can log or
        journal the exact same content.
        """
        ts = timestamp or now_et()
        stamp = et_stamp(ts)
        st = Palette.style_for(priority)

        stamp_line = f"[{stamp}]"
        self._w(self._paint(stamp_line, Palette.BOLD, st.fg) + "\n")
        self.flash(priority, headline, subline, cycles=cycles)

        if body:
            self._w(body.rstrip("\n") + "\n")
        if rule:
            self._w(self._paint("-" * self.width, Palette.DIM) + "\n")
        self._flush()

        plain = f"[{stamp}]\n{st.label} | {headline}"
        if subline:
            plain += f"\n{subline}"
        if body:
            plain += f"\n{body.rstrip()}"
        return plain

    def line(self, text: str, priority: Priority = Priority.INFO,
             timestamp: Optional[datetime] = None) -> str:
        """A single ET-stamped log line - no banner, no flash."""
        ts = timestamp or now_et()
        stamp = et_stamp(ts, with_seconds=True)
        st = Palette.style_for(priority)
        out = f"[{stamp}] {st.label:<12} {text}"
        self._w(self._paint(f"[{stamp}]", Palette.DIM)
                + self._paint(f" {st.label:<12} ", st.fg, Palette.BOLD)
                + text + "\n")
        self._flush()
        return out


#: Process-wide default renderer. Reconfigured by the CLI from SystemConfig.
console = AlertRenderer()


def configure(*, flash_enabled: bool = True, bell_enabled: bool = True,
              color: Optional[bool] = None, flash_cycles: int = 6,
              stream: Optional[TextIO] = None) -> AlertRenderer:
    """Reconfigure the module-level renderer and return it."""
    global console
    console = AlertRenderer(
        stream=stream,
        flash_enabled=flash_enabled,
        bell_enabled=bell_enabled,
        color=color,
        max_flash_cycles=max(0, int(flash_cycles)),
    )
    return console


def alert(priority: Priority, headline: str, body: str = "", **kw) -> str:
    return console.alert(priority, headline, body, **kw)


def flash(priority: Priority, headline: str, subline: str = "", **kw) -> None:
    console.flash(priority, headline, subline, **kw)


def banner(priority: Priority, headline: str, subline: str = "") -> str:
    return console.banner(priority, headline, subline)
