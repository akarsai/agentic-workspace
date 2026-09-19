"""The `uninstall` subcommand: remove an installed child instance setup."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config as cfgmod
from .manifest import load_manifest
from .paths import resolve_agent_root
from .util import die, say


def main(argv: list[str], agent_root: Path | None = None) -> int:
    agent_root = agent_root or resolve_agent_root()
    if agent_root is None:
        return die("cannot locate manifest.yaml (set AGENT_ROOT or run from an instance directory).")
    manifest = load_manifest(agent_root / "manifest.yaml")
    name = manifest.name

    install_dir = Path.home() / ".local" / "share" / name
    bin_dir = Path.home() / ".local" / "bin"
    purge = True
    assume_yes = False
    args = list(argv)
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--install-dir":
            if i + 1 >= len(args):
                return die("--install-dir requires a value")
            install_dir = Path(args[i + 1])
            i += 1
        elif a == "--bin-dir":
            if i + 1 >= len(args):
                return die("--bin-dir requires a value")
            bin_dir = Path(args[i + 1])
            i += 1
        elif a == "--keep-state":
            purge = False
        elif a == "--yes":
            assume_yes = True
        elif a in ("--help", "-h"):
            print(
                f"Usage:\n"
                f"  {name} --uninstall [OPTIONS]\n"
                f"\n"
                f"Options:\n"
                f"  --install-dir DIR  Remove checkout from DIR\n"
                f"  --bin-dir DIR      Remove symlink from DIR\n"
                f"  --keep-state       Keep config, local state, and docker image\n"
                f"  --yes              Skip confirmation prompt\n"
                f"  --help             Show this help"
            )
            return 0
        else:
            return die(f"unknown option: {a}")
        i += 1

    link = bin_dir / name
    print(f"This will uninstall {name}.")
    print(f"  Install dir: {install_dir}")
    print(f"  Bin symlink: {link}")
    if purge:
        print("  Will also remove config, local state, and docker image.")
    print()

    if not assume_yes:
        try:
            ans = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            ans = "n"
        if not ans.startswith("y"):
            print("Aborted.")
            return 0

    if link.is_symlink() or link.exists():
        link.unlink()
        print(f"Removed symlink: {link}")

    if install_dir.is_dir():
        shutil.rmtree(install_dir)
        print(f"Removed: {install_dir}")

    if purge:
        config_file = cfgmod.config_path(name)
        if config_file.is_file():
            config_file.unlink()
            print(f"Removed: {config_file}")
        state_root = Path.home() / ".cache" / name
        if state_root.is_dir():
            shutil.rmtree(state_root)
            print(f"Removed: {state_root}")
        try:
            subprocess.run(["docker", "rmi", manifest.image], check=True, capture_output=True)
            print(f"Removed image: {manifest.image}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    print()
    print("Uninstall complete.")
    return 0
