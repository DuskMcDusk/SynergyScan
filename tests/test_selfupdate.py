"""Updater tests.

The property under test throughout: **the pointer only ever moves after every
gate has passed.** A bug here cannot be fixed by shipping an update, so the
failure paths get more attention than the happy one.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from synergyscan.selfupdate import apply as A
from synergyscan.selfupdate import check as C
from synergyscan.selfupdate import version as V


# ------------------------------------------------------------------- version
@pytest.mark.parametrize("text", ["2026.09.25-1", "2026.9.5-12", "0.0.0-0"])
def test_valid_versions(text):
    assert V.is_valid(text)


@pytest.mark.parametrize("text", ["2026.09.25", "v2026.09.25-1", "1.2.3",
                                  "", "2026.09.25-", "abc", "2026.09.25-1 "])
def test_invalid_versions(text):
    assert not V.is_valid(text.strip()) or text.strip() != text


def test_versions_compare_numerically_not_as_strings():
    """The bug this exists to prevent: "2026.9.4-1" > "2026.10.2-1" as strings."""
    assert V.newer("2026.10.2-1", "2026.9.4-1")
    assert not V.newer("2026.9.4-1", "2026.10.2-1")


def test_same_day_sequence_numbers_order_correctly():
    assert V.newer("2026.09.25-10", "2026.09.25-9")


def test_a_source_checkout_reports_the_lowest_possible_version():
    """So every published release looks newer and a dev build is never "current"."""
    assert V.current(Path("/definitely/not/a/release")) == "0.0.0-0"
    assert V.is_source_checkout(Path("/definitely/not/a/release"))


def test_release_file_is_read_when_present(tmp_path: Path):
    (tmp_path / "RELEASE").write_text("2026.09.25-3\n", encoding="utf-8")
    assert V.current(tmp_path) == "2026.09.25-3"
    assert not V.is_source_checkout(tmp_path)


def test_a_corrupt_release_file_falls_back_rather_than_crashing(tmp_path: Path):
    (tmp_path / "RELEASE").write_text("not-a-version", encoding="utf-8")
    assert V.current(tmp_path) == "0.0.0-0"


# --------------------------------------------------------------------- check
GOOD = {
    "version": "2026.10.02-1",
    "url": "https://example.invalid/synergyscan-2026.10.02-1.zip",
    "sha256": "a" * 64,
    "notes": "hello",
}


def fake_channel(monkeypatch, payload, *, raises=None):
    """Stand in for the HTTP fetch."""
    def _open(req, timeout=None):
        if raises:
            raise raises
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R(body)

    monkeypatch.setattr(C.urllib.request, "urlopen", _open)


def test_fetch_channel_parses_a_good_file(monkeypatch):
    fake_channel(monkeypatch, GOOD)
    rel = C.fetch_channel("https://example.invalid/stable.json")
    assert rel.version == "2026.10.02-1"
    assert rel.sha256 == "a" * 64
    assert rel.min_version is None


@pytest.mark.parametrize("mutate,message", [
    ({"version": None}, "missing version"),
    ({"url": None}, "missing url"),
    ({"sha256": None}, "missing sha256"),
    ({"version": "10.2"}, "malformed"),
    ({"sha256": "abc"}, "64-character hex"),
    ({"sha256": "z" * 64}, "64-character hex"),
    ({"url": "http://example.invalid/x.zip"}, "must be https"),
    ({"min_version": "nope"}, "min_version"),
])
def test_fetch_channel_rejects_bad_files(monkeypatch, mutate, message):
    payload = {**GOOD, **mutate}
    payload = {k: v for k, v in payload.items() if v is not None}
    fake_channel(monkeypatch, payload)
    with pytest.raises(C.CheckError, match=message):
        C.fetch_channel("https://example.invalid/stable.json")


def test_plain_http_is_refused(monkeypatch):
    """Anyone on the path could otherwise swap the archive for their own."""
    fake_channel(monkeypatch, {**GOOD, "url": "http://example.invalid/x.zip"})
    with pytest.raises(C.CheckError, match="https"):
        C.fetch_channel("https://example.invalid/stable.json")


def test_fetch_channel_rejects_a_non_object(monkeypatch):
    fake_channel(monkeypatch, [1, 2, 3])
    with pytest.raises(C.CheckError, match="JSON object"):
        C.fetch_channel("https://example.invalid/stable.json")


def test_fetch_channel_rejects_garbage(monkeypatch):
    fake_channel(monkeypatch, b"<html>404</html>")
    with pytest.raises(C.CheckError, match="not valid JSON"):
        C.fetch_channel("https://example.invalid/stable.json")


def test_fetch_channel_refuses_an_absurdly_large_file(monkeypatch):
    fake_channel(monkeypatch, b"{" + b" " * (C.MAX_CHANNEL_BYTES + 10))
    with pytest.raises(C.CheckError, match="implausibly large"):
        C.fetch_channel("https://example.invalid/stable.json")


def test_available_returns_none_when_up_to_date(monkeypatch):
    fake_channel(monkeypatch, GOOD)
    assert C.available("https://x.invalid/s.json", "2026.10.02-1") is None
    assert C.available("https://x.invalid/s.json", "2026.11.01-1") is None


def test_available_returns_the_release_when_newer(monkeypatch):
    fake_channel(monkeypatch, GOOD)
    rel = C.available("https://x.invalid/s.json", "2026.09.25-1")
    assert rel is not None and rel.version == "2026.10.02-1"


def test_being_offline_is_not_an_error(monkeypatch):
    """A machine that cannot check for updates must still start and work."""
    fake_channel(monkeypatch, GOOD, raises=OSError("no network"))
    assert C.available("https://x.invalid/s.json", "2026.09.25-1") is None


def test_min_version_blocks_skipping_a_required_release(monkeypatch):
    fake_channel(monkeypatch, {**GOOD, "min_version": "2026.10.01-1"})
    assert C.available("https://x.invalid/s.json", "2026.09.01-1") is None
    rel = C.available("https://x.invalid/s.json", "2026.10.01-1")
    assert rel is not None


# -------------------------------------------------------------- archive prep
def make_release_zip(path: Path, version: str, *, extra: dict | None = None,
                     omit_release: bool = False,
                     unsafe_name: str | None = None) -> str:
    """Build a release archive and return its sha256."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pyproject.toml", "[project]\nname='synergyscan'\n")
        zf.writestr("src/synergyscan/__init__.py", "")
        if not omit_release:
            zf.writestr("RELEASE", version + "\n")
        for name, body in (extra or {}).items():
            zf.writestr(name, body)
        if unsafe_name:
            zf.writestr(unsafe_name, "pwned")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_url(p: Path) -> str:
    return p.resolve().as_uri()


