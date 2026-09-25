"""Installing a release.

The ordering here is the safety property, so it is worth stating plainly:

    download -> verify hash -> unpack -> build venv -> self-test -> migrate
    -> *only then* move the pointer

Every step before the last one is reversible by deleting a directory nothing
points at. The pointer move is a single atomic file replace. So any failure -
network, a wheel that will not build, a broken import, a bad migration - ends
with the previous release still installed, still pointed at, and still working.

That is the one thing this whole design exists to guarantee, because nobody on
site can repair a half-updated app.

Two deliberate choices worth not undoing:

* The venv is built **after** the release is moved into versions/. A venv
  records absolute paths in pyvenv.cfg and in its console scripts, so building
  it somewhere and then moving it produces an environment that half works.
* An abandoned versions/<v>/ directory is left on disk for the next run to
  clean up rather than deleted in a `finally`. If cleanup is what failed, we
  want the evidence.

Standard library only. See the note in pyproject.toml.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import version
from .check import Release

log = logging.getLogger(__name__)

CHUNK = 1 << 16
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024     # a release is a few MB; this is a sanity bound
KEEP_VERSIONS = 3                         # current + two to roll back to
UV_SYNC_TIMEOUT = 600
SELFTEST_TIMEOUT = 180
MIGRATE_TIMEOUT = 600


class ApplyError(RuntimeError):
    pass


@dataclass
class Applied:
    version: str
    release_dir: Path
    selftest: str
    migrate: str


# ----------------------------------------------------------------- download
def download(url: str, sha256: str, dest: Path, timeout: float = 120.0) -> Path:
    """Fetch the archive, hashing as it streams. Refuses a digest mismatch.

    Over HTTPS from GitHub the realistic threat is a truncated or corrupted
    download rather than a substituted file, so treat this as an integrity
    check. It is not a signature and does not pretend to be one.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    h = hashlib.sha256()
    total = 0
    req = urllib.request.Request(url, headers={"User-Agent": "SynergyScan-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, tmp.open("wb") as f:
            while chunk := r.read(CHUNK):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ApplyError("update archive is implausibly large; refusing it")
                h.update(chunk)
                f.write(chunk)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        tmp.unlink(missing_ok=True)
        raise ApplyError(f"download failed: {e}") from e

    got = h.hexdigest()
    if got != sha256.lower():
        tmp.unlink(missing_ok=True)
        raise ApplyError(
            f"update archive is corrupt: expected sha256 {sha256}, got {got}"
        )
    tmp.replace(dest)
    log.info("downloaded %s (%d bytes, sha256 verified)", dest.name, total)
    return dest


# ------------------------------------------------------------------- unpack
def _safe_members(zf: zipfile.ZipFile, target: Path) -> list[zipfile.ZipInfo]:
    """Reject absolute paths, traversal and symlinks before extracting anything.

    Python's extractall sanitises names, but it will still happily write a
    member called ``..\\..\\data\\inventory.db`` to a surprising place on some
    versions, and it does not stop symlink entries. We check first and refuse
    the whole archive rather than extracting part of it.
    """
    target = target.resolve()
    out = []
    for m in zf.infolist():
        name = m.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise ApplyError(f"update archive contains an unsafe path: {m.filename!r}")
        if (m.external_attr >> 16) & 0o170000 == 0o120000:      # S_IFLNK
            raise ApplyError(f"update archive contains a symlink: {m.filename!r}")
        dest = (target / name).resolve()
        if not str(dest).startswith(str(target) + os.sep) and dest != target:
            raise ApplyError(f"update archive escapes the target directory: {name!r}")
        out.append(m)
    return out


def unpack(archive: Path, target: Path, expect_version: str) -> Path:
    """Extract into `target`, which must not already exist."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        members = _safe_members(zf, target)
        zf.extractall(target, members=members)

    found = version.current(target)
    if found != expect_version:
        raise ApplyError(
            f"archive contains release {found!r} but the channel promised "
            f"{expect_version!r}; refusing it"
        )
    if not (target / "pyproject.toml").exists():
        raise ApplyError("archive has no pyproject.toml; it is not a release")
    log.info("unpacked release %s into %s", found, target)
    return target


# --------------------------------------------------------------------- venv
def find_uv(install_root: Path | None) -> Path:
    """uv.exe, shipped next to the install so updates never depend on PATH."""
    names = ("uv.exe", "uv")
    if install_root:
        for n in names:
            p = install_root / n
            if p.exists():
                return p
    found = shutil.which("uv")
    if found:
        return Path(found)
    raise ApplyError(
        "uv was not found, so the new release's dependencies cannot be "
        "installed. Reinstall SynergyScan to repair this."
    )


def venv_python(release_dir: Path) -> Path:
    sub = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return release_dir / ".venv" / sub / exe


def build_venv(release_dir: Path, uv: Path) -> None:
    """`uv sync --frozen`: install exactly what uv.lock pins, resolve nothing.

    --frozen matters. Without it a dependency could resolve differently on the
    customer's machine than it did in CI, which means the release you tested is
    not the release that runs.
    """
    log.info("building environment for %s", release_dir.name)
    r = subprocess.run(
        [str(uv), "sync", "--frozen", "--no-dev"],
        cwd=release_dir, capture_output=True, text=True, timeout=UV_SYNC_TIMEOUT,
    )
    if r.returncode != 0:
        raise ApplyError(
            "could not install the new release's dependencies:\n"
            + (r.stderr or r.stdout or "").strip()[-2000:]
        )
    if not venv_python(release_dir).exists():
        raise ApplyError(f"no interpreter at {venv_python(release_dir)} after uv sync")


# -------------------------------------------------------- gates before flip
def _run_module(release_dir: Path, module: str, timeout: int) -> str:
    py = venv_python(release_dir)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [str(py), "-m", module],
        cwd=release_dir, capture_output=True, text=True, timeout=timeout, env=env,
    )
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        detail = out or (r.stderr or "").strip()
        raise ApplyError(f"{module} failed:\n{detail[-2000:]}")
    return out


def self_test(release_dir: Path) -> str:
    log.info("self-testing %s", release_dir.name)
    return _run_module(release_dir, "synergyscan.selfupdate.selftest", SELFTEST_TIMEOUT)


def migrate(release_dir: Path) -> str:
    log.info("migrating the database with %s", release_dir.name)
    return _run_module(release_dir, "synergyscan.selfupdate.migrate", MIGRATE_TIMEOUT)


# ------------------------------------------------------------------ pointer
def write_pointer(pointer: Path, new_version: str) -> None:
    """Atomically point the launcher at a release."""
    tmp = pointer.with_suffix(".txt.tmp")
    tmp.write_text(new_version + "\n", encoding="utf-8")
    os.replace(tmp, pointer)
    log.info("pointer now reads %s", new_version)


def read_pointer(pointer: Path) -> str | None:
    try:
        text = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if version.is_valid(text) else None


def prune_versions(versions_dir: Path, keep_versions: list[str],
                   keep: int = KEEP_VERSIONS) -> None:
    """Remove old releases, always keeping `keep_versions` and the newest few.

    Keeping the previous release on disk is what makes rollback a one-line edit
    instead of a reinstall.
    """
    installed = sorted(
        (d for d in versions_dir.iterdir() if d.is_dir() and version.is_valid(d.name)),
        key=lambda d: version.parse(d.name),
        reverse=True,
    )
    protected = set(keep_versions)
    for d in installed[keep:]:
        if d.name in protected:
            continue
        log.info("removing old release %s", d.name)
        shutil.rmtree(d, ignore_errors=True)


def clean_staging(staging: Path) -> None:
    for p in staging.glob("*"):
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        except OSError:
            log.warning("could not clean up %s", p)


# ------------------------------------------------------------- orchestration
def apply_release(rel: Release, *, install_root: Path, versions_dir: Path,
                  staging: Path, pointer: Path,
                  download_timeout: float = 120.0) -> Applied:
    """Install `rel` and move the pointer. Raises ApplyError without doing so."""
    target = versions_dir / rel.version
    if target.exists():
        # A previous attempt got this far and then failed. Start clean.
        log.warning("release directory %s already exists; replacing it", target)
        shutil.rmtree(target, ignore_errors=True)

    clean_staging(staging)
    archive = staging / f"synergyscan-{rel.version}.zip"
    download(rel.url, rel.sha256, archive, timeout=download_timeout)

    try:
        unpack(archive, target, rel.version)
        uv = find_uv(install_root)
        build_venv(target, uv)
        selftest_out = self_test(target)
        migrate_out = migrate(target)
    except Exception:
        # Leave `target` in place: nothing points at it, so it is inert, and the
        # next run replaces it. If removing it is what broke, we want the trace.
        log.error("release %s failed before the pointer moved; "
                  "still running the previous release", rel.version)
        raise

    previous = read_pointer(pointer)
    write_pointer(pointer, rel.version)
    clean_staging(staging)
    prune_versions(versions_dir, keep_versions=[v for v in (previous,) if v])
    return Applied(version=rel.version, release_dir=target,
                   selftest=selftest_out, migrate=migrate_out)


def apply_from_paths(rel: Release, download_timeout: float = 120.0) -> Applied:
    """apply_release wired up from synergyscan.paths. Refuses a source checkout."""
    from .. import paths

    root, versions = paths.install_root(), paths.versions_dir()
    pointer = paths.current_pointer()
    if not (root and versions and pointer):
        raise ApplyError(
            "this is a source checkout, not an installed release; "
            "self-update only runs against an installed layout"
        )
    return apply_release(rel, install_root=root, versions_dir=versions,
                         staging=paths.staging_dir(), pointer=pointer,
                         download_timeout=download_timeout)


def main() -> int:
    """Install whatever the configured channel offers. `--force` ignores versions."""
    import argparse

    from .. import config
    from .check import available, fetch_channel

    ap = argparse.ArgumentParser(prog="python -m synergyscan.selfupdate.apply")
    ap.add_argument("--force", action="store_true",
                    help="install the channel release even if it is not newer")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    s = config.load()
    rel = fetch_channel(s.channel_url) if a.force else available(s.channel_url)
    if rel is None:
        print("already up to date")
        return 0
    applied = apply_from_paths(rel, download_timeout=s.update_timeout_s)
    print(f"installed {applied.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
