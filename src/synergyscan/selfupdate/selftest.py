"""Does this release actually work?

Run as a subprocess, with the **new** release's interpreter, before the pointer
is allowed to move:

    <new>/.venv/Scripts/python -m synergyscan.selfupdate.selftest

Running it out-of-process is the whole point. Importing the new code into the
old process would prove nothing about whether the new venv resolves, whether
its native extensions load, or whether it can start at all. A subprocess that
exits 0 is evidence; an import is not.

What it deliberately does not do: print anything, or touch the printer beyond
opening the library. The roll may be out, the cover may be open, the vendor app
may hold the device - none of which says anything about whether this release is
sound, and none of which should block an update.

Prints a JSON report on stdout and exits non-zero on the first hard failure.
"""

from __future__ import annotations

import json
import sys
import traceback


def _check_imports() -> dict:
    import synergyscan
    from synergyscan import app, config, db, paths  # noqa: F401
    from synergyscan.printer import protocol, render, service, transport  # noqa: F401
    return {"package": getattr(synergyscan, "__version__", "?")}


def _check_hid() -> dict:
    """hidapi is a native extension: a broken install is invisible until print time."""
    import hid
    return {"hid": getattr(hid, "__version__", "present")}


def _check_render() -> dict:
    """Render a real label with a barcode. No printer needed."""
    from synergyscan.printer.render import render
    from synergyscan.printer.spec import Barcode, LabelSpec

    spec = LabelSpec(
        lines=["Self test", "2026-09-25"],
        barcode=Barcode(symbology="code128", value="SELFTEST"),
    )
    img = render(spec, 40, 30)
    if img.mode != "1":
        raise AssertionError(f"expected a 1-bit image, got mode {img.mode}")
    if (img.width, img.height) != (320, 240):
        raise AssertionError(f"expected 320x240 dots at 40x30 mm, got {img.size}")
    black = sum(1 for p in img.convert("L").tobytes() if p == 0)
    # A blank label would mean fonts or the barcode writer silently failed.
    if black < 500:
        raise AssertionError(f"rendered label is nearly blank ({black} dots set)")
    return {"dots_set": black, "size": list(img.size)}


def _check_protocol() -> dict:
    """Pack, buffer and compress a label the way a real print would."""
    from PIL import Image

    from synergyscan.printer import protocol as P

    img = Image.new("1", (320, 240), 1)
    for y in range(0, 240, 3):
        for x in range(0, 320, 2):
            img.putpixel((x, y), 0)
    data, cols = P.pack(img)
    if cols != 240 or len(data) != P.BPL * 240:
        raise AssertionError(f"pack produced {len(data)} bytes over {cols} columns")
    bufs = P.build_buffers(data, cols, density=4)
    blocks = P.compress_blocks(bufs)
    if not blocks or any(len(b) > P.BUF_SIZE for b in blocks):
        raise AssertionError("compressed blocks do not fit the printer buffer")
    return {"buffers": len(bufs), "blocks": len(blocks)}


def _check_migrations() -> dict:
    """Migration files are well named and contiguous, and apply to a fresh DB."""
    import sqlite3

    from synergyscan import migrations

    avail = migrations.available()
    con = sqlite3.connect(":memory:", isolation_level=None)
    try:
        applied = migrations.apply_all(con)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    finally:
        con.close()
    if "items" not in tables or "stock_movements" not in tables:
        raise AssertionError(f"core tables missing after migration: {tables}")
    return {"available": len(avail), "applied": applied}


def _check_data_dir() -> dict:
    """The data directory is reachable and writable before we commit to this release."""
    from synergyscan import paths

    d = paths.data_dir()
    probe = d / ".selftest"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return {"data_dir": str(d)}


CHECKS = (
    ("imports", _check_imports),
    ("hid", _check_hid),
    ("render", _check_render),
    ("protocol", _check_protocol),
    ("migrations", _check_migrations),
    ("data_dir", _check_data_dir),
)


def run() -> tuple[bool, dict]:
    report: dict = {"ok": True, "checks": {}}
    for name, fn in CHECKS:
        try:
            report["checks"][name] = {"ok": True, **fn()}
        except Exception as e:
            report["ok"] = False
            report["checks"][name] = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(limit=6),
            }
            report["failed_at"] = name
            break            # first failure is the useful one
    return report["ok"], report


def main() -> int:
    ok, report = run()
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
