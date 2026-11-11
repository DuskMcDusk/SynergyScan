"""Runtime configuration, stored in data/config.json.

Lives with the data, not with the code, so an update never touches it and a
site's settings survive a rollback. Unknown keys are preserved on save, so a
config written by a newer version is not destroyed by an older one after a
rollback.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from . import paths

log = logging.getLogger(__name__)

# The update channel. `stable.json` is published from the repo, so pointing a
# machine at `beta.json` is all a canary rollout takes.
DEFAULT_CHANNEL_URL = (
    "https://raw.githubusercontent.com/DuskMcDusk/SynergyScan/main/channels/stable.json"
)


class Settings(BaseModel):
    site_name: str = "SynergyScan"
    host: str = "127.0.0.1"
    port: int = 8000

    # Set host to 0.0.0.0 to let phones and tablets on the LAN reach the app.
    # The printer stays on this machine either way.
    allow_lan: bool = False

    # --- printing
    label_width_mm: int = 40      # used only when the printer reports no roll
    label_height_mm: int = 30
    density: int = Field(default=4, ge=0, le=15)

    # --- updates
    channel_url: str = DEFAULT_CHANNEL_URL
    auto_update: bool = True
    update_timeout_s: int = 120

    # Keys written by a newer version, carried through unchanged so a rollback
    # does not silently discard them.
    _extra: dict[str, Any] = PrivateAttr(default_factory=dict)


def load() -> Settings:
    p = paths.config_path()
    raw: dict[str, Any] = {}
    if p.exists():
        try:
            # utf-8-sig: Notepad and PowerShell 5.1 may save it with a BOM.
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            # A corrupt config must not stop the app starting: a site that
            # cannot open the app cannot ship anything.
            log.exception("could not read %s; falling back to defaults", p)
            raw = {}
    if not isinstance(raw, dict):
        log.error("%s does not hold a JSON object; falling back to defaults", p)
        raw = {}
    known = {k: v for k, v in raw.items() if k in Settings.model_fields}
    try:
        s = Settings(**known)
    except ValidationError as e:
        # The same goes for one bad hand-edited value ("port": "8000x"): drop
        # just the invalid settings, keep the rest.
        bad = {str(err["loc"][0]) for err in e.errors() if err["loc"]}
        log.error("ignoring invalid settings in %s: %s", p, ", ".join(sorted(bad)))
        s = Settings(**{k: v for k, v in known.items() if k not in bad})
    s._extra = {k: v for k, v in raw.items() if k not in Settings.model_fields}
    return s


def _write(p: Path, data: dict[str, Any]) -> None:
    """Atomic: never leave a half-written config. Plain UTF-8, no BOM."""
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def save(s: Settings) -> None:
    _write(paths.config_path(), {**s._extra, **s.model_dump()})


# The keys a person might reasonably edit. channel_url and the update timeouts
# are left out on purpose: writing them would pin today's defaults into the file
# and stop a later release from changing them.
_STARTER_KEYS = ("site_name", "allow_lan", "port")


def ensure_file() -> None:
    """Create config.json with the editable settings if it does not exist.

    Nothing else ever writes this file, so without this a site has nothing to
    edit. Never overwrites, and never raises: a read-only data directory must
    not stop the app starting.
    """
    p = paths.config_path()
    if p.exists():
        return
    try:
        defaults = Settings().model_dump()
        starter = {k: defaults[k] for k in _STARTER_KEYS}
        p.write_text(json.dumps(starter, indent=2) + "\n", encoding="utf-8")
    except OSError:
        log.exception("could not create %s", p)


def set_value(key: str, value: Any) -> None:
    """Set one key in config.json, or remove it when value is None, leaving the
    rest exactly as written. Unlike save(), this does not pin every default into
    the file. The result is validated before it is written, so this can never
    leave a config the app would have to ignore. Raises ValueError (or OSError)
    on an unreadable or invalid file: the caller reports it."""
    ensure_file()
    p = paths.config_path()
    raw = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
    if not isinstance(raw, dict):
        # The file's content is wrong, not an argument: reported like bad JSON.
        raise ValueError("it does not hold a JSON object")  # noqa: TRY004
    if value is None:
        raw.pop(key, None)
    else:
        raw[key] = value
    Settings(**{k: v for k, v in raw.items() if k in Settings.model_fields})
    _write(p, raw)


# What the installer may set. Deliberately not every setting: a value written
# here is in the file every release reads.
CLI_KEYS = ("channel_url",)


def main(argv: list[str] | None = None) -> int:
    """For install.ps1, which cannot write JSON PowerShell 5.1 would not spoil:

        python -m synergyscan.config set channel_url https://...
        python -m synergyscan.config unset channel_url
    """
    import argparse

    ap = argparse.ArgumentParser(prog="python -m synergyscan.config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("key", choices=CLI_KEYS)
    s.add_argument("value")
    u = sub.add_parser("unset")
    u.add_argument("key", choices=CLI_KEYS)
    a = ap.parse_args(argv)
    if a.cmd == "set" and a.key == "channel_url" and not a.value.startswith("https://"):
        print("channel_url must be an https:// URL")
        return 1
    try:
        set_value(a.key, a.value if a.cmd == "set" else None)
    except (OSError, ValueError) as e:
        print(f"could not update {paths.config_path()}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