# ------------------------------------------------------------------ download
def test_download_verifies_the_digest(tmp_path: Path):
    src = tmp_path / "rel.zip"
    digest = make_release_zip(src, "2026.10.02-1")
    dest = tmp_path / "out" / "rel.zip"
    A.download(as_url(src), digest, dest)
    assert dest.exists()
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == digest


def test_download_refuses_a_digest_mismatch_and_leaves_nothing_behind(tmp_path: Path):
    src = tmp_path / "rel.zip"
    make_release_zip(src, "2026.10.02-1")
    dest = tmp_path / "out" / "rel.zip"
    with pytest.raises(A.ApplyError, match="corrupt"):
        A.download(as_url(src), "b" * 64, dest)
    assert not dest.exists()
    assert not dest.with_suffix(".zip.part").exists()


def test_download_reports_a_missing_file_clearly(tmp_path: Path):
    with pytest.raises(A.ApplyError, match="download failed"):
        A.download(as_url(tmp_path / "nope.zip"), "c" * 64, tmp_path / "o.zip")


# -------------------------------------------------------------------- unpack
def test_unpack_checks_the_version_inside_the_archive(tmp_path: Path):
    """The filename is not trusted; the archive has to say what it is."""
    src = tmp_path / "rel.zip"
    make_release_zip(src, "2026.10.02-1")
    with pytest.raises(A.ApplyError, match="promised"):
        A.unpack(src, tmp_path / "t", "2026.10.03-1")


