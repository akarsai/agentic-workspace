"""Private userspace fakeroot for Apptainer builds on locked-down hosts.

`apptainer build` from a definition file needs root, a fakeroot mapping
(/etc/subuid), or user namespaces. Many HPC login nodes offer none of those
for regular users — but their Apptainer is a setuid install, and a setuid
Apptainer can run the whole build (including every %post scriptlet and the
final mksquashfs) under the *fakeroot command*: a plain userspace tool that
fakes root via LD_PRELOAD, no privileges required. Apptainer documents this
path ("The %post section will be run under the fakeroot command") and looks
for `fakeroot-sysv` or `fakeroot` on PATH (internal/pkg/fakeroot.FindFake).
The only thing missing on such hosts is the command itself.

This module provisions it on demand, entirely inside the user's cache:

- fakeroot + libfakeroot .debs are downloaded from Debian (URLs and
  SHA-256 checksums pinned below, newest-first), extracted with a pure
  stdlib ar+tar reader (no dpkg needed), and laid out the way Apptainer's
  fakeroot support expects (bin/fakeroot-sysv next to bin/faked-sysv, the
  lib on the wrapper's LD_LIBRARY_PATH).
- a small relocatable sh wrapper replaces Debian's (which hardcodes
  /usr paths and needs getopt); it supports exactly the flags Apptainer
  passes (-f/--faked, -l/--lib) and plain command execution.
- every candidate is smoke-tested on the host before use: getuid must
  fake to 0 and a chown must be visible to stat *inside* the fakeroot
  session. This catches the known failure where a libfakeroot built
  against an older glibc silently stops interposing `stat` (glibc 2.33
  re-versioned the stat symbols), so a too-old candidate is skipped
  instead of producing an image with broken ownership.

Everything here runs unprivileged; nothing touches /usr or needs an admin.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .util import say, warn

# Cache root for the provisioned fakeroot (shared by every instance).
CACHE_DIR = Path(os.environ.get("AGENTIC_FAKEROOT_CACHE") or Path.home() / ".cache" / "agentic-workspace") / "fakeroot"

# Environment variable used as a cross-process memo: set once a private
# fakeroot is provisioned (or known broken) so child builds (launcher ->
# build -> container_build) skip the download/probe dance.
FAKEROOT_DIR_ENV = "AGENTIC_APPTAINER_FAKEROOT_DIR"

# Pinned download sources, tried newest-first. The fakeroot command is two
# packages (fakeroot: the wrapper + faked; libfakeroot: the LD_PRELOAD lib).
# Old releases drop off the main Debian pool, so each candidate carries its
# own stable base URL (buster lives on archive.debian.org).
_CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "name": "bookworm-1.31",
        "note": "glibc >= 2.34 hosts (RHEL 9, Ubuntu 22.04+, Debian 12+)",
        "base_amd64": "http://deb.debian.org/debian/pool/main/f/fakeroot",
        "base_arm64": "http://deb.debian.org/debian/pool/main/f/fakeroot",
        "file": "fakeroot_1.31-1.2_{arch}.deb",
        "lib_file": "libfakeroot_1.31-1.2_{arch}.deb",
        "sha256_amd64": "194fd3750e6d647f300045a266c20cc3a3d47f84fd2fc8ff8830c55098b63c0d",
        "sha256_arm64": "301bf8401e9067018fbb55481594f94eed6ee812c7f9c13607d57058fa975e2c",
        "lib_sha256_amd64": "539c1a013e6e90800b4c37877cf871e7583791b486a39e23f2466906bbe5061f",
        "lib_sha256_arm64": "27dcbc1ee8ed2cdbbf3640c779cf210a693b72394ff414e7c14fcdadb0f1d9e5",
    },
    {
        "name": "bullseye-1.25.3",
        "note": "glibc >= 2.31 hosts (Debian 11, Ubuntu 20.04)",
        "base_amd64": "http://deb.debian.org/debian/pool/main/f/fakeroot",
        "base_arm64": "http://deb.debian.org/debian/pool/main/f/fakeroot",
        "file": "fakeroot_1.25.3-1.1_{arch}.deb",
        "lib_file": "libfakeroot_1.25.3-1.1_{arch}.deb",
        "sha256_amd64": "b67966ee7bad5e87f4ae7eecb6f4fe76ba7c12af7b9876c09f1e758eb742232c",
        "sha256_arm64": "080de675890923093165d78ed62512c5f1dafc52867c9241b3c7e32d20325c21",
        "lib_sha256_amd64": "04dace71ea2e14940bd0491e41331df77b5a5da82fe98ead228df756b4fe0bc8",
        "lib_sha256_arm64": "fb6c9ebc9b9e33c8c68ac496764a4345859f758dd04c7afb22463b5d31962c68",
    },
    {
        "name": "buster-1.23",
        "note": "older hosts (glibc >= 2.14); stat interposition may be partial",
        "base_amd64": "http://archive.debian.org/debian/pool/main/f/fakeroot",
        "base_arm64": "http://archive.debian.org/debian/pool/main/f/fakeroot",
        "file": "fakeroot_1.23-1_{arch}.deb",
        "lib_file": "libfakeroot_1.23-1_{arch}.deb",
        "sha256_amd64": "e671d4fbf01230393897f3dc8a5b5699905431f1fff914320e82ba829a9420ad",
        "sha256_arm64": "d1266445438da4c5aca6ed9a92ba2718e74c0ab5efa2eb0f5b403cdf8e7d3da4",
        "lib_sha256_amd64": "7827d98f210bd8c7635bbf7dfeac3d9434c343d3649588340909a91590e0c0fd",
        "lib_sha256_arm64": "5f5f26beee80fe10ef3a74c3090a5afe132b2311edb061a90697b4e849465ce4",
    },
)

WRAPPER = r"""#!/bin/sh
# Generated by agentic-workspace — relocatable fakeroot front-end.
# Mirrors Debian's fakeroot-sysv for the flags Apptainer passes
# (-f/--faked, -l/--lib) without depending on getopt or /usr layout.
LIB=__LIBDIR__/libfakeroot-sysv.so
FAKED=__BINDIR__/faked-sysv
LIBDIR=__LIBDIR__
FAKED_MODE="unknown-is-root"
export FAKED_MODE

