"""Tests for the container build drivers (apptainer force-rebuild semantics).

A fake `apptainer` on PATH records every invocation, so the tests can tell a
real rebuild from the fingerprint memo skip without needing Apptainer.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_fake_container_dir(tmp_path: Path) -> Path:
    """Minimal stand-in for blueprint/container (real converter, tiny inputs)."""
    cdir = tmp_path / "container"
    cdir.mkdir()
    real = REPO_ROOT / "blueprint" / "container"
    (cdir / "Dockerfile.base").write_text(
        "FROM ubuntu:26.04\n"
        "COPY extensions/ /opt/pi-extensions/\n"
        "COPY agents/ /opt/pi-extensions/agents/\n"
        "COPY entrypoint.py /entrypoint.py\n"
    )
    (cdir / "entrypoint.py").write_text("# entrypoint\n")
    (cdir / "build_base.py").write_text("# build_base\n")
    (cdir / "dockerfile_to_def.py").write_bytes((real / "dockerfile_to_def.py").read_bytes())
    (cdir / "extensions").mkdir()
    (cdir / "extensions" / "plan.ts").write_text("// ext\n")
    (cdir / "agents").mkdir()
    (cdir / "agents" / "default.md").write_text("# agent\n")
    return cdir


def install_fake_apptainer(tmp_path: Path) -> tuple[Path, Path]:
    """A fake apptainer that logs argv and fakes a successful build."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "apptainer.log"
    script = bin_dir / "apptainer"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "apptainer $@" >> "{log}"\n'
        'if [ "$1" = "build" ]; then touch "$3"; fi\n'
        "exit 0\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir, log


@pytest.fixture()
def build_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Fake container dir + fake apptainer; returns (container_dir, log)."""
    from agentic_workspace import container_build, oci

    cdir = make_fake_container_dir(tmp_path)
    bin_dir, log = install_fake_apptainer(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(container_build, "container_dir", lambda: cdir)
    monkeypatch.setattr(oci, "is_linux", lambda: True)
    monkeypatch.setattr(oci, "apptainer_available", lambda: True)
    return cdir, log


def invocations(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def test_explicit_force_rebuilds_despite_matching_fingerprint(build_env) -> None:
    """force=True re-runs `apptainer build` even when the inputs did not
    change — the fingerprint must not serve a cached SIF to build commands."""
    from agentic_workspace import container_build

    _, log = build_env
    assert container_build.build_base_image("apptainer") == 0
    assert len(invocations(log)) == 1

    # Unforced: prerequisite check may skip when inputs are unchanged.
    assert container_build.build_base_image("apptainer") == 0
    assert len(invocations(log)) == 1

    # Forced: rebuild from scratch.
    assert container_build.build_base_image("apptainer", force=True) == 0
    assert len(invocations(log)) == 2
    assert invocations(log)[1].startswith("apptainer build --force")


def test_fingerprint_covers_copied_dirs(build_env) -> None:
    """extensions/ and agents/ are COPY'd into the base image, so editing a
    file there must mark the built SIF as stale even without force."""
    from agentic_workspace import container_build

    cdir, log = build_env
    assert container_build.build_base_image("apptainer") == 0
    (cdir / "extensions" / "plan.ts").write_text("// ext changed\n")

    assert container_build.build_base_image("apptainer") == 0
    assert len(invocations(log)) == 2


def test_cli_force_flag(build_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """`container_build base apptainer --force` (what build/update run) must
    rebuild even with an up-to-date fingerprint on disk."""
    from agentic_workspace import container_build

    _, log = build_env
    assert container_build.build_base_image("apptainer") == 0
    before = len(invocations(log))

    monkeypatch.setattr(sys, "argv", ["container_build", "base", "apptainer", "--force"])
    with pytest.raises(SystemExit) as exc:
        container_build.main()
    assert exc.value.code == 0
    assert len(invocations(log)) == before + 1


def test_module_entry_is_not_a_silent_noop() -> None:
    """Regression: container_build.py once had no `if __name__ == "__main__"`
    guard, so `python -m agentic_workspace.container_build ...` (what build and
    update invoke for every apptainer build) imported the module, exited 0 and
    built nothing — old SIFs kept being served as if cached. With apptainer
    stripped from PATH the module must now FAIL loudly, not exit 0."""
    env = {k: v for k, v in os.environ.items() if k != "PATH"}
    result = subprocess.run(
        [sys.executable, "-m", "agentic_workspace.container_build", "base", "apptainer"],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert "apptainer" in result.stderr.lower()


def test_build_subcommand_passes_force_to_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`./agentic-workspace build --apptainer` must force the base step, while
    the per-instance steps re-check the fingerprint (they skip the rebuild the
    base step just performed)."""
    from agentic_workspace import build as build_mod

    calls: list[list[str]] = []
    monkeypatch.setattr(build_mod, "ensure_runtime", lambda runtime: 0)
    monkeypatch.setattr(
        build_mod, "run_with_apptainer_fallback", lambda cmd: calls.append(cmd) or 0
    )
    monkeypatch.setattr(build_mod, "read_default_instances", lambda: ["agre"])

    assert build_mod.main(["--apptainer"]) == 0
    assert calls, "expected a base build subprocess"
    assert "base" in calls[0] and "--force" in calls[0]
    # instance builds ensure the base via the fingerprint memo, never --force
    for call in calls[1:]:
        assert "--force" not in call


