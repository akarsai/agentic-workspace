"""Unit tests for the host Slurm client shim (src/agentic_workspace/slurm.py).

The shim bind-mounts the host's Slurm client binaries, their runtime library
dirs, the slurm.conf directory, and (new) the PluginDir referenced by the
host's slurm.conf. These tests exercise the parsing and mount generation
without needing a Slurm installation.
"""
import os
from shutil import which as shutil_which
from pathlib import Path

from agentic_workspace import slurm


def _write_conf(tmp_path: Path, body: str) -> Path:
    conf = tmp_path / "etc" / "slurm" / "slurm.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(body)
    return conf


def test_plugin_dirs_parses_single_absolute(tmp_path: Path) -> None:
    plugins = tmp_path / "usr" / "lib64" / "slurm"
    plugins.mkdir(parents=True)
    conf = _write_conf(
        tmp_path,
        "# comment\n"
        f"PluginDir={plugins}\n"
        "SlurmctldHost=login01\n",
    )
    assert slurm._plugin_dirs(str(conf)) == [str(plugins)]


def test_plugin_dirs_multiple_and_space_separated(tmp_path: Path) -> None:
    a = tmp_path / "plugins-a"
    b = tmp_path / "plugins-b"
    a.mkdir()
    b.mkdir()
    conf = _write_conf(tmp_path, f"PluginDir={a} {b}\n")
    assert slurm._plugin_dirs(str(conf)) == [str(a), str(b)]


def test_plugin_dirs_relative_to_conf_dir(tmp_path: Path) -> None:
    plugins = tmp_path / "etc" / "slurm" / "plugindir"
    plugins.mkdir(parents=True)
    conf = _write_conf(tmp_path, "PluginDir=plugindir\n")
    assert slurm._plugin_dirs(str(conf)) == [str(plugins)]


def test_plugin_dirs_ignores_missing_dirs_and_comments(tmp_path: Path) -> None:
    conf = _write_conf(
        tmp_path,
        f"PluginDir={tmp_path / 'nope'} /also/missing\n"
        "# PluginDir=/etc/slurm\n",
    )
    assert slurm._plugin_dirs(str(conf)) == []