def test_unpack_requires_a_pyproject(tmp_path: Path):
    src = tmp_path / "rel.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("RELEASE", "2026.10.02-1\n")
    with pytest.raises(A.ApplyError, match="not a release"):
        A.unpack(src, tmp_path / "t", "2026.10.02-1")


def test_unpack_succeeds_and_places_files(tmp_path: Path):
    src = tmp_path / "rel.zip"
    make_release_zip(src, "2026.10.02-1", extra={"bootstrap/launcher.py": "x"})
    target = A.unpack(src, tmp_path / "t", "2026.10.02-1")
    assert (target / "pyproject.toml").exists()
    assert (target / "bootstrap" / "launcher.py").exists()
    assert V.current(target) == "2026.10.02-1"


@pytest.mark.parametrize("name", [
    "../escaped.txt",
    "../../data/inventory.db",
    "sub/../../escaped.txt",
    "/absolute.txt",
])
def test_unpack_refuses_paths_that_escape_the_target(tmp_path: Path, name):
    """Zip slip: a release archive must not be able to write over the database."""
    src = tmp_path / "rel.zip"
    make_release_zip(src, "2026.10.02-1", unsafe_name=name)
    with pytest.raises(A.ApplyError, match="unsafe path|escapes"):
        A.unpack(src, tmp_path / "t", "2026.10.02-1")
    assert not (tmp_path / "escaped.txt").exists()


def test_unpack_refuses_symlinks(tmp_path: Path):
    src = tmp_path / "rel.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("pyproject.toml", "[project]\n")
        zf.writestr("RELEASE", "2026.10.02-1\n")
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)          # S_IFLNK
        zf.writestr(info, "C:/Windows")
    with pytest.raises(A.ApplyError, match="symlink"):
        A.unpack(src, tmp_path / "t", "2026.10.02-1")


def test_unpack_replaces_an_existing_target(tmp_path: Path):
    target = tmp_path / "t"
    target.mkdir()
    (target / "stale.txt").write_text("old", encoding="utf-8")
    src = tmp_path / "rel.zip"
    make_release_zip(src, "2026.10.02-1")
    A.unpack(src, target, "2026.10.02-1")
    assert not (target / "stale.txt").exists()


# ------------------------------------------------------------------- pointer
def test_pointer_round_trip(tmp_path: Path):
    p = tmp_path / "current.txt"
    A.write_pointer(p, "2026.10.02-1")
    assert p.read_text(encoding="utf-8").strip() == "2026.10.02-1"
    assert A.read_pointer(p) == "2026.10.02-1"


def test_read_pointer_rejects_junk(tmp_path: Path):
    p = tmp_path / "current.txt"
    p.write_text("garbage\n", encoding="utf-8")
    assert A.read_pointer(p) is None


def test_read_pointer_handles_a_missing_file(tmp_path: Path):
    assert A.read_pointer(tmp_path / "nope.txt") is None


def test_write_pointer_leaves_no_temp_file(tmp_path: Path):
    p = tmp_path / "current.txt"
    A.write_pointer(p, "2026.10.02-1")
    assert not list(tmp_path.glob("*.tmp"))


def test_the_source_checkout_sentinel_sorts_below_every_release():
    """current() promises this; with a strict 4-digit year it raised instead."""
    assert V.newer("2026.09.25-1", V.current(Path("/nowhere")))


