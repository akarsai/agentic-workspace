"""The `build` subcommand: build the base image and instance images.

With no instance names it builds whatever is recorded in the clone-local
.agentic-instances marker (written by `install`). If the marker is missing, it
warns and builds nothing rather than building every instance in the repo.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import oci
from . import container_build
from . import interactive
from .manifest import load_manifest
from .paths import instances_dir, repo_root
from .util import die, run_with_apptainer_fallback, say, warn

DEFAULT_BIN_DIR = Path.home() / ".local" / "bin"


def ensure_runtime(runtime: str) -> int:
    if oci.which(runtime) is not None:
        if runtime == "apptainer" and not oci.is_linux():
            warn(f"Apptainer is only supported on Linux hosts. Current host: {os.uname().sysname}")
            return 1
        return 0
    # docker missing: offer to fall back to Apptainer (interactive, default yes).
    if runtime == "docker" and oci.apptainer_available() and oci.is_linux():
        warn("docker not found on PATH.")
        try:
            ans = input("Apptainer is available. Build with Apptainer instead? [Y/n] ").strip().lower()
        except EOFError:
            ans = ""
        if ans == "" or ans.startswith("y"):
            say("Falling back to Apptainer.")
            return "apptainer"  # type: ignore[return-value]
    warn(f"{runtime} not found on PATH.")
    return 1


def build_instance(name: str, runtime: str, agent_root: Path | None = None) -> bool:
    instance = instances_dir() / name
    if not (instance / "manifest.yaml").is_file():
        warn(f"instance '{name}' not found under instances/; skipping.")
        return False
    say(f"Building image: {name}")
    if runtime == "apptainer":
        rc = run_with_apptainer_fallback(
            [sys.executable, "-m", "agentic_workspace.container_build", "instance", "apptainer", str(instance)]
        )
    else:
        rc = container_build.build_instance_image(instance, runtime)
    return rc == 0


def main(argv: list[str], agent_root: Path | None = None) -> int:
    runtime = "docker"
    names: list[str] = []
    for a in argv:
        if a in ("--apptainer", "apptainer"):
            runtime = "apptainer"
        elif a in ("--docker", "docker"):
            runtime = "docker"
        elif a in ("--help", "-h"):
            print(
                "Usage: ./agentic-workspace build [--apptainer] [instance...]\n"
                "\n"
                "  --apptainer   Build with Apptainer (Linux HPC; no Docker needed)\n"
                "  --docker      Build with Docker (default)\n"
                "  instance...   Build only the named instances\n"
                "                (default: instances selected by ./agentic-workspace install)"
            )
            return 0
        elif a.startswith("-"):
            return die(f"unknown option: {a}")
        else:
            names.append(a)
        # runtime fallback: ensure_runtime may return "apptainer" (a str)
    rc = ensure_runtime(runtime)
    if isinstance(rc, str):
        runtime = rc
    elif rc != 0:
        return 1

    if runtime == "apptainer":
        base_rc = run_with_apptainer_fallback(
            [sys.executable, "-m", "agentic_workspace.container_build", "base", "apptainer", "--force"]
        )
    else:
        base_rc = container_build.build_base_image("docker")
    if base_rc != 0:
        return base_rc

    if not names:
        # Single instance when launched via `<instance> --build` (AGENT_ROOT set).
        if agent_root is not None:
            names = [load_manifest(agent_root / "manifest.yaml").name]
        else:
            names = interactive.read_marker()
            if not names:
                # Bare build with no install selection: ask.
                names = interactive.pick_instances("Build")
            if not names:
                warn("nothing selected: nothing to build")
                print()
                say("Build complete.")
                return 0

    for name in names:
        build_instance(name, runtime)

    print()
    say("Build complete.")
    return 0
