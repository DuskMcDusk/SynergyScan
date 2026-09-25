"""Asking whether there is a newer release.

We read a small JSON file from the repo rather than calling the GitHub API.
That buys three things:

* No rate limit to reason about (the unauthenticated API allows 60 requests an
  hour per IP - fine, but one more thing that can surprise you at a customer).
* A schema we own, so a change at GitHub cannot break the update path.
* **Publishing is separate from rolling out.** Cutting a release does not ship
  it; editing channels/stable.json does. That is what makes a canary possible -
  point one machine at channels/beta.json and nothing else moves.

Standard library only. See the note in pyproject.toml.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import version

log = logging.getLogger(__name__)

USER_AGENT = "SynergyScan-updater"
MAX_CHANNEL_BYTES = 64 * 1024        # a channel file is a few hundred bytes


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    notes: str = ""
    # Refuse to jump straight to this release from anything older than
    # min_version. Set it when a migration needs an intermediate step to run
    # first; leave it out otherwise.
    min_version: str | None = None


class CheckError(RuntimeError):
    """The channel could not be read or did not make sense."""


def fetch_channel(url: str, timeout: float = 15.0) -> Release:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_CHANNEL_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # Offline is the normal case, not an error worth alarming anyone about.
        raise CheckError(f"could not reach the update channel: {e}") from e
    if len(raw) > MAX_CHANNEL_BYTES:
        raise CheckError("update channel file is implausibly large; refusing it")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CheckError(f"update channel is not valid JSON: {e}") from e
    return _validate(data)


def _validate(data: object) -> Release:
    if not isinstance(data, dict):
        raise CheckError("update channel must be a JSON object")
    missing = [k for k in ("version", "url", "sha256") if not data.get(k)]
    if missing:
        raise CheckError(f"update channel is missing {', '.join(missing)}")
    v = str(data["version"]).strip()
    if not version.is_valid(v):
        raise CheckError(f"update channel version {v!r} is malformed")
    sha = str(data["sha256"]).strip().lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise CheckError("update channel sha256 is not a 64-character hex digest")
    url = str(data["url"]).strip()
    if not url.startswith("https://"):
        # Plain HTTP would let anyone on the path swap the archive for their own.
        raise CheckError("update archive URL must be https")
    mv = data.get("min_version")
    if mv is not None and not version.is_valid(str(mv)):
        raise CheckError(f"update channel min_version {mv!r} is malformed")
    return Release(version=v, url=url, sha256=sha,
                   notes=str(data.get("notes") or ""),
                   min_version=str(mv) if mv else None)


def available(channel_url: str, current_version: str | None = None,
              timeout: float = 15.0) -> Release | None:
    """The release to install, or None if there is nothing to do.

    Never raises: a machine that cannot check for updates must still start and
    keep working, so every failure here becomes a log line and a None.
    """
    cur = current_version or version.current()
    try:
        rel = fetch_channel(channel_url, timeout=timeout)
    except CheckError as e:
        log.info("update check skipped: %s", e)
        return None

    try:
        if not version.newer(rel.version, cur):
            log.info("up to date (running %s, channel offers %s)", cur, rel.version)
            return None
        if rel.min_version and version.newer(rel.min_version, cur):
            log.warning(
                "release %s requires at least %s but this machine runs %s; "
                "an intermediate release must be published to bridge the gap",
                rel.version, rel.min_version, cur,
            )
            return None
    except version.BadVersion as e:
        log.warning("update check skipped: %s", e)
        return None

    log.info("update available: %s -> %s", cur, rel.version)
    return rel