# --------------------------------------------------------------------- prune
def test_prune_keeps_the_newest_and_anything_protected(tmp_path: Path):
    versions = tmp_path / "versions"
    versions.mkdir()
    names = ["2026.09.01-1", "2026.09.10-1", "2026.09.20-1",
             "2026.10.01-1", "2026.10.02-1"]
    for n in names:
        (versions / n).mkdir()
    A.prune_versions(versions, keep_versions=["2026.09.01-1"], keep=2)
    left = sorted(d.name for d in versions.iterdir())
    assert left == ["2026.09.01-1", "2026.10.01-1", "2026.10.02-1"]


def test_prune_ignores_directories_that_are_not_versions(tmp_path: Path):
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "2026.10.02-1").mkdir()
    (versions / "scratch").mkdir()
    A.prune_versions(versions, keep_versions=[], keep=1)
    assert (versions / "scratch").exists()


# ------------------------------------------------- the ordering guarantee
@pytest.fixture
def install(tmp_path: Path):
    """A deployed layout with one release already installed and pointed at."""
    root = tmp_path / "SynergyScan"
    versions = root / "versions"
    staging = root / "staging"
    for d in (versions / "2026.09.25-1", staging, root / "data"):
        d.mkdir(parents=True)
    pointer = root / "current.txt"
    A.write_pointer(pointer, "2026.09.25-1")
    return {"root": root, "versions": versions, "staging": staging,
            "pointer": pointer}


def release_for(tmp_path: Path, version: str) -> C.Release:
    src = tmp_path / f"rel-{version}.zip"
    digest = make_release_zip(src, version)
    return C.Release(version=version, url=as_url(src), sha256=digest)


def stub_gates(monkeypatch, *, venv=None, selftest=None, migrate=None):
    """Replace the three steps that need a real interpreter."""
    monkeypatch.setattr(A, "find_uv", lambda root: Path("uv"))
    monkeypatch.setattr(A, "build_venv", venv or (lambda d, uv: None))
    monkeypatch.setattr(A, "self_test", selftest or (lambda d: "{}"))
    monkeypatch.setattr(A, "migrate", migrate or (lambda d: "{}"))


def test_apply_moves_the_pointer_when_everything_passes(tmp_path, install, monkeypatch):
    stub_gates(monkeypatch)
    rel = release_for(tmp_path, "2026.10.02-1")
    applied = A.apply_release(rel, install_root=install["root"],
                              versions_dir=install["versions"],
                              staging=install["staging"], pointer=install["pointer"])
    assert applied.version == "2026.10.02-1"
    assert A.read_pointer(install["pointer"]) == "2026.10.02-1"
    assert (install["versions"] / "2026.10.02-1" / "pyproject.toml").exists()


@pytest.mark.parametrize("failing", ["build_venv", "self_test", "migrate"])
def test_a_failure_at_any_gate_leaves_the_pointer_untouched(
        tmp_path, install, monkeypatch, failing):
    """The whole design in one test."""
    def boom(*a, **k):
        raise A.ApplyError(f"{failing} failed")

    stub_gates(monkeypatch, **{
        {"build_venv": "venv", "self_test": "selftest", "migrate": "migrate"}[failing]:
            boom
    })
    rel = release_for(tmp_path, "2026.10.02-1")
    with pytest.raises(A.ApplyError, match=failing):
        A.apply_release(rel, install_root=install["root"],
                        versions_dir=install["versions"],
                        staging=install["staging"], pointer=install["pointer"])
    assert A.read_pointer(install["pointer"]) == "2026.09.25-1"


def test_a_corrupt_download_never_reaches_the_versions_directory(
        tmp_path, install, monkeypatch):
    stub_gates(monkeypatch)
    src = tmp_path / "rel.zip"
    make_release_zip(src, "2026.10.02-1")
    rel = C.Release(version="2026.10.02-1", url=as_url(src), sha256="f" * 64)
    with pytest.raises(A.ApplyError, match="corrupt"):
        A.apply_release(rel, install_root=install["root"],
                        versions_dir=install["versions"],
                        staging=install["staging"], pointer=install["pointer"])
    assert not (install["versions"] / "2026.10.02-1").exists()
    assert A.read_pointer(install["pointer"]) == "2026.09.25-1"


