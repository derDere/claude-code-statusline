# Ultracode detection — verdict: not possible from a status line

**Bottom line (verified 2026-06-17): a `statusLine` command CANNOT detect whether the
session is in ultracode mode.** So the effort segment renders the underlying level
**`xhigh`** honestly, and — when `xhigh` runs with dynamic workflows enabled — shows an
honestly-labelled proxy **`wx`** (a full deep-purple bar meaning "ultracode is *possible*
this session"), never a false "ultracode". This page records why, and how `wx` is decided.

## What ultracode actually is

From <https://code.claude.com/docs/en/model-config> ("Adjust effort level"), verbatim:

> *"The `/effort` menu also offers `ultracode`. Ultracode is a Claude Code setting rather
> than a model effort level: it sends `xhigh` to the model and additionally has Claude
> orchestrate dynamic workflows for substantive tasks. It applies to the current session
> only. Set it through `/effort`, or pass `"ultracode": true` via `--settings` or an Agent
> SDK control request. It is not part of the `effortLevel` setting, the `--effort` flag,
> or `CLAUDE_CODE_EFFORT_LEVEL`."*

And on the effort setting:

> *"`max` and `ultracode` are session-only and are not accepted here."* (i.e. in
> `settings.json` `effortLevel`)

So ultracode is: **xhigh-to-the-model + workflow orchestration, session-only, never
persisted.**

## Why no channel can see it

| Channel | Result | Evidence |
|---|---|---|
| **statusLine payload** | The only effort field is `effort.level`, and *"Ultracode is not a distinct level and reports as `xhigh`."* No `ultracode`/`mode`/`workflow` field anywhere. | <https://code.claude.com/docs/en/statusline> (effort.level row, verified) |
| **Env vars** | Ultracode is explicitly *not* `CLAUDE_CODE_EFFORT_LEVEL`, the `--effort` flag, or `effortLevel`. No env var marks active ultracode. (Workflows being *enabled* is a different thing and is on by default — that was the original false-positive bug.) | model-config doc (above) |
| **Disk / session state** | Session-only, not written to `settings.json` or any session file a local process could read. | model-config doc (above) |
| **Hooks** | Hook payloads carry the same `effort.level` (= `xhigh`); no ultracode flag, and no hook fires when `/effort ultracode` is toggled mid-session. | — |

### The TUI "ultracode" label is a red herring
When ultracode is on (and no custom session name is set), Claude Code's TUI shows
"ultracode" **where the session name normally appears**. That is Claude Code's own effort
indicator painted in the name slot — it is **not** the payload's `session_name` value.
Confirmed by the upstream bug
[anthropics/claude-code#63899](https://github.com/anthropics/claude-code/issues/63899):
*"/rename replaces the ultracode effort indicator with the session name (effort level
stays active)."* So keying off `session_name` does not work — the user verified this
directly (UI showed "ultracode" while the status line correctly showed `xhigh`).

## Earlier guesses that were wrong (and why)

1. **`session_name == "ultracode"`** — the TUI label is not the payload field (see above).
   Permanent false negative; misleading code. **Do not reintroduce.**
2. **`disableWorkflows` heuristic *labelled as ultracode*** — promoting `xhigh` +
   workflows-enabled to `"ultracode"` claimed ultracode was *active*, which it usually
   wasn't. The signal isn't worthless, though — see the "wx" proxy below; the fix was the
   **label**, not the signal. **Never call this state "ultracode".**

## What the code does now

`main()` renders the honest truth, with a deliberate, clearly-labelled proxy:

```python
eff = data.get("effort") or {}
effort = eff.get("level")
if eff.get("ultracode") is True:                  # real signal; absent in payloads today
    effort = "ultracode"                          # genuine bar, letter "u"
elif effort == "xhigh" and workflows_enabled(data):
    effort = "wx"                                 # proxy: xhigh + workflows, letter "wx"
```

- **`ultracode` (letter `u`)** — dormant, future-proof hook. Lights up the genuine deep-
  purple 6/6 bar the day Claude Code exposes a real field (e.g. `effort.ultracode: true`).
- **`wx` (letter `wx`)** — the honest proxy chosen by the user: a full deep-purple bar
  (same look as the old ultracode bar) shown when effort is `xhigh` **and** dynamic
  workflows are enabled. Because **workflows are a precondition for ultracode** (when
  disabled, ultracode is removed from the `/effort` menu —
  <https://code.claude.com/docs/en/workflows>), "wx" honestly means *"xhigh + workflows on,
  so ultracode is possible this session"*. It does **not** claim ultracode is active.

### How `workflows_enabled()` decides (verified)
Per <https://code.claude.com/docs/en/workflows> ("Turn workflows off"), workflows are on by
default and disabled by either:
- env var **`CLAUDE_CODE_DISABLE_WORKFLOWS=1`** (read at startup, applies everywhere), or
- the **`disableWorkflows: true`** setting (default `false`) at user → project → local
  (more specific wins; settings override the env var in our reader).

Caveat the status line can't see: on **Pro**, workflows default *off* until enabled in
`/config`, and plan/`/config` state isn't in any file we read — so "wx" can be wrong there.
When Claude Code ships a real ultracode field, drop the `wx` branch (or keep it as a
secondary indicator) and rely on the `ultracode` branch.

## If you want to revisit this later

- **Watch upstream** for a real field. Relevant issues/requests:
  [#63899](https://github.com/anthropics/claude-code/issues/63899) (TUI indicator vs
  session name), [#63498](https://github.com/anthropics/claude-code/issues/63498)
  (ultracode/workflows), and the ACP request
  [agentclientprotocol/claude-agent-acp#725](https://github.com/agentclientprotocol/claude-agent-acp/issues/725).
  The clean fix would be `effort.level: "ultra"` or `effort.ultracode: true` in the payload.
- **Re-verify the schema** (it changes): read <https://code.claude.com/docs/en/statusline>
  and `.../model-config`, or dispatch the `claude-code-guide` subagent. Then capture a real
  payload while ultracode is ON (`STATUSLINE_DEBUG=1` → `_last_payload.json`; see
  [payload-schema.md](./payload-schema.md)) and diff against an xhigh capture — if a field
  differs, wire `main()` to it and update the dormant hook above.

## Files involved

- `statusline.py` → `main()`: reads `effort.level`; dormant `ultracode` hook.
- `statusline.py` → `EFFORT_COLORS` / `EFFORT_LETTER` / `EFFORT_ORDER`: ultracode styling.
- `effort_bar()`: renders the segment.
