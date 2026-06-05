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

# Effort level -> its own colour (own gradient, NOT the green->red ramp).
# Each entry: ("solid", (L, C, H))  -> subtle left->right lightness gradient
#             ("rainbow", None)     -> hue sweep across the whole bar
# NOTE: ultracode is NOT exposed in the statusline payload; it reports as
#       "xhigh" (per Claude Code docs). The deeppurple entry is therefore
#       future-proofing and will not trigger today.
EFFORT_COLORS = {
    "low":       ("solid",   (0.68, 0.15,  60.0)),   # orange
    "medium":    ("solid",   (0.66, 0.14, 150.0)),   # green
    "high":      ("solid",   (0.60, 0.14, 255.0)),   # blue
    "xhigh":     ("solid",   (0.75, 0.11, 322.0)),   # light purple
    "max":       ("rainbow", None),                  # rainbow
    "ultracode": ("solid",   (0.35, 0.16, 308.0)),   # deep purple
}
# Single-letter code shown in the effort bar (" <icon>  <x> ").
EFFORT_LETTER = {
    "low": "l", "medium": "m", "high": "h",
    "xhigh": "x", "max": "m", "ultracode": "u",
}
# Fill order: low fills 1/6 ... ultracode fills 6/6.
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


def _slope(i, width, amp):
    """Subtle left->right lightness offset for cell i, in [-amp, +amp]."""
    x = i / (width - 1) if width > 1 else 0.5
    return (x - 0.5) * 2.0 * amp


def _wrap(content, left_rgb, right_rgb):
    """Add pointy end-caps coloured to match the bar's edge cells."""
    return (RESET + fg(*left_rgb) + PL_L +
            content +
            RESET + fg(*right_rgb) + PL_R + RESET)


def _contrast_fg(rgb):
    """Pick a readable text colour (dark on light cells, white on dark cells)."""
    r, g, b = rgb
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return (15, 18, 24) if lum > 0.62 else WHITE


def bar(icon, text, pct, min_width=0, alwaysfill=False):
    """Fill bar with hue from `pct` and a subtle left->right lightness gradient.

    pct      0..100 — controls both the fill level and (via the ramp) the hue.
    alwaysfill       — render the bar completely filled (used for the cost bar):
                       the hue still tracks `pct`, but the whole width is lit.
    """
    label = _pad(_label(icon, text), min_width)
    width = len(label)
    p = _clamp(float(pct) if pct is not None else 0.0, 0.0, 100.0)

    lf, cf, hf = _ramp_at(p / 100.0)
    split = width if alwaysfill else int(round(width * p / 100.0))

    def cell_rgb(i):
        if i < split:                                   # lit (gradient)
            return oklch_rgb(lf + _slope(i, width, DL_FILL), cf, hf)
        return oklch_rgb(L_EMPTY + _slope(i, width, DL_FILL) * 0.6, C_EMPTY, hf)

    content = "".join(bg(*cell_rgb(i)) + fg(*WHITE) + ch
                      for i, ch in enumerate(label))
    return _wrap(content, cell_rgb(0), cell_rgb(width - 1))


def fixed_bar(icon, text):
    """Solid brand-colour bar (#10475D) with a subtle lightness gradient."""
    label = _label(icon, text)
    width = len(label)
    lb, cb, hb = _fixed_lch()

    def cell_rgb(i):
        return oklch_rgb(lb + _slope(i, width, DL_FIXED), cb, hb)

    content = "".join(bg(*cell_rgb(i)) + fg(*WHITE) + ch
                      for i, ch in enumerate(label))
    return _wrap(content, cell_rgb(0), cell_rgb(width - 1))


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

    def cell_rgb(i):
        slope = (i / (width - 1) - 0.5) * 2.0 * DL_FILL if width > 1 else 0.0
        if i < fill:                                   # filled (level colour)
            if kind == "rainbow":
                f = i / (fill - 1) if fill > 1 else 0.0
                return oklch_rgb(0.70 + slope * 0.3, 0.16, 300.0 * f)
            L, C, H = lch
            return oklch_rgb(L + slope, C, H)
        hue = 320.0 if kind == "rainbow" else lch[2]   # dark track, same hue
        return oklch_rgb(L_EMPTY + slope * 0.6, C_EMPTY, hue)

    content = "".join(bg(*cell_rgb(i)) + fg(*_contrast_fg(cell_rgb(i))) + ch
                      for i, ch in enumerate(text))
    return _wrap(content, cell_rgb(0), cell_rgb(width - 1))


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
    """Best-available proxy for 'workflows enabled': the persistent
    `disableWorkflows` setting (user -> project -> local, more specific wins).
    The session-only `/effort ultracode` flag is NOT exposed to the statusline,
    so this reads the settings files instead. Default: workflows enabled."""
    disabled = False
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


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        sys.stdout.write("claude\n")
        return

    # TEMP DEBUG: dump the real payload so we can see effort/ultracode fields.
    try:
        if os.environ.get("STATUSLINE_DEBUG", "0") == "1":
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "_last_payload.json"), "w", encoding="utf-8") as fh:
                fh.write(raw)
    except Exception:
        pass

    mid   = data.get("model", {}).get("id", "")
    mname = data.get("model", {}).get("display_name", mid)
    cw    = data.get("context_window", {}) or {}
    rate  = data.get("rate_limits", {}) or {}
    # No subscription rate-limits in the payload => running on API billing.
    is_api = not rate
    effort = (data.get("effort") or {}).get("level")
    # Ultracode == xhigh effort + workflows enabled. The live session flag is
    # not exposed to the statusline, so use the persistent workflow setting.
    if effort == "xhigh" and workflows_enabled(data):
        effort = "ultracode"

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

    sep = RESET + fg(90, 100, 120) + f" {DIAMOND} " + RESET
    sys.stdout.write(sep.join(segs) + "\n")


if __name__ == "__main__":
    main()
