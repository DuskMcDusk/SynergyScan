# SynergyScan

Inventory tracking for a small business. A USB barcode scanner goes in, labels
come out of a Katasymbol / Supvan **T50M Pro**, and the stock figures look after
themselves.

Built so that someone with no technical background can install it once and then
never think about it again. Updates install themselves and roll back on their
own if a release is bad.

- **Operators** — start at [Installing](#installing) and [Everyday use](#everyday-use).
- **Whoever supports it** — [When something goes wrong](#when-something-goes-wrong).
- **Developers** — [How it is put together](#how-it-is-put-together) onwards.

---

## Installing

Do this once, on the PC that has the scanner and the label printer plugged in.
It takes about five minutes, most of which is waiting. You do **not** need
Administrator rights, and you do not need to install Python or anything else
first — the installer fetches everything it needs.

1. Go to the [Releases page](https://github.com/DuskMcDusk/SynergyScan/releases) and
   download **`install.bat`** and **`install.ps1`** into the same folder
   (your Downloads folder is fine).
2. Double-click **`install.bat`**.
3. Watch for green `OK` lines. It finishes with `Installed.` and opens a browser.

That's it. There is now a **SynergyScan** icon on the desktop, and the app starts
by itself every time you log on.

> **If Windows shows a blue "Windows protected your PC" box**, click **More
> info**, then **Run anyway**. This happens because the installer is not signed
> with a paid certificate.

Everything lives in one folder, `C:\SynergyScan`. The part that matters is
`C:\SynergyScan\data` — that is your inventory database. **Back that folder up.**
Everything else can be downloaded again; that cannot.

<details>
<summary>Installing somewhere else, or on a test machine</summary>

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -InstallRoot D:\SynergyScan
powershell -ExecutionPolicy Bypass -File install.ps1 -Channel beta   # canary machine
powershell -ExecutionPolicy Bypass -File install.ps1 -NoAutostart -NoStart
```
</details>

### Plugging in the hardware

**The scanner** needs no setup. Almost every USB barcode scanner behaves like a
keyboard: it types the code and presses Enter. Plug it in and scan into the big
box at the top of the screen.

**The printer** needs no driver, which is unusual and good — there is nothing to
install and nothing to configure in Windows printer settings. Just USB and power.

> **Close the Katasymbol app if it is running.** Only one program can talk to the
> printer at a time, and the vendor's app will hold onto it.

The app reads the label size straight off the roll, so you can change to a
different size and it adapts on its own.

---

## Everyday use

Open SynergyScan from the desktop icon, or go to <http://localhost:8000>.

**The scan box is always ready.** You don't have to click into it first — if you
scan while looking at something else on the page, the app pulls the scan back
into the box.

| To do this | Do this |
|---|---|
| Look something up | Scan it, or type the SKU and press Enter |
| Book stock in | Scan, set the quantity, click **Receive in** |
| Book stock out | Scan, set the quantity, click **Issue out** |
| Correct a count | Scan, enter the counted number, click **Set count to…** |
| Print a label | Scan, open **Print a label**, click **Preview** then **Print** |
| Add a new product | Scan a code the system doesn't know, fill in the name, click **Add item** |
| See what needs reordering | Tick **Low stock only** |

The printer indicator at the top right is green when the printer is ready and red
when it needs something, and it says what.

### Two things worth knowing

**Counts are never overwritten, only adjusted.** When you set a count to 92 and
the system thought there were 100, it records an adjustment of −8 rather than
replacing the number. The full history stays, so you can always see what changed
and when. Mistakes are fixed by booking the opposite movement, not by deleting.

**The preview is exactly what prints.** It is produced by the same code that
drives the printer, at the same resolution, so what you see is what comes out of
the roll.

### Using a phone or tablet for stocktaking

By default the app is only reachable from the PC it runs on. To open it up to
phones and tablets on the same network, edit `C:\SynergyScan\data\config.json`:

```json
{ "allow_lan": true }
```

Restart SynergyScan, then browse to `http://<the PC's name>:8000` from the phone.
Labels still print on the PC with the printer attached.

> Only do this on a network you trust. There is no login — anyone who can reach
> the address can change stock figures.

---

## When something goes wrong

### The app tells you what to do

Printer problems appear as plain instructions, not error codes: *"The printer
cover is open. Close it until it clicks."* Do the thing it says, then try again.
These are the ones you'll see:

| Message | What to do |
|---|---|
| No label roll is loaded | Open the cover, put a roll in, close it |
| The label roll has run out | Fit a new roll |
| The printer cover is open | Close it until it clicks |
| The ribbon has run out | Fit a new ribbon cartridge |
| The print head is too hot | Wait about a minute |
| No label printer found | Check the USB cable and that it's switched on. Close the Katasymbol app if it's open |

### If the app won't start or is behaving oddly

**First, try turning it off and on.** Close the window, then use the desktop
icon. The app also restarts itself if it crashes.

**Then, send a diagnostics report.** Open
<http://localhost:8000/api/doctor>, save the page (Ctrl+S) and email it. It
contains the version, the printer's status, the database size and the recent log
— which is almost always enough to answer the question without a phone call.

If the app won't open at all, run `C:\SynergyScan\SynergyScan-console.bat`
instead of the desktop icon. It shows a black window with the error in it.

### Restarting and updating from the app

At the bottom of the left-hand menu:

- **Restart** stops the app and starts it again in a few seconds. Handy when the
  printer was plugged in after the app started. A restart also installs an
  update if one is waiting.
- **Check for updates** asks whether a newer version exists. The app also checks
  quietly when it opens and every few hours; if there is one, a yellow bar at
  the top offers **Update now**. The update downloads, is tested, and the app
  restarts into it. If anything fails the current version stays installed and
  nothing changes. Your data is never touched.

Both buttons only work in an installed copy, not when running from source.

### Going back to the previous version

If an update causes a problem, double-click **`C:\SynergyScan\rollback.bat`**.
It lists what's installed, asks which to go back to, and restarts the app. Your
data is not touched.

You can do this safely — every version stays on the PC, so going back is instant.

> If the newer version changed the database structure, the older one may not be
> able to read it. `rollback.bat` will say so. Pre-update backups are in
> `C:\SynergyScan\data\backups\`, and restoring one is a support job.

### Removing it

```powershell
powershell -ExecutionPolicy Bypass -File C:\SynergyScan\uninstall.ps1
```

Your data is kept unless you add `-DeleteData`.

---

## How it is put together

Everything is Python. One language, one runtime, no build step, and the source is
right there on the machine when something needs diagnosing at 8am.

```
┌──────────────────── C:\SynergyScan ────────────────────┐
│                                                        │
│  launcher.py       supervisor. Outside versions\, so   │
│  current.txt       an update can never break it.       │
│                    One line: which release to run.     │
│                                                        │
│  versions\                                             │
│    2026.09.25-1\   immutable release + its own .venv   │
│    2026.10.02-1\                                       │
│                                                        │
│  data\             THE PART THAT MATTERS               │
│    inventory.db      SQLite, append-only stock ledger  │
│    config.json       per-site settings                 │
│    backups\          taken before every migration      │
│    logs\                                               │
└────────────────────────────────────────────────────────┘
        ▲                          ▲
   USB scanner (HID keyboard)   USB printer (HID, no driver)
```

Two rules shape the whole layout:

1. **Releases are immutable and disposable.** Nothing writable lives inside
   `versions\<v>\`, so a release directory can be deleted and rebuilt with no
   cleanup, and the running version is never half-modified.
2. **`data\` is never touched by an update.** Which is what makes rollback safe
   and updates boring.

### The repository

| Path | Lifecycle | Contents |
|---|---|---|
| `src/synergyscan/` | ships in every release | the application, including the updater |
| `bootstrap/` | copied to the machine once, at install | launcher, install and rollback scripts |
| `tools/` | never ships | release building |
| `channels/` | read by installed machines | which release to run |
| `tests/` | never ships | 219 tests, none needing hardware |

The split is by **lifecycle, not by "is it a script"**. `bootstrap/` holds only
things that must survive an update; `tools/` only things that run on a developer's
machine.

**The updater is in `src/`, not in `bootstrap/`, on purpose.** It is the only code
whose bugs cannot be fixed by shipping an update — break it and the fix is a site
visit. That makes it the highest-stakes code in the project, so it lives as a
tested, versioned module rather than a loose script. What's in `bootstrap/` is
deliberately dumb enough never to need changing.

### The printer stack

```
protocol.py   pure wire format — bit packing, LZMA, checksums. No I/O.
render.py     LabelSpec -> 1-bit bitmap. No I/O.
transport.py  USB HID. The only module that touches hardware.
service.py    the job queue. The only thing the rest of the app talks to.
```

The app never builds a bitmap and never learns anything about HID or LZMA. It
describes what should be on a label (`LabelSpec`) and this layer decides how to
draw it and how to get it onto the roll. That's what makes the on-screen preview
pixel-identical to the print, and it means the transport could be replaced later
without the app noticing.

`protocol.py` is a port of the USB transport from
[heeen/supvan-cups](https://github.com/heeen/supvan-cups) (MIT), reverse-engineered
from the vendor app. **Read the warning at the top of that file before changing
it.** Several of its constants look wrong and are not; the firmware validates a
checksum and fails silently when the byte stream is subtly off, with no error to
debug from. Changes there need real hardware to verify.

### Stock is a ledger

There is no `quantity` column. Every change is a row in `stock_movements`, and
the figure on hand is derived:

```sql
SELECT COALESCE(SUM(delta), 0) FROM stock_movements WHERE item_id = ?
```

For very little extra work this gives a complete audit trail, makes stocktake
reconciliation a query rather than a correction, and removes the lost-update race
you get when two people scan the same item at once. Nothing ever updates or
deletes a movement — a mistake is corrected by appending the opposite one.

---

## How updates work

```
launcher          reads current.txt, starts that release
app startup       asks channels/stable.json whether anything is newer
     ↓ yes
download → verify sha256 → unpack → build venv → self-test → migrate
     ↓ all passed
write current.txt → exit(75)
     ↓
launcher          sees 75, starts the new release
```

**The pointer moves last.** Everything before it is reversible by deleting a
directory nothing points at, and the pointer move itself is one atomic file
replace. So any failure — no network, a wheel that won't build, a broken import,
a bad migration — ends with the previous release still installed, still pointed
at, and still working. That is the single property the whole design exists for,
because nobody on site can repair a half-updated app.

Some details that matter:

- **The database is backed up before any migration**, using SQLite's online
  backup API rather than a file copy (with WAL on, a copy can miss committed
  transactions). If a migration fails the backup is restored and the update is
  abandoned. If the backup *can't* be taken, the migration doesn't run at all —
  refusing an update is always recoverable; migrating with no way back is not.
- **The self-test runs out-of-process, in the new release's interpreter.** An
  import inside the old process would prove nothing about whether the new venv
  resolves or its native extensions load. It checks imports, hidapi, rendering a
  real barcode label, the wire protocol, migrations against a scratch database,
  and that `data\` is writable. It does not require a printer.
- **`uv sync --frozen`** installs exactly what `uv.lock` pins. Without `--frozen`
  a dependency could resolve differently on the customer's machine than in
  testing, and the release you tested isn't the release that runs.
- **The launcher rolls back by itself** if a release crashes within 30 seconds,
  three times running.
- **Updates only ever go forwards.** Pointing the channel back at an older
  release does not downgrade machines that already took the newer one, because an
  automatic downgrade across a migration loses data. Use `rollback.bat` for that.

### The update check is synchronous at startup

On the days a release lands, the user waits 20–30 seconds behind an "Updating…"
message on a PC that boots once a morning. That's the right trade for update code
simple enough to be obviously correct. If the delay ever becomes the complaint,
stage the download in the background and apply it at next launch — same
`selfupdate` module, called from a different place.

---

## Developing

```bash
git clone https://github.com/DuskMcDusk/SynergyScan
cd SynergyScan
uv sync                      # or: python -m venv .venv && pip install -e ".[dev]"
uv run python -m synergyscan  # http://127.0.0.1:8000
uv run pytest
```

A source checkout reports its version as `0.0.0-0` and **never self-updates** —
so you can't accidentally overwrite your working tree with a release.

### The printer CLI

The debugging tool when labels aren't coming out. `--preview` needs no hardware.

```bash
python -m synergyscan.printer --list      # HID interfaces the printer exposes
python -m synergyscan.printer --probe     # status, faults, loaded roll size
python -m synergyscan.printer --preview out.png --text "Pasta" --barcode SKU-0042
python -m synergyscan.printer --text "Pasta" --barcode SKU-0042 --copies 2
python -m synergyscan.printer --text "Pasta" --barcode SKU-0042 --symbology qr
```

A healthy `--probe` looks like this:

```json
{
  "reachable": true,
  "status": { "cover_open": false, "label_end": false, "print_count": 0 },
  "material": { "width": 40, "height": 30, "gap": 3, "device_sn": "T0205C260411G911" }
}
```

### Tests

```bash
uv run pytest                    # 219 tests, ~7s, no hardware needed
uv run pytest tests/test_protocol.py -v
```

Nothing in the suite needs a printer, and the `no_printer` fixture makes the
printer look absent even when one is plugged in — so tests behave the same on a
developer's desk as in CI.

Two things get exact assertions rather than property checks:

- **The wire protocol**, because it can't be re-derived. If someone "tidies up"
  the checksum or the bit packing, a test has to fail immediately rather than a
  customer's printer quietly producing blank labels.
- **Barcode bar widths**, because whole-dot bars are the difference between a
  label that scans and one that doesn't.

Text rendering is checked by properties — that it fits, that it's pure black and
white, that nothing lands in the margins — never pixel-exactly, because installed
fonts differ between this machine, a customer PC and CI.

### Cutting a release

```bash
python tools/release.py --notes "Shorter barcodes on 30 mm rolls"
gh release create v2026.10.02-1 tools/out/synergyscan-2026.10.02-1.zip
# install it somewhere real and use it
git add channels/stable.json && git commit -m "Ship 2026.10.02-1" && git push
```

**Publishing and rolling out are separate acts, deliberately.** Creating the
GitHub release ships nothing. Editing `channels/stable.json` is what ships it.
That's what makes a canary possible and what lets you abandon a bad build before
anyone gets it. Don't collapse them into one step.

See [`channels/README.md`](channels/README.md) for the channel format and how to
run a canary machine.

### Changing the database

Add `src/synergyscan/migrations/versions/NNN_description.sql`. Numbered, forward
only, applied in one transaction each. Don't put `BEGIN`/`COMMIT` in the file —
the runner manages the transaction, and `executescript` would silently discard a
Python-level one.

If a release needs an intermediate migration to have run first, set
`min_version` in the channel file and machines older than that will wait.

---

## Reference

### `data\config.json`

Written on first run. Unknown keys are preserved, so a config written by a newer
version survives a rollback.

| Key | Default | Meaning |
|---|---|---|
| `site_name` | `SynergyScan` | Shown in the UI |
| `host` / `port` | `127.0.0.1` / `8000` | Where the app listens |
| `allow_lan` | `false` | Bind `0.0.0.0` so phones and tablets can reach it |
| `label_width_mm` / `label_height_mm` | `40` / `30` | Used only if the printer reports no roll |
| `density` | `4` | Print darkness, 0–15 |
| `channel_url` | `channels/stable.json` | Point at `beta.json` to make this a canary |
| `auto_update` | `true` | Turn off to pin a machine |
| `update_timeout_s` | `120` | Download timeout |

### HTTP API

Browsable at <http://localhost:8000/api/docs>.

| Endpoint | Purpose |
|---|---|
| `POST /api/scan` | Resolve a scanned code. A miss is a normal answer, not an error |
| `GET /api/items` | Stock list. `?search=` and `?low=true` |
| `POST /api/items` · `PATCH /api/items/{id}` | Create and edit |
| `POST /api/movements` | Append a stock movement |
| `POST /api/stocktake` | Reconcile to a counted figure |
| `POST /api/print` | Queue a label; returns a job id |
| `GET /api/print/{job_id}` | Poll for the result |
| `POST /api/print/preview` | PNG of exactly what would print. No printer needed |
| `GET /api/printer` | Printer status, with a plain-language message |
| `GET /api/doctor` · `GET /api/logs` | Diagnostics |

### Label spec

The contract between the app and the printing layer:

```json
{
  "lines": ["Pasta di semola", "SKU-0042"],
  "barcode": { "symbology": "code128", "value": "SKU-0042", "show_text": false },
  "density": 4,
  "copies": 1
}
```

`code128`, `code39`, `ean13` and `qr` are supported. A code too long to print
with scannable bars is **refused** rather than silently shrunk — a missing label
is a visible problem, an unreadable one is found weeks later at stocktake.
Roughly, a 40 mm roll fits an 8–9 character Code 128; longer codes should use QR.

---

## Known limits

- **No authentication.** Anyone who can reach the address can change stock. Fine
  on `127.0.0.1`; think before setting `allow_lan`.
- **The printer protocol is reverse-engineered.** A firmware update could break
  it. Record the model and firmware you validated against, and don't let the
  vendor app update the printer.
- **The `sha256` on updates is an integrity check, not a signature.** Over HTTPS
  from GitHub the realistic risk is a truncated download, not a substituted file.
  Real tamper resistance needs code signing.
- **One site, one PC.** The design assumes a single machine owns the database.
  Multiple sites would need a shared server and a rethink of the update model.

## Licence and credits

USB protocol ported from [heeen/supvan-cups](https://github.com/heeen/supvan-cups)
(MIT), reverse-engineered from the Katasymbol app.
