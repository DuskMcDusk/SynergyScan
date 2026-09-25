# Release channels

Each file here is what installed machines read to decide whether to update.
A machine follows exactly one channel, set in its `data\config.json`.

```json
{
  "version": "2026.10.02-1",
  "url": "https://github.com/DuskMcDusk/SynergyScan/releases/download/v2026.10.02-1/synergyscan-2026.10.02-1.zip",
  "sha256": "3f9a…",
  "notes": "Shorter barcodes on 30 mm rolls",
  "min_version": "2026.09.25-1"
}
```

| field | required | meaning |
|---|---|---|
| `version` | yes | `YYYY.MM.DD-N`. Compared numerically, never as a string. |
| `url` | yes | Must be `https`. The release archive. |
| `sha256` | yes | Integrity check on the download. Not a signature. |
| `notes` | no | Shown in release listings; not used by the updater. |
| `min_version` | no | Refuse to jump here from anything older. Set it when a migration needs an intermediate release to run first. |

## Why a file in the repo instead of the GitHub API

- No rate limit to reason about at a customer site.
- A schema we own, so a change at GitHub cannot break the update path.
- **Publishing and rolling out become separate acts.** Creating a GitHub release
  ships nothing. Editing the channel file is what ships it.

That separation is the point. Build a release, install it somewhere real, and
only then push the channel change. If the build turns out bad before you push,
nobody ever saw it.

## Canary

`beta.json` exists so one machine can lead. Point it at the new release, leave
`stable.json` alone, and set that machine's `config.json`:

```json
{ "channel_url": "https://raw.githubusercontent.com/DuskMcDusk/SynergyScan/main/channels/beta.json" }
```

Let it run a few days of real work, then copy the same values into
`stable.json`.

## Rolling back a bad release

Point the channel file back at the previous version. Machines already on the
bad release will **not** downgrade on their own — the updater only ever moves
forward, deliberately, because an automatic downgrade across a database
migration loses data.

To recover a machine that already took it, run `rollback.bat` on it. That is
why every release stays on disk.
