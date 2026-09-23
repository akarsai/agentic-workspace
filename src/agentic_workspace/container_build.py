"""Container image builds (base + instance layers).

Shared by the `build` subcommand and the standalone
blueprint/container/build_base.py / build_image.py scripts.

Base image policy:
- Docker: always build — unchanged layers hit Docker's cache, so an up-to-date
  base costs a few seconds and changed COPY/entrypoint layers are picked up.
- Apptainer: SIF builds have no layer cache, so explicit build commands
  (`build`, `update`, `<launcher> --build`, build_base.py) force a fresh base
  build — `apptainer build --force` only overwrites the output file, so a
  fingerprint-style skip would keep serving a stale SIF. The fingerprint file
  survives as a memo for prerequisite checks: build_instance_image skips the
  (minutes-long) base rebuild when the recorded inputs are unchanged, so
  `build --apptainer` rebuilds the shared base once, not once per instance.

Unprivileged builds: many HPC sites allow neither root nor fakeroot nor
user namespaces, so `apptainer build` from a definition file dies with
"Building from a definition file requires root or some kind of fake root".
When a previous SIF exists, `update` (which sets AGENTIC_BEST_EFFORT_BUILD=1)
keeps it and continues instead of failing the whole update; explicit build
commands still fail loudly with admin-facing guidance, because there the
user asked for a rebuild and must not be handed a stale image in silence.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from . import oci
from .manifest import load_manifest
from .paths import container_dir
from .tool_versions import build_arg_flags
from .util import warn

BASE_IMAGE_DEFAULT = "agentic-blueprint:base"
FINGERPRINT_FILES = ("Dockerfile.base", "entrypoint.py", "dockerfile_to_def.py", "build_base.py", "versions.json")
# Directories COPY'd into the base image (see Dockerfile.base) — they are
# build inputs just like the recipe files, so a change to any of them must
# mark a previously built SIF as stale.
FINGERPRINT_DIRS = ("extensions", "agents")

# apptainer/singularity FATAL lines for "cannot build unprivileged": matched
# against the captured build output so the failure can be explained (and, for
# update, degraded gracefully) instead of left as a bare FATAL.
_PRIVILEGE_FAILURE_MARKERS = (
    "requires root or some kind of fake root",  # apptainer >= 1.1
    "requires root privileges",                # older apptainer
    "you must be root to build",               # singularity <= 3.x
)

# Set by `update` (and inherited by every build subprocess it spawns): on a
# privilege failure, keep a previously built SIF and carry on with a warning
# instead of failing the update. Explicit build commands leave it unset.
BEST_EFFORT_ENV = "AGENTIC_BEST_EFFORT_BUILD"

# The guidance block is long; once per process is plenty (update and each
# launcher-driven build are separate processes, so it stays visible).
_privilege_guidance_shown = False


def _base_image() -> str:
    return os.environ.get("AGENTIC_BASE_IMAGE", BASE_IMAGE_DEFAULT)


def _apptainer_artifacts() -> tuple[Path, Path]:
    app_dir = container_dir() / ".apptainer"
    return app_dir / "agentic-blueprint-base.sif", app_dir / "agentic-blueprint-base.fingerprint"


def _base_build_inputs() -> list[Path]:
    """Every file that feeds the base-image build, in a stable order."""
    inputs = [f for f in (container_dir() / name for name in FINGERPRINT_FILES) if f.is_file()]
    for d in FINGERPRINT_DIRS:
        inputs.extend(
            p for p in sorted((container_dir() / d).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        )
    return inputs


def base_fingerprint() -> str:
    """Stable content hash of the base-image build inputs (recipe files plus
    everything COPY'd into the image), keyed by repo-relative path."""
    h = hashlib.sha256()
    root = container_dir()
    for f in _base_build_inputs():
        h.update(str(f.relative_to(root)).encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _base_needs_rebuild(sif: Path, fingerprint_file: Path) -> bool:
    """True when the SIF is missing or was built from different inputs."""
    if not sif.is_file() or not fingerprint_file.is_file():
        return True
    return fingerprint_file.read_text().strip() != base_fingerprint()


def _run_apptainer_build(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run `apptainer build`, echoing its output while collecting it, so a
    privilege FATAL can be recognised afterwards. stderr is merged in: that
    is where apptainer writes its INFO/FATAL lines."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
        chunks.append(line)
    rc = proc.wait()
    return rc, "".join(chunks)


def _is_privilege_failure(output: str) -> bool:
    return any(marker in output for marker in _PRIVILEGE_FAILURE_MARKERS)


def _print_privilege_guidance(sif: Path | None = None) -> None:
    """Explain the unprivileged-build FATAL and what unblocks it."""
    global _privilege_guidance_shown
    if _privilege_guidance_shown:
        return
    _privilege_guidance_shown = True
    warn("Apptainer cannot build from a definition file as your user on this host:")
    warn("it needs root, a fakeroot mapping, or unprivileged user namespaces, and none worked.")
    print()
    print("Ask your cluster admin for one of:")
    print("  - a fakeroot mapping for your user (adds /etc/subuid + /etc/subgid entries):")
    print("      sudo apptainer config fakeroot --add $USER   # needs the 'uidmap' package")
    print("  - enabled unprivileged user namespaces (root-mapped builds), or")
    print("  - the 'fakeroot' package installed on the host")
    print("Alternatives (no admin needed):")
    print("  - build the image on a host that allows it (e.g. with Docker) and copy the SIF over")
    if sif is not None:
        print(f"      expected here: {sif}")


def _handle_build_failure(rc: int, output: str, sif: Path) -> int:
    """Common tail for a failed apptainer build: explain privilege failures,
    and in best-effort mode (update) keep a previously built SIF."""
    if _is_privilege_failure(output):
        _print_privilege_guidance(sif)
        if os.environ.get(BEST_EFFORT_ENV) == "1" and sif.is_file():
            warn(f"keeping the previous image: {sif}")
            warn("  it still runs, but this update could not rebuild it on this host")
            return 0
    return rc


def build_base_image(runtime: str, *, force: bool = False) -> int:
    if runtime == "apptainer":
        if not oci.is_linux():
            warn(f"Apptainer builds are only supported on Linux hosts. Current host: {os.uname().sysname}")
            return 1
        if not oci.apptainer_available():
            warn("'apptainer' is not installed or not on PATH.")
            return 1
        sif, fingerprint_file = _apptainer_artifacts()
        # `force` comes from explicit build commands: rebuild from scratch even
        # when the fingerprint is unchanged. Without it this is a prerequisite
        # check (see module docstring) — skip only when inputs are unchanged.
        if not force and not _base_needs_rebuild(sif, fingerprint_file):
            print(f"Apptainer base image up to date: {sif}")
            return 0
        sif.parent.mkdir(parents=True, exist_ok=True)
        print(f"Building base image: {sif}")
        def_file = sif.with_suffix(".sif.def")
        with open(def_file, "w") as out:
            subprocess.run(
                [sys.executable, str(container_dir() / "dockerfile_to_def.py"), "--mode", "base",
                 *build_arg_flags(), str(container_dir() / "Dockerfile.base")],
                stdout=out,
                check=True,
            )
        # Apptainer resolves %files COPY sources relative to the build CWD, so
        # run from the Dockerfile's context directory.
        rc, output = _run_apptainer_build(
            ["apptainer", "build", "--force", str(sif), str(def_file)],
            cwd=container_dir(),
        )
        def_file.unlink(missing_ok=True)
        if rc != 0:
            return _handle_build_failure(rc, output, sif)
        fingerprint_file.write_text(base_fingerprint() + "\n")
        print()
        print(f"Apptainer base image built: {sif}")
        return 0

    if not oci.docker_available():
        warn("'docker' is not installed or not on PATH.")
        return 1
    image = _base_image()
    print(f"Building base image: {image} (cached layers when unchanged)")
    cmd = ["docker", "build"]
    # --pull refreshes the FROM image (ubuntu:26.04) so apt packages do not
    # linger at whatever the local cache happened to have. update sets this;
    # plain `build` stays offline-friendly and works from cache alone.
    if os.environ.get("AGENTIC_DOCKER_PULL") == "1":
        cmd.append("--pull")
    cmd += [*build_arg_flags(), "-t", image, "-f", str(container_dir() / "Dockerfile.base"), str(container_dir())]
    rc = subprocess.call(cmd)
    print()
    print(f"Docker base image built: {image}")
    return rc


def build_instance_image(instance: Path, runtime: str) -> int:
    manifest_path = instance / "manifest.yaml"
    if not manifest_path.is_file():
        warn(f"cannot locate manifest.yaml at {manifest_path}")
        return 1
    manifest = load_manifest(manifest_path)
    name = manifest.name
    image = manifest.image

    # Proxy: config -> environment.
    from . import config as cfgmod

    cfg = cfgmod.load(name)
    proxy = cfg.get("AGENTIC_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY")
    env = os.environ.copy()
    if proxy:
        env["https_proxy"] = proxy
        env["http_proxy"] = cfg.get("AGENTIC_HTTP_PROXY") or os.environ.get("HTTP_PROXY") or proxy

    if runtime == "apptainer":
        if not oci.is_linux():
            warn(f"Apptainer builds are only supported on Linux hosts. Current host: {os.uname().sysname}")
            warn(f"  Use Docker on this machine: {name} --build")
            return 1
        if not oci.apptainer_available():
            warn("'apptainer' is not installed or not on PATH.")
            print()
            print(f"Install Apptainer on the Linux host, then rerun:")
            print(f"  {name} --apptainer --build")
            return 1
        state_root = Path(cfg.get("AGENTIC_STATE_ROOT", str(Path.home() / ".cache" / name)))
        cache = state_root / "apptainer_cache"
        tmp = state_root / "apptainer_tmp"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
        env.setdefault("APPTAINER_CACHEDIR", str(cache))
        env.setdefault("APPTAINER_TMPDIR", str(tmp))

        rc = build_base_image("apptainer")
        if rc != 0:
            return rc

        sif_dir = instance / ".apptainer"
        sif_dir.mkdir(parents=True, exist_ok=True)
        sif = sif_dir / f"{name}.sif"
        def_file = sif_dir / f"{name}.def"
        print(f"Building Apptainer container image: {name}")
        print("This may take several minutes on first build.")
        print()
        base_sif, _ = _apptainer_artifacts()
        with open(def_file, "w") as out:
            subprocess.run(
                [sys.executable, str(container_dir() / "dockerfile_to_def.py"),
                 "--mode", "instance", "--base-sif", str(base_sif),
                 "--default-entrypoint", "/entrypoint.py",
                 *build_arg_flags(), str(instance / "container" / "Dockerfile")],
                stdout=out,
                check=True,
            )
        rc, output = _run_apptainer_build(
            ["apptainer", "build", "--force", str(sif), str(def_file)],
            cwd=instance / "container",
            env=env,
        )
        def_file.unlink(missing_ok=True)
        if rc != 0:
            return _handle_build_failure(rc, output, sif)
        print()
        print(f"Apptainer image built: {sif}")
        print()
        print(f"Run with: {instance / name}")
        return 0

    if not oci.docker_available():
        warn("'docker' is not installed or not on PATH.")
        print()
        print("On a Linux HPC without Docker, try Apptainer:")
        print(f"  {name} --apptainer --build")
        return 1

    rc = build_base_image("docker")
    if rc != 0:
        return rc
    print(f"Building container image: {image}")
    rc = subprocess.call(
        ["docker", "build", *build_arg_flags(), "-t", image, "-f", str(instance / "container" / "Dockerfile"),
         str(instance / "container")],
        env=env,
    )
    print()
    print(f"Docker image built: {image}")
    print()
    print(f"Run with: {instance / name}")
    return rc


def main() -> None:
    """CLI entry: python -m agentic_workspace.container_build (base|instance) RUNTIME [INSTANCE_DIR] [--force].

    Used by build/update so the apptainer `module load` fallback can re-run the
    whole build inside a fresh bash login shell. `--force` makes the base step
    rebuild even when the recorded fingerprint is unchanged.
    """
    force = "--force" in sys.argv[1:]
    argv = [a for a in sys.argv[1:] if a != "--force"]
    if not argv:
        print("usage: container_build (base|instance) (docker|apptainer) [--force] [instance-dir]", file=sys.stderr)
        sys.exit(1)
    what = argv[0]
    runtime = argv[1] if len(argv) > 1 else "docker"
    if what == "base":
        sys.exit(build_base_image(runtime, force=force))
    if len(argv) < 3:
        print("usage: container_build instance RUNTIME INSTANCE_DIR", file=sys.stderr)
        sys.exit(1)
    sys.exit(build_instance_image(Path(argv[2]), runtime))


if __name__ == "__main__":
    main()
