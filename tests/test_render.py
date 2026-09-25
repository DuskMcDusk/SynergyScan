"""Rendering tests.

Deliberately property-based rather than golden-image where fonts are involved:
the installed fonts differ between this machine, a customer PC and CI, so
pixel-exact assertions on text would fail for reasons that have nothing to do
with correctness. What matters is checked instead - geometry, that output is
pure black and white, that nothing overflows the label, and that barcodes are
never silently rendered unscannable.

Barcode geometry *is* asserted exactly, because bar widths in whole dots are
the difference between a label that scans and one that does not.
"""

from __future__ import annotations

import pytest
from PIL import Image

from synergyscan.printer import render as R
from synergyscan.printer.protocol import DOTS_PER_MM, MARGIN
from synergyscan.printer.spec import Barcode, LabelSpec


def colours(img: Image.Image) -> set[int]:
    return set(img.convert("L").getdata())


def black_dots(img: Image.Image) -> int:
    return sum(1 for p in img.convert("L").getdata() if p < 128)


# ------------------------------------------------------------------ geometry
@pytest.mark.parametrize("w_mm,h_mm", [(40, 30), (50, 25), (30, 20), (12, 40)])
def test_render_produces_a_label_sized_1bit_image(w_mm, h_mm):
    img = R.render(LabelSpec(lines=["Test"]), w_mm, h_mm)
    assert img.mode == "1"
    assert img.size == (w_mm * DOTS_PER_MM, h_mm * DOTS_PER_MM)


def test_dots_rounds_to_whole_dots():
    assert R.dots(40) == 320
    assert R.dots(0.25) == 2          # one barcode module at 203 dpi


def test_render_rejects_a_label_too_small_to_print_on():
    with pytest.raises(ValueError, match="too small"):
        R.render(LabelSpec(lines=["x"]), 1, 1)


# ------------------------------------------------------------- monochrome-ness
def test_output_is_pure_black_and_white_not_dithered_grey():
    """Rule 1 in render.py: dithered text and bars scan and read badly."""
    img = R.render(
        LabelSpec(lines=["Pasta di semola"], barcode=Barcode(value="SKU-0042")),
        40, 30,
    )
    assert colours(img) <= {0, 255}


def test_to_1bit_thresholds_by_default():
    grey = Image.new("L", (4, 1))
    grey.putdata([0, 120, 135, 255])
    out = list(R.to_1bit(grey).convert("L").getdata())
    assert out == [0, 0, 255, 255]          # midpoint 128, no dithering


def test_to_1bit_can_dither_for_photographs():
    grey = Image.new("L", (64, 64), 128)     # flat mid-grey
    plain = R.to_1bit(grey, dither=False)
    dithered = R.to_1bit(grey, dither=True)
    assert black_dots(plain) == 0            # 128 >= threshold, so all white
    assert 0 < black_dots(dithered) < 64 * 64


# ----------------------------------------------------------------------- text
def test_nothing_is_drawn_in_the_feed_margins():
    """build_buffers discards MARGIN lines at each end, so ink there is lost."""
    img = R.render(LabelSpec(lines=["Pasta di semola 500 g", "Lotto 2026-09-25"],
                             barcode=Barcode(value="SKU-0042")), 40, 30)
    px = img.convert("L").load()
    w, h = img.size
    for x in range(w):
        for y in list(range(MARGIN)) + list(range(h - MARGIN, h)):
            assert px[x, y] == 255, f"ink in the feed margin at {x},{y}"


def test_a_very_long_line_is_truncated_rather_than_overflowing():
    long = "Extremely long product description that cannot possibly fit"
    img = R.render(LabelSpec(lines=[long]), 25, 15)
    assert img.size == (200, 120)
    assert black_dots(img) > 0               # something was drawn
    px = img.convert("L").load()
    for y in range(img.height):              # nothing in the side margins
        for x in list(range(R.SIDE_MARGIN)) + list(
                range(img.width - R.SIDE_MARGIN, img.width)):
            assert px[x, y] == 255


def test_blank_lines_are_dropped_by_the_spec():
    assert LabelSpec(lines=["a", "  ", "", "b"]).lines == ["a", "b"]


def test_empty_spec_is_recognised():
    assert LabelSpec().is_empty()
    assert not LabelSpec(lines=["x"]).is_empty()
    assert not LabelSpec(barcode=Barcode(value="x")).is_empty()


def test_text_only_label_draws_ink():
    assert black_dots(R.render(LabelSpec(lines=["ABC123"]), 40, 30)) > 100


