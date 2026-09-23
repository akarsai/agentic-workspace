"""Tests for the private userspace fakeroot (apptainer unprivileged builds).

Everything here is hermetic: a minimal .deb is assembled in the test with a
hand-rolled `ar` archive so extract_deb is exercised against the real
format, and the smoke test is pointed at stub binaries.
"""
from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_deb(dest: Path, files: dict[str, bytes]) -> None:
    """A minimal but real .deb: ar archive with a data.tar.gz member."""
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o755 if name.endswith("faked-sysv") else 0o644
            tar.addfile(info, io.BytesIO(body))

    def ar_member(name: str, payload: bytes) -> bytes:
        header = (
            f"{name:<16}{0:>12}{0:>6}{0:>6}{0o100644:>8}{len(payload):>10}"
        ).encode() + b"`\n"
        pad = b"\n" if len(payload) % 2 else b""
        return header + payload + pad

    out = b"!<arch>\n"
    out += ar_member("debian-binary", b"2.0\n")
    out += ar_member("data.tar.gz", data.getvalue())
    dest.write_bytes(out)


@pytest.fixture()
def fr_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import agentic_workspace.fakeroot as fr

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv(fr.FAKEROOT_DIR_ENV, raising=False)
    monkeypatch.setattr(fr, "CACHE_DIR", tmp_path / "cache" / "fakeroot")
    return fr


def test_extract_deb_unpacks_data_tar(fr_mod, tmp_path: Path) -> None:
    """extract_deb must read the ar container and unpack data.tar.gz."""
    deb = tmp_path / "f.deb"
    make_deb(deb, {"usr/bin/hello": b"#!/bin/sh\necho hi\n", "usr/lib/l/x.so": b"\x7fELF"})
    dest = tmp_path / "out"
    dest.mkdir()
    fr_mod.extract_deb(deb, dest)
    assert (dest / "usr/bin/hello").read_bytes().startswith(b"#!/bin/sh")
    assert (dest / "usr/lib/l/x.so").read_bytes() == b"\x7fELF"


def test_extract_deb_rejects_non_ar(fr_mod, tmp_path: Path) -> None:
    bad = tmp_path / "x.deb"
    bad.write_bytes(b"definitely not an ar archive\n")
    with pytest.raises(ValueError):
        fr_mod.extract_deb(bad, tmp_path / "out")


def test_wrapper_layout_and_names(fr_mod, tmp_path: Path) -> None:
    """The generated layout must match what apptainer's fakeroot support
    looks for: fakeroot-sysv (FindFake), faked-sysv beside it, and the lib
    reachable via the wrapper's LD_LIBRARY_PATH (GetFakeBinds parses
    `fakeroot env`)."""
    root = tmp_path / "fr"
    bin_deb = tmp_path / "a.deb"
    lib_deb = tmp_path / "b.deb"
    make_deb(bin_deb, {"usr/bin/faked-sysv": b"#!/bin/sh\nexit 0\n"})
    make_deb(lib_deb, {"usr/lib/x86_64-linux-gnu/libfakeroot/libfakeroot-sysv.so": b"\x7fELFlib"})
    fr_mod._install_from_debs(bin_deb, lib_deb, root)

    wrapper = root / "bin" / "fakeroot"
    assert wrapper.is_file()
    assert os.access(wrapper, os.X_OK)
    assert (root / "bin" / "fakeroot-sysv").is_symlink()
    assert (root / "bin" / "faked-sysv").is_file()
    assert (root / "lib" / "libfakeroot-sysv.so").is_file()
    # the wrapper points at this layout and preloads by BARE lib name
    # (apptainer's GetFakeBinds only recognizes entries with the
    # "libfakeroot" prefix, then finds the dir via LD_LIBRARY_PATH)
    body = wrapper.read_text()
    assert str(root / "lib") in body
    assert "libfakeroot-sysv.so" in body
    assert 'LD_PRELOAD="$LIBNAME' in body
    # apptainer invokes the bound copy with -f/-l; the wrapper must accept them
    assert "-f|--faked" in body and "-l|--lib" in body


def test_smoke_test_detects_non_interposing_fakeroot(fr_mod, tmp_path: Path) -> None:
    """A fakeroot whose chown/stat do not interpose (the old-glibc failure
    mode) must fail the smoke test instead of producing a broken image."""
    root = tmp_path / "fr"
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir(parents=True)
    # faked that "starts" fine and a wrapper that preloads nothing at all
    (root / "bin" / "faked-sysv").write_text("#!/bin/sh\necho 12345:1\n")
    (root / "bin" / "faked-sysv").chmod(0o755)
    (root / "lib" / "libfakeroot-sysv.so").write_text("not an elf")
    fr_mod._write_wrapper(root / "bin", root / "lib")
    assert fr_mod.smoke_test(root) is False


def test_build_env_with_fakeroot(fr_mod, monkeypatch: pytest.MonkeyPatch) -> None:
    env = fr_mod.build_env_with_fakeroot({"PATH": "/usr/bin"})
    assert env["PATH"] == "/usr/bin"  # no memo: untouched

    env = fr_mod.build_env_with_fakeroot({"PATH": "/usr/bin"}, fakeroot_dir=Path("/opt/fr"))
    assert env["PATH"].startswith("/opt/fr/bin:")
    assert env[fr_mod.FAKEROOT_DIR_ENV] == "/opt/fr"

    # memo env propagates into a fresh env copy (child processes)
    monkeypatch.setenv(fr_mod.FAKEROOT_DIR_ENV, "/opt/fr2")
    env = fr_mod.build_env_with_fakeroot({"PATH": "/a:/b"})
    assert env["PATH"] == f"/opt/fr2/bin{os.pathsep}/a:/b"


def test_cached_fakeroot_dir_requires_smoke_stamp(fr_mod) -> None:
    """The cache is only trusted with its smoke-test stamp: a leftover dir
    without .smoke-ok must not be prepended to build PATHs."""
    root = fr_mod.CACHE_DIR
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "fakeroot-sysv").write_text("#!/bin/sh\nexit 0\n")
    assert fr_mod.cached_fakeroot_dir() is None
    root.joinpath(fr_mod.SMOKE_STAMP).write_text("bookworm-1.31\n")
    assert fr_mod.cached_fakeroot_dir() == root


def test_ensure_fakeroot_uses_cached_dir(fr_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A previously provisioned, smoke-passing cache dir is reused and memoized."""
    root = fr_mod.CACHE_DIR
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir(parents=True)
    (root / "bin" / "faked-sysv").write_text("#!/bin/sh\necho 1:1\n")
    (root / "bin" / "faked-sysv").chmod(0o755)
    (root / "lib" / "libfakeroot-sysv.so").write_text("x")
    fr_mod._write_wrapper(root / "bin", root / "lib")
    monkeypatch.setattr(fr_mod, "smoke_test", lambda r: True)

    assert fr_mod.ensure_fakeroot() == root
    assert os.environ[fr_mod.FAKEROOT_DIR_ENV] == str(root)


def test_ensure_fakeroot_offline_returns_none(fr_mod, monkeypatch: pytest.MonkeyPatch) -> None:
    """With every download failing (offline host) provisioning gives up
    cleanly with a warning, and no half-written cache is left behind."""
    warned: list[str] = []

    def fail_download(url, dest, sha):
        warned.append(url)
        return False

    monkeypatch.setattr(fr_mod, "_download", fail_download)
    assert fr_mod.ensure_fakeroot() is None
    assert warned, "expected download attempts"
    assert not fr_mod.CACHE_DIR.exists()
