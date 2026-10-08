# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

SynergyScan is a small-business inventory app: USB barcode scanner in, labels out on a Katasymbol/Supvan T50M Pro printer. Python 3.12, FastAPI + SQLite, plain HTML/JS frontend (no build step). It is installed on non-technical users' PCs and self-updates, so changes are judged by "can this brick an installed machine?". `README.md` is thorough; read it for the full design rationale.

## Commands

```bash
uv sync                                  # or: python -m venv .venv && pip install -e ".[dev]"
uv run python -m synergyscan             # http://127.0.0.1:8000 (source checkout never self-updates; version 0.0.0-0)
python run_local.py [port] [--lan] [--no-browser]   # dev launcher using ./.venv and ./data
uv run pytest                            # whole suite, no hardware needed
uv run pytest tests/test_protocol.py -v  # one file; add -k name for one test
uv run ruff check .                      # line length 100
python -m synergyscan.printer --preview out.png --text "Pasta" --barcode SKU-0042   # no hardware
python -m synergyscan.printer --probe    # needs the printer
python tools/release.py --notes "..."    # build a release zip (see README "Cutting a release")
```

Tests marked `hardware` need a physical printer. `tests/conftest.py` points `SYNERGYSCAN_DATA` at a tmp dir for every test; never bypass that, the dev machine may hold a live database.

## Architecture

**Installed layout vs. repo.** On a customer PC, `C:\SynergyScan` holds `launcher.py` + `current.txt` (from `bootstrap/`, outside any version), immutable `versions\<v>\` each with its own venv, and `data\` (SQLite db, config.json, backups, logs). Updates never touch `data\`. The repo is split by lifecycle: `src/synergyscan/` ships in every release, `bootstrap/` is installed once and must stay dumb and stable, `tools/` and `tests/` never ship, `channels/*.json` is read by installed machines to decide what to run.

**Self-update (`src/synergyscan/selfupdate/`).** download, verify sha256, unpack, `uv sync --frozen`, out-of-process self-test, migrate, then atomically write `current.txt` and `exit(75)` so the launcher restarts into the new version. The pointer moves last, so any failure leaves the old release working. Rules:
- This package uses the **standard library only**. It is the one code that cannot be fixed by shipping an update; do not add third-party imports there.
- DB is backed up (SQLite online backup API) before migrating; if the backup fails, the migration does not run.
- Updates only move forward; downgrades are `rollback.bat`.
- Publishing a GitHub release ships nothing; editing `channels/stable.json` is the rollout. Keep these separate. Versions are `YYYY.MM.DD-N`, compared numerically.

**Database (`db.py`, `migrations/`).** Stock is a ledger: no `quantity` column, every change is a row in `stock_movements`, on-hand is `SUM(delta)`. Never UPDATE or DELETE movements; correct by appending the opposite row. Counts are adjustments, not overwrites. Migrations are forward-only `NNN_lower_snake.sql` files in `migrations/versions/`, contiguous numbering enforced (next is `009_`), each applied in one transaction. Items belong to areas, categories (with SKU prefix) and locations; archiving cascades (migration 008).

**Printer stack (`src/synergyscan/printer/`).** Strict layering: `protocol.py` (wire format, no I/O) -> `render.py` (`LabelSpec` -> 1-bit bitmap, no I/O) -> `transport.py` (USB HID, the only hardware contact) -> `service.py` (job queue, the only thing the rest of the app uses). The app only builds a `LabelSpec`; the on-screen preview and the print come from the same renderer. `protocol.py` is ported from heeen/supvan-cups; read the warning at the top before touching it. Odd-looking constants are correct and the firmware fails silently on a bad checksum, so changes need real hardware. Its tests assert exact bytes and barcode bar widths on purpose; text rendering is tested by properties only (fonts differ per machine).

**App (`app.py`, `web/`).** Single FastAPI module serving `/api/*` plus the static UI (`web/index.html`, `web/static/app.js|css`). `/api/doctor` produces the support diagnostics report. `lifecycle.py` handles restart/update orchestration, `config.py`/`paths.py` resolve `data\config.json` and `SYNERGYSCAN_DATA`. Binds localhost unless `allow_lan` is set; there is no auth.

**UI branding.** UI follows the Sinergy Flow brand; `Docs/brand.md` is the working reference (the PDF in `Docs/` wins on conflict). `Docs/requirements.md` lists the questions the system must answer.

## Notes

- `data/` and `.venv` are untracked; `.claude/worktrees/` holds local worktrees.
- Commit messages in this repo are terse version stamps (e.g. `2026.10.06-2`) or short descriptions.
