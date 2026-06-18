# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file status line for Claude Code. `statusline.py` reads one JSON payload
from **stdin** (the data Claude Code sends each render) and writes **one ANSI line**
to stdout — a Powerline-style bar of colored segments. All colors are computed in the
**OKLCH** color space (via `coloraide`) for perceptually smooth green→red gradients,
then gamut-mapped to sRGB truecolor escapes.

> This repo lives at `~/.claude/statusline` and **is** the live status line. If the
> status line is installed as an editable `uv run --project …` command (README option B),
> edits to `statusline.py` take effect on the next render. A script that crashes or has a
> syntax error degrades the status line to the literal fallback text `claude` (see the
> top-level `try/except` in `main()`), so verify a run before considering a change done.

## Docs & staying current

The behaviour of this script is entirely dictated by the **JSON payload Claude Code
feeds it**, and that payload gains fields over time. Don't trust memory — check:

- [`docs/payload-schema.md`](docs/payload-schema.md) — every payload field, which ones
  the script uses, behaviour notes, and **how to refresh** (official docs URLs + the
  `STATUSLINE_DEBUG=1` capture trick).
- [`docs/ultracode-detection.md`](docs/ultracode-detection.md) — the ultracode/xhigh
  distinction, the current (unverified) detection, and how to verify it.

To get authoritative, up-to-date Claude Code facts, dispatch the **`claude-code-guide`**
subagent (it can `WebFetch`/`WebSearch` the official docs) or read
<https://code.claude.com/docs/en/statusline> and `.../model-config` directly.

## Commands

```sh
uv sync                                       # create .venv (installs coloraide)
echo '{...payload...}' | uv run claude-code-statusline   # run the entry point against a payload
uv run --script statusline.py < payload.json  # PEP 723 mode — no pyproject involved
```

There is **no test suite and no linter configured**. "Testing" means piping a representative
JSON payload through the script and eyeballing the rendered line. A minimal smoke payload:

```sh
echo '{"model":{"id":"claude-opus-4-8","display_name":"Opus 4.8"},"context_window":{"context_window_size":1000000,"used_percentage":20},"rate_limits":{"five_hour":{"used_percentage":10}}}' | uv run claude-code-statusline
```

To capture the **real** payload Claude Code sends, set `STATUSLINE_DEBUG=1`; the next render
dumps it to `_last_payload.json` next to the script (gitignored). This is the way to discover
new/changed payload fields.

## Architecture

**Data flow** (`main()`): read stdin → `json.loads` → pull fields → append segment strings to
a `segs` list in fixed order → join with the diamond separator → write one line. Segment order:
context · 5h · 7d · cost · model · effort · directory.

**Three segment builders**, all ending in `_wrap()` (which adds the pointy Powerline end-caps):
- `bar(icon, text, pct, …)` — fill bar whose **hue** comes from `pct` via `RAMP` and whose
  fill **level** is also `pct`. `alwaysfill=True` lights the whole width but still hues by `pct`
  (used by the cost bar).
- `fixed_bar(icon, text)` — solid brand color `FIXED_HEX` (model / directory).
- `effort_bar(level)` — 6-cell bar, fills 1/6…6/6 per `EFFORT_ORDER`, colored by `EFFORT_COLORS`
  (each level has its own color or a `rainbow` sweep — **not** the green→red ramp).

**Color core:** `RAMP` is four OKLCH stops (green→yellow→orange→red); `_ramp_at(frac)` linearly
interpolates them. `oklch_rgb(L,C,H)` converts to sRGB and is `@lru_cache`d — keep its inputs
hashable. Every bar carries a subtle left→right lightness gradient via `_slope()`.

### Two billing modes drive what's shown
`is_api = not rate` (no `rate_limits` in the payload ⇒ API billing). On **subscription**, the
5h/7d bars render and the cost bar is **hidden** (Claude Code reports only an *estimate* there).
On **API billing**, there are no rate-limit bars and the cost bar shows real
`cost.total_cost_usd` (only when `> 0`).

### Ultracode detection — impossible to detect; "wx" proxy instead
Official docs (verified 2026-06-17): *"Ultracode is not a distinct level and reports as
`xhigh`."* It is **session-only** — not in any `settings.json`, no env var, not on disk —
so **nothing the status line can read distinguishes ultracode from plain `xhigh`**. The
effort segment in `main()` therefore does three things, in order:
1. **`ultracode` (letter `u`)** — dormant, future-proof hook (`eff.get("ultracode") is True`),
   off today; lights up the genuine bar if Claude Code ever exposes a real field.
2. **`wx` (letter `wx`)** — honest proxy: when `effort == "xhigh"` **and**
   `workflows_enabled()`, show a full deep-purple bar (same look as old ultracode). Since
   workflows are a *precondition* for ultracode, this truthfully means "ultracode is
   *possible* this session" — it does **not** claim it's active.
3. Otherwise render `effort.level` as-is.

`workflows_enabled()` reads the documented signals (`CLAUDE_CODE_DISABLE_WORKFLOWS` env +
`disableWorkflows` setting, user→project→local). Full write-up + citations:
[`docs/ultracode-detection.md`](docs/ultracode-detection.md).

> **Never label the xhigh+workflows state "ultracode".** That earlier mistake claimed
> ultracode was *active* when it usually wasn't — the fix was the **label** (`wx`), not the
> signal. Also dead: `session_name == "ultracode"` — the purple TUI label is Claude Code's
> effort indicator drawn in the name slot, **not** the payload's `session_name` (upstream
> bug [#63899](https://github.com/anthropics/claude-code/issues/63899)).

## Editing conventions

- **Two dependency declarations must stay in sync:** the PEP 723 header at the top of
  `statusline.py` (`# dependencies = [...]`) and `dependencies` in `pyproject.toml`. The script
  must run both as a standalone PEP 723 script and via the installed entry point.
- **Tuning knobs live at the top of `statusline.py`** (`RAMP`, `COST_GREEN`/`COST_RED`,
  `EFFORT_COLORS`, `EFFORT_LETTER`, `DL_FILL`/`DL_FIXED`, `L_EMPTY`/`C_EMPTY`, `FIXED_HEX`,
  `ICON_*`). Prefer changing these over rewriting the builders; they are documented in the README
  "Customization" table.
- **Icons are Nerd Font codepoints** (`ICON_*`) and the output assumes a **Nerd Font + 24-bit
  truecolor** terminal. Swapping a glyph means swapping its codepoint.
- **Fail soft:** the script should always emit *something* on a line. Wrap risky parsing so a bad
  payload never produces a stack trace into the status line.
- `statusline.py` is a **top-level module, not a package** (`[tool.hatch.build.targets.wheel]`
  includes only `statusline.py`); the entry point is `statusline:main`.
