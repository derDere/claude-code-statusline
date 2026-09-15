"""Render the real status line in every colour depth and on both backgrounds.

Drives statusline.py itself through its --colors / --light / --dark overrides,
so what shows up here is exactly what Claude Code would draw.
"""
import io, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = {
    "prompt_id": "preview",
    "model": {"id": "claude-opus-5", "display_name": "Opus 5 (1M context)"},
    "cwd": HERE,
    "workspace": {"project_dir": HERE},
    "context_window": {"context_window_size": 1_000_000, "used_percentage": 9,
                       "total_input_tokens": 90_000, "current_usage": 1},
    "rate_limits": {"five_hour": {"used_percentage": 65},
                    "seven_day": {"used_percentage": 92}},
    "effort": {"level": "xhigh"},
}

def render(*argv, starting=False):
    payload = dict(PAYLOAD)
    if starting:
        payload.pop("prompt_id")
    env = dict(os.environ, COLUMNS="9999")
    out = subprocess.run([sys.executable, os.path.join(HERE, "statusline.py"), *argv],
                         input=json.dumps(payload), capture_output=True,
                         text=True, env=env)
    return out.stdout.rstrip("\n") or f"<no output: {out.stderr.strip()[:80]}>"

for bgname, bgflag in (("DARK  background", "--dark"), ("LIGHT background", "--light")):
    print(f"\n\033[1m═══ {bgname} " + "═" * 44 + "\033[0m")
    for depth in ("truecolor", "256", "16", "mono"):
        print(f"  {depth:<10}", render("--colors", depth, bgflag))
    print(f"  {'startup':<10}", render(bgflag, starting=True))
