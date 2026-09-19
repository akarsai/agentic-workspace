"""The `clean` subcommand: remove launcher-managed local state conservatively."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config as cfgmod
from .manifest import load_manifest
from .paths import resolve_agent_root
from .util import die, say, warn


def _show_help() -> None:
    print(
        "Usage:\n"
        "  agentic-workspace clean [NAME] [OPTIONS]\n"
        "  <name> --clean [OPTIONS]\n"
        "\n"
        "Options:\n"
        "  --yes             Skip confirmation prompt\n"
        "  --include-config  Also remove the instance config file\n"
        "  --include-image   Also remove the container image from Docker\n"
        "  --all             Equivalent to --include-config --include-image\n"
        "  --help            Show this help\n"
        "\n"
        "Default behavior:\n"
        "  Removes only launcher-managed local state under the configured state root.\n"
        "  Does not remove project files.\n"
        "  Does not remove container images unless explicitly requested."
    )


def main(argv: list[str], agent_root: Path | None = None) -> int:
    from . import interactive

    if any(a in ("--help", "-h") for a in argv):
        _show_help()
        return 0

    agent_root = agent_root or resolve_agent_root()
    if agent_root is None:
        # Bare CLI: clean [NAME]. Without a name, ask.
        name, argv = interactive.extract_instance_arg(argv)
        if name is None:
            name = interactive.pick_instance("Clean")
            if name is None:
                warn("No instance selected.")
                return 0
        if not interactive.is_instance(name):
            return die(f"instance '{name}' not found under instances/")
        agent_root = interactive.instance_root(name)
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
