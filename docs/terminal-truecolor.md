# Colour depth: what each terminal gets, and how to reach true colour

Every colour in this status line is computed in OKLCH. What reaches the terminal
depends on what it can encode, and `statusline.py` carries a renderer for each of
the four depths rather than one renderer that degrades:

| Depth | What the bars look like |
|---|---|
| `TRUECOLOR` | the full green→yellow→orange→red ramp as `ESC[38;2;r;g;b m` / `ESC[48;2;r;g;b m`, with the subtle left→right lightness gradient |
| `ANSI256` | the same ramp rounded onto the 6×6×6 colour cube, drawn as flat blocks |
| `ANSI16` | three zones — green, yellow, red — in the legacy SGR colours, flat |
| `MONO` | no colour; the filled part of a bar is marked with reverse video (`ESC[7m`) |

Only 24-bit carries the whole design, which is why it is worth reaching if your
terminal can do it. The rest of this document is how to get there.

Two of the lower renderers make a deliberate choice worth knowing about. `ANSI256`
draws **flat** because the cube is far too coarse for a subtle gradient: adjacent
cells either round to the same index, showing nothing, or jump a whole step,
showing a seam. `ANSI16` maps the ramp by **meaning** — green, yellow, red — and
never by nearest colour, because matched metrically any mid-lightness green lands
on grey, which would turn a healthy context bar into a dead-looking one.

## How the depth is decided

`detect_color_caps()` measures the terminal once per render and returns a
`ColorCaps` record, which `main()` stores in the module-level `COLOR` variable:

| Field | Meaning |
|---|---|
| `level` | `MONO` (0), `ANSI16` (1), `ANSI256` (2) or `TRUECOLOR` (3) — the best colour depth the terminal is believed to handle |
| `certain` | whether `level` was measured, or assumed because nothing could be measured |
| `reason` | short text naming why `level` is below `TRUECOLOR`, or `None` |

`level` selects the renderer. Anything undecidable is recorded as truecolor with
`certain = False`, so an unmeasurable terminal gets the full design rather than a
guessed-down one.

| Situation | Signal `statusline.py` reads | Verdict |
|---|---|---|
| `TMUX` is set | `tmux display-message -p '#{client_termfeatures}'` contains `RGB` | `TRUECOLOR`, measured |
| `TMUX` is set | the same query lists features but no `RGB` | `ANSI256`/`ANSI16`, measured — reason: `tmux passes no RGB` |
| `TMUX` is set | the query cannot be answered, or comes back empty | `TRUECOLOR`, assumed (no evidence either way) |
| No tmux | `COLORTERM` is `truecolor` or `24bit` | `TRUECOLOR`, measured |
| No tmux | `TERM` contains `direct` (e.g. `xterm-direct`) | `TRUECOLOR`, measured |
| No tmux | `TERM` contains `256color` | `ANSI256`, measured — reason: `COLORTERM is not truecolor` |
| No tmux | `TERM` is unset or `dumb` | `MONO`, measured — reason: `TERM is dumb` |
| No tmux | none of the above | `ANSI16`, measured — reason: `COLORTERM is not truecolor` |

An **empty** feature list counts as no evidence rather than as a missing `RGB`.
tmux answers the query while a client is still attaching and has not finished
negotiating features, and reading that silence as a negative would drop a terminal
whose colours are perfectly fine down to sixteen.

Inside tmux, tmux itself is the component that downgrades colours, so its own
negotiated client features are the authoritative answer. Note that
`tmux info | grep RGB` is **not** a usable check: it reports the terminfo entry
of the outer terminal type and stays `[missing]` even when tmux does emit RGB
because `terminal-features` or `terminal-overrides` granted it.

## Seeing a depth without changing terminals

`--colors` forces the renderer, and `--light` / `--dark` force the background:

```sh
echo '{...payload...}' | uv run --project . claude-code-statusline --colors 16
echo '{...payload...}' | uv run --project . claude-code-statusline --colors mono --light
```

Accepted depths are `truecolor` (`24bit`), `256` (`ansi256`), `16` (`ansi16`) and
`mono` (`bw`).

## Reaching true colour inside tmux

tmux determines a client's colour capabilities from the outer `TERM` when the
client **attaches**. SSH clients that announce themselves as plain `xterm`
(Termius on iOS does) leave tmux believing it drives an 8-colour terminal.

**1. Put the settings in a file tmux actually reads.** tmux loads
`~/.tmux.conf` or `~/.config/tmux/tmux.conf` — and nothing else. A file named
`~/.tmuxrc` is silently ignored, which makes it look as though correct settings
have no effect.

**2. Grant the outer terminal 24-bit colour** in that file:

```tmux
set -g default-terminal "tmux-256color"
set -as terminal-features ",xterm*:RGB:256"
set -as terminal-features ",screen*:RGB:256"
set -ga terminal-overrides ",xterm*:Tc"
setenv -g COLORTERM truecolor
set -ga update-environment " COLORTERM"
```

`RGB` enables the 24-bit escapes; `256` lifts the colour count so that indexed
colours (`colour238` and friends, commonly used in tmux status-bar themes) stop
being rounded onto the 8 basic ANSI colours.

**3. Re-attach the client.** `tmux source-file ~/.tmux.conf` applies the options
but **cannot** change an already-attached client's capabilities. The developer
detaches with `prefix` + `d` and runs `tmux attach` again; the tmux session and
everything running in it survive that.

**4. Optionally fix the root cause in the SSH client.** Setting the terminal type
to `xterm-256color` in the client (in Termius: *Settings → Terminal → Terminal
type*) makes the announcement honest, so tmux detects truecolor on its own.

## Reaching true colour outside tmux

Terminals advertise 24-bit support through the `COLORTERM` environment variable.
If the terminal in use does support truecolor but does not set it, the developer
exports it from the shell profile:

```sh
export COLORTERM=truecolor
```

## Verifying

Inside tmux, the negotiated feature list must contain `RGB` and `256`:

```sh
tmux display-message -p '#{client_termfeatures}'
# 256,bpaste,ccolour,clipboard,cstyle,focus,RGB,title
```

In any terminal, this prints a smooth red→green gradient when 24-bit colour
works, and a handful of coarse blocks when it does not:

```sh
for i in $(seq 0 76); do printf "\033[48;2;$((255-i*3));$((i*3));0m "; done; printf '\033[0m\n'
```
