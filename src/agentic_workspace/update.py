"""The `update` subcommand: pull the monorepo, refresh pinned tool versions,
rebuild base + instance images.

Without --name it updates only the instances selected by `install` (the
clone-local .agentic-instances marker). When --apptainer/--docker is passed
explicitly, the instances' configs are switched to that runtime so the
launcher keeps using the runtime the images were just rebuilt with. With no
runtime flag the runtime comes from the instances' configs (falling back to
what is installed), so a plain `update` on an Apptainer install does not
try to rebuild with a Docker that was never there.

On hosts where apptainer cannot build unprivileged (no root, no fakeroot
mapping, no user namespaces) update degrades to best effort: an existing
SIF is kept with a warning instead of failing the update — see
container_build.BEST_EFFORT_ENV. Explicit build commands still fail loudly.
A build that fails outright (nothing to keep) makes update exit nonzero.

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
from . import interactive
from . import oci
from . import tool_versions
from .paths import instances_dir, repo_root
from .util import die, run_with_apptainer_fallback, say, warn

DEFAULT_BIN_DIR = Path.home() / ".local" / "bin"


def sync_runtime_in_config(name: str, runtime: str) -> None:
    cfg = cfgmod.load(name)
    if not cfg:
        return
    cfg["AGENTIC_CONTAINER_RUNTIME"] = runtime
    cfgmod.write(name, cfg)
    say(f"  config: {cfgmod.config_path(name)} (runtime -> {runtime})")


def resolved_runtime(names: list[str]) -> tuple[str, str]:
    """(runtime, source) for a flag-less update: what the instances are
    configured with, else whatever is installed (docker preferred, apptainer
    on docker-less Linux hosts). Keeps a plain `update` from rebuilding an
    Apptainer install with a Docker that is not there."""
    for name in names:
        runtime = (cfgmod.load(name) or {}).get("AGENTIC_CONTAINER_RUNTIME", "")
        if runtime in ("docker", "apptainer"):
            return runtime, f"{name}'s config"
    return oci.detect_runtime(), "what is installed"


def rebuild_instance(name: str, runtime: str, bin_dir: Path) -> int:
    launcher = bin_dir / name
    if not (launcher.is_symlink() or launcher.exists()):
        say(f"Launcher {name} not installed; skipping.")
        return 0
    say(f"Rebuilding {name}...")
    if runtime == "apptainer":
        rc = run_with_apptainer_fallback([str(launcher), "--apptainer", "--build"])
    else:
        rc = subprocess.call([str(launcher), "--build"])
    if rc != 0:
        warn(f"{name} image build failed (run '{name} --build' to see errors)")
    return rc


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
    failing the update. The affected tool then rebuilds at its pinned (or,
    if never pinned, build-time 'latest') version.
    """
    say(f"Refreshing tool versions ({tool_versions.versions_file().relative_to(repo_root())})")
    result = tool_versions.refresh_versions()
    for key, (old, new) in sorted(result.changed.items()):
        name = tool_versions.DISPLAY.get(key, key)
        if not old:
            say(f"  {name:<12} pinned {new}")
        elif old == new:
            # same version, new/changed download checksums: nothing moved,
            # the pin just verifies the download now
            say(f"  {name:<12} {new} (checksums pinned)")
        else:
            say(f"  {name:<12} {old} -> {new}")
    if result.unchanged:
        names = ", ".join(sorted(tool_versions.DISPLAY.get(k, k) for k in result.unchanged))
        say(f"  already latest: {names}")
    for key in result.failed:
        name = tool_versions.DISPLAY.get(key, key)
        warn(f"  could not resolve latest {name}; keeping previous pin")
    if result.changed:
        bumped = {k: v for k, v in result.changed.items() if v[0] != v[1]}
        checksum_only = {k: v for k, v in result.changed.items() if v[0] == v[1]}
        if checksum_only:
            say(
                "  checksum pins updated for: "
                + ", ".join(sorted(tool_versions.DISPLAY.get(k, k) for k in checksum_only))
            )
        if bumped:
            ok_changed = ", ".join(
                sorted(tool_versions.DISPLAY.get(k, k) for k in bumped)
            )
            say(f"  rebuilding with: {ok_changed}")
        else:
            say("  no new upstream versions; rebuilding only to verify downloads")


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
        elif a in ("--runtime", "--tool"):
            if i + 1 >= len(args):
                return die(f"{a} requires a value: docker or apptainer")
            val = args[i + 1]
            if val not in ("docker", "apptainer"):
                return die(f"unsupported runtime '{val}' (expected docker or apptainer)")
            runtime = val
            runtime_flag = True
            i += 1
        elif a == "--name":
            if i + 1 >= len(args):
                return die("--name requires a value")
            only = args[i + 1]
            i += 1
        elif a == "--no-tools":
            no_tools = True
        elif a in ("--help", "-h"):
            print(
                "Usage: ./agentic-workspace update [--apptainer|--docker] [--runtime docker|apptainer] [--name <instance>] [--no-tools]\n"
                "\n"
                "  --apptainer   Build with Apptainer (Linux HPC; no Docker needed)\n"
                "              also switches the instances' config runtime to apptainer\n"
                "  --docker      Build with Docker\n"
                "              also switches the instances' config runtime to docker\n"
                "  --runtime R   Same as --apptainer/--docker, spelled out (--tool also works)\n"
                "  --name <n>    Update only the named instance\n"
                "  --no-tools    Skip the tool-version refresh (offline update)\n"
                "\n"
                "Without a runtime flag, the instances' configured runtime is used\n"
                "(falling back to whatever is installed: Docker, else Apptainer).\n"
                "\n"
                "Keeps everything current: pulls the repo, refreshes the pinned tool\n"
                "versions in blueprint/container/versions.json (claude code, pi, opencode,\n"
                "codex, node, gh, yq, typst, uv, bun, ...), re-pulls the base OS image and\n"
                "rebuilds. A tool only rebuilds when its pin changed, so an up-to-date\n"
                "image costs seconds of cache hits.\n"
                "\n"
                "On hosts where Apptainer cannot build unprivileged (no fakeroot mapping\n"
                "or user namespaces), a previously built image is kept with a warning\n"
                "instead of failing the update; rebuilds then need a host that allows\n"
                "them or an admin-provided fakeroot mapping."
            )
            return 0
        else:
            return die(f"unknown option: {a}")
        i += 1

    bin_dir = Path(os.environ.get("BIN_DIR", str(DEFAULT_BIN_DIR)))

    # Which instances: --name, the install selection, or an interactive pick.
    if only is not None:
        names = [only]
    else:
        names = interactive.read_marker()
        if not names:
            names = interactive.pick_instances("Update")
        if not names:
            warn("nothing selected: nothing to update")
            print()
            print("Update complete.")
            return 0

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

    # An apptainer host that allows no unprivileged builds must not lose its
    # working (older) SIFs over an update: keep them, warn, carry on.
    os.environ[container_build.BEST_EFFORT_ENV] = "1"

    if not runtime_flag:
        runtime, source = resolved_runtime(names)
        say(f"Using runtime: {runtime} (from {source})")

    if only is not None:
        if runtime_flag:
            sync_runtime_in_config(only, runtime)
        say(f"Rebuilding {only}...")
        if runtime == "apptainer":
            rc = run_with_apptainer_fallback([str(bin_dir / only), "--apptainer", "--build"])
        else:
            rc = subprocess.call([str(bin_dir / only), "--build"])
        if rc != 0:
            warn(f"{only} image build failed (run '{only} --build' to see errors)")
            print()
            print("Update finished with build failures.")
            return 1
        print()
        print("Update complete.")
        return 0

    failed = 0
    say("Rebuilding the base image...")
    if runtime == "apptainer":
        if run_with_apptainer_fallback(
            [sys.executable, "-m", "agentic_workspace.container_build", "base", "apptainer", "--force"]
        ) != 0:
            failed = 1
    else:
        if container_build.build_base_image("docker") != 0:
            failed = 1

    for name in names:
        if not (instances_dir() / name).is_dir():
            continue
        if not (bin_dir / name).is_symlink() and not (bin_dir / name).exists():
            say(f"Launcher {name} not installed; skipping.")
            continue
        if runtime_flag:
            sync_runtime_in_config(name, runtime)
        if rebuild_instance(name, runtime, bin_dir) != 0:
            failed = 1

    print()
    if failed:
        print("Update finished with build failures.")
    else:
        print("Update complete.")
    return failed
