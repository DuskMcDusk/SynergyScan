"""The print queue: the app's only entry point to the printer.

Two constraints shape this module.

* **One owner.** Only one process, and within it one thread, may hold the HID
  handle. Everything funnels through a single worker thread.
* **Printing blocks for seconds.** The protocol polls the printer between
  blocks, so a label takes a second or more. No HTTP request ever waits on
  that: callers enqueue a job, get an id back, and poll for the result.

The device is opened per job rather than held open. It costs a few
milliseconds and it means unplugging the printer, or closing the vendor app
that was holding it, recovers on the next job with no restart.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .errors import PrinterError
from .render import render
from .spec import LabelSize, LabelSpec, PrintResult

log = logging.getLogger(__name__)

DEFAULT_SIZE = LabelSize(width_mm=40, height_mm=30)
JOB_HISTORY = 200        # completed jobs kept for the UI and diagnostics


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    spec: LabelSpec
    state: str = "queued"            # queued | printing | done | failed
    queued_at: str = field(default_factory=_now)
    finished_at: str | None = None
    result: PrintResult | None = None


class PrintService:
    """Serialises label printing onto a single worker thread."""

    def __init__(self, fallback_size: LabelSize = DEFAULT_SIZE,
                 on_complete: Callable[[Job], None] | None = None):
        self.fallback_size = fallback_size
        self.on_complete = on_complete
        self._q: queue.Queue[Job | None] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stopping.clear()
        self._worker = threading.Thread(target=self._run, name="printer",
                                        daemon=True)
        self._worker.start()
        log.info("print service started")

    def stop(self, timeout: float = 10.0) -> None:
        self._stopping.set()
        self._q.put(None)
        if self._worker:
            self._worker.join(timeout)
        log.info("print service stopped")

    # ---------------------------------------------------------------- queue
    def submit(self, spec: LabelSpec) -> Job:
        if spec.is_empty():
            raise ValueError("nothing to print: the label has no text and no barcode")
        job = Job(id=uuid.uuid4().hex[:12], spec=spec)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._trim()
        self._q.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order[-limit:])]

    def _trim(self) -> None:
        while len(self._order) > JOB_HISTORY:
            self._jobs.pop(self._order.pop(0), None)

    # --------------------------------------------------------------- worker
    def _run(self) -> None:
        while not self._stopping.is_set():
            job = self._q.get()
            if job is None:
                break
            try:
                job.state = "printing"
                job.result = self._print(job.spec)
                job.state = "done" if job.result.ok else "failed"
            except Exception as e:                       # never kill the worker
                log.exception("print job %s crashed", job.id)
                job.state = "failed"
                job.result = PrintResult(
                    ok=False,
                    error="Something went wrong while printing. Run the "
                          "diagnostics report and send it to support.",
                    retryable=False,
                )
                _ = e
            finally:
                job.finished_at = _now()
                if self.on_complete:
                    try:
                        self.on_complete(job)
                    except Exception:
                        log.exception("on_complete hook failed for job %s", job.id)

    def _print(self, spec: LabelSpec) -> PrintResult:
        from .transport import UsbPrinter

        try:
            with UsbPrinter() as p:
                if not p.check_device():
                    return PrintResult(
                        ok=False, retryable=True,
                        error="The printer did not respond. Switch it off and on, "
                              "then try again.",
                    )
                size = p.label_size(self.fallback_size)
                bw = render(spec, size.width_mm, size.height_mm)
                st = None
                for _ in range(spec.copies):
                    st = p.print_bitmap(bw, density=spec.density)
                return PrintResult(
                    ok=True,
                    label_mm=(size.width_mm, size.height_mm),
                    copies=spec.copies,
                    print_count=(st or {}).get("print_count"),
                )
        except PrinterError as e:
            log.warning("print failed: %s (flag=%s)", e, e.flag)
            return PrintResult(ok=False, error=str(e), flag=e.flag,
                               retryable=e.retryable)
        except ValueError as e:
            # Bad label geometry or an over-long barcode: the operator's input
            # is at fault, and the message from render() already says how.
            return PrintResult(ok=False, error=str(e), retryable=False)

    # ----------------------------------------------------------- no-hardware
    def preview(self, spec: LabelSpec, size: LabelSize | None = None):
        """Render without printing. Used by the UI and by the update self-test."""
        size = size or self.probe_size()
        return render(spec, size.width_mm, size.height_mm)

    def probe_size(self) -> LabelSize:
        """Loaded label size, falling back when no printer is reachable."""
        from .transport import UsbPrinter
        try:
            with UsbPrinter() as p:
                return p.label_size(self.fallback_size)
        except PrinterError:
            return self.fallback_size

    def probe(self) -> dict:
        """Everything we can learn about the printer, for the doctor report."""
        from .transport import UsbPrinter, list_devices
        out: dict = {"devices": [], "reachable": False}
        try:
            out["devices"] = [
                {"product_id": f"{d.get('product_id', 0):04x}",
                 "product": d.get("product_string"),
                 "usage_page": f"0x{d.get('usage_page', 0):04x}"}
                for d in list_devices()
            ]
            with UsbPrinter() as p:
                out["reachable"] = p.check_device()
                out["status"] = p.status()
                out["material"] = p.material()
        except PrinterError as e:
            out["error"] = str(e)
            out["flag"] = e.flag
        return out
