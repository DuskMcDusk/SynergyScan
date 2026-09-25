"""The label spec: the contract between the app and the printing layer.

The app never builds a bitmap and never learns anything about HID or LZMA. It
describes what should be on the label; this layer decides how to draw it and
how to get it onto the roll.

Keeping the boundary here (rather than "app renders a PNG, printer sends it")
has two payoffs: the on-screen preview is pixel-identical to the print because
it comes from the same renderer, and the transport can be replaced later
without the app noticing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Symbology = Literal["code128", "ean13", "code39", "qr"]


class Barcode(BaseModel):
    symbology: Symbology = "code128"
    value: str = Field(min_length=1, max_length=128)
    # Print the human-readable digits under the bars. Off by default: on a
    # 40x30 label the space is usually better spent on the item name.
    show_text: bool = False

    @field_validator("value")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class LabelSpec(BaseModel):
    """What goes on one label."""

    lines: list[str] = Field(default_factory=list, max_length=6)
    barcode: Barcode | None = None
    # 0-15, darker as it rises. 4 matches the vendor app's default.
    density: int = Field(default=4, ge=0, le=15)
    copies: int = Field(default=1, ge=1, le=50)
    rotate: Literal[0, 90, 180, 270] = 0

    @field_validator("lines")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return [ln.strip() for ln in v if ln.strip()]

    def is_empty(self) -> bool:
        return not self.lines and self.barcode is None


class LabelSize(BaseModel):
    """Physical label size in mm, as reported by the loaded roll."""

    width_mm: int = Field(ge=5, le=60)    # across the tape
    height_mm: int = Field(ge=5, le=300)  # along the feed direction


class PrintResult(BaseModel):
    ok: bool
    label_mm: tuple[int, int] | None = None
    copies: int = 0
    print_count: int | None = None   # printer's lifetime counter, for diagnostics
    error: str | None = None         # operator-facing sentence, not a flag name
    flag: str | None = None          # protocol flag name, for logs
    retryable: bool = False
