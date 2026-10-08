"""PostToolUse hook: after Claude edits a .py file, run ruff on it and mypy on src.

Reads the hook JSON on stdin. Exit 0 when clean or not a Python file; exit 2 with the
tool output on stderr when a check fails, which the harness feeds back to Claude.
"""
import json
import subprocess
import sys


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return p.returncode, (p.stdout + p.stderr).strip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        path = payload["tool_input"]["file_path"]
    except (ValueError, KeyError, TypeError):
        return 0
    if not str(path).endswith(".py"):
        return 0

    failures = []
    for label, cmd in (
        ("ruff", ["uv", "run", "ruff", "check", path]),
        ("mypy", ["uv", "run", "mypy", "src"]),
    ):
        code, out = run(cmd)
        if code != 0:
            failures.append(f"[{label}] {' '.join(cmd[2:])} failed:\n{out}")
    if failures:
        print("\n\n".join(failures), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
