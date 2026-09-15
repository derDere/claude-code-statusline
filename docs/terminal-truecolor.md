# True-colour requirement, and the red error banner

Every colour in this status line is computed in OKLCH and emitted as a **24-bit
truecolor** escape (`ESC[38;2;r;g;b m` / `ESC[48;2;r;g;b m`). A terminal that
only offers the 8 or 16 legacy ANSI colours rounds each of those to the nearest
palette entry: the green→red ramps collapse into flat blocks, the brand-coloured
model and directory bars turn plain blue, and the subtle left→right lightness
gradient disappears entirely.

Because a downgraded line shows *wrong* information rather than obviously broken
output, `statusline.py` checks for 24-bit support on every render and, when it is
missing, replaces the whole line with a red banner:

```
 NO TRUECOLOR (tmux passes no RGB) -> https://github.com/derDere/claude-code-statusline/blob/main/docs/terminal-truecolor.md
```

The banner is drawn with the legacy SGR codes `1;41;97` (bold, red background,
bright white text) rather than 24-bit escapes, so it renders correctly in exactly
the degraded terminals it warns about.

The check runs once the session has produced its first API response. During the
startup window before that, the script prints its startup line, which uses the same
legacy colours and needs no verdict about the terminal to be correct.

## How the check decides

`detect_color_caps()` in `statusline.py` measures the terminal once per render and
returns a `ColorCaps` record, which `main()` stores in the module-level `COLOR`
variable so the rest of the script can consult it:

| Field | Meaning |
|---|---|
| `level` | `MONO` (0), `ANSI16` (1), `ANSI256` (2) or `TRUECOLOR` (3) — the best colour depth the terminal is believed to handle |
| `certain` | whether `level` was measured, or assumed because nothing could be measured |
| `reason` | short text naming why `level` is below `TRUECOLOR`, or `None` |

The banner appears only when `certain` is true **and** `level` is below
`TRUECOLOR`. Anything undecidable is recorded as truecolor with `certain = False`,
so the banner never cries wolf.

| Situation | Signal `statusline.py` reads | Verdict |
|---|---|---|
| `TMUX` is set | `tmux display-message -p '#{client_termfeatures}'` contains `RGB` | `TRUECOLOR`, measured — fine |
| `TMUX` is set | the same query lists features but no `RGB` | `ANSI256`/`ANSI16`, measured — banner: `tmux passes no RGB` |
| `TMUX` is set | the query cannot be answered, or comes back empty | `TRUECOLOR`, assumed — fine (no evidence either way) |
| No tmux | `COLORTERM` is `truecolor` or `24bit` | `TRUECOLOR`, measured — fine |
| No tmux | `TERM` contains `direct` (e.g. `xterm-direct`) | `TRUECOLOR`, measured — fine |
| No tmux | `TERM` contains `256color` | `ANSI256`, measured — banner: `COLORTERM is not truecolor` |
| No tmux | `TERM` is unset or `dumb` | `MONO`, measured — banner: `TERM is dumb` |
| No tmux | none of the above | `ANSI16`, measured — banner: `COLORTERM is not truecolor` |

An **empty** feature list counts as no evidence rather than as a missing `RGB`.
tmux answers the query while a client is still attaching and has not finished
negotiating features, and reading that silence as a negative would fire the banner
on a terminal whose colours are perfectly fine.

Inside tmux, tmux itself is the component that downgrades colours, so its own
negotiated client features are the authoritative answer. Note that
`tmux info | grep RGB` is **not** a usable check: it reports the terminfo entry
of the outer terminal type and stays `[missing]` even when tmux does emit RGB
because `terminal-features` or `terminal-overrides` granted it.

## Fixing it inside tmux

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

## Fixing it outside tmux

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
