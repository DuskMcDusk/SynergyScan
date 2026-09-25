"""Shared fixtures.

Every test runs against a throwaway data directory. SYNERGYSCAN_DATA is set
before synergyscan.paths is used so nothing can reach the developer's real
database - which, on the machine where this is being built, is also a live
printer's machine.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("SYNERGYSCAN_DATA", str(d))
    return d


@pytest.fixture
def con(isolated_data: Path) -> sqlite3.Connection:
    from synergyscan import db

    c = db.init(isolated_data / "inventory.db")
    yield c
    c.close()


@pytest.fixture
def item(con: sqlite3.Connection) -> int:
    from synergyscan import db

    return db.create_item(con, sku="SKU-0042", name="Pasta di semola 500 g",
                          unit="pcs", min_qty=10)


@pytest.fixture
def no_printer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the printer look absent, whatever is plugged into this machine.

    Without this, tests behave differently on a developer's desk than in CI -
    and on this project the developer's desk has a printer on it.
    """
    from synergyscan.printer import transport

    monkeypatch.setattr(transport, "list_devices", lambda: [])


@pytest.fixture
def client(con, no_printer):
    from fastapi.testclient import TestClient

    from synergyscan import app as appmod

    with TestClient(appmod.app) as c:
        yield c


def has_printer() -> bool:
    try:
        import hid

        return bool(hid.enumerate(0x1820, 0))
    except Exception:
        return False


needs_printer = pytest.mark.skipif(
    not has_printer(), reason="no T50M Pro connected"
)
