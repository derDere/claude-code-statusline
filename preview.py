# /// script
# requires-python = ">=3.12"
# dependencies = ["coloraide>=4.0"]
# ///
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render every state this status line can show, in any colour depth.

    uv run --script preview.py              # the whole gallery, as detected
    uv run --script preview.py --ansi       # the same gallery in 16 colours
    uv run --script preview.py --light      # ... as it looks on a light terminal
    uv run --script preview.py --matrix     # only the depth x background grid

Every flag `statusline.py` accepts works here too and is passed straight through
(`--help` lists them). The scrolling is the one thing left out: it needs a
terminal narrower than the line plus a clock to advance it, neither of which
belongs in a still gallery, so every row is drawn with `--no-scroll`.
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statusline as S

HERE = os.path.dirname(os.path.abspath(__file__))
BOLD, DIM, OFF = "\033[1m", "\033[2m", "\033[0m"


# ── Rendering one row ────────────────────────────────────────────────────────
def render(payload: dict, argv: tuple = ()) -> str:
    """Run the real `main()` against `payload` and capture the line it writes."""
    saved_argv, saved_in, saved_out = sys.argv, sys.stdin, sys.stdout
    sys.argv = ["statusline.py", "--no-scroll", *argv]
    sys.stdin, buffer = io.StringIO(json.dumps(payload)), io.StringIO()
    sys.stdout = buffer
    try:
        S.main()
    finally:
        sys.argv, sys.stdin, sys.stdout = saved_argv, saved_in, saved_out
    return buffer.getvalue().rstrip("\n")


def payload(*, model=("claude-opus-5", "Opus 5"), size=1_000_000, used=30.0,
            five=None, seven=None, cost=None, effort="high", answered=True,
            starting=False) -> dict:
    """Build the payload for one scenario.

    `five`/`seven` present means a subscription; their absence together with
    `answered` is what the script reads as API billing.
    """
    data = {
        "prompt_id": "preview",
        "model": {"id": model[0], "display_name": model[1]},
        "cwd": HERE,
        "workspace": {"project_dir": HERE},
        "context_window": {
            "context_window_size": size,
            "used_percentage": used,
            "total_input_tokens": int(size * used / 100),
            "current_usage": 1 if answered else None,
        },
        "effort": {"level": effort},
    }
    if five is not None or seven is not None:
        data["rate_limits"] = {}
        if five is not None:
            data["rate_limits"]["five_hour"] = {"used_percentage": five}
        if seven is not None:
            data["rate_limits"]["seven_day"] = {"used_percentage": seven}
    if cost is not None:
        data["cost"] = {"total_cost_usd": cost}
    if starting:
        del data["prompt_id"]
    return data


# ── What gets shown ──────────────────────────────────────────────────────────
#: (caption, payload, extra flags) — the states a real session passes through.
SESSIONS = [
    ("subscription · fresh start · effort high",
     payload(used=8, five=12, seven=21, effort="high"), ()),
    ("subscription · mid-session · xhigh with workflows on -> wx",
     payload(used=54, five=47, seven=33, effort="xhigh"), ("--workflows",)),
    ("subscription · the same xhigh with workflows off -> plain x",
     payload(used=40, five=30, seven=18, effort="xhigh"), ("--no-workflows",)),
    ("subscription · nearly full, 200k context · effort max",
     payload(model=("claude-sonnet-5", "Sonnet 5"), size=200_000, used=92,
             five=81, seven=88, effort="max"), ()),
    ("API billing · $12.40 · effort high",
     payload(used=30, cost=12.40, effort="high"), ()),
    ("API billing · $58.00, cost bar gone red · effort low",
     payload(used=71, cost=58.00, effort="low"), ()),
    ("API billing · response not back yet -> the cost stays hidden",
     payload(used=30, cost=12.40, answered=False), ()),
    ("before the first prompt -> the startup line",
     payload(starting=True), ()),
]

RAMP_STEPS = [0, 15, 30, 45, 60, 75, 90, 100]
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max", "wx", "ultracode"]
MODELS = [("claude-opus-5", "Opus 5"), ("claude-opus-5[1m]", "Opus 5 (1M context)"),
          ("claude-sonnet-5", "Sonnet 5"), ("claude-haiku-4-5", "Haiku 4.5")]
EFFORT_NOTES = {
    "wx": "xhigh + workflows — ultracode is possible, not proven",
    "ultracode": "dormant: no payload field exposes this today",
}


def heading(text: str) -> None:
    print(f"\n{BOLD}═══ {text} " + "═" * max(0, 56 - len(text)) + OFF)


def sessions(argv: tuple) -> None:
    heading("session states")
    for text, data, extra in SESSIONS:
        print(f"\n  {DIM}{text}{OFF}")
        print("   ", render(data, argv + extra))


def ramp(argv: tuple) -> None:
    heading("the fill ramp — hue and level both follow the percentage")
    print()
    for pct in RAMP_STEPS:
        print(f"  {pct:3d}%  {render(payload(used=pct, five=pct, seven=pct), argv)}")


def segments(argv: tuple) -> None:
    """The three builders on their own, including what only they can show."""
    heading("segment builders")
    render(payload(), argv)          # settles PALETTE and INK for the direct calls
    print(f"\n  {DIM}effort levels{OFF}")
    for level in EFFORT_LEVELS:
        seg = S.effort_bar(level)
        note = EFFORT_NOTES.get(level, "")
        print(f"    {level:<10}{seg or '-'}   {DIM}{note}{OFF}" if note
              else f"    {level:<10}{seg or '-'}")
    print(f"\n  {DIM}model names, as model_label() shortens them{OFF}")
    for mid, name in MODELS:
        print(f"    {S.fixed_bar(S.ICON_MODEL, S.model_label(mid, name))}")
    print(f"\n  {DIM}working directory — get_cwd() shortens anything over 40 chars{OFF}")
    for path in ("~", "~/sources/claude-code-statusline",
                 "~/.../deeply/nested"):
        print(f"    {S.fixed_bar(S.ICON_DIR, path)}")


def matrix() -> None:
    heading("colour depth x terminal background")
    data = payload(used=54, five=47, seven=33, effort="xhigh")
    for background in ("--dark", "--light"):
        print(f"\n  {BOLD}{background[2:]} background{OFF}")
        for depth in ("truecolor", "256", "16", "mono"):
            print(f"    {depth:<10}",
                  render(data, ("--colors", depth, background, "--workflows")))


def main() -> None:
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        print("\nFlags passed through to statusline.py:\n")
        print(S.USAGE[S.USAGE.index("  --light"):].rstrip())
        return
    argv = tuple(a for a in args if a != "--matrix")
    if "--matrix" in args:
        matrix()
        return
    sessions(argv)
    ramp(argv)
    segments(argv)
    matrix()


if __name__ == "__main__":
    main()