usage() {
    echo "usage: fakeroot [-l lib] [-f faked] [-u] [-i file] [-s file] [--] command" >&2
    exit 1
}

FAKEDOPTS=""
while [ $# -gt 0 ]; do
    case "$1" in
        -l|--lib)   [ $# -ge 2 ] || usage; LIB="$2"; shift 2 ;;
        -f|--faked) [ $# -ge 2 ] || usage; FAKED="$2"; shift 2 ;;
        -i)         [ $# -ge 2 ] || usage; [ -f "$2" ] && FAKEDOPTS="$FAKEDOPTS --load"; shift 2 ;;
        -s)         [ $# -ge 2 ] || usage; FAKEDOPTS="$FAKEDOPTS --save-file $2"; shift 2 ;;
        -u|--unknown-is-real) FAKED_MODE="unknown-is-real"; shift ;;
        -b|--fd-base) [ $# -ge 2 ] || usage; FAKEROOT_FD_BASE="$2"; shift 2 ;;
        -h|--help) usage ;;
        -v|--version) echo "fakeroot (agentic-workspace wrapper)"; exit 0 ;;
        --) shift; break ;;
        -*) echo "fakeroot: unknown option: $1" >&2; usage ;;
        *) break ;;
    esac
done

case "$LIB" in
    */*) LIBDIR=$(dirname "$LIB"); LIBNAME=$(basename "$LIB") ;;
    *)   LIBNAME="$LIB" ;;
esac

if [ -n "$FAKEROOTKEY" ]; then
    echo "fakeroot: nested operation not supported (FAKEROOTKEY set)" >&2
    exit 1
fi

KEY_PID=$(eval "\"\$FAKED\" $FAKEDOPTS" 2>/dev/null)
FAKEROOTKEY=$(echo "$KEY_PID" | cut -d: -f1)
PID=$(echo "$KEY_PID" | cut -d: -f2)
if [ -z "$FAKEROOTKEY" ] || [ -z "$PID" ]; then
    echo "fakeroot: could not start the faked daemon ($FAKED)" >&2
    exit 1
fi
trap 'kill -s TERM $PID 2>/dev/null' EXIT INT

if [ $# -eq 0 ]; then
    set -- "${SHELL:-/bin/sh}"
fi

export FAKEROOT_FD_BASE
LD_LIBRARY_PATH="$LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
LD_PRELOAD="$LIBNAME${LD_PRELOAD:+:$LD_PRELOAD}" \
FAKEROOTKEY="$FAKEROOTKEY" \
exec "$@"
"""


def _arch() -> str:
    machine = platform.machine()
    return "arm64" if machine in ("aarch64", "arm64") else "amd64"


# Written once a provisioned fakeroot passed its smoke test, so later
# builds can prepend it to PATH without re-running the probe.
SMOKE_STAMP = ".smoke-ok"


def cached_fakeroot_dir() -> Path | None:
    """The provisioned fakeroot when it exists and passed its smoke test."""
    if (CACHE_DIR / "bin" / "fakeroot-sysv").is_file() and (CACHE_DIR / SMOKE_STAMP).is_file():
        return CACHE_DIR
    return None


def extract_deb(deb: Path, dest: Path) -> None:
    """Extract a .deb's data.tar.* into dest (pure stdlib, no dpkg).

    A .deb is an `ar` archive whose `data.tar.<comp>` member holds the
    filesystem payload; tarfile handles every compression it ships with.
    """
    data = deb.read_bytes()
    if data[:8] != b"!<arch>\n":
        raise ValueError(f"not an ar archive: {deb}")
    pos = 8
    found = False
    while pos + 60 <= len(data):
        header = data[pos : pos + 60]
        pos += 60
        name = header[0:16].decode("ascii", "replace").strip()
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError:
            raise ValueError(f"corrupt ar header in {deb}")
        body = data[pos : pos + size]
        pos += size + (size % 2)
        member = name.rstrip("/")
        if member == "data.tar" or member.startswith("data.tar."):
            with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
                tmp.write(body)
                tmp_path = tmp.name
            try:
                with tarfile.open(tmp_path) as tar:
                    try:
                        tar.extractall(dest, filter="data")
                    except TypeError:  # python < 3.12: no filter kwarg
                        tar.extractall(dest)
            finally:
                os.unlink(tmp_path)
            found = True
    if not found:
        raise ValueError(f"no data.tar member in {deb}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, sha256: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as out:
            shutil.copyfileobj(resp, out)
    except Exception as exc:  # offline, proxy, 404 after a pool refresh
        warn(f"fakeroot download failed: {url}")
        warn(f"  {exc}")
        return False
    got = _sha256(dest)
    if got != sha256:
        warn(f"fakeroot checksum mismatch for {url}")
        warn(f"  expected {sha256}")
        warn(f"  got      {got}")
        return False
    return True


def _write_wrapper(bindir: Path, libdir: Path) -> None:
    body = WRAPPER.replace("__LIBDIR__", str(libdir)).replace("__BINDIR__", str(bindir))
    wrapper = bindir / "fakeroot"
    wrapper.write_text(body)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # Apptainer's FindFake() prefers fakeroot-sysv; make both names work.
    link = bindir / "fakeroot-sysv"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to("fakeroot")


def _install_from_debs(bin_deb: Path, lib_deb: Path, dest: Path) -> None:
    """Lay out dest/ the way Apptainer's fakeroot support expects."""
    tmp = dest.parent / f".{dest.name}.extract"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        extract_deb(bin_deb, tmp)
        extract_deb(lib_deb, tmp)
        faked = next(tmp.rglob("faked-sysv"), None)
        lib = next(tmp.rglob("libfakeroot-sysv.so"), None)
        if faked is None or lib is None:
            raise ValueError("deb did not contain faked-sysv/libfakeroot-sysv.so")
        bindir = dest / "bin"
        libdir = dest / "lib"
        bindir.mkdir(parents=True, exist_ok=True)
        libdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(faked, bindir / "faked-sysv")
        faked.chmod(faked.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        shutil.copy2(lib, libdir / "libfakeroot-sysv.so")
        _write_wrapper(bindir, libdir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def smoke_test(root: Path) -> bool:
    """True when fakeroot in `root` fakes root convincingly on this host.

    Both halves matter for an image build: getuid()/chown() faking is what
    apt/dpkg need, and stat() interposition is what keeps ownership visible
    to mksquashfs. A libfakeroot built for a pre-2.33 glibc fakes the former
    but not the latter on modern hosts — that must disqualify a candidate,
    not silently produce a wrong image.
    """
    bindir = root / "bin"
    libdir = root / "lib"
    with tempfile.TemporaryDirectory(prefix="agentic-fakeroot-smoke-") as tmp:
        probe = Path(tmp) / "probe"
        probe.write_text("")
        script = (
            f'chown 0:0 "{probe}" && [ "$(stat -c %u:%g "{probe}")" = "0:0" ] && [ "$(id -u)" = "0" ]'
        )
        cmd = [
            str(bindir / "fakeroot"),
            "-l", str(libdir / "libfakeroot-sysv.so"),
            "-f", str(bindir / "faked-sysv"),
            "--", "sh", "-c", script,
        ]
        try:
            return subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False


def ensure_fakeroot() -> Path | None:
    """A working fakeroot dir for this host, provisioning it if needed.

    Returns None when nothing could be provisioned (offline, unsupported
    platform, or every candidate failed its smoke test); the caller then
    keeps the previous best-effort/guidance behavior.
    """
    if sys.platform != "linux":
        return None

    cached = cached_fakeroot_dir()
    if cached is not None:
        os.environ[FAKEROOT_DIR_ENV] = str(cached)
        return cached

    arch = _arch()
    if arch not in ("amd64", "arm64"):
        return None
    say("No fakeroot on this host; provisioning a private one (userspace, no admin needed)")
    for cand in _CANDIDATES:
        base = cand.get(f"base_{arch}")
        if not base:
            continue
        root = CACHE_DIR
        tmp_dir = root.parent / f".{root.name}.dl"
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True)
        bin_deb = tmp_dir / "fakeroot.deb"
        lib_deb = tmp_dir / "libfakeroot.deb"
        try:
            url_bin = f"{base}/{cand['file'].format(arch=arch)}"
            url_lib = f"{base}/{cand['lib_file'].format(arch=arch)}"
            if not _download(url_bin, bin_deb, cand[f"sha256_{arch}"]):
                continue
            if not _download(url_lib, lib_deb, cand[f"lib_sha256_{arch}"]):
                continue
            try:
                _install_from_debs(bin_deb, lib_deb, root)
            except Exception as exc:
                warn(f"fakeroot candidate {cand['name']} unusable: {exc}")
                continue
            if not smoke_test(root):
                warn(f"fakeroot candidate {cand['name']} failed its smoke test ({cand['note']})")
                continue
            root.joinpath(SMOKE_STAMP).write_text(cand["name"] + "\n")
            say(f"fakeroot ready: {root} ({cand['name']})")
            os.environ[FAKEROOT_DIR_ENV] = str(root)
            return root
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    return None


def build_env_with_fakeroot(
    env: dict[str, str] | None = None, fakeroot_dir: Path | None = None
) -> dict[str, str]:
    """A copy of `env` (or os.environ) with the private fakeroot first on PATH.

    An explicit dir wins (the rescue retry); otherwise the cross-process
    memo decides (children of a process that already provisioned it).
    """
    out = dict(os.environ if env is None else env)
    root = str(fakeroot_dir) if fakeroot_dir else out.get(FAKEROOT_DIR_ENV) or os.environ.get(
        FAKEROOT_DIR_ENV
    )
    if root:
        bindir = str(Path(root) / "bin")
        if not out.get("PATH", "").startswith(bindir):
            out["PATH"] = bindir + os.pathsep + out.get("PATH", "")
        out[FAKEROOT_DIR_ENV] = root
    return out
