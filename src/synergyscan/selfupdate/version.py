"""Release versions.

Format: ``YYYY.MM.DD-N`` - a date plus a same-day sequence number. Calendar
versioning rather than semver because nobody here is consuming an API; what a
support call actually needs is "which day's build is on that machine".

Compared as a tuple of integers, never as strings: "2026.9.4-1" must sort below
"2026.10.2-1", which string comparison gets backwards.
"""

from __future__ import annotations

import re
from pathlib import Path

# The year is \d{1,4} rather than \d{4} so the source-checkout sentinel
# "0.0.0-0" parses and really does sort below every published release. With a
# strict 4-digit year it raised BadVersion instead, and the comparison in
# current() below would have been a lie.
VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,2})\.(\d{1,2})-(\d+)$")

# Written into every release archive by tools/release.py.
RELEASE_FILE = "RELEASE"


class BadVersion(ValueError):
    pass


def parse(v: str) -> tuple[int, int, int, int]:
    m = VERSION_RE.match((v or "").strip())
    if not m:
        raise BadVersion(f"{v!r} is not a version like 2026.10.02-1")
    return tuple(int(g) for g in m.groups())      # type: ignore[return-value]


def is_valid(v: str) -> bool:
    try:
        parse(v)
        return True
    except BadVersion:
        return False


def newer(candidate: str, than: str) -> bool:
    return parse(candidate) > parse(than)


def current(release_dir: Path | None = None) -> str:
    """Version of the running release.

    Reads the RELEASE file shipped in the archive. In a checkout that file does
    not exist, so we report 0.0.0-0, which makes every published release look
    newer - deliberately: a developer running from source should never have the
    updater decide it is already up to date.
    """
    from .. import paths

    root = release_dir or paths.release_dir()
    f = root / RELEASE_FILE
    if f.exists():
        text = f.read_text(encoding="utf-8").strip()
        if is_valid(text):
            return text
    return "0.0.0-0"


def is_source_checkout(release_dir: Path | None = None) -> bool:
    return current(release_dir) == "0.0.0-0"
