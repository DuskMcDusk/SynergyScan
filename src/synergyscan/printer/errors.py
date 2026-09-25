"""Printer faults translated into sentences an operator can act on.

The printer reports eight distinct fault flags. Surfacing them as raw flag
names, or as a Python traceback, wastes the best diagnostic we have - the
person standing next to the printer can nearly always fix the problem
themselves if we tell them what it is.

Every message must name the physical thing to do. "label_end" is useless;
"The label roll has run out" is not.
"""

from __future__ import annotations


class PrinterError(RuntimeError):
    """A printer fault. `flag` is the protocol flag name, if there was one."""

    def __init__(self, message: str, flag: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.flag = flag
        self.retryable = retryable


class PrinterNotFound(PrinterError):
    pass


class PrinterBusy(PrinterError):
    pass


# flag -> (operator message, retryable once they have fixed it)
MESSAGES: dict[str, tuple[str, bool]] = {
    "label_not_installed": ("No label roll is loaded. Open the cover, insert a roll, and close it.", True),
    "label_end": ("The label roll has run out. Fit a new roll and try again.", True),
    "cover_open": ("The printer cover is open. Close it until it clicks.", True),
    "ribbon_end": ("The ribbon has run out. Fit a new ribbon cartridge.", True),
    "head_temp_high": ("The print head is too hot. Wait about a minute, then try again.", True),
    "label_mode_error": ("The loaded roll is not the type the printer expects. Check you are using the right labels.", True),
    "label_rw_error": ("The printer cannot read the label roll's tag. Reseat the roll; if it keeps happening the roll may be faulty.", True),
    "ribbon_rw_error": ("The printer cannot read the ribbon's tag. Reseat the ribbon cartridge.", True),
    "low_battery": ("The printer battery is low. Connect it to power.", True),
    "buf_full": ("The printer is still busy with the previous label.", True),
}

NOT_FOUND = (
    "No label printer found. Check the USB cable is connected and the printer "
    "is switched on. If the Katasymbol app is open, close it - only one program "
    "can use the printer at a time."
)


def message_for(flag: str) -> tuple[str, bool]:
    """Operator message and retryability for a protocol flag name."""
    return MESSAGES.get(flag, (f"The printer reported a fault ({flag}).", False))


def from_flags(flags: list[str]) -> PrinterError:
    """Build the error to raise for a set of asserted status flags.

    Flags arrive in priority order from protocol.status_errors, so the first
    one is the one worth telling the operator about.
    """
    if not flags:
        return PrinterError("The printer reported a fault with no details.")
    text, retryable = message_for(flags[0])
    if len(flags) > 1:
        text += " (Also reported: " + ", ".join(flags[1:]) + ".)"
    return PrinterError(text, flag=flags[0], retryable=retryable)
