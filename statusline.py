# /// script
# requires-python = ">=3.12"
# dependencies = ["coloraide>=4.0"]
# ///
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code status line — Powerlevel10k style.

Two kinds of segments:

  * fill bars   (context / 5h / 7d / cost)  -> bar()
        The hue runs green -> yellow -> orange -> red depending on how full the
        bar is (or, for the cost bar, how expensive the session got).  Each bar
        carries a subtle left(darker)->right(lighter) lightness gradient that
        spans the full width even when the bar is not completely filled.  Hues
        are computed in OKLCH (perceptually smooth, no green->yellow jump) and
        converted to sRGB via coloraide.

  * fixed bars  (model / working dir)        -> fixed_bar()
        Solid brand colour #10475D with the same subtle lightness gradient.

The cost bar is a special case: a fixed bar that is always filled
(`alwaysfill=True`) but whose hue still shifts green->red with the real
session cost reported by Claude Code (`cost.total_cost_usd`).
"""

import sys
import io
import json
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache

from coloraide import Color

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Powerline glyphs ─────────────────────────────────────────────────────────
PL_R = chr(0xE0B0)     # ▶  right end-cap
PL_L = chr(0xE0B2)     # ◀  left  end-cap
DIAMOND = chr(0x2B29)  # ⬩  separator (black small diamond)

RESET = "\033[0m"
def fg(r, g, b): return f"\033[38;2;{r};{g};{b}m"
def bg(r, g, b): return f"\033[48;2;{r};{g};{b}m"

WHITE = (235, 238, 245)

# ── Colour model ──────────────────────────────────────────────────────────────
# Fill ramp in OKLCH: (position 0..1, Lightness 0..1, Chroma, Hue°).
# Hues decrease monotonically green->yellow->orange->red so linear interpolation
# stays perceptually smooth without a hue jump.
RAMP = [
    (0.00, 0.60, 0.13, 150.0),   # green
    (0.45, 0.66, 0.13, 110.0),   # yellow
    (0.70, 0.63, 0.15,  62.0),   # orange
    (1.00, 0.56, 0.17,  29.0),   # red
]

# Dark "empty" track of a fill bar: same hue as the fill, low lightness/chroma.
L_EMPTY = 0.26
C_EMPTY = 0.035

# Left->right lightness gradient amplitude (subtle).
DL_FILL  = 0.055
DL_FIXED = 0.040

FIXED_HEX = "#10475D"   # brand colour for model / directory bars
SEP_RGB = (90, 100, 120)   # the diamond drawn between two segments


# ── Palettes: how the bars sit on a dark vs a light terminal background ───────
# A dark terminal wants a fill that is *lighter* than its empty track; a light
# terminal wants the opposite, or every bar reads as a heavy block stamped onto
# the page. Each palette therefore carries its own lightness pair plus the text
# colour that stays readable across both, in 24-bit form and in legacy SGR codes
# for the 16-colour renderer that has no lightness to tune.
@dataclass(frozen=True)
class Palette:
    """Lightness and contrast parameters for one terminal background."""
    dl_fill: float          #: lightness added to every fill colour
    l_empty: float          #: lightness of the empty track
    c_empty: float          #: chroma of the empty track
    text: tuple             #: text colour on a fill bar
    fixed_l: float          #: lightness of the model / directory bars
    fixed_text: tuple       #: text colour on those bars
    bright16: bool          #: use the bright SGR backgrounds for filled cells
    track16: tuple          #: (bg, fg) SGR codes for the empty track
    fill16_fg: int          #: SGR text colour on a filled cell
    fixed16: tuple          #: (bg, fg) SGR codes for the model / directory bars
    sep16: int              #: SGR text colour of the separator diamond

DARK_PALETTE = Palette(
    dl_fill=0.00, l_empty=L_EMPTY, c_empty=C_EMPTY, text=WHITE,
    fixed_l=0.38, fixed_text=WHITE,
    bright16=True, track16=(40, 37), fill16_fg=30, fixed16=(44, 97), sep16=90,
)
LIGHT_PALETTE = Palette(
    dl_fill=0.12, l_empty=0.93, c_empty=0.040, text=(28, 32, 40),
    fixed_l=0.86, fixed_text=(18, 38, 54),
    bright16=False, track16=(47, 30), fill16_fg=30, fixed16=(47, 34), sep16=30,
)

#: Active palette; `main()` replaces it once the background is known.
PALETTE = DARK_PALETTE

# Effort level -> its own colour (own gradient, NOT the green->red ramp).
# Each entry: ("solid", (L, C, H))  -> subtle left->right lightness gradient
#             ("rainbow", None)     -> hue sweep across the whole bar
# Two non-payload pseudo-levels share the deep-purple, always-full look:
#   "ultracode" -- the REAL thing, kept ready for the day Claude Code exposes a
#                  detectable signal (letter "u"); does not trigger today.
#   "wx"        -- our honest proxy: xhigh + dynamic workflows enabled (letter
#                  "wx"). Workflows are a *precondition* for ultracode, so this
#                  says "ultracode is possible this session", not "it's on".
# See main() and docs/ultracode-detection.md.
EFFORT_COLORS = {
    "low":       ("solid",   (0.68, 0.15,  60.0)),   # orange
    "medium":    ("solid",   (0.66, 0.14, 150.0)),   # green
    "high":      ("solid",   (0.60, 0.14, 255.0)),   # blue
    "xhigh":     ("solid",   (0.75, 0.11, 322.0)),   # light purple
    "max":       ("rainbow", None),                  # rainbow
    "ultracode": ("solid",   (0.35, 0.16, 308.0)),   # deep purple
    "wx":        ("solid",   (0.35, 0.16, 308.0)),   # deep purple (same look)
}
# Code shown in the effort bar (" <icon>  <code> "); usually one letter, "wx" two.
EFFORT_LETTER = {
    "low": "l", "medium": "m", "high": "h",
    "xhigh": "x", "max": "m", "ultracode": "u", "wx": "wx",
}
# The same levels in legacy SGR: 16 colours have no lightness to tune, so each
# level picks the nearest of the eight base hues (2 green, 3 yellow, 4 blue,
# 5 magenta, 6 cyan) rather than being rounded off an RGB value.
EFFORT_SGR = {
    "low": 3, "medium": 2, "high": 4, "xhigh": 5,
    "max": 6, "ultracode": 5, "wx": 5,
}
# Fill order: low fills 1/6 ... ultracode fills 6/6. Pseudo-levels NOT listed
# here (e.g. "wx") are rendered fully filled by effort_bar().
EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultracode"]


def _clamp(v, lo, hi): return lo if v < lo else hi if v > hi else v
def _lerp(a, b, t): return a + (b - a) * t


def _ramp_at(frac):
    """Interpolate (L, C, H) along RAMP for frac in 0..1."""
    frac = _clamp(frac, 0.0, 1.0)
    for j in range(len(RAMP) - 1):
        p0, l0, c0, h0 = RAMP[j]
        p1, l1, c1, h1 = RAMP[j + 1]
        if frac <= p1:
            t = (frac - p0) / (p1 - p0) if p1 > p0 else 0.0
            return _lerp(l0, l1, t), _lerp(c0, c1, t), _lerp(h0, h1, t)
    return RAMP[-1][1], RAMP[-1][2], RAMP[-1][3]


@lru_cache(maxsize=1024)
def oklch_rgb(L, C, H):
    """OKLCH (L 0..1, C, H°) -> sRGB 0..255 tuple, gamut-mapped."""
    L = _clamp(L, 0.0, 1.0)
    c = Color("oklch", [L, C, H]).convert("srgb").fit("srgb")
    return tuple(int(round(_clamp(c.get(ch), 0.0, 1.0) * 255))
                 for ch in ("red", "green", "blue"))


@lru_cache(maxsize=4)
def _fixed_lch():
    c = Color(FIXED_HEX).convert("oklch")
    return c.get("lightness"), c.get("chroma"), c.get("hue")


def _slope(i, width, amp):
    """Subtle left->right lightness offset for cell i, in [-amp, +amp]."""
    x = i / (width - 1) if width > 1 else 0.5
    return (x - 0.5) * 2.0 * amp


def _contrast_fg(rgb):
    """Pick a readable text colour (dark on light cells, white on dark cells)."""
    r, g, b = rgb
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return (15, 18, 24) if lum > 0.62 else WHITE


class ColorSupport(IntEnum):
    """Colour depth a terminal can render, ordered worst to best."""

    MONO = 0        #: no colour at all (TERM unset or "dumb")
    ANSI16 = 1      #: the 8/16 legacy SGR colours
    ANSI256 = 2     #: the indexed 256-colour palette
    TRUECOLOR = 3   #: 24-bit RGB, what every bar in this script needs


# ── Ink: one abstract bar cell -> the escapes a terminal understands ─────────
def _sgr(*codes) -> str:
    """Legacy SGR escape built from plain parameter numbers."""
    return "\033[" + ";".join(str(c) for c in codes) + "m"


def _x256(rgb: tuple) -> int:
    """Nearest xterm-256 index for an sRGB triple.

    Near-neutral colours go to the 24-step grey ramp, which is far finer than
    the 6x6x6 colour cube and keeps the empty track from turning into a muddy
    tinted block.
    """
    r, g, b = rgb
    if max(rgb) - min(rgb) < 14:
        return 232 + int(_clamp((round((r + g + b) / 3) - 8) // 10, 0, 23))
    ax = lambda v: 0 if v < 48 else 1 if v < 115 else (v - 35) // 40
    return 16 + 36 * ax(r) + 6 * ax(g) + ax(b)


#: Ramp position -> base SGR colour for the 16-colour renderer. The green->red
#: ramp carries *meaning*, so it is mapped by what it says, never by nearest
#: RGB: matched metrically, any mid-lightness green lands on grey.
RAMP16 = ((0.45, 2), (0.72, 3), (1.01, 1))     # green, yellow, red


class Ink:
    """Renders bar cells for one terminal colour depth.

    Every builder asks its Ink for two escapes per cell: one that styles the
    cell itself, and one that tints the Powerline end-cap beside it. A cap is a
    glyph drawn in the *foreground*, so it needs its neighbouring cell's
    background colour expressed as a foreground escape.

    The lightness gradient is a class attribute because it is a question of
    resolution, not taste: rounded onto a coarse palette it reads as banding
    rather than as depth, so only the 24-bit renderer draws it.
    """

    gradient: bool = False   #: draw the subtle left->right lightness gradient
    caps: bool = True        #: draw the pointy Powerline end-caps

    def ramp(self, frac: float, filled: bool, i: int, width: int) -> tuple[str, str]:
        """Cell of a fill bar at ramp position `frac`."""
        raise NotImplementedError

    def fixed(self, i: int, width: int) -> tuple[str, str]:
        """Cell of a solid brand-coloured bar (model / directory)."""
        raise NotImplementedError

    def effort(self, level: str, kind: str, lch, filled: bool,
               i: int, fill: int, width: int) -> tuple[str, str]:
        """Cell of the effort bar, which carries its own colour per level."""
        raise NotImplementedError

    def separator(self) -> str:
        """The run drawn between two segments."""
        raise NotImplementedError


class RgbInk(Ink):
    """Base for the depths that can express an arbitrary sRGB triple.

    Subclasses supply only the encoding; the colour decisions are made once,
    here, so 24-bit and 256-colour cannot drift apart.
    """

    def _bg(self, rgb: tuple) -> str:
        raise NotImplementedError

    def _fg(self, rgb: tuple) -> str:
        raise NotImplementedError

    def _tilt(self, i: int, width: int, amp: float) -> float:
        return _slope(i, width, amp) if self.gradient else 0.0

    def _cell(self, rgb: tuple, text: tuple) -> tuple[str, str]:
        return self._bg(rgb) + self._fg(text), self._fg(rgb)

    def _track(self, hue: float, tilt: float) -> tuple:
        return oklch_rgb(PALETTE.l_empty + tilt * 0.6, PALETTE.c_empty, hue)

    def ramp(self, frac, filled, i, width):
        L, C, H = _ramp_at(frac)
        tilt = self._tilt(i, width, DL_FILL)
        rgb = (oklch_rgb(L + PALETTE.dl_fill + tilt, C, H) if filled
               else self._track(H, tilt))
        return self._cell(rgb, PALETTE.text)

    def fixed(self, i, width):
        _, C, H = _fixed_lch()
        rgb = oklch_rgb(PALETTE.fixed_l + self._tilt(i, width, DL_FIXED), C, H)
        return self._cell(rgb, PALETTE.fixed_text)

    def effort(self, level, kind, lch, filled, i, fill, width):
        tilt = self._tilt(i, width, DL_FILL)
        if not filled:
            rgb = self._track(320.0 if kind == "rainbow" else lch[2], tilt)
        elif kind == "rainbow":
            f = i / (fill - 1) if fill > 1 else 0.0
            rgb = oklch_rgb(0.70 + PALETTE.dl_fill + tilt * 0.3, 0.16, 300.0 * f)
        else:
            L, C, H = lch
            rgb = oklch_rgb(L + PALETTE.dl_fill + tilt, C, H)
        return self._cell(rgb, _contrast_fg(rgb))

    def separator(self):
        return RESET + self._fg(SEP_RGB) + f" {DIAMOND} " + RESET


class TrueColorInk(RgbInk):
    """24-bit RGB: the full ramp with its left->right lightness gradient."""

    gradient = True

    def _bg(self, rgb): return bg(*rgb)
    def _fg(self, rgb): return fg(*rgb)


class Ansi256Ink(RgbInk):
    """The indexed 256-colour palette, drawn as flat blocks.

    The 6x6x6 cube is far too coarse for a subtle gradient: neighbouring cells
    either round to the same index, which shows nothing, or jump a whole cube
    step, which shows a seam.
    """

    def _bg(self, rgb): return f"\033[48;5;{_x256(rgb)}m"
    def _fg(self, rgb): return f"\033[38;5;{_x256(rgb)}m"


class Ansi16Ink(Ink):
    """The 8/16 legacy SGR colours.

    Neither a gradient nor an orange exists at this depth, so the ramp collapses
    to three honest zones and every bar is flat.
    """

    def _lit(self, base: int) -> tuple[str, str]:
        b = (100 + base) if PALETTE.bright16 else (40 + base)
        return _sgr(b, PALETTE.fill16_fg), _sgr(b - 10)

    def _empty(self) -> tuple[str, str]:
        b, f = PALETTE.track16
        return _sgr(b, f), _sgr(b - 10)

    def ramp(self, frac, filled, i, width):
        if not filled:
            return self._empty()
        return self._lit(next(c for t, c in RAMP16 if frac < t))

    def fixed(self, i, width):
        b, f = PALETTE.fixed16
        return _sgr(b, f), _sgr(b - 10)

    def effort(self, level, kind, lch, filled, i, fill, width):
        return self._lit(EFFORT_SGR.get(level, 5)) if filled else self._empty()

    def separator(self):
        return RESET + _sgr(PALETTE.sep16) + f" {DIAMOND} " + RESET


class MonoInk(Ink):
    """No colour at all.

    Reverse video is an SGR *attribute*, not a colour, so a filled cell still
    reads as filled where no palette exists. The pointy caps are dropped: at
    this depth nothing is known about the terminal beyond its lack of colour.
    """

    caps = False

    def _state(self, filled: bool) -> tuple[str, str]:
        return ("\033[7m" if filled else "\033[27m"), ""

    def ramp(self, frac, filled, i, width):
        return self._state(filled)

    def fixed(self, i, width):
        return self._state(True)

    def effort(self, level, kind, lch, filled, i, fill, width):
        return self._state(filled)

    def separator(self):
        """A plain pipe: the pointy caps are gone here, and so is the diamond."""
        return " | "


INKS = {
    ColorSupport.MONO:      MonoInk,
    ColorSupport.ANSI16:    Ansi16Ink,
    ColorSupport.ANSI256:   Ansi256Ink,
    ColorSupport.TRUECOLOR: TrueColorInk,
}

#: Active renderer; `main()` replaces it once the colour depth is known.
INK = TrueColorInk()


# ── Segment builders ──────────────────────────────────────────────────────────
def _label(icon, text):
    """Build a padded ` icon text ` label, gracefully handling empty icon/text."""
    if icon and text != "":
        return f" {icon} {text} "
    if icon:
        return f" {icon} "
    return f" {text} "


def _pad(label, min_width):
    """Pad a label up to min_width (spaces before trailing space)."""
    if len(label) >= min_width:
        return label
    return label[:-1] + " " * (min_width - len(label)) + label[-1]




def _compose(label, cells):
    """Join styled cells into one segment, capping it when the Ink draws caps.

    `cells` carries one (cell escape, cap tint) pair per character of `label`,
    as produced by the active Ink.
    """
    body = "".join(esc + ch for (esc, _), ch in zip(cells, label))
    if not INK.caps:
        return body + RESET
    return (RESET + cells[0][1] + PL_L +
            body +
            RESET + cells[-1][1] + PL_R + RESET)




def bar(icon, text, pct, min_width=0, alwaysfill=False):
    """Fill bar with hue from `pct` and a subtle left->right lightness gradient.

    pct      0..100 — controls both the fill level and (via the ramp) the hue.
    alwaysfill       — render the bar completely filled (used for the cost bar):
                       the hue still tracks `pct`, but the whole width is lit.
    """
    label = _pad(_label(icon, text), min_width)
    width = len(label)
    p = _clamp(float(pct) if pct is not None else 0.0, 0.0, 100.0)
    split = width if alwaysfill else int(round(width * p / 100.0))
    return _compose(label, [INK.ramp(p / 100.0, i < split, i, width)
                            for i in range(width)])


def fixed_bar(icon, text):
    """Solid brand-colour bar (FIXED_HEX) for the model and directory segments."""
    label = _label(icon, text)
    width = len(label)
    return _compose(label, [INK.fixed(i, width) for i in range(width)])


def effort_bar(level):
    """6-cell FILL bar for the effort level: fills 1/6 (low) .. 6/6 (ultracode).
    The filled part uses the level's own colour (orange/green/blue/purple, or a
    rainbow sweep for `max`); the rest is a dark track. Content is " <icon> <x> "
    (speedometer icon + single-letter code). Returns None for unknown levels."""
    spec = EFFORT_COLORS.get(level)
    if spec is None:
        return None
    kind, lch = spec
    text = f" {ICON_EFFORT}  {EFFORT_LETTER.get(level, '?')} "   # 6 cells
    width = len(text)
    fill = EFFORT_ORDER.index(level) + 1 if level in EFFORT_ORDER else width

    return _compose(text, [INK.effort(level, kind, lch, i < fill, i, fill, width)
                           for i in range(width)])


# ── Formatting helpers ────────────────────────────────────────────────────────
def fmt_tok(n):
    """623 -> 623, 1.7k, 46k, 764k, 1M, 1.5M.
    Only abbreviate once the unit is actually reached: k at >=1000, M at
    >=1_000_000. One decimal only for small values (<10), else integer."""
    if n is None:
        return "?"
    n = int(n)
    if n >= 1_000_000:
        v, unit = n / 1_000_000, "M"
    elif n >= 1_000:
        v, unit = n / 1_000, "k"
    else:
        return str(n)
    if v < 10 and v != int(v):
        return f"{v:.1f}{unit}"
    return f"{int(v)}{unit}"


def model_label(mid, mname):
    m = mid.lower()
    if "opus-4"     in m:                    return "Opus 4"
    if "sonnet-4"   in m and "3-5" not in m: return "Sonnet 4.6"
    if "3-5-sonnet" in m:                    return "S3.5"
    if "haiku"      in m:                    return "Haiku"
    if "3-opus"     in m or "opus-3" in m:   return "Opus 3"
    return mname.replace("Claude ", "")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def workflows_enabled(data):
    """Best-effort: are dynamic workflows enabled? Used ONLY to label the effort
    bar "wx" (xhigh + workflows). Workflows are a precondition for ultracode
    (when disabled, ultracode is removed from the /effort menu), so this is the
    closest readable proxy -- it does NOT prove ultracode is active.

    Sources Claude Code documents (https://code.claude.com/docs/en/workflows):
    `CLAUDE_CODE_DISABLE_WORKFLOWS=1` and the `disableWorkflows` setting (default
    false) at user -> project -> local; more specific wins, settings override the
    env var. Default: enabled. Caveat: we cannot see plan-level availability
    (Pro defaults workflows off until enabled in /config), so this can be wrong
    on Pro."""
    disabled = False
    env = os.environ.get("CLAUDE_CODE_DISABLE_WORKFLOWS")
    if env is not None:
        disabled = env.strip().lower() in ("1", "true", "yes")
    home = os.path.expanduser("~")
    proj = ((data.get("workspace") or {}).get("project_dir")
            or data.get("cwd") or os.getcwd())
    for p in (os.path.join(home, ".claude", "settings.json"),
              os.path.join(proj, ".claude", "settings.json"),
              os.path.join(proj, ".claude", "settings.local.json")):
        cfg = _read_json(p)
        if isinstance(cfg, dict) and "disableWorkflows" in cfg:
            disabled = bool(cfg["disableWorkflows"])
    return not disabled


def get_cwd():
    try:
        cwd = os.getcwd()
        home = os.path.expanduser("~")
        if cwd.lower().startswith(home.lower()):
            cwd = "~" + cwd[len(home):]
        cwd = cwd.replace("\\", "/")
        if len(cwd) > 40:
            parts = cwd.split("/")
            if len(parts) > 3:
                cwd = parts[0] + "/.../" + "/".join(parts[-2:])
    except Exception:
        cwd = "~"
    return cwd


# ── Terminal colour capabilities ──────────────────────────────────────────────
# The measured depth selects a renderer from INKS, so every terminal gets a real
# status line rather than a refusal. What the depth costs is detail: the
# left->right gradient needs 24-bit, and the green->yellow->orange->red ramp
# loses its orange once it is down to the eight base hues.
def _tmux_termfeatures() -> set[str] | None:
    """Terminal features tmux negotiated for the attached client, or None.

    `tmux display-message -p '#{client_termfeatures}'` is the authoritative
    answer: it lists what tmux will actually emit (e.g. "256,RGB,title").
    `tmux info` is not usable for this -- it reports the terminfo entry of the
    outer terminal type and keeps showing `RGB: [missing]` even when
    `terminal-features` / `terminal-overrides` granted RGB.

    Returns None when tmux cannot be asked at all (missing binary, dead server,
    timeout), which the caller treats as "no evidence", not as a failure.
    """
    try:
        out = subprocess.run(["tmux", "display-message", "-p",
                              "#{client_termfeatures}"],
                             capture_output=True, text=True, timeout=1.0)
        if out.returncode != 0:
            return None
        return {f.strip() for f in out.stdout.strip().split(",") if f.strip()}
    except Exception:
        return None




@dataclass(frozen=True)
class ColorCaps:
    """What the terminal receiving this render is believed to support.

    @param level    Best colour depth the terminal is believed to handle.
    @param certain  True when @p level was measured, False when it was assumed
                    because nothing could be measured. Only a *certain* lack of
                    truecolor may raise the banner; guessing would cry wolf.
    @param reason   Short human-readable reason why @p level is below
                    TRUECOLOR, or None when truecolor is available.
    """

    level: ColorSupport
    certain: bool
    reason: str | None = None

    @property
    def truecolor(self) -> bool:
        """Whether 24-bit escapes reach the terminal faithfully."""
        return self.level >= ColorSupport.TRUECOLOR


#: Colour capabilities of the terminal this process writes to. Assumed-truecolor
#: until main() replaces it with the measured value, so importing this module
#: never shells out to tmux and never blames a terminal it has not inspected.
COLOR = ColorCaps(level=ColorSupport.TRUECOLOR, certain=False)


def _term_level(term: str) -> ColorSupport:
    """Colour depth implied by a TERM string, ignoring any truecolor hint.

    Used only once truecolor has been ruled out, to record how much colour is
    left rather than collapsing every shortfall to a single flag.
    """
    t = term.strip().lower()
    if not t or t == "dumb":
        return ColorSupport.MONO
    return ColorSupport.ANSI256 if "256color" in t else ColorSupport.ANSI16


def detect_color_caps() -> ColorCaps:
    """Measure the terminal's colour depth, erring towards "it is fine".

    Inside tmux, tmux is the component that downgrades colours and therefore the
    authority on what reaches the terminal; outside tmux the convention is
    COLORTERM=truecolor|24bit or a `*-direct` terminfo entry. Anything that
    cannot be measured comes back as truecolor with @c certain=False, so the
    banner never fires without evidence. See docs/terminal-truecolor.md.
    """
    assumed = ColorCaps(level=ColorSupport.TRUECOLOR, certain=False)
    try:
        term = os.environ.get("TERM", "")
        if os.environ.get("TMUX"):
            feats = _tmux_termfeatures()
            # No answer at all, or an answer given before tmux finished
            # negotiating features with its client -- an empty list is routine
            # while a client attaches. Neither is evidence of missing RGB.
            if not feats:
                return assumed
            if "RGB" in feats:
                return ColorCaps(level=ColorSupport.TRUECOLOR, certain=True)
            level = ColorSupport.ANSI256 if "256" in feats else _term_level(term)
            return ColorCaps(level=level, certain=True,
                             reason="tmux passes no RGB")
        if os.environ.get("COLORTERM", "").strip().lower() in ("truecolor", "24bit"):
            return ColorCaps(level=ColorSupport.TRUECOLOR, certain=True)
        if "direct" in term.lower():
            return ColorCaps(level=ColorSupport.TRUECOLOR, certain=True)
        level = _term_level(term)
        reason = ("TERM is dumb" if level is ColorSupport.MONO
                  else "COLORTERM is not truecolor")
        return ColorCaps(level=level, certain=True, reason=reason)
    except Exception:
        return assumed


# ── Terminal background and command-line overrides ───────────────────────────
# Whether the terminal is light or dark cannot be measured from inside this
# script: the payload does not carry it, and the OSC 11 query that would ask the
# terminal directly needs a reply on stdin -- which Claude Code has already
# filled with the payload. Claude Code's own theme setting is the readable
# answer, and its names all begin with "light" or "dark" (the plain, "-ansi" and
# "-daltonized" variants alike).
def detect_background(data) -> str:
    """Return "light" or "dark" from Claude Code's configured theme.

    Reads the same settings cascade as `workflows_enabled()` -- user, then
    project, then project-local, with the most specific winning. Defaults to
    "dark", which is Claude Code's own default.
    """
    theme = ""
    home = os.path.expanduser("~")
    proj = ((data.get("workspace") or {}).get("project_dir")
            or data.get("cwd") or os.getcwd())
    for path in (os.path.join(home, ".claude", "settings.json"),
                 os.path.join(proj, ".claude", "settings.json"),
                 os.path.join(proj, ".claude", "settings.local.json")):
        cfg = _read_json(path)
        if isinstance(cfg, dict) and isinstance(cfg.get("theme"), str):
            theme = cfg["theme"]
    return "light" if theme.strip().lower().startswith("light") else "dark"


#: Depth names accepted by --colors.
COLOR_ARGS = {
    "mono": ColorSupport.MONO, "bw": ColorSupport.MONO,
    "ansi16": ColorSupport.ANSI16, "16": ColorSupport.ANSI16,
    "ansi256": ColorSupport.ANSI256, "256": ColorSupport.ANSI256,
    "truecolor": ColorSupport.TRUECOLOR, "24bit": ColorSupport.TRUECOLOR,
}


@dataclass(frozen=True)
class Overrides:
    """What the command line forces, in place of what would be detected."""

    background: str | None = None      #: "light", "dark", or None to detect
    level: ColorSupport | None = None  #: forced colour depth, or None to detect

    def level_caps(self) -> "ColorCaps | None":
        """The forced depth as a ColorCaps, or None when nothing was forced."""
        if self.level is None:
            return None
        return ColorCaps(level=self.level, certain=True,
                         reason="forced on the command line")


def parse_args(argv) -> Overrides:
    """Read `--light`, `--dark` and `--colors LEVEL` (or `--colors=LEVEL`).

    Unrecognised arguments are ignored on purpose: this script is invoked from a
    command string in settings.json, and a status line that aborts over its own
    arguments is worse than one that renders with detected values.
    """
    background = level = None
    rest = iter(argv)
    for arg in rest:
        if arg == "--light":
            background = "light"
        elif arg == "--dark":
            background = "dark"
        elif arg.startswith("--colors"):
            value = arg.split("=", 1)[1] if "=" in arg else next(rest, "")
            level = COLOR_ARGS.get(value.strip().lower(), level)
    return Overrides(background, level)


# ── Startup line ──────────────────────────────────────────────────────────────
# Shown before anything about the session or the terminal has been established,
# so it is deliberately the plainest thing this script can draw: the 8/16 legacy
# SGR colours and plain ASCII only -- no 24-bit escapes, no Nerd Font glyphs, no
# Powerline end-caps. It stays readable on a monochrome terminal.
STARTUP_SGR = "\033[40;37m"        # dark terminal: grey on black, deliberately quiet
STARTUP_SGR_LIGHT = "\033[47;90m"  # light terminal: grey on white, same idea
STARTUP_TEXT = "starting..."
STARTUP_SEP = " | "


def is_starting(data) -> bool:
    """Whether no prompt has been submitted in this run of the session yet.

    `prompt_id` is the id of the prompt being handled and is **absent until the
    first user input**, which makes its absence the one signal that means "the
    user has not typed anything yet". Claude Code renders the status line once
    when a session starts -- including when it is resumed -- and that render is
    the one this guards.

    The measured fields cannot stand in for it. A resumed session restores its
    context, cost and duration (they reset only on `/clear`), so token counters
    and `used_percentage` are already non-zero at startup. `current_usage` is
    null both before the first API call *and* after `/compact`, so on its own it
    would drag the startup line back after every compaction.
    """
    return not data.get("prompt_id")


def startup_line(data) -> str:
    """The line shown until real data arrives, holding only what is trustworthy.

    This early, the model and the working directory are already correct while
    every measured value is not, so those two are shown next to a plain
    "starting..." block and nothing else is claimed.
    """
    model = data.get("model") or {}
    mid = model.get("id") or ""
    blocks = [STARTUP_TEXT,
              model_label(mid, model.get("display_name") or mid),
              get_cwd()]
    body = STARTUP_SEP.join(b for b in blocks if b)
    sgr = STARTUP_SGR_LIGHT if PALETTE is LIGHT_PALETTE else STARTUP_SGR
    return f"{sgr} {body} {RESET}"


# ── Icons (Nerd Font) ─────────────────────────────────────────────────────────
ICON_DIR   = chr(0xF07C)   # nf-fa-folder_open
ICON_MODEL = chr(0xF489)   # nf-dev-terminal
ICON_CTX   = chr(0xE28C)   # brain glyph (user's Nerd Font)
ICON_5H    = chr(0xF017)   # nf-fa-clock_o
ICON_7D    = chr(0xF073)   # nf-fa-calendar
ICON_COST  = chr(0xF155)   # nf-fa-dollar
ICON_EFFORT = chr(0xF04C5) # nf-md-speedometer



# ── Cost: thresholds for the green->red hue of the (always-filled) cost bar ────
COST_GREEN = 15.0   # <= this many $ stays green
COST_RED   = 50.0   # >= this many $ is fully red


def cost_pct(cost):
    return _clamp((cost - COST_GREEN) / (COST_RED - COST_GREEN) * 100.0, 0.0, 100.0)


# ── Marquee: scrolling a line that is wider than the terminal ─────────────────
SCROLL_SPEED  = 6.0   # visible cells travelled per second
SCROLL_HOLD   = 2.0   # seconds the line rests at each end before turning back
SCROLL_MARGIN = 0     # cells kept free on the right (room for notifications)

#: Every escape this script emits is an SGR (colour) sequence.
_SGR = re.compile(r"\033\[[0-9;]*m")


def _char_cells(ch: str) -> int:
    """How many terminal cells one character occupies.

    Combining marks ride on the previous glyph and take no width of their own;
    East-Asian wide and fullwidth characters take two. Nerd Font glyphs live in
    the private use areas, which report neither -- they render single-width in
    the Mono variants this status line is built for.
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_width(s: str) -> int:
    """Width of a rendered line in terminal cells, ignoring colour escapes."""
    return sum(_char_cells(c) for c in _SGR.sub("", s))


def _slice_cells(s: str, start: int, count: int) -> str:
    """Cut `count` visible cells out of `s`, starting at cell `start`.

    A plain string slice cannot do this: the rendered line is mostly colour
    escapes (roughly 3900 bytes carry 124 visible cells), so slicing by index
    would cut an escape in half and would also drop every colour set before the
    window began. This walks the string instead, keeps the pen state -- the SGR
    escapes seen since the last full reset -- and re-emits it at the window's
    first visible character, so the cut-out piece renders in the colours it had.

    A double-width glyph straddling either edge becomes a space, which keeps the
    result exactly `count` cells wide instead of overflowing by one.
    """
    out: list[str] = []
    pen: list[str] = []
    opened = False
    col = 0
    end = start + count
    i = 0
    n = len(s)
    while i < n:
        m = _SGR.match(s, i)
        if m:
            esc = m.group(0)
            if esc[2:-1] in ("", "0"):        # a full reset clears the pen
                pen.clear()
            else:
                pen.append(esc)
            if opened:
                out.append(esc)
            i = m.end()
            continue
        ch = s[i]
        w = _char_cells(ch)
        if col >= start and col + w <= end:
            if not opened:
                out.append("".join(pen))
                opened = True
            out.append(ch)
        elif w == 2 and col < end and col + w > start:
            if not opened:
                out.append("".join(pen))
                opened = True
            out.append(" ")
        col += w
        i += 1
    out.append(RESET)
    return "".join(out)


def _scroll_offset(overflow: int, now: float) -> int:
    """Where the viewport sits at time `now`: a triangle wave with end pauses.

    The cycle is rest at home, slide out to `overflow`, rest there, slide back.
    Deriving the position from the wall clock rather than a frame counter keeps
    the speed identical whether Claude Code renders once a second (the idle
    `refreshInterval`) or three times (while the model is working), and needs no
    state carried between what are otherwise unrelated one-shot processes.
    """
    travel = overflow / SCROLL_SPEED if SCROLL_SPEED > 0 else 0.0
    period = 2.0 * (travel + SCROLL_HOLD)
    if travel <= 0 or period <= 0:
        return 0
    t = now % period
    if t < SCROLL_HOLD:                                   # resting at home
        return 0
    t -= SCROLL_HOLD
    if t < travel:                                        # sliding out
        return int(round(overflow * t / travel))
    t -= travel
    if t < SCROLL_HOLD:                                   # resting at the far end
        return overflow
    t -= SCROLL_HOLD
    return int(round(overflow * (1.0 - t / travel)))      # sliding home


def terminal_width() -> int | None:
    """Terminal width in cells, or None when Claude Code reported none.

    Claude Code captures this script's stdout instead of attaching it to the
    terminal, so `os.get_terminal_size()` and `tput cols` see nothing; the size
    arrives only in the `COLUMNS` environment variable.
    """
    try:
        w = int(os.environ.get("COLUMNS", ""))
    except ValueError:
        return None
    return w if w > 0 else None


def marquee(line: str, now: float | None = None) -> str:
    """Scroll `line` back and forth while it is wider than the terminal.

    A line that fits comes back untouched, and so does one whose terminal width
    is unknown -- cutting a line to a guessed width would hide segments that
    were rendering fine.
    """
    width = terminal_width()
    if width is None:
        return line
    avail = width - SCROLL_MARGIN
    if avail <= 0:
        return line
    overflow = visible_width(line) - avail
    if overflow <= 0:
        return line
    offset = _scroll_offset(overflow, time.time() if now is None else now)
    return _slice_cells(line, offset, avail)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        sys.stdout.write("claude\n")
        return

    # DEBUG capture: see docs/payload-schema.md ("How to refresh"). Enabled by
    # STATUSLINE_DEBUG=1 or by creating the marker file `_capture` next to this
    # script -- the marker needs no restart, which is what makes the renders
    # around session start observable at all.
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if (os.environ.get("STATUSLINE_DEBUG", "0") == "1"
                or os.path.exists(os.path.join(here, "_capture"))):
            with open(os.path.join(here, "_last_payload.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(raw)
            # Append every render, so the sequence around session start survives
            # instead of only the most recent line. Capped so a marker file left
            # behind by accident cannot grow without bound.
            log = os.path.join(here, "_payload_log.jsonl")
            if not os.path.exists(log) or os.path.getsize(log) < 8_000_000:
                with open(log, "a", encoding="utf-8") as fh:
                    # COLUMNS/LINES ride along because the terminal size reaches
                    # this script only through the environment, never through the
                    # payload -- and the scrolling in marquee() depends on it.
                    fh.write(json.dumps({
                        "at": time.time(),
                        "cols": os.environ.get("COLUMNS"),
                        "lines": os.environ.get("LINES"),
                        "payload": data,
                    }) + "\n")
    except Exception:
        pass

    # The background decides every lightness in the line, the startup line's
    # included, so it is settled before anything is drawn. It needs no terminal
    # probing -- it comes from Claude Code's own theme setting.
    global COLOR, INK, PALETTE
    opts = parse_args(sys.argv[1:])
    PALETTE = (LIGHT_PALETTE
               if (opts.background or detect_background(data)) == "light"
               else DARK_PALETTE)

    # Before the first API response the payload is still filling in. Announce
    # that plainly instead of drawing bars over fields that have not arrived.
    if is_starting(data):
        sys.stdout.write(startup_line(data) + "\n")
        return

    # Pick the renderer for whatever colour depth this terminal has. Every
    # depth draws a real status line, so nothing is refused here.
    COLOR = opts.level_caps() or detect_color_caps()
    INK = INKS[COLOR.level]()

    mid   = data.get("model", {}).get("id", "")
    mname = data.get("model", {}).get("display_name", mid)
    cw    = data.get("context_window", {}) or {}
    rate  = data.get("rate_limits", {}) or {}
    # Billing mode is inferred, never given: `rate_limits` appears only for
    # subscriptions, and only *after the first API response in the session*. Its
    # absence therefore means "API billing" only once a response has actually
    # happened -- which `current_usage` records (null before the first API call).
    # Without that second half, a session that has not called the API yet reads
    # as API billing and bills the user for a cost it merely restored from disk.
    answered = cw.get("current_usage") is not None
    is_api = not rate and answered
    eff = data.get("effort") or {}
    effort = eff.get("level")
    # Real ultracode CANNOT be detected from a status line (verified 2026-06-17):
    # the payload carries effort only as `effort.level`, and "Ultracode is not a
    # distinct level and reports as xhigh." It is session-only and not on disk;
    # the purple "ultracode" in the TUI is Claude Code's own effort indicator
    # drawn where the session name goes (anthropics/claude-code #63899), NOT the
    # payload's `session_name`. See docs/ultracode-detection.md.
    #   * If Claude Code ever exposes a real flag -> the first branch lights up
    #     the genuine "ultracode" bar (letter "u").
    #   * Until then, surface the honest proxy "wx" (xhigh + workflows enabled).
    #     Workflows are a precondition for ultracode, so "wx" == "ultracode is
    #     possible this session" -- a full deep-purple bar like the old look,
    #     but labelled truthfully rather than claiming ultracode is active.
    if eff.get("ultracode") is True:                       # absent in payloads today
        effort = "ultracode"
    elif effort == "xhigh" and workflows_enabled(data):
        effort = "wx"

    ctx_size = cw.get("context_window_size", 200_000) or 200_000
    used_pct = cw.get("used_percentage")
    total_in = cw.get("total_input_tokens", 0) or 0
    used_tok = int(ctx_size * used_pct / 100) if used_pct is not None else total_in

    five_pct  = (rate.get("five_hour") or {}).get("used_percentage")
    seven_pct = (rate.get("seven_day") or {}).get("used_percentage")

    # Real session cost as reported by Claude Code (no own pricing estimate).
    cost = (data.get("cost", {}) or {}).get("total_cost_usd")

    segs = []

    # 1. Context window
    ctx_label = f"{fmt_tok(used_tok)}/{fmt_tok(ctx_size)}"
    if used_pct is not None:
        ctx_label += f" {used_pct:.0f}%"
    segs.append(bar(ICON_CTX, ctx_label, used_pct))

    # 2. 5-hour limit (subscription only)
    if five_pct is not None:
        segs.append(bar(ICON_5H, f"{five_pct:.0f}%", five_pct, min_width=9))

    # 3. 7-day limit (subscription only)
    if seven_pct is not None:
        segs.append(bar(ICON_7D, f"{seven_pct:.0f}%", seven_pct, min_width=9))

    # 4. Real cost — only on API billing (no subscription rate-limits) and > 0.
    #    On a subscription, cost.total_cost_usd is just an estimate -> hide it.
    #    is_api only means anything past the startup gate above: an early payload
    #    has no rate_limits yet and would read as API billing.
    if is_api and cost is not None and cost > 0:
        cstr = "<$0.01" if cost < 0.01 else f"${cost:.2f}"
        segs.append(bar("", cstr, cost_pct(cost), min_width=8, alwaysfill=True))

    # 5. Model (fixed)
    segs.append(fixed_bar(ICON_MODEL, model_label(mid, mname)))

    # 6. Effort level (6-cell colour bar) between model and directory
    e_seg = effort_bar(effort)
    if e_seg is not None:
        segs.append(e_seg)

    # 7. Working directory (fixed)
    segs.append(fixed_bar(ICON_DIR, get_cwd()))

    sys.stdout.write(marquee(INK.separator().join(segs)) + "\n")


if __name__ == "__main__":
    main()
