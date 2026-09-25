"""Wire protocol for the Katasymbol / Supvan T50M Pro.

Reverse-engineered from the Katasymbol Windows app; ported from
https://github.com/heeen/supvan-cups (MIT). The printer exposes no Windows
print driver - it is a raw USB HID device (VID 0x1820).

    ============================ READ THIS ============================
    The constants and the checksum in this module were derived by
    observing real USB traffic. They are not derivable from first
    principles and several of them look wrong. They are not wrong.

      * The checksum samples one byte every 256 (buf[i * 256 - 1]).
      * MARGIN = 8 dots is trimmed from BOTH ends of the feed direction.
      * The LZMA1 filter parameters are exact, and the uncompressed size
        is patched into the FORMAT_ALONE header at offset 5 because the
        firmware reads it from there.
      * calc_speed's thresholds are a lookup table, not a formula.

    Do not "clean this up" or "simplify" it. The firmware validates the
    checksum and silently refuses, or prints garbage, when the stream is
    subtly wrong - and there is no error message to debug from. Changes
    here must be verified against physical hardware.
    ===================================================================

Pure functions only: no USB, no I/O. That is what lets tests/test_protocol.py
cover this module with no printer attached.
"""

from __future__ import annotations

import lzma
import struct

# ----------------------------------------------------------------- geometry
DOTS_PER_MM = 8            # 203 dpi
HEAD_DOTS = 384            # 48 mm printhead
BPL = HEAD_DOTS // 8       # 48 bytes per printhead line
MARGIN = 8                 # dots skipped at each end of the label (~1 mm)

# ------------------------------------------------------------------ buffers
BUF_SIZE = 4096
BUF_HDR = 14
MAX_BUF_DATA = 4074
BUFS_PER_BLOCK = 4
MULTI_BLOCK_SPEED = 20

# ----------------------------------------------------------------- commands
CMD_BUF_FULL = 0x10
CMD_INQUIRY_STA = 0x11
CMD_CHECK_DEVICE = 0x12
CMD_START_PRINT = 0x13
CMD_STOP_PRINT = 0x14
CMD_RETURN_MAT = 0x30
CMD_NEXT_ZIPPEDBULK = 0x5C

# --------------------------------------------------------------- USB/report
VID = 0x1820
REPORT = 64                # HID report size, bytes

# Status flags that mean a human has to do something, in the order we report
# them. printer.errors maps these to operator-facing sentences.
ERROR_FLAGS = (
    "label_not_installed",
    "label_end",
    "cover_open",
    "ribbon_end",
    "head_temp_high",
    "label_mode_error",
    "label_rw_error",
    "ribbon_rw_error",
)


def frame(cmd: int, p1: int = 0, p2: int | None = None) -> bytes:
    """Build an 8- or 10-byte command frame."""
    f = bytes([0xC0, 0x40, p1 >> 8 & 0xFF, p1 & 0xFF, cmd, 0x00, 0x08, 0x00])
    if p2 is not None:
        f += bytes([p2 >> 8 & 0xFF, p2 & 0xFF])
    return f


def decode_status(r: bytes | None) -> dict | None:
    """Decode a CMD_INQUIRY_STA response."""
    if not r or len(r) < 7:
        return None
    b0, b1, b2, b3 = r[1], r[2], r[3], r[4]
    return {
        "buf_full": bool(b0 & 0x01),
        "label_rw_error": bool(b0 & 0x02),
        "label_end": bool(b0 & 0x04),
        "label_mode_error": bool(b0 & 0x08),
        "ribbon_rw_error": bool(b0 & 0x10),
        "ribbon_end": bool(b0 & 0x20),
        "low_battery": bool(b0 & 0x40),
        "device_busy": bool(b1 & 0x04),
        "head_temp_high": bool(b1 & 0x08),
        "cover_open": bool(b2 & 0x08),
        "printing": bool(b2 & 0x40),
        "label_not_installed": bool(b3 & 0x01),
        "print_count": r[5] | r[6] << 8,
    }


def decode_material(r: bytes | None) -> dict | None:
    """Decode a CMD_RETURN_MAT response: which label roll is loaded."""
    if not r or len(r) < 22:
        return None
    sn_tail = r[40:].split(b"\0")[0].decode("ascii", "replace") if len(r) > 40 else ""
    return {
        "width": r[19],     # mm, across the tape
        "height": r[20],    # mm, along the feed direction
        "gap": r[21],
        "type": r[18],
        "device_sn": sn_tail,
    }


def status_errors(st: dict) -> list[str]:
    """Error flag names currently asserted, in reporting order."""
    return [e for e in ERROR_FLAGS if st.get(e)]


