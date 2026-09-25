"""Turning a LabelSpec into a 1-bit bitmap at the printer's native resolution.

Two rules drive everything here.

1. **Threshold, never dither.** The original script called `.convert("1")`,
   which applies Floyd-Steinberg dithering. That is right for photographs and
   wrong for everything we print: dithered bar edges scan badly, and dithered
   text at 203 dpi looks like fuzz. We threshold at mid-grey instead, and only
   dither when asked to place an actual photo.

2. **Never emit an unscannable barcode.** Bar widths must be whole dots, so we
   only ever use integer dots-per-module, and if the data cannot fit at 1 dot
   per module we raise instead of scaling something down until a scanner
   refuses it. A missing label is a visible problem; an unreadable one is an
   invisible one that surfaces weeks later at stocktake.
"""

from __future__ import annotations


from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .protocol import DOTS_PER_MM, MARGIN
from .spec import Barcode, LabelSpec

# Tried in order. The first two cover Windows and most Linux CI images; the
# bitmap default is a last resort so rendering never hard-fails on font setup.
FONT_CANDIDATES = (
    "arialbd.ttf",
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
    "FreeSansBold.ttf",
)

THRESHOLD = 128
MIN_FONT_PX = 9          # below this, thermal output is not reliably legible

# MARGIN (from the protocol) is 8 dots dropped at each end of the *feed*
# direction - build_buffers really does discard those lines, so the canvas has
# to leave them blank. Across the tape there is no such constraint: pack()
# centres the image on the 384-dot head and a 40 mm label is only 320 dots
# wide. Insetting by MARGIN there was simply throwing away 2 mm of usable
# width, which is the difference between a barcode at 2 dots per bar and one at
# 1. A small inset remains for label alignment tolerance.
SIDE_MARGIN = 4

# Code 128 and Code 39 want a quiet zone of ten times the bar width. It has to
# scale with the bars, not sit at a fixed 1 mm, or wide bars end up under-spec
# and scanners get unreliable at exactly the sizes that should be easiest.
QUIET_ZONE_DOTS = 8              # floor, and what the QR path uses
QUIET_ZONE_MODULES = 10


@lru_cache(maxsize=64)
def _font(size: int):
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _has_truetype() -> bool:
    """False when we fell back to the bitmap font (sizing is then fixed)."""
    return not isinstance(_font(20), ImageFont.ImageFont)


def to_1bit(img: Image.Image, dither: bool = False) -> Image.Image:
    """Greyscale -> 1-bit. Thresholded by default; see rule 1 above."""
    g = img.convert("L")
    if dither:
        return g.convert("1")
    return g.point(lambda p: 255 if p >= THRESHOLD else 0, mode="1")


def dots(mm: float) -> int:
    return int(round(mm * DOTS_PER_MM))


# --------------------------------------------------------------------- text
def _fit_text(draw, text: str, font_path_size: int, box_w: int) -> tuple[int, str]:
    """Largest font size at which `text` fits `box_w`, and the text to draw.

    Shrinks to MIN_FONT_PX, then truncates with an ellipsis rather than letting
    the line run off the edge of the label.
    """
    size = font_path_size
    while size > MIN_FONT_PX:
        if draw.textlength(text, font=_font(size)) <= box_w:
            return size, text
        size -= 1
    # At the floor: truncate instead of overflowing.
    f = _font(MIN_FONT_PX)
    if draw.textlength(text, font=f) <= box_w:
        return MIN_FONT_PX, text
    cut = text
    while cut and draw.textlength(cut + "…", font=f) > box_w:
        cut = cut[:-1]
    return MIN_FONT_PX, (cut + "…") if cut else ""


