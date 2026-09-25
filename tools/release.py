"""Build a release archive and update a channel file.

    python tools/release.py                      # build today's release
    python tools/release.py --notes "Fixes X"    # with release notes
    python tools/release.py --publish            # also push it with gh
    python tools/release.py --channel beta       # point the canary at it

Publishing is two separate acts, on purpose:

    1. Build and upload the archive.          Nothing happens on any machine.
    2. Update channels/<name>.json.           Machines take it on next start.

Keeping them apart is what makes a canary possible and what lets you abandon a
bad build before anyone gets it. Do not collapse them into one step.

Dev-only. This never ships inside a release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "out"

# What a release contains. Everything else - tests, tools, .git, data, the
# developer's own .venv - stays out, so what the customer runs is only what
# they need to run it.
INCLUDE_DIRS = ("src", "bootstrap")
INCLUDE_FILES = ("pyproject.toml", "uv.lock", "README.md", "LICENSE")
EXCLUDE_PARTS = {"__pycache__", ".venv", ".pytest_cache", ".ruff_cache",
                 ".git", "data", "out"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log"}


def next_version(existing: set[str], on: date | None = None) -> str:
    """Today's date plus the next free sequence number."""
    d = on or date.today()
    prefix = f"{d.year}.{d.month:02d}.{d.day:02d}"
    n = 1
    while f"{prefix}-{n}" in existing:
        n += 1
    return f"{prefix}-{n}"


def published_versions() -> set[str]:
    out = set()
    for p in (ROOT / "channels").glob("*.json"):
        try:
            out.add(json.loads(p.read_text(encoding="utf-8"))["version"])
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    out |= {p.stem.replace("synergyscan-", "") for p in OUT.glob("synergyscan-*.zip")}
    return out


def wanted(path: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return False
    return path.suffix not in EXCLUDE_SUFFIXES


def collect() -> list[tuple[Path, str]]:
    """(source, archive path) pairs. Archive paths are relative to its root."""
    items: list[tuple[Path, str]] = []
    for name in INCLUDE_FILES:
        p = ROOT / name
        if p.exists():
            items.append((p, name))
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.exists():
            raise SystemExit(f"missing {d}/ - run this from the repo root")
        for p in sorted(base.rglob("*")):
            if p.is_file() and wanted(p.relative_to(ROOT)):
                items.append((p, p.relative_to(ROOT).as_posix()))
    return items


def build(version: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    archive = OUT / f"synergyscan-{version}.zip"
    archive.unlink(missing_ok=True)
    items = collect()

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in items:
            zf.writestr(arc, src.read_bytes())
        # The version is carried inside the archive so the installer and the
        # updater can verify they got the release the channel promised, rather
        # than trusting the filename.
        zf.writestr("RELEASE", version + "\n")

    print(f"built {archive.name}  ({len(items) + 1} files, "
          f"{archive.stat().st_size / 1024:.0f} KiB)")
    return archive


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 16):
            h.update(chunk)
    return h.hexdigest()


def repo_slug() -> str:
    """owner/name from the git remote, so the URL is not hardcoded."""
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"],
                             cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "DuskMcDusk/SynergyScan"
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else "DuskMcDusk/SynergyScan"


def write_channel(channel: str, version: str, url: str, digest: str,
                  notes: str, min_version: str | None) -> Path:
    p = ROOT / "channels" / f"{channel}.json"
    p.parent.mkdir(exist_ok=True)
    payload: dict = {"version": version, "url": url, "sha256": digest,
                     "notes": notes}
    if min_version:
        payload["min_version"] = min_version
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"updated {p.relative_to(ROOT)} -> {version}")
    return p


def publish(version: str, archive: Path, notes: str) -> None:
    if not shutil.which("gh"):
        raise SystemExit("gh is not installed; upload the archive manually")
    tag = f"v{version}"
    print(f"creating release {tag}")
    subprocess.run(
        ["gh", "release", "create", tag, str(archive),
         "--title", f"SynergyScan {version}",
         "--notes", notes or f"Release {version}"],
        cwd=ROOT, check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", help="override the version (default: today-N)")
    ap.add_argument("--channel", default="stable", choices=["stable", "beta"])
    ap.add_argument("--notes", default="", help="release notes")
    ap.add_argument("--min-version",
                    help="refuse to update machines older than this; set it when "
                         "an intermediate release must run first")
    ap.add_argument("--publish", action="store_true",
                    help="upload the archive with gh after building")
    ap.add_argument("--no-channel", action="store_true",
                    help="build only; do not touch the channel file")
    a = ap.parse_args()

    version = a.version or next_version(published_versions())
    print(f"release {version}\n")

    archive = build(version)
    digest = sha256(archive)
    print(f"sha256 {digest}")

    slug = repo_slug()
    url = (f"https://github.com/{slug}/releases/download/"
           f"v{version}/{archive.name}")

    if a.publish:
        publish(version, archive, a.notes)

    if not a.no_channel:
        write_channel(a.channel, version, url, digest, a.notes, a.min_version)

    print("\nNext:")
    if not a.publish:
        print(f"  1. gh release create v{version} {archive} "
              f'--title "SynergyScan {version}" --notes "{a.notes or version}"')
    print(f"  2. Check the archive installs: extract it somewhere and run "
          f"bootstrap\\install.ps1 -InstallRoot C:\\SynergyScan-test")
    print(f"  3. Only then: git add channels/{a.channel}.json && git commit && git push")
    print("     Machines take the update on their next start. Until you push,")
    print("     nothing has shipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
