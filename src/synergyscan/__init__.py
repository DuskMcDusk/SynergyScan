"""SynergyScan - inventory tracking with barcode scanning and label printing.

    synergyscan.printer      labels: protocol, rendering, USB HID, queue
    synergyscan.selfupdate   versioned releases and the pointer flip
    synergyscan.db           SQLite; stock is an append-only ledger
    synergyscan.app          the local HTTP API and UI
    synergyscan.paths        where data lives - outside the release directories

The package version below tracks the source. What is actually deployed is the
release version in the RELEASE file; see selfupdate.version.current.
"""

__version__ = "0.1.0"
