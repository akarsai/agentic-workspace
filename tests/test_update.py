"""Tests for the `update` subcommand's runtime selection, flags and pin
reporting (build execution itself is covered in test_container_build.py)."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def update_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated HOME + an installed fake launcher named 'agre'."""
    import agentic_workspace.update as update_mod

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    launcher = bin_dir / "agre"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("BIN_DIR", str(bin_dir))
    monkeypatch.delenv("AGENTIC_BEST_EFFORT_BUILD", raising=False)
    monkeypatch.delenv("AGENTIC_DOCKER_PULL", raising=False)
    monkeypatch.setattr(update_mod, "subprocess", SimpleNamespace(run=lambda *a, **k: None, call=lambda *a, **k: 0))
    monkeypatch.setattr(update_mod.interactive, "read_marker", lambda: ["agre"])
    monkeypatch.setattr(update_mod, "refresh_tool_pins", lambda: None)
    return update_mod, bin_dir


def test_plain_update_derives_runtime_from_instance_config(update_env, monkeypatch) -> None:
    """A flag-less `update` must not rebuild an Apptainer install with a
    Docker that is not there: the instances' configured runtime wins."""
    update_mod, bin_dir = update_env
    calls: list[list[str]] = []
    monkeypatch.setattr(update_mod, "run_with_apptainer_fallback", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(update_mod.cfgmod, "load", lambda name: {"AGENTIC_CONTAINER_RUNTIME": "apptainer"})
    monkeypatch.setattr(update_mod.oci, "docker_available", lambda: False)

    assert update_mod.main(["--no-tools"]) == 0
    base_calls = [c for c in calls if "base" in c]
    assert base_calls and "apptainer" in base_calls[0]
    instance_calls = [c for c in calls if str(bin_dir / "agre") in c]
    assert instance_calls and "--apptainer" in instance_calls[0] and "--build" in instance_calls[0]


def test_plain_update_detects_runtime_without_config(update_env, monkeypatch) -> None:
    """No config, no docker, apptainer present: detection picks apptainer."""
    update_mod, _ = update_env
    monkeypatch.setattr(update_mod.cfgmod, "load", lambda name: {})
    monkeypatch.setattr(update_mod.oci, "docker_available", lambda: False)
    monkeypatch.setattr(update_mod.oci, "apptainer_available", lambda: True)
    monkeypatch.setattr(update_mod.oci, "is_linux", lambda: True)

    assert update_mod.resolved_runtime(["agre"]) == ("apptainer", "what is installed")


def test_resolved_runtime_prefers_config(update_env, monkeypatch) -> None:
    update_mod, _ = update_env
    monkeypatch.setattr(update_mod.cfgmod, "load", lambda name: {"AGENTIC_CONTAINER_RUNTIME": "apptainer"})
    monkeypatch.setattr(update_mod.oci, "docker_available", lambda: True)

    assert update_mod.resolved_runtime(["agre"]) == ("apptainer", "agre's config")


def test_runtime_flag_aliases(update_env, monkeypatch) -> None:
    """--runtime and --tool spell out what --apptainer/--docker shorthand."""
    update_mod, _ = update_env
    calls: list[list[str]] = []
    docker_calls: list[str] = []
    monkeypatch.setattr(update_mod, "run_with_apptainer_fallback", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(update_mod.container_build, "build_base_image", lambda rt: docker_calls.append(rt) or 0)
    monkeypatch.setattr(update_mod.cfgmod, "load", lambda name: {})

    assert update_mod.main(["--runtime", "apptainer", "--no-tools"]) == 0
    assert any("apptainer" in c and "base" in c for c in calls)

    calls.clear()
    assert update_mod.main(["--tool", "docker", "--no-tools"]) == 0
    assert docker_calls == ["docker"]


def test_runtime_flag_rejects_unknown_values(update_env, monkeypatch) -> None:
    update_mod, _ = update_env
    builds: list[str] = []
    monkeypatch.setattr(update_mod, "run_with_apptainer_fallback", lambda cmd: builds.append(cmd) or 0)
    monkeypatch.setattr(update_mod.container_build, "build_base_image", lambda rt: builds.append(rt) or 0)

    assert update_mod.main(["--tool", "pi", "--no-tools"]) == 1  # --tool pi is a launcher flag
    assert update_mod.main(["--runtime"]) == 1  # missing value
    assert builds == []


def test_update_sets_best_effort_for_builds(update_env, monkeypatch) -> None:
    """update must tolerate hosts that cannot build apptainer images at all:
    it exports the best-effort switch the builds degrade on."""
    update_mod, _ = update_env
    monkeypatch.setattr(update_mod, "run_with_apptainer_fallback", lambda cmd: 0)
    assert update_mod.main(["--apptainer", "--no-tools"]) == 0
    assert os.environ.get("AGENTIC_BEST_EFFORT_BUILD") == "1"


def test_refresh_tool_pins_distinguishes_checksum_only_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A pin whose version did not move but gained checksums must not be
    reported as 'x -> x' nor as 'rebuilding with' — that reads like a
    version bump that never happened."""
    import agentic_workspace.tool_versions as tv
    import agentic_workspace.update as update_mod

    fake_root = tmp_path / "repo"
    versions = fake_root / "blueprint" / "container" / "versions.json"
    versions.parent.mkdir(parents=True)
    monkeypatch.setattr(update_mod, "repo_root", lambda: fake_root)
    monkeypatch.setattr(tv, "versions_file", lambda: versions)
    monkeypatch.setattr(
        tv,
        "refresh_versions",
        lambda path=None: tv.RefreshResult(
            changed={
                "NODE_VERSION": ("v26.9.0", "v26.10.0"),  # real bump
                "BUN_VERSION": ("1.4.2", "1.4.2"),        # checksums pinned only
                "GH_VERSION": ("", "2.101.0"),            # first pin
            },
            unchanged=["PI_VERSION"],
            failed=[],
            path=versions,
        ),
    )

    update_mod.refresh_tool_pins()
    out = capsys.readouterr().out
    assert "node         v26.9.0 -> v26.10.0" in out
    assert "bun          1.4.2 (checksums pinned)" in out
    assert "gh           pinned 2.101.0" in out
    assert "already latest: pi" in out
    assert "checksum pins updated for: bun" in out
    assert "rebuilding with: gh, node" in out  # gh is a first pin, node a real bump
    assert "rebuilding with: bun" not in out
    assert "1.4.2 -> 1.4.2" not in out


def test_refresh_tool_pins_checksums_only_no_rebuilding_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """When every 'change' is checksum-only, say so instead of implying a
    version refresh ('rebuilding with: ...' must not appear)."""
    import agentic_workspace.tool_versions as tv
    import agentic_workspace.update as update_mod

    fake_root = tmp_path / "repo"
    versions = fake_root / "blueprint" / "container" / "versions.json"
    versions.parent.mkdir(parents=True)
    monkeypatch.setattr(update_mod, "repo_root", lambda: fake_root)
    monkeypatch.setattr(tv, "versions_file", lambda: versions)
    monkeypatch.setattr(
        tv,
        "refresh_versions",
        lambda path=None: tv.RefreshResult(
            changed={
                "BUN_VERSION": ("1.4.2", "1.4.2"),
                "GH_VERSION": ("2.101.0", "2.101.0"),
            },
            unchanged=[],
            failed=[],
            path=versions,
        ),
    )

    update_mod.refresh_tool_pins()
    out = capsys.readouterr().out
    assert "checksum pins updated for: bun, gh" in out
    assert "rebuilding with" not in out
    assert "no new upstream versions" in out


def test_update_exits_nonzero_when_a_build_fails(update_env, monkeypatch) -> None:
    """A failed rebuild must not hide behind 'Update complete.' — scripts and
    users need the nonzero exit. (Best-effort keeps return 0 and don't.)"""
    update_mod, _ = update_env
    monkeypatch.setattr(update_mod, "run_with_apptainer_fallback", lambda cmd: 255)
    monkeypatch.setattr(update_mod.cfgmod, "load", lambda name: {})

    assert update_mod.main(["--apptainer", "--no-tools"]) == 1


def test_update_name_path_exits_nonzero_when_build_fails(update_env, monkeypatch) -> None:
    update_mod, _ = update_env
    monkeypatch.setattr(update_mod, "run_with_apptainer_fallback", lambda cmd: 255)

    assert update_mod.main(["--name", "agre", "--apptainer", "--no-tools"]) == 1
