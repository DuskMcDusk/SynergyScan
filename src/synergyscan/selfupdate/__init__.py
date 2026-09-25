"""Self-update.

This is the highest-stakes code in the project, and it is the only code whose
bugs cannot be fixed by shipping an update. Break the updater and the fix is a
phone call or a site visit. That is why it lives here as a tested package
rather than as a script in a tools directory, and why it imports nothing but
the standard library.

Layout on a deployed machine, and why:

    C:\\SynergyScan\\
      launcher.py      supervisor. Reads current.txt, starts that release,
                       restarts on exit code 75. Deliberately trivial, because
                       it is the one piece an update cannot replace.
      current.txt      the pointer. One line, one version.
      versions\\<v>\\  immutable releases, each with its own .venv
      data\\           the database, config, logs, backups. Never touched.

The updater ships *inside* each release, so the code that installs version N+1
is version N's. Improvements to the updater therefore take one release to take
effect - the normal property of self-updating software, and the reason the
launcher outside must stay dumb enough never to need changing.

Startup flow:

    launcher            reads current.txt, execs that release
    app startup         calls check_and_apply()
    update installed    write current.txt, exit(RESTART_EXIT_CODE)
    launcher            sees 75, loops, execs the new release
"""

from __future__ import annotations

import logging

from .apply import Applied, ApplyError, apply_from_paths
from .check import CheckError, Release, available
from .version import BadVersion, current, is_source_checkout, newer

log = logging.getLogger(__name__)

# EX_TEMPFAIL. Tells launcher.py "I stopped on purpose, start me again" - as
# distinct from a crash, which it should report rather than silently loop on.
RESTART_EXIT_CODE = 75

__all__ = [
    "Applied",
    "ApplyError",
    "BadVersion",
    "CheckError",
    "RESTART_EXIT_CODE",
    "Release",
    "apply_from_paths",
    "available",
    "check_and_apply",
    "current",
    "is_source_checkout",
    "newer",
]


def check_and_apply(channel_url: str, *, enabled: bool = True,
                    timeout: int = 120) -> Applied | None:
    """Install an update if one is waiting. Returns what was installed, or None.

    Never raises. Every failure path leaves the current release running, which
    is the point - an update that cannot be installed is an inconvenience, and
    an app that will not start is a stopped warehouse.
    """
    if not enabled:
        log.info("automatic updates are switched off in config.json")
        return None
    if is_source_checkout():
        log.info("running from a source checkout; skipping the update check")
        return None

    rel = available(channel_url)          # already swallows its own errors
    if rel is None:
        return None

    try:
        applied = apply_from_paths(rel, download_timeout=timeout)
    except ApplyError as e:
        log.error("update to %s was not installed: %s", rel.version, e)
        return None
    except Exception:
        log.exception("unexpected failure installing %s", rel.version)
        return None

    log.info("installed %s; restarting", applied.version)
    return applied
