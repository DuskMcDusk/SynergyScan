"""Where things live.

The single most important rule in the deployed layout: **mutable state lives
outside the release directories.**

    C:\\SynergyScan\\
      launcher.py          supervisor, outside versions, rarely changes
      current.txt          which release to run
      versions\\
        2026.09.25-1\\     immutable: code + its own .venv
        2026.10.02-1\\
      data\\               <- everything here survives every update
        inventory.db
        config.json
        backups\\
        logs\\

Because nothing writable sits inside a release, a release directory is
disposable: an update can be deleted and retried with no cleanup, and the
running version is never half-modified.

In a checkout there are no `versions/`, so `data/` sits at the repo root and is
gitignored.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_DATA_DIR = "SYNERGYSCAN_DATA"


def release_dir() -> Path:
    """Directory of the running release (the repo root in a checkout)."""
    # src/synergyscan/paths.py -> src/synergyscan -> src -> root
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Writable state directory. Created if missing.

    Resolution order:
      1. $SYNERGYSCAN_DATA - what launcher.py sets, and how tests redirect it.
      2. A `data` sibling of `versions/` when running from a deployed release.
      3. `data/` in the checkout.
    """
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        d = Path(env)
    else:
        rel = release_dir()
        # .../versions/<version>/  ->  .../data
        if rel.parent.name.lower() == "versions":
            d = rel.parent.parent / "data"
        else:
            d = rel / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "inventory.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def backups_dir() -> Path:
    d = data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def versions_dir() -> Path | None:
    """The deployed `versions/` directory, or None in a checkout."""
    rel = release_dir()
    return rel.parent if rel.parent.name.lower() == "versions" else None


def install_root() -> Path | None:
    """C:\\SynergyScan on a deployed machine, None in a checkout."""
    v = versions_dir()
    return v.parent if v else None


def current_pointer() -> Path | None:
    """The current.txt that launcher.py reads, or None in a checkout."""
    root = install_root()
    return root / "current.txt" if root else None


def staging_dir() -> Path:
    """Where an update unpacks before it is allowed to become `current`.

    Deliberately not inside `versions/`, so a half-finished download can never
    be mistaken for a release.
    """
    root = install_root()
    d = (root / "staging") if root else (data_dir() / "staging")
    d.mkdir(parents=True, exist_ok=True)
    return d