def text_block(lines: list[str], box_w: int, box_h: int) -> Image.Image:
    """Stack of centred lines, each as large as it can be within the box.

    The first line gets the most room - it is the item name, and that is what
    someone reads from a metre away.
    """
    img = Image.new("L", (max(box_w, 1), max(box_h, 1)), 255)
    if not lines:
        return img
    d = ImageDraw.Draw(img)

    # Give line 1 a bigger share, then split the rest evenly.
    weights = [1.6] + [1.0] * (len(lines) - 1)
    total = sum(weights)
    slots = [max(int(box_h * w / total), MIN_FONT_PX) for w in weights]

    sized: list[tuple[int, str]] = []
    ceiling = box_h
    for text, slot in zip(lines, slots):
        start = max(int(slot * 0.82), MIN_FONT_PX + 1)
        size, fitted = _fit_text(d, text, min(start, ceiling), box_w)
        # Never let a later line out-shout an earlier one. Without this a short
        # second line ("500 g") keeps its full slot while a long first line
        # shrinks to fit the width, and the label reads back to front.
        sized.append((size, fitted))
        ceiling = size

    used = sum(s for s, _ in sized)
    if used > box_h:
        # Scale everything down rather than running off the label.
        factor = box_h / used
        sized = [(max(int(s * factor), MIN_FONT_PX), t) for s, t in sized]
        sized = [_fit_text(d, t, s, box_w) for s, t in sized]
        used = sum(s for s, _ in sized)
    y = max((box_h - used) // 2, 0)
    for size, text in sized:
        if not text:
            y += size
            continue
        d.text((box_w // 2, y + size // 2), text, font=_font(size), fill=0, anchor="mm")
        y += size
    return img


# ------------------------------------------------------------------ barcode
def render_barcode(bc: Barcode, box_w: int, box_h: int) -> Image.Image:
    """1D barcode or QR code sized in whole dots. Raises if it cannot fit."""
    if bc.symbology == "qr":
        return _render_qr(bc, box_w, box_h)
    return _render_1d(bc, box_w, box_h)


def _render_qr(bc: Barcode, box_w: int, box_h: int) -> Image.Image:
    import qrcode

    side = min(box_w, box_h)
    q = qrcode.QRCode(version=None, border=2, box_size=1,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(bc.value)
    q.make(fit=True)
    modules = q.modules_count + 2 * q.border
    box = side // modules
    if box < 1:
        raise ValueError(
            f"QR code for {bc.value!r} needs at least {modules} dots "
            f"({modules / DOTS_PER_MM:.1f} mm) but only {side} are available"
        )
    q.box_size = box
    img = q.make_image(fill_color="black", back_color="white").convert("L")
    return img


def _render_1d(bc: Barcode, box_w: int, box_h: int) -> Image.Image:
    """Draw the bars ourselves, one module to a whole number of dots.

    python-barcode's ImageWriter is not usable here. It positions bars by
    converting millimetres to pixels and rounding each one independently, so
    asking for a 3-dot module yields a mix of 2, 3 and 4 dot bars, and at 1 dot
    it collapses to a zero-width rectangle and raises. Uneven bars are exactly
    what a thermal label cannot afford.

    So we take the module string from build() - a plain run of "1" and "0" -
    and fill rectangles at an exact integer width. Dot-exact, deterministic,
    and it makes the quiet zone ours to control.
    """
    import barcode

    cls = barcode.get_barcode_class(bc.symbology)
    if box_w < 32:
        raise ValueError("not enough width on this label for a barcode")

    try:
        modules = "".join(cls(bc.value).build())
    except Exception as e:
        # Wrong number of digits for EAN-13, an unencodable character for
        # Code 39, and so on. The operator can act on this.
        raise ValueError(
            f"{bc.value!r} is not a valid {bc.symbology} code: {e}"
        ) from e

    n = len(modules)
    # Widest bars that still leave a proper quiet zone. 2 dots (~0.25 mm) is
    # the usual minimum for dependable scanning; 1 dot is a last resort we warn
    # about rather than refuse.
    module_dots, quiet = 0, QUIET_ZONE_DOTS
    for d_ in (4, 3, 2, 1):
        q = max(QUIET_ZONE_DOTS, QUIET_ZONE_MODULES * d_)
        if n * d_ + 2 * q <= box_w:
            module_dots, quiet = d_, q
            break
    if not module_dots:
        raise ValueError(
            f"barcode {bc.value!r} needs {n} bars, which will not fit a "
            f"{box_w / DOTS_PER_MM:.0f} mm label even at the minimum bar width. "
            f"Use a shorter code, a QR code, or a wider roll."
        )
    if module_dots == 1:
        import logging
        logging.getLogger(__name__).warning(
            "barcode %r rendered at 1 dot per bar - may scan poorly; "
            "consider a shorter code, a QR code, or a wider label", bc.value
        )

    text_h = min(MIN_FONT_PX + 3, box_h // 3) if bc.show_text else 0
    bar_h = max(box_h - text_h, 1)
    width = n * module_dots + 2 * quiet

    img = Image.new("L", (width, box_h), 255)
    d = ImageDraw.Draw(img)
    for i, m in enumerate(modules):
        if m == "1":
            x0 = quiet + i * module_dots
            d.rectangle([x0, 0, x0 + module_dots - 1, bar_h - 1], fill=0)

    if text_h:
        human = cls(bc.value).get_fullcode()
        size, text = _fit_text(d, human, text_h, width - 2 * quiet)
        if text:
            d.text((width // 2, bar_h + text_h // 2), text, font=_font(size),
                   fill=0, anchor="mm")
    return img


# ------------------------------------------------------------------- layout
def render(spec: LabelSpec, width_mm: int, height_mm: int) -> Image.Image:
    """Compose a LabelSpec onto a label-sized 1-bit canvas.

    `width_mm` is across the tape, `height_mm` along the feed direction - the
    same convention the printer reports in CMD_RETURN_MAT.
    """
    w, h = dots(width_mm), dots(height_mm)
    canvas = Image.new("L", (w, h), 255)
    # Vertical inset is the protocol's - build_buffers drops those lines.
    # Horizontal is ours, and much smaller; see SIDE_MARGIN.
    inner_w, inner_h = w - 2 * SIDE_MARGIN, h - 2 * MARGIN
    if inner_w < 8 or inner_h < 8:
        raise ValueError(f"label {width_mm}x{height_mm} mm is too small to print on")

    if spec.barcode is None:
        canvas.paste(text_block(spec.lines, inner_w, inner_h), (SIDE_MARGIN, MARGIN))
    elif spec.barcode.symbology == "qr":
        # QR beside the text: a square code plus a readable name reads better
        # than either alone when someone is picking from a shelf.
        side = min(inner_h, inner_w // 2) if spec.lines else min(inner_h, inner_w)
        qr = to_1bit(render_barcode(spec.barcode, side, side)).convert("L")
        qy = MARGIN + (inner_h - qr.height) // 2
        if spec.lines:
            canvas.paste(qr, (SIDE_MARGIN, qy))
            gap = 6
            tx = SIDE_MARGIN + qr.width + gap
            canvas.paste(text_block(spec.lines, w - SIDE_MARGIN - tx, inner_h),
                         (tx, MARGIN))
        else:
            canvas.paste(qr, (SIDE_MARGIN + (inner_w - qr.width) // 2, qy))
    else:
        # 1D barcode across the full width at the bottom, text above it.
        bar_h = int(inner_h * (0.55 if spec.lines else 0.9))
        bar = to_1bit(render_barcode(spec.barcode, inner_w, bar_h)).convert("L")
        by = h - MARGIN - bar.height
        canvas.paste(bar, (SIDE_MARGIN + (inner_w - bar.width) // 2, by))
        if spec.lines:
            canvas.paste(text_block(spec.lines, inner_w, by - MARGIN - 2),
                         (SIDE_MARGIN, MARGIN))

    if spec.rotate:
        canvas = canvas.rotate(spec.rotate, expand=True, fillcolor=255)
        canvas = fit_image(canvas, width_mm, height_mm, dither=False).convert("L")
    return to_1bit(canvas, dither=False)


def fit_image(img: Image.Image, width_mm: float, height_mm: float,
              dither: bool = True) -> Image.Image:
    """Scale and centre an arbitrary image onto a label-sized 1-bit canvas.

    This is the photo path, so dithering is on by default here.
    """
    w, h = dots(width_mm), dots(height_mm)
    img = ImageOps.exif_transpose(img).convert("L")
    img = ImageOps.contain(img, (w, h))
    canvas = Image.new("L", (w, h), 255)
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return to_1bit(canvas, dither=dither)