def test_update_subcommand_passes_force_to_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`./agentic-workspace update --apptainer` must force the base step too."""
    from types import SimpleNamespace

    from agentic_workspace import update as update_mod

    calls: list[list[str]] = []
    monkeypatch.setenv("BIN_DIR", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "agre").write_text("#!/bin/sh\n")
    monkeypatch.setattr(update_mod, "subprocess", SimpleNamespace(run=lambda *a, **k: None))
    monkeypatch.setattr(
        update_mod, "run_with_apptainer_fallback", lambda cmd: calls.append(cmd) or 0
    )
    monkeypatch.setattr(update_mod, "read_default_instances", lambda: ["agre"])

    assert update_mod.main(["--apptainer"]) == 0
    base_calls = [c for c in calls if "base" in c]
    assert base_calls and "--force" in base_calls[0]


def test_fingerprint_covers_versions_file(build_env) -> None:
    """versions.json is a build input (the pins reach the build as args), so
    a changed pin must mark a previously built SIF as stale."""
    from agentic_workspace import container_build

    cdir, log = build_env
    assert container_build.build_base_image("apptainer") == 0
    assert len(invocations(log)) == 1

    (cdir / "versions.json").write_text('{"NODE_VERSION": "v26.8.2"}\n')
    assert container_build.build_base_image("apptainer") == 0  # no force: fingerprint decides
    assert len(invocations(log)) == 2


def install_fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    """A fake docker that logs argv and fakes a successful build."""
    bin_dir = tmp_path / "dbin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    script = bin_dir / "docker"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "docker $*" >> "{log}"\n'
        "exit 0\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir, log


def docker_build_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pins: dict[str, str]):
    """container dir + fake docker + canned version pins -> (log, cdir)."""
    from agentic_workspace import container_build, oci

    cdir = make_fake_container_dir(tmp_path)
    bin_dir, log = install_fake_docker(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(container_build, "container_dir", lambda: cdir)
    monkeypatch.setattr(oci, "docker_available", lambda: True)

    def fake_flags() -> list[str]:
        flags: list[str] = []
        for key, value in sorted(pins.items()):
            flags += ["--build-arg", f"{key}={value}"]
        return flags

    monkeypatch.setattr(container_build, "build_arg_flags", fake_flags)
    monkeypatch.delenv("AGENTIC_DOCKER_PULL", raising=False)
    return log, cdir


def test_docker_base_build_passes_version_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pins from versions.json must reach `docker build` as --build-arg
    flags: they are the only thing that busts a tool's layer cache."""
    from agentic_workspace import container_build

    log, _ = docker_build_env(tmp_path, monkeypatch, {"NODE_VERSION": "v26.8.2", "CLAUDE_CODE_VERSION": "2.1.267"})
    assert container_build.build_base_image("docker") == 0
    argv = invocations(log)[0]
    assert "build" in argv
    assert "--build-arg NODE_VERSION=v26.8.2" in argv
    assert "--build-arg CLAUDE_CODE_VERSION=2.1.267" in argv
    assert "-t agentic-blueprint:base" in argv
    # plain `build` never passes --pull (offline-friendly, cache-only)
    assert "--pull" not in argv


def test_docker_base_build_pulls_when_update_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """update sets AGENTIC_DOCKER_PULL=1 so the FROM image (ubuntu:26.04)
    refreshes too -- otherwise apt packages linger at the cached digest."""
    from agentic_workspace import container_build

    log, _ = docker_build_env(tmp_path, monkeypatch, {})
    monkeypatch.setenv("AGENTIC_DOCKER_PULL", "1")
    assert container_build.build_base_image("docker") == 0
    argv = invocations(log)[0].split()
    assert argv[:3] == ["docker", "build", "--pull"]



def test_update_refreshes_tool_pins_and_exports_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """update must refresh versions.json before rebuilding and export
    AGENTIC_DOCKER_PULL so the child builds re-pull the base OS image."""
    from types import SimpleNamespace

    from agentic_workspace import update as update_mod

    calls: list[list[str]] = []
    monkeypatch.setenv("BIN_DIR", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "agre").write_text("#!/bin/sh\n")
    monkeypatch.setattr(update_mod, "subprocess", SimpleNamespace(run=lambda *a, **k: None, call=lambda *a, **k: 0))
    monkeypatch.setattr(
        update_mod, "run_with_apptainer_fallback", lambda cmd: calls.append(cmd) or 0
    )
    monkeypatch.setattr(update_mod, "read_default_instances", lambda: ["agre"])
    monkeypatch.delenv("AGENTIC_DOCKER_PULL", raising=False)

    refreshed: list[bool] = []
    monkeypatch.setattr(update_mod, "refresh_tool_pins", lambda: refreshed.append(True))

    assert update_mod.main(["--docker"]) == 0
    assert refreshed == [True]
    assert os.environ.get("AGENTIC_DOCKER_PULL") == "1"


def test_update_no_tools_skips_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-tools is the offline escape hatch: no version lookups, no pull."""
    from types import SimpleNamespace

    from agentic_workspace import update as update_mod

    monkeypatch.setenv("BIN_DIR", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "agre").write_text("#!/bin/sh\n")
    monkeypatch.setattr(update_mod, "subprocess", SimpleNamespace(run=lambda *a, **k: None, call=lambda *a, **k: 0))
    monkeypatch.setattr(update_mod, "run_with_apptainer_fallback", lambda cmd: 0)
    monkeypatch.setattr(update_mod, "read_default_instances", lambda: ["agre"])

    def boom() -> None:
        raise AssertionError("--no-tools must skip the refresh")

    monkeypatch.setattr(update_mod, "refresh_tool_pins", boom)
    assert update_mod.main(["--docker", "--no-tools"]) == 0
