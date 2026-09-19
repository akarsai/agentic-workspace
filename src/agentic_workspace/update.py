"""The `update` subcommand: pull the monorepo, refresh pinned tool versions,
rebuild base + instance images.

Without --name it updates only the instances selected by `install` (the
clone-local .agentic-instances marker). When --apptainer/--docker is passed
explicitly, the instances' configs are switched to that runtime so the
launcher keeps using the runtime the images were just rebuilt with.

Tool freshness: rebuilding alone never moves a tool inside an image (Docker
freezes a RUN layer until its command text changes), so update first
refreshes blueprint/container/versions.json from upstream (npm registry,
GitHub releases, the Node.js index) and the builds consume those pins as
build args. --no-tools skips the refresh (offline updates, say).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod
from . import container_build
from . import tool_versions
from .paths import instances_dir, marker_file, repo_root
from .util import die, run_with_apptainer_fallback, say, warn

DEFAULT_BIN_DIR = Path.home() / ".local" / "bin"


def read_default_instances() -> list[str]:
    marker = marker_file()
    if not marker.is_file():
        return []
    names = []
    for line in marker.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def sync_runtime_in_config(name: str, runtime: str) -> None:
    cfg = cfgmod.load(name)
    if not cfg:
        return
    cfg["AGENTIC_CONTAINER_RUNTIME"] = runtime
    cfgmod.write(name, cfg)
    say(f"  config: {cfgmod.config_path(name)} (runtime -> {runtime})")


def rebuild_instance(name: str, runtime: str, bin_dir: Path) -> None:
    launcher = bin_dir / name
    if not (launcher.is_symlink() or launcher.exists()):
        say(f"Launcher {name} not installed; skipping.")
        return
    say(f"Rebuilding {name}...")
    if runtime == "apptainer":
        rc = run_with_apptainer_fallback([str(launcher), "--apptainer", "--build"])
    else:
        rc = subprocess.call([str(launcher), "--build"])
    if rc != 0:
        warn(f"{name} image build failed (run '{name} --build' to see errors)")


def apply_build_proxy(names: list[str]) -> None:
    """Export the first configured instance proxy so version lookups, git and
    the builds all honor it (mirrors what build_instance_image does)."""
    for name in names:
        cfg = cfgmod.load(name)
        proxy = (cfg or {}).get("AGENTIC_HTTPS_PROXY", "")
        if proxy:
            os.environ.setdefault("https_proxy", proxy)
            os.environ.setdefault("http_proxy", (cfg or {}).get("AGENTIC_HTTP_PROXY") or proxy)
            return


def refresh_tool_pins() -> None:
    """Resolve latest upstream versions into versions.json and report.

    Failed lookups keep the previous pin (warned per tool) instead of
    failing the update; the affected tool then rebuilds at its pinned — or,
    if never pinned, build-time 'latest' — version.
    """
    say(f"Refreshing tool versions ({tool_versions.versions_file().relative_to(repo_root())})")
    result = tool_versions.refresh_versions()
    for key, (old, new) in sorted(result.changed.items()):
        name = tool_versions.DISPLAY.get(key, key)
        if old:
            say(f"  {name:<12} {old} -> {new}")
        else:
            say(f"  {name:<12} pinned {new}")
    if result.unchanged:
        names = ", ".join(sorted(tool_versions.DISPLAY.get(k, k) for k in result.unchanged))
        say(f"  already latest: {names}")
    for key in result.failed:
        name = tool_versions.DISPLAY.get(key, key)
        warn(f"  could not resolve latest {name}; keeping previous pin")
    if result.changed:
        ok_changed = ", ".join(
            sorted(tool_versions.DISPLAY.get(k, k) for k in result.changed)
        )
        say(f"  rebuilding with: {ok_changed}")


def main(argv: list[str]) -> int:
    runtime = "docker"
    runtime_flag = False
    only: str | None = None
    no_tools = False
    args = list(argv)
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--apptainer", "apptainer"):
            runtime = "apptainer"
            runtime_flag = True
        elif a in ("--docker", "docker"):
            runtime = "docker"
            runtime_flag = True
        elif a == "--name":
            if i + 1 >= len(args):
                return die("--name requires a value")
            only = args[i + 1]
            i += 1
        elif a == "--no-tools":
            no_tools = True
        elif a in ("--help", "-h"):
            print(
                "Usage: ./agentic-workspace update [--apptainer|--docker] [--name <instance>] [--no-tools]\n"
                "\n"
                "  --apptainer   Build with Apptainer (Linux HPC; no Docker needed)\n"
                "              also switches the instances' config runtime to apptainer\n"
                "  --docker      Build with Docker (default)\n"
                "              also switches the instances' config runtime to docker\n"
                "  --name <n>    Update only the named instance\n"
                "  --no-tools    Skip the tool-version refresh (offline update)\n"
                "\n"
                "Keeps everything current: pulls the repo, refreshes the pinned tool\n"
                "versions in blueprint/container/versions.json (claude code, pi, opencode,\n"
                "codex, node, gh, yq, typst, uv, bun, ...), re-pulls the base OS image and\n"
                "rebuilds. A tool only rebuilds when its pin changed, so an up-to-date\n"
                "image costs seconds of cache hits."
            )
            return 0
        else:
            return die(f"unknown option: {a}")
        i += 1

    bin_dir = Path(os.environ.get("BIN_DIR", str(DEFAULT_BIN_DIR)))

    if only is None and not read_default_instances():
        warn("no .agentic-instances marker found (run ./agentic-workspace install to pick instances);")
        warn("nothing to update — name an instance explicitly, e.g. ./agentic-workspace update --name agre")
        print()
        print("Update complete.")
        return 0

    names = [only] if only is not None else read_default_instances()

    print(f"Updating agentic-workspace ({repo_root()})")
    try:
        subprocess.run(["git", "-C", str(repo_root()), "pull", "--rebase", "--autostash"], check=False)
    except FileNotFoundError:
        warn("git not found; skipping pull")

    if not no_tools:
        apply_build_proxy(names)
        refresh_tool_pins()

    # Refresh the FROM image too (apt packages), not just the tool layers.
    os.environ["AGENTIC_DOCKER_PULL"] = "1"

    if only is not None:
        if runtime_flag:
            sync_runtime_in_config(only, runtime)
        say(f"Rebuilding {only}...")
        if runtime == "apptainer":
            run_with_apptainer_fallback([str(bin_dir / only), "--apptainer", "--build"])
        else:
            subprocess.call([str(bin_dir / only), "--build"])
        return 0

    say("Rebuilding the base image...")
    if runtime == "apptainer":
        run_with_apptainer_fallback(
            [sys.executable, "-m", "agentic_workspace.container_build", "base", "apptainer", "--force"]
        )
    else:
        container_build.build_base_image("docker")

    for name in names:
        if not (instances_dir() / name).is_dir():
            continue
        if not (bin_dir / name).is_symlink() and not (bin_dir / name).exists():
            say(f"Launcher {name} not installed; skipping.")
            continue
        if runtime_flag:
            sync_runtime_in_config(name, runtime)
        rebuild_instance(name, runtime, bin_dir)

    print()
    print("Update complete.")
    return 0