def test_plugin_dirs_deduplicates(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    conf = _write_conf(tmp_path, f"PluginDir={plugins}\nPluginDir={plugins}\n")
    assert slurm._plugin_dirs(str(conf)) == [str(plugins)]


def test_setup_mounts_plugin_dir_and_shim(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(slurm.platform, "system", lambda: "Linux")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_sbatch = bin_dir / "sbatch"
    fake_sbatch.write_text("#!/bin/sh\n")
    fake_sbatch.chmod(0o755)

    def fake_which(cmd: str) -> str | None:
        return str(fake_sbatch) if cmd == "sbatch" else None

    monkeypatch.setattr(slurm.shutil, "which", fake_which)

    plugins = tmp_path / "usr" / "lib64" / "slurm"
    plugins.mkdir(parents=True)
    conf = _write_conf(
        tmp_path, f"PluginDir={plugins}\nSlurmctldHost=ctld\n"
    )

    state = tmp_path / "state"
    mounts, envs = slurm.setup(state, env={"SLURM_CONF": str(conf)})

    # The host plugin dir is bound at its own path so the host conf works
    # verbatim (this is the gap that previously forced hand-rolled client
    # confs and symlink hacks).
    assert f"{plugins}:{plugins}:ro" in mounts
    # The conf dir and the whole shim dir (wrappers + client conf) are
    # mounted too.
    assert f"{conf.parent}:{conf.parent}:ro" in mounts
    assert f"{state / 'slurm-shim'}:/opt/slurm-host:ro" in mounts
    # Generated wrapper scripts exist and are executable.
    shim_bin = state / "slurm-shim" / "bin"
    assert (shim_bin / "sbatch").is_file()
    assert os.access(shim_bin / "sbatch", os.X_OK)
    assert any(e.startswith("SLURM_CONF=") for e in envs)


def test_find_host_conf_via_scontrol(tmp_path: Path, monkeypatch) -> None:
    """Clusters that install Slurm outside /etc (Kestrel: /nopt/slurm/etc)
    are found by asking a host client for its compiled-in conf path."""
    conf = tmp_path / "nopt" / "slurm" / "etc" / "slurm.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("SlurmctldHost=ctld\n")

    fake_scontrol = tmp_path / "bin" / "scontrol"
    fake_scontrol.parent.mkdir(parents=True)
    fake_scontrol.write_text("#!/bin/sh\n")

    monkeypatch.setattr(slurm, "CONF_CANDIDATES", (str(tmp_path / "etc/slurm"),))
    monkeypatch.setattr(slurm.shutil, "which", lambda c: str(fake_scontrol) if c == "scontrol" else None)

    class FakeRun:
        def __call__(self, cmd, **kwargs):
            class R:
                stdout = f"Configuration file = {conf}\nSlurmctldHost(1) = ctld\n"
            return R()

    monkeypatch.setattr(slurm.subprocess, "run", FakeRun())
    assert slurm._find_host_conf({}) == str(conf)
    # $SLURM_CONF still wins when the file exists.
    env_conf = tmp_path / "env" / "slurm.conf"
    env_conf.parent.mkdir(parents=True)
    env_conf.write_text("SlurmctldHost=other\n")
    assert slurm._find_host_conf({"SLURM_CONF": str(env_conf)}) == str(env_conf)


def test_write_client_conf_rewrites_slurm_user(tmp_path: Path) -> None:
    """The generated client conf replaces SlurmUser with the sandbox user in
    the plain form -- the image has no 'slurm' passwd entry (host value fails
    to parse) and the 'name(uid)' form is rejected by Kestrel's Slurm."""
    conf = tmp_path / "slurm.conf"
    conf.write_text(
        "# comment with = signs\n"
        "SlurmctldHost=ctld\n"
        "SlurmUser=slurm\n"
        "AuthInfo=auth/munge\n"
    )
    out = slurm._write_client_conf(str(conf), tmp_path / "client.conf", "akarsai")
    assert out is not None
    text = out.read_text()
    assert "SlurmUser=akarsai" in text
    assert "SlurmUser=slurm" not in text
    assert "SlurmctldHost=ctld" in text          # everything else verbatim
    # invalid user names leave the conf untouched
    out2 = slurm._write_client_conf(str(conf), tmp_path / "client2.conf", "bad(name)")
    assert "SlurmUser=slurm" in out2.read_text()


def test_write_client_conf_maps_paths_into_the_sandbox(tmp_path: Path) -> None:
    """PluginDir/Include are rewritten to in-sandbox locations: a host dir
    that is also a mounted shim lib dir becomes /opt/slurm-host/lib/<i>
    (reproduces the empirically working Kestrel hand recipe), other paths
    become absolute host paths (bound at that exact path)."""
    conf_dir = tmp_path / "nopt" / "slurm" / "etc"
    conf_dir.mkdir(parents=True)
    conf = conf_dir / "slurm.conf"
    conf.write_text(
        "SlurmUser=slurm\n"
        "PluginDir=/nopt/slurm/lib/0\n"
        "Include=topology.conf\n"
    )
    lib_map = {"/nopt/slurm/lib/0": "/opt/slurm-host/lib/3"}
    out = slurm._write_client_conf(str(conf), tmp_path / "client.conf", "akarsai", lib_map)
    text = out.read_text()
    assert "PluginDir=/opt/slurm-host/lib/3" in text
    # relative Include is absolutized against the host conf dir (mounted at
    # its own path); other lines stay verbatim
    assert f"Include={conf_dir}/topology.conf" in text
    assert "SlurmUser=akarsai" in text


def test_setup_generates_client_conf_next_to_host_conf(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end: the client conf rides along with the shim at /opt/slurm-host
    (NEVER as a file bind into the read-only host conf dir -- Apptainer cannot
    create the mount point there and aborts container creation), and
    SLURM_CONF points at it. Covers the Kestrel layout: conf outside /etc,
    SlurmUser=slurm, plugin dir, absolute Include."""
    monkeypatch.setattr(slurm.platform, "system", lambda: "Linux")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_sbatch = bin_dir / "sbatch"
    fake_sbatch.write_text("#!/bin/sh\n")
    fake_sbatch.chmod(0o755)

    def fake_which(cmd: str) -> str | None:
        return str(fake_sbatch) if cmd == "sbatch" else None

    monkeypatch.setattr(slurm.shutil, "which", fake_which)

    base = tmp_path / "nopt" / "slurm"
    conf_dir = base / "etc"
    plugins = base / "lib"
    topology = tmp_path / "etc" / "topology.conf"   # outside the conf dir
    conf_dir.mkdir(parents=True)
    plugins.mkdir(parents=True)
    topology.parent.mkdir(parents=True)
    topology.write_text("NodeName=n[01-02]\n")
    conf = conf_dir / "slurm.conf"
    conf.write_text(
        "SlurmctldHost=ctld\n"
        "SlurmUser=slurm\n"
        f"PluginDir={plugins}\n"
        f"Include={topology}\n"
    )

    state = tmp_path / "state"
    mounts, envs = slurm.setup(state, env={"SLURM_CONF": str(conf)})

    assert "SLURM_CONF=/opt/slurm-host/agentic-slurm-client.conf" in envs
    shim_mount = f"{state / 'slurm-shim'}:/opt/slurm-host:ro"
    assert shim_mount in mounts
    # no file bind into the (read-only) host conf dir
    assert not any(m.endswith(f":{conf_dir}/agentic-slurm-client.conf:ro") for m in mounts)
    assert f"{conf_dir}:{conf_dir}:ro" in mounts
    assert f"{plugins}:{plugins}:ro" in mounts
    assert f"{topology}:{topology}:ro" in mounts
    generated = state / "slurm-shim" / "agentic-slurm-client.conf"
    assert generated.is_file()
    assert f"SlurmUser={slurm._launch_user()}" in generated.read_text()


def test_compiled_plugin_dir_from_scontrol(tmp_path: Path, monkeypatch) -> None:
    """Kestrel's slurm.conf carries no PluginDir line; the effective value
    comes from the clients' compiled-in default, printed by scontrol."""
    plugins = tmp_path / "nopt" / "slurm" / "25.05.5" / "lib" / "slurm"
    plugins.mkdir(parents=True)
    fake_scontrol = tmp_path / "bin" / "scontrol"
    fake_scontrol.parent.mkdir(parents=True)
    fake_scontrol.write_text("#!/bin/sh\n")
    monkeypatch.setattr(slurm.shutil, "which", lambda c: str(fake_scontrol) if c == "scontrol" else None)

    class FakeRun:
        def __call__(self, cmd, **kwargs):
            class R:
                stdout = f"Configuration file = /nopt/slurm/etc/slurm.conf\nPluginDir = {plugins}\n"
            return R()

    monkeypatch.setattr(slurm.subprocess, "run", FakeRun())
    assert slurm._compiled_plugin_dir() == str(plugins)


def test_write_client_conf_appends_missing_plugin_dir(tmp_path: Path) -> None:
    """No PluginDir line in the host conf -> one is appended pointing at the
    fallback's in-sandbox location (without it every client dies with 'Bad
    value for PluginDir'). No fallback -> the conf stays line-less."""
    conf = tmp_path / "slurm.conf"
    conf.write_text("SlurmctldHost=ctld\nSlurmUser=slurm\n")

    out = slurm._write_client_conf(
        str(conf), tmp_path / "client.conf", "akarsai",
        lib_map={}, plugin_dir_fallback="/opt/slurm-host/lib/0",
    )
    lines = out.read_text().splitlines()
    assert lines[-1] == "PluginDir=/opt/slurm-host/lib/0"
    assert "SlurmUser=akarsai" in lines

    out2 = slurm._write_client_conf(str(conf), tmp_path / "client2.conf", "akarsai")
    assert not any(l.startswith("PluginDir=") for l in out2.read_text().splitlines())


def test_setup_appends_compiled_plugin_dir_when_conf_has_none(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end Kestrel case: slurm.conf WITHOUT PluginDir, plugins only
    reachable through the clients' compiled-in default. The dir is mounted at
    its host path and a PluginDir line pointing at it is written into the
    client conf (here the fake client is a static script, so no shim lib dirs
    exist and the host path is the in-sandbox location)."""
    monkeypatch.setattr(slurm.platform, "system", lambda: "Linux")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_sbatch = bin_dir / "sbatch"
    fake_sbatch.write_text("#!/bin/sh\n")
    fake_sbatch.chmod(0o755)

    def fake_which(cmd: str) -> str | None:
        if cmd == "sbatch":
            return str(fake_sbatch)
        return str(fake_sbatch) if cmd == "scontrol" else None

    monkeypatch.setattr(slurm.shutil, "which", fake_which)

    conf_dir = tmp_path / "nopt" / "slurm" / "etc"
    plugins = tmp_path / "nopt" / "slurm" / "25.05.5" / "lib" / "slurm"
    conf_dir.mkdir(parents=True)
    plugins.mkdir(parents=True)
    (plugins / "select_cons_tres.so").write_text("")
    conf = conf_dir / "slurm.conf"
    conf.write_text("SlurmctldHost=kestrel-ctld\nSlurmUser=slurm\n")  # no PluginDir

    class FakeRun:
        def __call__(self, cmd, **kwargs):
            class R:
                stdout = f"Configuration file = {conf}\nPluginDir = {plugins}\n"
            return R()

    monkeypatch.setattr(slurm.subprocess, "run", FakeRun())

    state = tmp_path / "state"
    mounts, envs = slurm.setup(state, env={"SLURM_CONF": str(conf)})

    assert f"{plugins}:{plugins}:ro" in mounts
    assert "SLURM_CONF=/opt/slurm-host/agentic-slurm-client.conf" in envs
    generated = state / "slurm-shim" / "agentic-slurm-client.conf"
    text = generated.read_text()
    assert f"PluginDir={plugins}" in text
    assert "SlurmUser=slurm" not in text


def test_setup_mounts_authinfo_munge_socket(tmp_path: Path, monkeypatch) -> None:
    """A site-specific munge socket= path in AuthInfo is mounted at /run/munge."""
    monkeypatch.setattr(slurm.platform, "system", lambda: "Linux")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_sbatch = bin_dir / "sbatch"
    fake_sbatch.write_text("#!/bin/sh\n")
    fake_sbatch.chmod(0o755)

    def fake_which(cmd: str) -> str | None:
        return str(fake_sbatch) if cmd == "sbatch" else None

    monkeypatch.setattr(slurm.shutil, "which", fake_which)
    monkeypatch.setattr(slurm, "MUNGE_CANDIDATES", ())  # only AuthInfo applies

    munge_dir = tmp_path / "var" / "run" / "munge-custom"
    munge_dir.mkdir(parents=True)
    conf = _write_conf(
        tmp_path, f"AuthInfo=cred_expire=300 socket={munge_dir}/munge.socket.2\n"
    )

    state = tmp_path / "state"
    mounts, _envs = slurm.setup(state, env={"SLURM_CONF": str(conf)})
    assert f"{munge_dir}:/run/munge" in mounts


def test_setup_compiled_plugin_dir_maps_to_shim_lib(
    tmp_path: Path, monkeypatch
) -> None:
    """The real Kestrel composition: the conf has no PluginDir, and the
    compiled-in plugin dir is also an ldd-visible dependency dir (libslurm
    lives there). It is then already mounted as a shim lib dir, and the
    appended PluginDir must point at that target -- /opt/slurm-host/lib/<i> --
    reproducing the hand recipe the agre session verified on the cluster."""
    monkeypatch.setattr(slurm.platform, "system", lambda: "Linux")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_sbatch = bin_dir / "sbatch"
    fake_sbatch.write_text("#!/bin/sh\n")
    fake_sbatch.chmod(0o755)
    ldd = shutil_which("ldd") or "/usr/bin/ldd"

    def fake_which(cmd: str) -> str | None:
        return {
            "sbatch": str(fake_sbatch),
            "scontrol": str(fake_sbatch),
            "ldd": ldd,
        }.get(cmd)

    monkeypatch.setattr(slurm.shutil, "which", fake_which)

    conf_dir = tmp_path / "nopt" / "slurm" / "etc"
    plugins = tmp_path / "nopt" / "slurm" / "25.05.5" / "lib" / "slurm"
    conf_dir.mkdir(parents=True)
    plugins.mkdir(parents=True)
    conf = conf_dir / "slurm.conf"
    conf.write_text("SlurmctldHost=kestrel-ctld\nSlurmUser=slurm\n")  # no PluginDir

    class FakeRun:
        def __call__(self, cmd, **kwargs):
            class R:
                stdout = ""
            if cmd[0] == "ldd":
                R.stdout = (
                    "\tlinux-vdso.so.1 (0x00007ffd2b3d1000)\n"
                    f"\tlibslurm.so.1 => {plugins}/libslurm.so.1 (0x00007f5e2a3d1000)\n"
                    "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f5e2a1d1000)\n"
                    "\t/lib64/ld-linux-x86-64.so.2 (0x00007f5e2a5d1000)\n"
                )
            else:  # scontrol show config
                R.stdout = f"Configuration file = {conf}\nPluginDir = {plugins}\n"
            return R()

    monkeypatch.setattr(slurm.subprocess, "run", FakeRun())

    state = tmp_path / "state"
    mounts, envs = slurm.setup(state, env={"SLURM_CONF": str(conf)})

    assert f"{plugins}:/opt/slurm-host/lib/0:ro" in mounts
    assert "SLURM_CONF=/opt/slurm-host/agentic-slurm-client.conf" in envs
    generated = (state / "slurm-shim" / "agentic-slurm-client.conf").read_text()
    assert "PluginDir=/opt/slurm-host/lib/0" in generated
    # no duplicate bind at the host path (the lib mount already covers it)
    assert f"{plugins}:{plugins}:ro" not in mounts
