"""Label printing for the Katasymbol / Supvan T50M Pro.

Layering, innermost first:

    protocol.py   pure wire format - packing, LZMA, checksums. No I/O.
    render.py     LabelSpec -> 1-bit bitmap. No I/O.
    transport.py  USB HID. The only module that touches hardware.
    service.py    the queue. The only thing the app talks to.

`python -m synergyscan.printer --help` is the hardware debugging CLI.
"""

from .errors import PrinterError, PrinterNotFound
from .service import DEFAULT_SIZE, PrintService
from .spec import Barcode, LabelSize, LabelSpec, PrintResult

__all__ = [
    "Barcode",
    "DEFAULT_SIZE",
    "LabelSize",
    "LabelSpec",
    "PrintResult",
    "PrintService",
    "PrinterError",
    "PrinterNotFound",
]
