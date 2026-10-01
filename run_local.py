"""Start SynergyScan from this checkout on a port of your choosing.

    python run_local.py              # asks for the port (Enter = 8000)
    python run_local.py 8123         # or pass it directly
    python run_local.py 8123 --lan   # also reachable from phones on the network
    python run_local.py 8123 --no-browser

Uses the checkout's .venv if there is one, runs from src\\ so edits take effect
on restart, and skips the update check (a source checkout never self-updates).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8000


def venv_python() -> str:
    sub, exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    py = ROOT / ".venv" / sub / exe
    return str(py) if py.exists() else sys.executable


def ask_port() -> int:
    while True:
        raw = input(f"Port [{DEFAULT_PORT}]: ").strip() or str(DEFAULT_PORT)
        if raw.isdigit() and 1 <= int(raw) <= 65535:
            return int(raw)
        print("Enter a number between 1 and 65535.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", type=int, help="port to listen on")
    ap.add_argument("--lan", action="store_true", help="bind 0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    port = a.port if a.port is not None else ask_port()
    if not 1 <= port <= 65535:
        ap.error("port must be between 1 and 65535")

    host = "0.0.0.0" if a.lan else "127.0.0.1"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("SYNERGYSCAN_DATA", str(ROOT / "data"))
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [venv_python(), "-m", "synergyscan", "--no-update",
           "--host", host, "--port", str(port)]
    if a.verbose:
        cmd.append("-v")

    if not a.no_browser:
        threading.Timer(2.5, webbrowser.open, [f"http://127.0.0.1:{port}/"]).start()

    print(f"SynergyScan on http://{host}:{port}/  (Ctrl+C to stop)")
    try:
        return subprocess.run(cmd, cwd=ROOT, env=env).returncode
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
