"""Protocol tests.

These are the regression net around reverse-engineered code that cannot be
re-derived. They assert exact bytes deliberately: if someone "tidies up" the
checksum or the bit packing, the point is that a test fails immediately rather
than a customer's printer quietly producing blank labels.

Nothing here touches hardware.
"""

from __future__ import annotations

import lzma
import struct

import pytest
from PIL import Image

from synergyscan.printer import protocol as P


# ------------------------------------------------------------------ geometry
def test_head_geometry_is_unchanged():
    # 203 dpi over a 48 mm head. Every layout calculation depends on these.
    assert P.DOTS_PER_MM == 8
    assert P.HEAD_DOTS == 384
    assert P.BPL == 48
    assert P.MARGIN == 8


# --------------------------------------------------------------------- frame
def test_frame_is_eight_bytes_without_p2():
    assert P.frame(P.CMD_INQUIRY_STA) == bytes.fromhex("c0 40 00 00 11 00 08 00".replace(" ", ""))


def test_frame_appends_p2_big_endian():
    f = P.frame(P.CMD_BUF_FULL, 0x1234, 0x0020)
    assert f[2:4] == b"\x12\x34"
    assert f[-2:] == b"\x00\x20"
    assert len(f) == 10


# ---------------------------------------------------------------------- pack
def test_pack_sets_the_expected_bit_for_a_single_dot():
    """LSB-first within each byte, and the image is centred on the head.

    The head burns right-to-left relative to image space (see pack()'s
    docstring), so a column's local, centred offset is mirrored around
    HEAD_DOTS-1 before it is packed.
    """
    img = Image.new("1", (8, 1), 1)      # 1 = white
    img.putpixel((0, 0), 0)             # black at x=0
    data, cols = P.pack(img)

    assert cols == 1
    assert len(data) == P.BPL
    # An 8-dot-wide image is centred: local offset = (384 - 8) // 2 = 188,
    # mirrored to head dot 383 - 188 = 195.
    dot = 195
    assert data[dot // 8] == 1 << (dot % 8)
    assert sum(data) == 1 << (dot % 8)


def test_pack_crops_a_wide_label_symmetrically():
    """A 50 mm roll is 400 dots; the head is 384, so 8 come off each side."""
    img = Image.new("1", (400, 1), 1)
    img.putpixel((0, 0), 0)             # inside the cropped-away left margin
    img.putpixel((8, 0), 0)             # first (leftmost) dot that survives
    data, _ = P.pack(img)
    # Mirrored: the leftmost surviving column lands at the highest head dot,
    # not the lowest - see pack()'s docstring.
    assert data[P.BPL - 1] == 0x80
    assert sum(data) == 0x80


def test_pack_length_tracks_height():
    img = Image.new("1", (320, 240), 1)
    data, cols = P.pack(img)
    assert cols == 240
    assert len(data) == P.BPL * 240


def test_pack_blank_label_is_all_zero():
    data, _ = P.pack(Image.new("1", (320, 30), 1))
    assert set(data) == {0}


# ------------------------------------------------------------------- buffers
def test_build_buffer_header_layout():
    buf = P.build_buffer(b"\xff" * 48, cols=1, first=True, last=True, density=4)

    assert len(buf) == P.BUF_SIZE
    assert buf[2] == 0x02 | 0x04 | 0x08                  # first and last
    assert buf[3] == ((4 & 0x0F) << 2) | (1 << 6)        # density and mat
    assert struct.unpack("<H", buf[4:6])[0] == 1         # cols
    assert buf[6] == P.BPL
    assert struct.unpack("<H", buf[8:10])[0] == P.MARGIN
    assert struct.unpack("<H", buf[10:12])[0] == P.MARGIN
    assert buf[12] == 4
    assert buf[P.BUF_HDR:P.BUF_HDR + 48] == b"\xff" * 48


def test_build_buffer_first_and_last_flags_differ():
    mid = P.build_buffer(b"\x00" * 48, 1, first=False, last=False, density=4)
    first = P.build_buffer(b"\x00" * 48, 1, first=True, last=False, density=4)
    last = P.build_buffer(b"\x00" * 48, 1, first=False, last=True, density=4)
    assert mid[2] == 0x00
    assert first[2] == 0x02
    assert last[2] == 0x0C           # 0x04 | 0x08 - the firmware wants both


def test_checksum_matches_the_reverse_engineered_formula():
    """Pinned against the observed firmware behaviour, odd sampling included."""
    chunk = bytes(range(256)) * 12                        # 3072 bytes
    cols = len(chunk) // P.BPL
    buf = P.build_buffer(chunk, cols=cols, first=True, last=True, density=6)

    data_end = cols * P.BPL + P.BUF_HDR
    expected = (sum(buf[2:14])
                + sum(buf[i * 256 - 1] for i in range(1, data_end // 256 + 1)))
    assert struct.unpack("<H", buf[0:2])[0] == expected & 0xFFFF


def test_checksum_covers_payload_once_it_is_long_enough_to_be_sampled():
    """The payload only reaches the checksum through the every-256th-byte sampling.

    A difference at a sampled offset changes the checksum; the sampling starts
    at buffer offset 255, so the payload has to reach that far to count.
    """
    long_a = bytearray(b"\x00" * 3072)
    long_b = bytearray(long_a)
    long_b[255 - P.BUF_HDR] = 0xFF          # lands exactly on buf[255]
    cols = len(long_a) // P.BPL
    ca = P.build_buffer(bytes(long_a), cols, True, True, 4)
    cb = P.build_buffer(bytes(long_b), cols, True, True, 4)
    assert ca[0:2] != cb[0:2]


def test_checksum_does_not_cover_a_short_payload_at_all():
    """Documented weakness, not a bug to fix.

    For a one-column buffer data_end is 62, so the sampling loop is empty and
    only the header contributes. Two single-column buffers with different
    payloads therefore carry the same checksum. That is what the firmware does;
    this test exists so nobody "corrects" the formula on the assumption that it
    must cover the data.
    """
    a = P.build_buffer(b"\x00" * 48, 1, True, True, 4)
    b = P.build_buffer(b"\xff" * 48, 1, True, True, 4)
    assert a[0:2] == b[0:2]


def test_density_is_clamped_to_fifteen():
    buf = P.build_buffer(b"\x00" * 48, 1, True, True, density=99)
    assert buf[12] == 15


def test_build_buffers_drops_the_margins_at_both_ends():
    cols = 240
    data = bytes(P.BPL * cols)
    bufs = P.build_buffers(data, cols, density=4)
    total = sum(struct.unpack("<H", b[4:6])[0] for b in bufs)
    assert total == cols - 2 * P.MARGIN


def test_build_buffers_splits_long_labels():
    cols = 400                                    # 50 mm of feed at 8 dots/mm
    bufs = P.build_buffers(bytes(P.BPL * cols), cols, density=4)
    assert len(bufs) > 1
    assert struct.unpack("<H", bufs[0][4:6])[0] == P.MAX_BUF_DATA // P.BPL
    assert all(len(b) == P.BUF_SIZE for b in bufs)


def test_build_buffers_rejects_a_label_shorter_than_the_margins():
    with pytest.raises(ValueError, match="feed direction"):
        P.build_buffers(bytes(P.BPL * 10), 10, density=4)


# ---------------------------------------------------------------- compression
def unpatch(z: bytes) -> bytes:
    """Restore the "unknown size" header so liblzma will read the stream back.

    compress() writes the real uncompressed size into the FORMAT_ALONE header
    because the firmware reads it from there. liblzma then refuses the stream:
    a known size alongside the end-of-stream marker the encoder emitted is a
    combination its alone-decoder treats as corrupt. Undoing just those eight
    bytes lets a test verify the payload, and proves the patch touched nothing
    else.
    """
    b = bytearray(z)
    struct.pack_into("<Q", b, 5, 0xFFFFFFFFFFFFFFFF)
    return bytes(b)


def test_compress_writes_the_alone_header_the_firmware_expects():
    raw = bytes(range(256)) * 8
    z = P.compress(raw)
    assert z[0] == 0x5D                                   # lc=3 lp=0 pb=2
    assert struct.unpack("<I", z[1:5])[0] == 8192          # dict_size
    assert struct.unpack("<Q", z[5:13])[0] == len(raw)     # patched real size


def test_compress_payload_round_trips_once_the_header_is_restored():
    raw = bytes(range(256)) * 8
    assert lzma.decompress(unpatch(P.compress(raw)), format=lzma.FORMAT_ALONE) == raw


def test_patched_stream_is_not_readable_by_liblzma():
    """Pins the quirk above, so it is a documented fact rather than a surprise."""
    with pytest.raises(lzma.LZMAError):
        lzma.decompress(P.compress(b"x" * 1000), format=lzma.FORMAT_ALONE)


def test_compress_round_trips_a_realistic_buffer():
    buf = P.build_buffer(bytes(range(256)) * 12, cols=64, first=True, last=True,
                         density=4)
    assert lzma.decompress(unpatch(P.compress(buf)),
                           format=lzma.FORMAT_ALONE) == buf


def test_compress_blocks_alone_can_overflow_the_printer_buffer():
    """The hole in the upstream algorithm, pinned so the fix cannot regress.

    compress_blocks stops shrinking its grouping at one buffer and returns it
    oversized rather than failing. prepare() is what closes this; see below.
    """
    import random

    rng = random.Random(1)
    cols = 400
    noise = bytes(rng.getrandbits(8) for _ in range(P.BPL * cols))
    blocks = P.compress_blocks(P.build_buffers(noise, cols, density=4))
    assert any(len(b) > P.BUF_SIZE for b in blocks)


# --------------------------------------------------------------------- prepare
def _noisy_label(w: int = 320, h: int = 240, seed: int = 1) -> Image.Image:
    """A worst case for the compressor: what a dithered photo looks like."""
    import random

    rng = random.Random(seed)
    img = Image.new("1", (w, h), 1)
    px = img.load()
    for y in range(h):
        for x in range(w):
            if rng.getrandbits(1):
                px[x, y] = 0
    return img


def test_prepare_keeps_every_block_within_the_printer_buffer():
    bufs, blocks = P.prepare(_noisy_label(), density=4)
    assert blocks
    assert all(len(b) <= P.BUF_SIZE for b in blocks)
    assert all(len(b) == P.BUF_SIZE for b in bufs)


def test_prepare_covers_every_printable_column_exactly_once():
    """Shrinking the chunk size must not drop or duplicate feed lines."""
    bufs, _ = P.prepare(_noisy_label(h=240), density=4)
    total = sum(struct.unpack("<H", b[4:6])[0] for b in bufs)
    assert total == 240 - 2 * P.MARGIN


def test_prepare_marks_exactly_one_first_and_one_last_buffer():
    bufs, _ = P.prepare(_noisy_label(h=240), density=4)
    assert sum(1 for b in bufs if b[2] & 0x02) == 1
    assert sum(1 for b in bufs if b[2] & 0x04) == 1
    assert bufs[0][2] & 0x02
    assert bufs[-1][2] & 0x04


def test_prepare_leaves_an_ordinary_label_on_the_fast_path():
    """A text-and-barcode label compresses easily; do not split it needlessly."""
    from synergyscan.printer.render import render
    from synergyscan.printer.spec import Barcode, LabelSpec

    img = render(LabelSpec(lines=["Pasta", "2026-09"],
                           barcode=Barcode(value="SKU-0042")), 40, 30)
    bufs, blocks = P.prepare(img, density=4)
    assert struct.unpack("<H", bufs[0][4:6])[0] == P.MAX_BUF_DATA // P.BPL
    assert all(len(b) <= P.BUF_SIZE for b in blocks)


def test_compress_blocks_groups_up_to_four_buffers():
    bufs = [P.build_buffer(bytes(48), 1, i == 0, i == 3, 4) for i in range(4)]
    blocks = P.compress_blocks(bufs)
    # Mostly-empty buffers compress hard, so all four fit one block.
    assert len(blocks) == 1
    assert lzma.decompress(unpatch(blocks[0]),
                           format=lzma.FORMAT_ALONE) == b"".join(bufs)


# --------------------------------------------------------------------- speed
@pytest.mark.parametrize("avg,speed", [
    (4000, 10), (2900, 15), (2600, 20), (2100, 25),
    (1600, 40), (1200, 45), (600, 55), (100, 60),
])
def test_calc_speed_table(avg, speed):
    assert P.calc_speed(avg) == speed


# -------------------------------------------------------------------- status
def test_decode_status_reads_every_flag():
    r = bytes([0x00, 0x7F, 0x0C, 0x48, 0x01, 0x2A, 0x01])
    st = P.decode_status(r)
    assert st is not None
    for flag in ("buf_full", "label_rw_error", "label_end", "label_mode_error",
                 "ribbon_rw_error", "ribbon_end", "low_battery", "device_busy",
                 "head_temp_high", "cover_open", "printing",
                 "label_not_installed"):
        assert st[flag] is True, flag
    assert st["print_count"] == 0x012A


def test_decode_status_clear_when_all_bits_low():
    st = P.decode_status(bytes(7))
    assert st is not None
    assert not P.status_errors(st)
    assert st["print_count"] == 0


def test_decode_status_rejects_a_short_response():
    assert P.decode_status(b"\x00\x00") is None
    assert P.decode_status(None) is None


def test_status_errors_are_ordered_most_actionable_first():
    st = P.decode_status(bytes([0, 0x04, 0, 0x08, 0x01, 0, 0]))
    assert st is not None
    # label_not_installed outranks label_end and cover_open: it is the thing
    # the operator should deal with first.
    assert P.status_errors(st)[0] == "label_not_installed"


def test_decode_material_reads_the_loaded_roll():
    r = bytearray(64)
    r[18], r[19], r[20], r[21] = 2, 40, 30, 3
    r[40:46] = b"SN1234"
    mat = P.decode_material(bytes(r))
    assert mat == {"width": 40, "height": 30, "gap": 3, "type": 2,
                   "device_sn": "SN1234"}


def test_decode_material_rejects_a_short_response():
    assert P.decode_material(bytes(10)) is None