def test_a_failed_attempt_is_replaced_on_the_next_run(tmp_path, install, monkeypatch):
    """A leftover release directory from a failed attempt must not be reused."""
    stale = install["versions"] / "2026.10.02-1"
    stale.mkdir()
    (stale / "half-written.txt").write_text("junk", encoding="utf-8")

    stub_gates(monkeypatch)
    rel = release_for(tmp_path, "2026.10.02-1")
    A.apply_release(rel, install_root=install["root"],
                    versions_dir=install["versions"],
                    staging=install["staging"], pointer=install["pointer"])
    assert not (stale / "half-written.txt").exists()
    assert (stale / "pyproject.toml").exists()


def test_staging_is_cleaned_after_a_successful_apply(tmp_path, install, monkeypatch):
    stub_gates(monkeypatch)
    rel = release_for(tmp_path, "2026.10.02-1")
    A.apply_release(rel, install_root=install["root"],
                    versions_dir=install["versions"],
                    staging=install["staging"], pointer=install["pointer"])
    assert list(install["staging"].iterdir()) == []


def test_the_previous_release_survives_pruning(tmp_path, install, monkeypatch):
    """Rollback depends on it still being on disk."""
    stub_gates(monkeypatch)
    rel = release_for(tmp_path, "2026.10.02-1")
    A.apply_release(rel, install_root=install["root"],
                    versions_dir=install["versions"],
                    staging=install["staging"], pointer=install["pointer"])
    assert (install["versions"] / "2026.09.25-1").exists()


def test_self_update_refuses_to_run_in_a_source_checkout(monkeypatch):
    from synergyscan import paths

    monkeypatch.setattr(paths, "install_root", lambda: None)
    monkeypatch.setattr(paths, "versions_dir", lambda: None)
    monkeypatch.setattr(paths, "current_pointer", lambda: None)
    with pytest.raises(A.ApplyError, match="source checkout"):
        A.apply_from_paths(C.Release(version="2026.10.02-1",
                                     url="https://x.invalid/a.zip",
                                     sha256="a" * 64))


# ------------------------------------------------------- check_and_apply hook
def test_check_and_apply_never_raises(monkeypatch):
    """Whatever goes wrong, the running release keeps running."""
    import synergyscan.selfupdate as SU

    monkeypatch.setattr(SU, "is_source_checkout", lambda: False)
    monkeypatch.setattr(SU, "available",
                        lambda url: C.Release(version="2026.10.02-1",
                                              url="https://x.invalid/a.zip",
                                              sha256="a" * 64))
    monkeypatch.setattr(SU, "apply_from_paths",
                        lambda rel, download_timeout=120: (_ for _ in ()).throw(
                            RuntimeError("something unexpected")))
    assert SU.check_and_apply("https://x.invalid/s.json") is None


def test_check_and_apply_respects_the_config_switch(monkeypatch):
    import synergyscan.selfupdate as SU

    called = []
    monkeypatch.setattr(SU, "available", lambda url: called.append(url))
    assert SU.check_and_apply("https://x.invalid/s.json", enabled=False) is None
    assert called == []


def test_check_and_apply_skips_a_source_checkout(monkeypatch):
    import synergyscan.selfupdate as SU

    called = []
    monkeypatch.setattr(SU, "is_source_checkout", lambda: True)
    monkeypatch.setattr(SU, "available", lambda url: called.append(url))
    assert SU.check_and_apply("https://x.invalid/s.json") is None
    assert called == []


def test_restart_exit_code_matches_the_launcher():
    """launcher.py hardcodes 75; they have to agree or updates never take effect."""
    import re

    import synergyscan.selfupdate as SU

    launcher = (Path(__file__).resolve().parents[1] / "bootstrap" / "launcher.py")
    m = re.search(r"RESTART_EXIT_CODE\s*=\s*(\d+)", launcher.read_text(encoding="utf-8"))
    assert m and int(m.group(1)) == SU.RESTART_EXIT_CODE
