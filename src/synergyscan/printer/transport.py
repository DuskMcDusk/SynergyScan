"""USB HID transport and the print sequence for the T50M Pro.

This is the only module that touches hardware. `hid` is imported lazily so the
rest of the package - and the whole test suite - works on a machine with no
printer and no hidapi installed.

Only one process can hold the HID handle. printer.service owns that constraint;
nothing else in the app should construct a UsbPrinter directly.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from . import protocol as P
from .errors import NOT_FOUND, PrinterError, PrinterNotFound
from .spec import LabelSize

log = logging.getLogger(__name__)


def list_devices() -> list[dict]:
    """Every HID interface exposed by a Supvan/Katasymbol printer."""
    try:
        import hid
    except ImportError as e:      # pragma: no cover - depends on host setup
        raise PrinterError(
            "The hidapi library is not installed, so the printer cannot be used. "
            "Reinstall SynergyScan to repair this."
        ) from e
    return list(hid.enumerate(P.VID, 0))


class UsbPrinter:
    """An open HID connection. Use as a context manager."""

    def __init__(self, path: bytes | None = None, verbose: bool = False):
        import hid

        devices = list_devices()
        if not devices:
            raise PrinterNotFound(NOT_FOUND, retryable=True)
        if path is None:
            # Prefer a vendor-defined collection (usage page >= 0xFF00) when the
            # printer exposes several interfaces.
            devices.sort(key=lambda d: d.get("usage_page", 0) < 0xFF00)
            path = devices[0]["path"]
        self.dev = hid.device()
        try:
            self.dev.open_path(path)
        except OSError as e:
            # Almost always the vendor app holding the device open.
            raise PrinterNotFound(NOT_FOUND, retryable=True) from e
        self.verbose = verbose

    # ------------------------------------------------------------- plumbing
    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:      # pragma: no cover - closing a dead handle
            pass

    def __enter__(self) -> UsbPrinter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _write(self, data: bytes) -> None:
        report = bytes(data[:P.REPORT]).ljust(P.REPORT, b"\0")
        # Leading 0x00 means "no report ID"; hidapi on Windows requires it.
        self.dev.write(b"\x00" + report)

    def _read(self, timeout_ms: int = 2000) -> bytes | None:
        r = self.dev.read(P.REPORT, timeout_ms)
        return bytes(r) if r else None

    def cmd(self, cmd: int, p1: int = 0, p2: int | None = None,
            read: bool = True) -> bytes | None:
        f = P.frame(cmd, p1, p2)
        self._write(f)
        resp = self._read() if read else None
        if self.verbose:
            log.debug("TX %s  RX %s", f.hex(" "),
                      resp[:16].hex(" ") if resp else "-")
        return resp

    # ----------------------------------------------------------- high level
    def check_device(self) -> bool:
        return bool(self.cmd(P.CMD_CHECK_DEVICE))

    def status(self) -> dict | None:
        return P.decode_status(self.cmd(P.CMD_INQUIRY_STA))

    def material(self) -> dict | None:
        return P.decode_material(self.cmd(P.CMD_RETURN_MAT))

    def label_size(self, fallback: LabelSize) -> LabelSize:
        """Size of the loaded roll, read from the printer.

        Templates are laid out from this rather than hardcoded, so swapping to a
        different roll just works.
        """
        mat = self.material()
        if mat and mat.get("height") and mat.get("width"):
            return LabelSize(width_mm=mat["width"], height_mm=mat["height"])
        log.info("printer did not report a label size; using fallback %s", fallback)
        return fallback

    def _wait(self, cond: Callable[[dict], bool], tries: int, interval: float,
              what: str) -> dict:
        """Poll status until `cond` holds, raising on a printer fault."""
        from .errors import from_flags
        for _ in range(tries):
            st = self.status()
            if st:
                flags = P.status_errors(st)
                if flags:
                    raise from_flags(flags)
                if cond(st):
                    return st
            time.sleep(interval)
        raise PrinterError(
            f"The printer stopped responding while {what}. Switch it off and on, "
            "then try again.", retryable=True
        )

    def print_bitmap(self, bw, density: int = 4) -> dict:
        """Send one prepared 1-bit label image. Blocks until the printer is done."""
        bufs, blocks = P.prepare(bw, density)
        speed = (P.MULTI_BLOCK_SPEED if len(blocks) > 1
                 else P.calc_speed(sum(map(len, blocks)) // len(bufs)))
        log.info("printing %d buffers in %d block(s) at speed %d",
                 len(bufs), len(blocks), speed)

        self._wait(lambda s: not s["device_busy"] and not s["printing"],
                   60, 0.1, "waiting for the printer to be ready")
        self.cmd(P.CMD_START_PRINT)
        self._wait(lambda s: s["printing"], 60, 0.1, "starting the print")

        for z in blocks:
            self._wait(lambda s: not s["buf_full"], 200, 0.02,
                       "waiting for printer buffer space")
            if not self.cmd(P.CMD_NEXT_ZIPPEDBULK, len(z)):
                raise PrinterError(
                    "The printer did not acknowledge the label data. Switch it "
                    "off and on, then try again.", retryable=True
                )
            for off in range(0, len(z), P.REPORT):
                self._write(z[off:off + P.REPORT])
                time.sleep(0.001)
            time.sleep(0.1)
            self.cmd(P.CMD_BUF_FULL, len(z), speed)
            time.sleep(0.1)

        return self._wait(
            lambda s: not (s["printing"] or s["device_busy"] or s["buf_full"]),
            300, 0.1, "finishing the print",
        )
