"""Hardware debugging CLI for the label printer.

Kept deliberately thin - it is a window onto the library, not a place for logic.
This is what you reach for on a customer machine when labels are not coming out.

    python -m synergyscan.printer --list
    python -m synergyscan.printer --probe
    python -m synergyscan.printer --preview --text "Pasta" --barcode SKU-0042
    python -m synergyscan.printer --text "Pasta" --barcode SKU-0042   # prints

--preview needs no printer, which is why it is also the update self-test's
rendering check.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from PIL import Image

from .errors import PrinterError
from .render import fit_image, render
from .service import DEFAULT_SIZE, PrintService
from .spec import Barcode, LabelSize, LabelSpec


def _size(text: str) -> LabelSize:
    w, h = (int(v) for v in text.lower().split("x"))
    return LabelSize(width_mm=w, height_mm=h)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m synergyscan.printer",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", action="append", default=[],
                    help="a line of text; repeat for more lines")
    ap.add_argument("--barcode", help="barcode value")
    ap.add_argument("--symbology", default="code128",
                    choices=["code128", "ean13", "code39", "qr"])
    ap.add_argument("--show-text", action="store_true",
                    help="print the human-readable digits under the bars")
    ap.add_argument("--image", help="print an image file instead of a spec")
    ap.add_argument("--density", type=int, default=4, help="0-15, darker as it rises")
    ap.add_argument("--copies", type=int, default=1)
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    ap.add_argument("--size", type=_size, default=None,
                    help="WxH in mm; default is whatever roll the printer reports")
    ap.add_argument("--list", action="store_true", help="show detected HID interfaces")
    ap.add_argument("--probe", action="store_true", help="status and loaded label info")
    ap.add_argument("--preview", metavar="PATH", nargs="?", const="preview.png",
                    help="save a PNG instead of printing (no printer needed)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,          # stdout stays clean for JSON
    )

    svc = PrintService(fallback_size=a.size or DEFAULT_SIZE)

    if a.list:
        from .transport import list_devices
        print(json.dumps(list_devices(), indent=2, default=str))
        return 0

    if a.probe:
        print(json.dumps(svc.probe(), indent=2, default=str))
        return 0

    spec = LabelSpec(
        lines=a.text,
        barcode=Barcode(symbology=a.symbology, value=a.barcode,
                        show_text=a.show_text) if a.barcode else None,
        density=a.density,
        copies=a.copies,
        rotate=a.rotate,
    )

    size = a.size or (DEFAULT_SIZE if a.preview else svc.probe_size())

    if a.preview:
        if a.image:
            img = fit_image(Image.open(a.image), size.width_mm, size.height_mm)
        else:
            if spec.is_empty():
                ap.error("give --text and/or --barcode, or --image")
            img = render(spec, size.width_mm, size.height_mm)
        img.save(a.preview)
        print(f"saved {a.preview}  ({size.width_mm}x{size.height_mm} mm, "
              f"{img.width}x{img.height} dots)")
        return 0

    try:
        if a.image:
            from .transport import UsbPrinter
            with UsbPrinter(verbose=a.verbose) as p:
                sz = a.size or p.label_size(DEFAULT_SIZE)
                bw = fit_image(Image.open(a.image), sz.width_mm, sz.height_mm)
                for _ in range(a.copies):
                    p.print_bitmap(bw, density=a.density)
            print("done")
            return 0

        if spec.is_empty():
            ap.error("give --text and/or --barcode, or --image")
        result = svc._print(spec)            # synchronous: this is a debug tool
        print(json.dumps(result.model_dump(), indent=2))
        return 0 if result.ok else 1
    except PrinterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
