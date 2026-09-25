"""Runtime configuration, stored in data/config.json.

Lives with the data, not with the code, so an update never touches it and a
site's settings survive a rollback. Unknown keys are preserved on save, so a
config written by a newer version is not destroyed by an older one after a
rollback.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

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
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt config must not stop the app starting: a site that
            # cannot open the app cannot ship anything.
            log.exception("could not read %s; falling back to defaults", p)
            raw = {}
    known = set(Settings.model_fields)
    s = Settings(**{k: v for k, v in raw.items() if k in known})
    s._extra = {k: v for k, v in raw.items() if k not in known}
    return s


def save(s: Settings) -> None:
    p = paths.config_path()
    payload = {**s._extra, **s.model_dump()}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)          # atomic: never leave a half-written config
