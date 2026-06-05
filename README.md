# Claude Code Statusline

A colourful, Powerline-style status line for [Claude Code](https://claude.com/claude-code).
Bars are coloured in the **OKLCH** colour space for smooth, perceptually even
gradients, and each progress bar carries a subtle left→right lightness gradient.

![screenshot](screenshot.png)

## Segments

| Segment | Icon | What it shows | Colour |
|---|---|---|---|
| **Context** | brain | Used / total context tokens + `%` | fills green → yellow → orange → red as it grows |
| **5h limit** | clock | 5‑hour rate‑limit usage `%` *(subscription only)* | green → red by usage |
| **7d limit** | calendar | 7‑day rate‑limit usage `%` *(subscription only)* | green → red by usage |
| **Cost** | – | Real session cost `$x.xx` *(API billing only)* | always filled, green ≤ \$15 → red ≥ \$50 |
| **Model** | terminal | Short model name | fixed brand colour `#10475D` |
| **Effort** | speedometer | Reasoning effort, filled 1/6 … 6/6 + a letter | own palette per level (see below) |
| **Directory** | folder | Current working directory | fixed brand colour `#10475D` |

### Effort levels

The effort bar fills from `1/6` (low) to `6/6` (ultracode) and uses its own colour
per level — independent of the green→red progress ramp:

| Level | Fill | Letter | Colour |
|---|---|---|---|
| `low` | 1/6 | `l` | orange |
| `medium` | 2/6 | `m` | green |
| `high` | 3/6 | `h` | blue |
| `xhigh` | 4/6 | `x` | light purple |
| `max` | 5/6 | `m` | rainbow |
| `ultracode` | 6/6 | `u` | deep purple |

> `medium` and `max` share the letter `m`; they are easily told apart by colour
> and fill level.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — runs the script and manages its single
  dependency ([`coloraide`](https://facelessuser.github.io/coloraide/)) automatically.
- A **Nerd Font** in your terminal (for the glyphs). Tested with the
  Material‑Design speedometer (`U+F04C5`) and a brain glyph (`U+E28C`); swap the
  codepoints near the top of `statusline.py` if your font differs.
- A terminal with **24‑bit true‑colour** support.

## Installation

1. Clone this repo somewhere stable, e.g. into your Claude config dir:

   ```sh
   git clone https://github.com/derDere/claude-code-statusline.git
   ```

2. Point Claude Code at it. In your **`settings.json`** (`~/.claude/settings.json`)
   add a `statusLine` entry that runs the script via `uv`:

   ```jsonc
   {
     "statusLine": {
       "type": "command",
       "command": "uv run --script /ABSOLUTE/PATH/TO/statusline.py"
     }
   }
   ```

   Use an **absolute path** (on Windows, forward slashes work fine, e.g.
   `C:/Users/<you>/.claude/statusline/statusline.py`).

That's it — the first run installs `coloraide` into uv's cache; subsequent runs are fast.

### Why `uv run --script`?

`statusline.py` is a self‑contained [PEP 723](https://peps.python.org/pep-0723/)
script: its dependencies are declared in the `# /// script` header at the top of
the file, so `uv run --script` needs **no** virtual environment or
`pyproject.toml` to run it. The `pyproject.toml` in this repo exists only for
local development (see below); the runtime command above does not use it.

## Customization

All knobs live near the top of `statusline.py`:

- `RAMP` — the four OKLCH stops of the green→yellow→orange→red progress ramp.
- `COST_GREEN` / `COST_RED` — dollar thresholds where the cost bar is fully green / red.
- `EFFORT_COLORS` — per‑level colour (or `rainbow`) for the effort bar.
- `EFFORT_LETTER` — the single‑letter code shown per effort level.
- `DL_FILL` / `DL_FIXED` — strength of the left→right lightness gradient.
- `L_EMPTY` / `C_EMPTY` — lightness/chroma of the empty bar track.
- `FIXED_HEX` — brand colour of the fixed (model / directory) bars.
- `ICON_*` — glyph codepoints.

## Behaviour notes

### Cost is only shown on API billing

The cost segment appears **only when no subscription rate‑limits are present**
in the payload (i.e. you are billed per‑API‑call) and the reported cost is `> 0`.
On a subscription, Claude Code still reports an *estimated* `cost.total_cost_usd`,
which is intentionally hidden — the bar reflects money actually spent, not an
estimate of the session's worth.

### Ultracode detection

Claude Code does **not** expose "ultracode" in the status‑line payload — it
reports as `effort.level == "xhigh"`. As a best‑available proxy, this script
treats `xhigh` **+ workflows enabled** as ultracode, reading the persistent
`disableWorkflows` setting (user → project → local; more specific wins) to decide
whether workflows are enabled. The live, session‑only `/effort ultracode` toggle
is not visible to the status line, so `xhigh` with workflows enabled always
renders as ultracode.

## Development

```sh
uv sync                       # create .venv with coloraide
uv run python statusline.py < sample.json   # run against a captured payload
```

To capture the real payload Claude Code sends, set `STATUSLINE_DEBUG=1`; the next
render writes `_last_payload.json` next to the script.

## License

MIT — see [LICENSE](LICENSE).
