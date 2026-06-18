# Status-line payload schema

Claude Code pipes **one JSON object on stdin** to the `statusLine` command on every
render. `statusline.py` reads it in `main()`. This is the documented schema as of the
snapshot date below — **re-verify before trusting it** (see
[How to refresh](#how-to-refresh-this-when-claude-code-changes)); Claude Code adds
fields over time.

- **Snapshot date:** 2026-06-17
- **Primary source:** <https://code.claude.com/docs/en/statusline> ("Available data")
- **Effort/ultracode source:** <https://code.claude.com/docs/en/model-config> ("Adjust effort level")

## Fields

Legend: **[used]** = read by `statusline.py` today · **[avail]** = present in the
payload but not yet used.

| Field | Type | Meaning | |
|---|---|---|---|
| `model.id` | string | Model id, e.g. `claude-opus-4-8` | [used] |
| `model.display_name` | string | Display name, e.g. `Opus` | [used] |
| `session_id` | string | Unique session id | [avail] |
| `session_name` | string | Custom session name; **absent** when none set. Does **not** carry the ultracode indicator (that's a TUI-only label) — see [ultracode-detection.md](./ultracode-detection.md) | [avail] |
| `transcript_path` | string | Path to the conversation transcript file | [avail] |
| `version` | string | Claude Code version | [avail] |
| `cwd` | string | Current working directory (alias of `workspace.current_dir`) | [avail]¹ |
| `workspace.current_dir` | string | Current working directory (preferred over `cwd`) | [avail]¹ |
| `workspace.project_dir` | string | Directory where Claude Code was launched | [avail] |
| `workspace.added_dirs` | array | Extra dirs from `/add-dir` | [avail] |
| `workspace.git_worktree` | string | Linked worktree name; absent otherwise | [avail] |
| `workspace.repo.{host,owner,name}` | string | Git remote info; absent outside a git repo / without `origin` | [avail] |
| `context_window.context_window_size` | number | Max context size (200000, or 1000000 extended) | [used] |
| `context_window.used_percentage` | number | % of context used (may be `null` early) | [used] |
| `context_window.remaining_percentage` | number | % of context remaining | [avail] |
| `context_window.total_input_tokens` | number | Input tokens in context (incl. cache) | [used]² |
| `context_window.total_output_tokens` | number | Output tokens of last response | [avail] |
| `context_window.current_usage` | object\|null | Token counts by category; `null` before first API call and after `/compact` | [avail] |
| `exceeds_200k_tokens` | boolean | Whether total tokens passed the fixed 200k mark | [avail] |
| `effort.level` | string | `low`\|`medium`\|`high`\|`xhigh`\|`max`. **Absent** if the model has no effort param. Reflects live `/effort` changes | [used] |
| `thinking.enabled` | boolean | Extended thinking on/off | [avail] |
| `rate_limits.five_hour.used_percentage` | number | 5h window usage 0–100 (subscription only) | [used] |
| `rate_limits.five_hour.resets_at` | number | Unix seconds when 5h window resets | [avail] |
| `rate_limits.seven_day.used_percentage` | number | 7d window usage 0–100 (subscription only) | [used] |
| `rate_limits.seven_day.resets_at` | number | Unix seconds when 7d window resets | [avail] |
| `cost.total_cost_usd` | number | **Estimated** session cost (see note) | [used]³ |
| `cost.total_duration_ms` | number | Wall-clock since session start | [avail] |
| `cost.total_api_duration_ms` | number | Time spent waiting on the API | [avail] |
| `cost.total_lines_added` / `…_removed` | number | Lines of code added / removed | [avail] |
| `output_style.name` | string | Current output style | [avail] |
| `vim.mode` | string | `NORMAL`\|`INSERT`\|`VISUAL`\|`VISUAL LINE`; absent if vim off | [avail] |
| `agent.name` | string | Agent name when run with `--agent`; absent otherwise | [avail] |
| `pr.number` / `pr.url` / `pr.review_state` | number/string | Open PR info; `review_state` ∈ approved/pending/changes_requested/draft | [avail] |
| `worktree.{name,path,branch,original_cwd,original_branch}` | string | Present only during `--worktree` sessions | [avail] |

¹ The script uses Python's `os.getcwd()` for the directory segment, **not** the
  payload's `cwd`/`workspace.current_dir`. Worth revisiting — the payload value is
  more correct when Claude Code's CWD differs from the spawned process's.
² Only as a fallback: `used_tok = ctx_size * used_pct/100` when `used_percentage` is
  present, else `total_input_tokens`.
³ Shown **only on API billing** — see the cost note below.

## Behaviour notes baked into the script

- **Billing mode** is inferred, not given: `is_api = not rate_limits`. Subscription
  payloads contain `rate_limits` (→ show 5h/7d bars, **hide** cost, since cost is only
  an estimate there); API payloads have none (→ show the real cost bar when `> 0`).
- **Absent vs null:** many fields are simply missing rather than `null`. Use
  `(data.get(x) or {}).get(y)` patterns, never assume presence.

## How to refresh this when Claude Code changes

Two complementary ways — do both when in doubt:

1. **Read the official docs** (authoritative for the schema):
   - <https://code.claude.com/docs/en/statusline>
   - <https://code.claude.com/docs/en/model-config>
   - Or dispatch the **`claude-code-guide`** subagent (it has `WebFetch`/`WebSearch`)
     with a request to enumerate the full statusLine schema + effort fields and cite URLs.
2. **Capture the real payload** (authoritative for what *your* build actually receives —
   docs sometimes lag, and only a capture reveals undocumented/changed fields):
   - Set `STATUSLINE_DEBUG=1` in the environment of the Claude Code process (e.g. the
     `env` block of `~/.claude/settings.json`, then restart Claude Code so it re-spawns
     the status-line command with the var).
   - The next render writes the raw payload to `_last_payload.json` next to
     `statusline.py` (gitignored). Inspect it, then turn the flag back off.
   - This is the **only** way to confirm fields that depend on session state (ultracode,
     PR, worktree, vim, …) — capture once per state and diff.
