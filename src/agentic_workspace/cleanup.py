"""The `clean` subcommand: remove launcher-managed local state conservatively."""
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
    image = manifest.image

    assume_yes = False
    remove_config = False
    remove_image = False
    for a in argv:
        if a == "--yes":
            assume_yes = True
        elif a == "--include-config":
            remove_config = True
        elif a == "--include-image":
            remove_image = True
        elif a == "--all":
            remove_config = True
            remove_image = True
        elif a in ("--help", "-h"):
            print(
                f"Usage:\n"
                f"  {name} --clean [OPTIONS]\n"
                f"\n"
                f"Options:\n"
                f"  --yes             Skip confirmation prompt\n"
                f"  --include-config  Also remove {cfgmod.config_path(name)}\n"
                f"  --include-image   Also remove {image} from Docker if available\n"
                f"  --all             Equivalent to --include-config --include-image\n"
                f"  --help            Show this help\n"
                f"\n"
                f"Default behavior:\n"
                f"  Removes only launcher-managed local state under the configured state root.\n"
                f"  Does not remove project files.\n"
                f"  Does not remove container images unless explicitly requested."
            )
            return 0
        else:
            return die(f"unknown option: {a}")

    cfg = cfgmod.load(name)
    state_root = Path(cfg.get("AGENTIC_STATE_ROOT") or str(Path.home() / ".cache" / name))
    config_file = cfgmod.config_path(name)

    print(f"This will remove launcher-managed local state for {name}.")
    print(f"  State root: {state_root}")
    if remove_config:
        print(f"  Config:     {config_file}")
    if remove_image:
        print(f"  Image:      {image}")
    print()

    if not assume_yes:
        try:
            ans = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            ans = "n"
        if not ans.startswith("y"):
            print("Aborted.")
            return 0

    if state_root.is_dir():
        shutil.rmtree(state_root)
        print(f"Removed: {state_root}")

    if remove_config and config_file.is_file():
        config_file.unlink()
        print(f"Removed: {config_file}")

    if remove_image:
        try:
            subprocess.run(["docker", "rmi", image], check=True, capture_output=True)
            print(f"Removed image: {image}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"Image not present or removal failed: {image}")

    print()
    print("Cleanup complete.")
    return 0