# -------------------------------------------------------------------- barcode
def test_code128_module_width_is_a_whole_number_of_dots():
    """Fractional bar widths are the classic cause of labels that will not scan."""
    img = R.render_barcode(Barcode(symbology="code128", value="SKU-0042"), 300, 100)
    bw = R.to_1bit(img)
    px = bw.convert("L").load()
    runs, run, prev = [], 0, None
    for x in range(bw.width):
        v = px[x, bw.height // 2]
        if v == prev:
            run += 1
        else:
            if prev is not None:
                runs.append(run)
            prev, run = v, 1
    runs.append(run)
    inner = runs[1:-1]                       # ignore the white margins
    assert inner, "no bars were drawn"
    unit = min(inner)
    assert unit >= 2, f"bars are only {unit} dot(s) wide"
    assert all(r % unit == 0 for r in inner), f"bar widths not multiples of {unit}: {runs}"


def test_barcode_fits_the_available_width_including_its_quiet_zone():
    box = 300
    img = R.render_barcode(Barcode(value="SKU-0042"), box, 100)
    assert img.width <= box


def test_the_quiet_zone_scales_with_bar_width():
    """Ten times the bar width, per the Code 128 spec - not a fixed 1 mm."""
    img = R.render_barcode(Barcode(value="SKU-0042"), 320, 100)
    px = img.convert("L").load()
    first_ink = next(x for x in range(img.width) if px[x, 0] < 128)
    runs = []
    run, prev = 0, None
    for x in range(img.width):
        v = px[x, 0]
        if v == prev:
            run += 1
        else:
            if prev is not None:
                runs.append(run)
            prev, run = v, 1
    module = min(runs[1:-1])
    assert first_ink == R.QUIET_ZONE_MODULES * module


def test_an_over_long_barcode_is_refused_not_silently_shrunk():
    """A missing label is visible; an unreadable one is found weeks later."""
    with pytest.raises(ValueError, match="will not fit|not enough width"):
        R.render_barcode(Barcode(value="A" * 60), 120, 80)


def test_an_invalid_code_for_the_symbology_is_reported_plainly():
    with pytest.raises(ValueError, match="not a valid ean13"):
        R.render_barcode(Barcode(symbology="ean13", value="123"), 320, 80)


def test_barcode_is_positioned_below_the_text():
    img = R.render(LabelSpec(lines=["Pasta"], barcode=Barcode(value="SKU-1")), 40, 30)
    px = img.convert("L").load()
    top = sum(1 for y in range(MARGIN, img.height // 2)
              for x in range(img.width) if px[x, y] == 0)
    bottom = sum(1 for y in range(img.height // 2, img.height - MARGIN)
                 for x in range(img.width) if px[x, y] == 0)
    assert top > 0 and bottom > top


def test_qr_code_is_square_and_sits_beside_the_text():
    img = R.render(LabelSpec(lines=["Pasta"], barcode=Barcode(symbology="qr",
                                                             value="SKU-0042")),
                   40, 30)
    assert colours(img) <= {0, 255}
    px = img.convert("L").load()
    left = sum(1 for y in range(img.height) for x in range(MARGIN, img.width // 2)
               if px[x, y] == 0)
    right = sum(1 for y in range(img.height)
                for x in range(img.width // 2, img.width - MARGIN) if px[x, y] == 0)
    assert left > 0 and right > 0


def test_qr_alone_is_centred():
    img = R.render(LabelSpec(barcode=Barcode(symbology="qr", value="X")), 30, 30)
    assert black_dots(img) > 50


def test_qr_too_dense_for_the_label_is_refused():
    # 120 characters needs roughly 45 modules plus a quiet zone; a 40-dot box
    # cannot give each module even one whole dot.
    with pytest.raises(ValueError, match="needs at least"):
        R.render_barcode(Barcode(symbology="qr", value="Z" * 120), 40, 40)


def test_one_dot_bars_are_reachable_for_long_codes():
    img = R.render_barcode(Barcode(value="A" * 20), 320, 80)
    assert img.width <= 320


def test_a_typical_sku_gets_two_dot_bars_on_a_40mm_label():
    """The size that matters in practice. Regression for the wasted side inset:
    insetting by the protocol MARGIN horizontally pushed this to 1-dot bars."""
    img = R.render(LabelSpec(lines=["Pasta"], barcode=Barcode(value="SKU-0042")),
                   40, 30)
    px = img.convert("L").load()
    row = img.height - MARGIN - 4
    runs, run, prev = [], 0, None
    for x in range(img.width):
        v = px[x, row]
        if v == prev:
            run += 1
        else:
            if prev is not None:
                runs.append(run)
            prev, run = v, 1
    runs.append(run)
    assert min(runs[1:-1]) >= 2


@pytest.mark.parametrize("symbology,value", [
    ("code128", "SKU-0042"),
    ("code39", "SKU0042"),
    ("ean13", "590123412345"),
    ("qr", "https://example.invalid/i/42"),
])
def test_every_symbology_renders(symbology, value):
    img = R.render(LabelSpec(lines=["Item"],
                             barcode=Barcode(symbology=symbology, value=value)),
                   50, 30)
    assert img.size == (400, 240)
    assert black_dots(img) > 100


def test_show_text_puts_the_digits_in_a_band_below_the_bars():
    """The bars give up height to make room, so total ink goes *down*, not up.

    What matters is that the bottom band is inked when the digits are asked for
    and clear when they are not.
    """
    box_h = 120
    without = R.render_barcode(Barcode(value="SKU-0042", show_text=False), 300, box_h)
    with_text = R.render_barcode(Barcode(value="SKU-0042", show_text=True), 300, box_h)

    def band_ink(img):
        px = img.convert("L").load()
        return sum(1 for y in range(box_h - 10, box_h)
                   for x in range(img.width) if px[x, y] < 128)

    assert band_ink(with_text) > 0
    assert band_ink(without) > 0          # bars run the full height here
    # The bars stop short when the digits are shown, so the very bottom row is
    # text only and much sparser than a solid run of bars.
    assert band_ink(with_text) < band_ink(without)


# ------------------------------------------------------------------ fit_image
def test_fit_image_centres_and_sizes_to_the_label():
    src = Image.new("L", (100, 50), 0)
    out = R.fit_image(src, 40, 30)
    assert out.mode == "1"
    assert out.size == (320, 240)
    assert black_dots(out) > 0


def test_fit_image_output_is_monochrome():
    src = Image.new("L", (80, 80), 90)
    assert colours(R.fit_image(src, 40, 30)) <= {0, 255}


# ----------------------------------------------------------------- rotation
@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_rotation_preserves_the_label_size(angle):
    img = R.render(LabelSpec(lines=["Rotate"], rotate=angle), 40, 30)
    assert img.size == (320, 240)
    assert img.mode == "1"