def pack(bw) -> tuple[bytes, int]:
    """1-bit label image -> column-major, LSB-first bytes centred on the head.

    Returns (data, columns). "Columns" is the image height: the printer counts
    printhead lines along the feed direction.
    """
    w, h = bw.size
    crop = max(0, w - HEAD_DOTS) // 2        # 50 mm labels: trim ~1 mm per side
    x_off = max(0, HEAD_DOTS - w) // 2
    out = bytearray(BPL * h)
    px = bw.load()
    for y in range(h):
        base = y * BPL
        for x in range(crop, min(w, crop + HEAD_DOTS)):
            if px[x, y] == 0:                # 0 = black = burn this dot
                dot = x - crop + x_off
                out[base + dot // 8] |= 1 << (dot % 8)
    return bytes(out), h


def build_buffer(chunk: bytes, cols: int, first: bool, last: bool, density: int) -> bytes:
    """One 4096-byte printer buffer, checksummed."""
    buf = bytearray(BUF_SIZE)
    b0 = (0x02 if first else 0) | (0x04 if last else 0) | (0x08 if last else 0)
    b1 = ((density & 0x0F) << 2) | (1 << 6)          # nodu=density, mat=1
    buf[2], buf[3] = b0, b1
    buf[4:6] = struct.pack("<H", cols)
    buf[6] = BPL
    buf[8:10] = struct.pack("<H", MARGIN)
    buf[10:12] = struct.pack("<H", MARGIN)
    buf[12] = min(density, 15)
    buf[BUF_HDR:BUF_HDR + len(chunk)] = chunk
    data_end = cols * BPL + BUF_HDR
    # Header bytes, plus one sampled byte per 256 bytes of payload. Yes, really.
    chk = sum(buf[2:14]) + sum(buf[i * 256 - 1] for i in range(1, data_end // 256 + 1))
    buf[0:2] = struct.pack("<H", chk & 0xFFFF)
    return bytes(buf)


def build_buffers(data: bytes, total_cols: int, density: int,
                  max_cols: int | None = None) -> list[bytes]:
    """Split packed image data into printer buffers, dropping the margins.

    `max_cols` caps the printhead lines per buffer. It defaults to as many as
    the payload area holds; `prepare` lowers it when a buffer's payload is too
    incompressible to fit a block. Fewer columns per buffer is not a special
    case - the last buffer of every multi-buffer label is already short.
    """
    max_cols = max_cols or MAX_BUF_DATA // BPL
    cols = total_cols - 2 * MARGIN
    if cols <= 0:
        raise ValueError(
            f"label is only {total_cols} dots along the feed direction; "
            f"needs more than {2 * MARGIN}"
        )
    chunks: list[tuple[int, int]] = []
    c = 0
    while c < cols:
        n = min(max_cols, cols - c)
        chunks.append((c, n))
        c += n
    bufs = []
    for i, (start, n) in enumerate(chunks):
        s = (MARGIN + start) * BPL
        bufs.append(
            build_buffer(data[s:s + n * BPL], n, i == 0, i == len(chunks) - 1, density)
        )
    return bufs


def compress(data: bytes) -> bytes:
    """LZMA1 with the exact filter parameters the firmware expects.

    The FORMAT_ALONE header is 1 properties byte, 4 bytes of dict size, then 8
    bytes of uncompressed size. Python writes 0xFF..FF there (unknown, because
    it streams) and emits an end-of-stream marker; the firmware instead reads
    the real size out of the header, so we patch it in.

    Consequence worth knowing before you "fix" this: the result is no longer
    decompressible by liblzma. A known size plus a trailing end marker is a
    combination the alone-decoder rejects as corrupt, so lzma.decompress on
    this output raises. The printer accepts it. tests/test_protocol.py checks
    the payload by reversing the patch first.
    """
    filters = [{
        "id": lzma.FILTER_LZMA1, "dict_size": 8192, "lc": 3, "lp": 0, "pb": 2,
        "mode": lzma.MODE_NORMAL, "nice_len": 128, "mf": lzma.MF_BT4,
    }]
    z = bytearray(lzma.compress(data, format=lzma.FORMAT_ALONE, filters=filters))
    struct.pack_into("<Q", z, 5, len(data))      # firmware reads the exact size here
    return bytes(z)


def compress_blocks(bufs: list[bytes]) -> list[bytes]:
    """Group buffers into blocks that each compress to <= BUF_SIZE."""
    blocks: list[bytes] = []
    i = 0
    while i < len(bufs):
        take = min(BUFS_PER_BLOCK, len(bufs) - i)
        while True:
            z = compress(b"".join(bufs[i:i + take]))
            if len(z) <= BUF_SIZE or take == 1:
                break
            take -= 1
        blocks.append(z)
        i += take
    return blocks


def prepare(bw, density: int) -> tuple[list[bytes], list[bytes]]:
    """Everything between a bitmap and the wire: pack, buffer, compress.

    Exists because of a hole in the upstream implementation. compress_blocks
    shrinks its grouping until a block fits BUF_SIZE, but gives up at one buffer
    and returns it oversized anyway. A single buffer whose payload barely
    compresses - which is exactly what a dithered photograph produces - then
    overflows the printer's buffer, and the failure is silent: garbage output or
    a refused print, with no status flag to explain it.

    The fix is to put fewer printhead lines in each buffer, halving until every
    block fits. That always converges, because a buffer's compressed size is
    driven by its payload (the rest of the 4096 bytes is zero padding), and one
    column is 48 bytes.

    Returns (buffers, blocks).
    """
    data, cols = pack(bw)
    max_cols = MAX_BUF_DATA // BPL
    while True:
        bufs = build_buffers(data, cols, density, max_cols=max_cols)
        blocks = compress_blocks(bufs)
        if all(len(b) <= BUF_SIZE for b in blocks):
            return bufs, blocks
        if max_cols <= 1:
            # Unreachable in practice; better an explicit error than a corrupt
            # stream if it ever is reached.
            raise ValueError(
                "this label cannot be compressed into the printer's buffer; "
                "try a simpler image or lower density"
            )
        max_cols = max(1, max_cols // 2)


def calc_speed(avg: int) -> int:
    """Feed speed from average compressed block size. Lookup table, not a curve."""
    for limit, speed in [(3000, 10), (2800, 15), (2500, 20), (2000, 25),
                         (1500, 40), (1000, 45), (500, 55)]:
        if avg > limit:
            return speed
    return 60
