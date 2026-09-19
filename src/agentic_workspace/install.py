"""The `install` subcommand: one-time interactive installer.

Sets up the launcher symlinks, instance configs, and optionally builds the
container images. In the monorepo the repo IS the install: launchers are
symlinks into instances/, so there are no stale snapshots to refresh.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod
from . import oci
from .manifest import list_instances, load_manifest
from .paths import instances_dir, marker_file, repo_root
from .util import banner, bold, confirm, die, dim, info, ok, run_with_apptainer_fallback, say, warn

BIN_DIR_DEFAULT = Path.home() / ".local" / "bin"


def write_marker(names: list[str]) -> None:
    marker = marker_file()
    lines = ["# instances selected by install (clone-local, gitignored)\n"]
    lines += [f"{n}\n" for n in names]
    marker.write_text("".join(lines))
    dim(f"  selection: {marker}")


def add_to_marker(name: str) -> None:
    existing: list[str] = []
    marker = marker_file()
    if marker.is_file():
        for line in marker.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                existing.append(line)
    if name not in existing:
        write_marker(existing + [name])


def write_config(name: str, runtime: str, brain_dir: str | None = None) -> Path:
    values = {
        "AGENTIC_CONTAINER_RUNTIME": runtime,
        "AGENTIC_AUTH_MODE": "tool",
        "AGENTIC_API_PROVIDER": "",
        "AGENTIC_API_KEY_ENV": "",
        "AGENTIC_CUSTOM_ENDPOINT": "",
        "AGENTIC_CUSTOM_ANTHROPIC_ENDPOINT": "",
        "AGENTIC_CLI_TOOL": "pi",
        "AGENTIC_MODEL_SUBSCRIPTION": "",
        "AGENTIC_DEFAULT_MODEL": "",
        "AGENTIC_HTTPS_PROXY": "",
        "AGENTIC_HTTP_PROXY": "",
        "AGENTIC_STATE_ROOT": f"{Path.home() / '.cache' / name}",
        "AGENTIC_EXTRA_BIND_DIRS": "",
    }
    path = cfgmod.write(name, values)
    say(f"  config: {path}")
    return path


def install_launcher(name: str, instance: Path, bin_dir: Path) -> Path:
    wrapper = instance / name
    link = bin_dir / name
    bin_dir.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(wrapper)
    ok(f"launcher: {link} -> {wrapper}")
    return link


def install_one(
    name: str, runtime: str, bin_dir: Path, build: bool, env: dict[str, str]
) -> tuple[bool, bool]:
    """Returns (built_images, deferred_build)."""
    instance = instances_dir() / name
    if not (instance / "manifest.yaml").is_file():
        warn(f"instance '{name}' not found under instances/; skipping.")
        return False, False
    say(f"Installing {name}:")
    write_config(name, runtime)
    launcher = install_launcher(name, instance, bin_dir)

    if not build:
        return False, True

    if runtime == "apptainer":
        info("building image with Apptainer...")
        ok_ = run_with_apptainer_fallback([str(launcher), "--apptainer", "--build"]) == 0
        return ok_, not ok_
    if oci.docker_available():
        info("building image...")
        ok_ = subprocess.call([str(launcher), "--build"]) == 0
        return ok_, not ok_
    return False, True


def install_update_helper(bin_dir: Path) -> None:
    """Create the ~/.local/bin/agentic-update helper pointing at the repo.

    Replaces any existing entry first: the old installer created the helper as
    a symlink to update.sh, and writing through a stale symlink would recreate
    update.sh in the repo instead of the helper file.
    """
    helper = bin_dir / "agentic-update"
    if helper.is_symlink() or helper.exists():
        helper.unlink()
    body = (
        "#!/usr/bin/env python3\n"
        "import subprocess, sys\n"
        f"sys.exit(subprocess.call([sys.executable, {str(repo_root() / 'agentic-workspace')!r}, 'update', *sys.argv[1:]]))\n"
    )
    helper.write_text(body)
    helper.chmod(0o755)
    ok(f"helper: {helper} -> agentic-workspace update")


def main(argv: list[str]) -> int:
    env = os.environ.copy()
    runtime = "docker"
    install_one_name: str | None = None
    build = True

    args = list(argv)
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--apptainer", "apptainer"):
            runtime = "apptainer"
        elif a in ("--docker", "docker"):
            runtime = "docker"
        elif a == "--name":
            if i + 1 >= len(args):
                return die("--name requires a value")
            install_one_name = args[i + 1]
            i += 1
        elif a == "--no-build":
            build = False
        elif a in ("--help", "-h"):
            print(
                "Usage: ./agentic-workspace install [--apptainer|--docker] [--name <instance>] [--no-build]\n"
                "\n"
                "  --apptainer   Build with Apptainer (Linux HPC; no Docker needed)\n"
                "  --docker      Build with Docker (default)\n"
                "  --name <n>    Install one instance non-interactively\n"
                "  --no-build    Skip image builds during install"
            )
            return 0
        else:
            return die(f"unknown option: {a}")
        i += 1

    bin_dir = Path(env.get("BIN_DIR", str(BIN_DIR_DEFAULT)))

    if install_one_name is not None:
        _built, deferred = install_one(install_one_name, runtime, bin_dir, build, env)
        add_to_marker(install_one_name)
        print()
        if deferred:
            if runtime == "apptainer":
                ok(f"Done. Next, build the image: {install_one_name} --apptainer --build")
            else:
                ok(f"Done. Next, build the image: {install_one_name} --build")
            say(f"  Then launch: {install_one_name} ~/your-project")
        else:
            ok(f"Done (image built). Run: {install_one_name} ~/your-project")
        return 0

    # Interactive
    banner("agentic-workspace — installer", bold=True)
    print()
    info(f"Repo: {repo_root()}")
    print()

    if runtime == "apptainer" and not oci.apptainer_available():
        warn("apptainer not found on PATH.")
        say("You can install instance configs/launchers, but image builds")
        say("will not work until Apptainer is available.")
    elif runtime == "docker" and not oci.docker_available():
        warn("docker not found on PATH.")
        say("You can install instances later with the launcher, but image builds")
        say("will not work until Docker is available.")
    else:
        ok(f"{runtime} found")
    print()

    available = list_instances(instances_dir())
    if not available:
        warn(f"no instances found under {instances_dir()}")
        return 1
    bold(f"Instances found: {' '.join(available)}")
    print()

    selected: list[str] = []
    if confirm("Install all instances?", "Y/n"):
        selected = list(available)
    else:
        for name in available:
            if confirm(f"Install {name}?", "y/N"):
                selected.append(name)

    if not selected:
        warn("Nothing selected — nothing installed.")
        return 0

    print()
    bold(f"Installing: {' '.join(selected)}")
    built_any = False
    deferred = False
    for name in selected:
        b, d = install_one(name, runtime, bin_dir, build, env)
        built_any = built_any or b
        deferred = deferred or d
        print()

    write_marker(selected)

    if confirm("Install the 'agentic-update' helper (~/.local/bin/agentic-update)?", "Y/n"):
        install_update_helper(bin_dir)

    print()
    if str(bin_dir) in env.get("PATH", "").split(":"):
        ok(f"PATH already includes {bin_dir}")
    else:
        warn("Add this to your shell profile:")
        say(f"  export PATH=\"{bin_dir}:$PATH\"")
    print()
    bold("Next:")
    if deferred:
        if runtime == "apptainer":
            say(f"  1. Build the images:   ./agentic-workspace build --apptainer   (or: <name> --apptainer --build for one)")
        else:
            say("  1. Build the images:   ./agentic-workspace build        (or: <name> --build for one)")
            if not oci.docker_available() and oci.apptainer_available():
                say("       On HPC without Docker:  ./agentic-workspace build --apptainer")
        say("  2. Launch:             <name> ~/your-project")
    else:
        say("  1. Launch:             <name> ~/your-project   (images built during install)")
        say("  2. Rebuild later:      ./agentic-workspace build")
    say("  3. Update everything:  ./agentic-workspace update")
    return 0
