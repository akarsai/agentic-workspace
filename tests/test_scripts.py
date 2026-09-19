"""Functional tests for the workspace job scripts (instances/*/scripts).

submit.sh is the piece the sandbox runs against the real Slurm client, so its
contract is tested against a fake sbatch: AGENTIC_WORKSPACE_HOST is a HOST
path (invisible inside the sandbox — --chdir is resolved by the controller),
the logs dir is created relative to the CWD, and every argument is forwarded.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBMIT = REPO_ROOT / "instances" / "agre" / "scripts" / "submit.sh"


def install_fake_sbatch(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "sbatch.log"
    script = bin_dir / "sbatch"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" >> "{log}"\n'
        "exit 0\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir, log


def run_submit(tmp_path: Path, env_extra: dict[str, str], *args: str):
    bin_dir, log = install_fake_sbatch(tmp_path)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
           "HOME": os.environ.get("HOME", "/tmp"), **env_extra}
    return subprocess.run(
        ["bash", str(SUBMIT), *args],
        cwd=tmp_path / "ws",
        env=env,
        capture_output=True,
        text=True,
    ), log


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def test_submit_forwards_invisible_host_chdir(ws: Path, tmp_path: Path) -> None:
    """--chdir is resolved by the controller on the HOST: the workspace host
    path must NOT be tested for visibility inside the sandbox (regression:
    the -d test aborted every submission because the path never exists here)."""
    host_dir = tmp_path / "host" / "velocity-rom"   # deliberately NOT created
    rc, log = run_submit(
        tmp_path, {"AGENTIC_WORKSPACE_HOST": str(host_dir)}, "scripts/smoke.sbatch"
    )
    assert rc.returncode == 0, rc.stderr
    out = log.read_text().splitlines()
    assert out[0] == f"--chdir={host_dir}"
    assert out[1:] == ["scripts/smoke.sbatch"]
    # logs dir is created relative to the CWD (the workspace root), not under
    # the host path (unwritable/invisible inside the sandbox)
    assert (ws / "logs").is_dir()
    assert not (host_dir / "logs").exists()


def test_submit_requires_workspace_host(ws: Path, tmp_path: Path) -> None:
    rc, _log = run_submit(tmp_path, {}, "scripts/smoke.sbatch")
    assert rc.returncode != 0
    assert "AGENTIC_WORKSPACE_HOST" in rc.stderr


def test_submit_forwards_extra_flags(ws: Path, tmp_path: Path) -> None:
    rc, log = run_submit(
        tmp_path,
        {"AGENTIC_WORKSPACE_HOST": "/host/ws"},
        "--array=1-36", "--account=extremedata", "scripts/run.sbatch",
    )
    assert rc.returncode == 0, rc.stderr
    assert log.read_text().splitlines() == [
        "--chdir=/host/ws", "--array=1-36", "--account=extremedata", "scripts/run.sbatch",
    ]


def test_sbatch_templates_find_prologue_from_spool_copy(tmp_path: Path) -> None:
    """On the node, slurmd executes a SPOOL COPY of the script
    (/var/spool/slurmd/<jobid>/slurm_script), so dirname "$0" is the spool
    dir -- the old templates sourced a nonexistent file there, the prologue
    never ran, and every job died on the unrepaired environment. The
    templates must resolve job-header.sh via AGENTIC_WORKSPACE_HOST instead."""
    scripts = REPO_ROOT / "instances" / "agre" / "scripts"
    ws = tmp_path / "hostws"                      # node-visible workspace
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "job-header.sh").write_text(
        'touch "$AGENTIC_WORKSPACE_HOST/prologue-ran"\n'
    )
    spool = tmp_path / "spool"                    # slurmd's copy location
    spool.mkdir()

    env = {
        "PATH": "/usr/bin:/bin",                  # no uv, no nvidia-smi
        "HOME": str(tmp_path),
        "AGENTIC_WORKSPACE_HOST": str(ws),
    }
    smoke = spool / "smoke.sbatch"
    shutil.copy2(scripts / "smoke.sbatch", smoke)
    rc = subprocess.run(["bash", str(smoke)], env=env, capture_output=True, text=True)
    assert (ws / "prologue-ran").is_file()
    assert rc.returncode == 0, rc.stderr
    assert "SMOKE OK" in rc.stdout

    # template.sbatch: prologue must resolve the same way (its placeholder
    # entrypoint fails afterwards -- expected, only the prologue is under test)
    (ws / "prologue-ran").unlink()
    tpl = spool / "template.sbatch"
    shutil.copy2(scripts / "template.sbatch", tpl)
    rc2 = subprocess.run(["bash", str(tpl)], env=env, capture_output=True, text=True)
    assert (ws / "prologue-ran").is_file()


def test_smoke_sbatch_does_not_report_success_after_failure(tmp_path: Path) -> None:
    """set -euo pipefail precedes the prologue source: a failing prologue
    aborts the script. Regression: the smoke job once printed SMOKE OK even
    though the prologue and the python check had both failed."""
    scripts = REPO_ROOT / "instances" / "agre" / "scripts"
    ws = tmp_path / "hostws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "job-header.sh").write_text("echo prologue failed >&2\nexit 1\n")
    spool = tmp_path / "spool"
    spool.mkdir()
    smoke = spool / "smoke.sbatch"
    shutil.copy2(scripts / "smoke.sbatch", smoke)

    rc = subprocess.run(
        ["bash", str(smoke)],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "AGENTIC_WORKSPACE_HOST": str(ws)},
        capture_output=True,
        text=True,
    )
    assert rc.returncode != 0
    assert "SMOKE OK" not in rc.stdout
