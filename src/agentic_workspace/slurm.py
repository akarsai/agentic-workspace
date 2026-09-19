"""Host Slurm client shim.

On Linux hosts with Slurm client commands on PATH (an HPC login node), bind the
host's own binaries and their shared libraries into the sandbox so agents can
run sbatch/squeue/scontrol without an image-baked (and version-mismatched)
Slurm install. Generates wrapper scripts that invoke the host dynamic loader
explicitly so host library directories never shadow the container's own libs.

The host slurm.conf is located via $SLURM_CONF, the standard /etc dirs, or by
asking a host client (`scontrol show config` reports its compiled-in path —
clusters that install into e.g. /nopt/slurm/etc have nothing in /etc). A
client copy is then generated with SlurmUser rewritten to the launching user:
the image has no "slurm" user entry, so the host value fails to parse inside
the sandbox (observed on Kestrel: plain "SlurmUser=<name>" is required — the
"name(uid)" form is rejected by the host's Slurm version). The copy rides
along with the client shim at /opt/slurm-host (a file bind into the read-only
host conf dir is not possible under Apptainer), and $SLURM_CONF inside the
sandbox points at it; PluginDir=/Include= entries are rewritten to their
in-sandbox locations (shim lib mounts or host-path binds), and when the host
conf carries no PluginDir at all (compiled-in default, e.g. Kestrel) the
effective dir is discovered via `scontrol show config` and appended.
Regenerated on every launch, so there is no hand-patched conf to go stale.

Returns (mount_specs, env_pairs) for the launcher.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

SLURM_CMDS = [
    "sbatch", "squeue", "scontrol", "sacct", "scancel", "sinfo", "srun",
    "salloc", "sattach", "sbcast", "sstat", "sshare", "sprio", "sdiag",
    "sreport", "sacctmgr", "strigger",
]

CONF_CANDIDATES = ("/etc/slurm", "/etc/slurm-llnl")
MUNGE_CANDIDATES = ("/run/munge", "/var/run/munge", "/var/lib/munge")
CLIENT_CONF_NAME = "agentic-slurm-client.conf"


def _launch_user() -> str:
    """Name of the user running the launcher (the user the container runs as).

    Used for the SlurmUser rewrite in the generated client conf; must be a
    plain name (no uid parens — some Slurm versions reject "name(uid)").
    """
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USER", "") or ""


def _find_host_conf(env: dict[str, str]) -> str | None:
    """Locate the host's slurm.conf.

    Order: $SLURM_CONF, the standard /etc candidates, then ask a host client —
    `scontrol show config` prints its compiled-in "Configuration file = ..."
    path, which finds cluster installs outside /etc (e.g. /nopt/slurm/etc on
    Kestrel). Runs on the host, where the clients find their own conf.
    """
    slurm_conf = env.get("SLURM_CONF")
    if slurm_conf and os.path.isfile(slurm_conf):
        return os.path.realpath(slurm_conf)
    for d in CONF_CANDIDATES:
        if os.path.isfile(f"{d}/slurm.conf"):
            return os.path.realpath(f"{d}/slurm.conf")
    if shutil.which("scontrol") is None:
        return None
    try:
        out = subprocess.run(
            ["scontrol", "show", "config"], capture_output=True, text=True
        ).stdout
    except OSError:
        return None
    m = re.search(r"Configuration file\s*=\s*(\S+)", out)
    if m and os.path.isfile(m.group(1)):
        return os.path.realpath(m.group(1))
    return None


def _include_targets(conf_path: str) -> list[str]:
    """Existing files/dirs referenced by Include= in slurm.conf.

    Include paths may be absolute or relative to the conf's directory; each is
    mounted at its host path so the conf works verbatim inside the sandbox.
    """
    try:
        text = Path(conf_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    conf_dir = os.path.dirname(os.path.realpath(conf_path))
    out: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != "Include":
            continue
        for p in value.split():
            if not p.startswith("/"):
                p = os.path.join(conf_dir, p)
            rp = os.path.realpath(p)
            if os.path.exists(rp) and rp not in out and not rp.startswith(conf_dir + os.sep):
                out.append(rp)  # inside the conf dir it is already mounted
    return out


def _authinfo_socket_dir(conf_path: str) -> str | None:
    """Munge socket dir from an AuthInfo= socket=... entry (site-specific
    socket locations are invisible to the standard candidates)."""
    try:
        text = Path(conf_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != "AuthInfo":
            continue
        m = re.search(r"(?:^|\s)socket=(/\S+)", value)
        if m:
            d = os.path.realpath(os.path.dirname(m.group(1)))
            if os.path.isdir(d):
                return d
    return None


def _compiled_plugin_dir() -> str | None:
    """Plugin dir compiled into the host Slurm clients (`scontrol show config`).

    slurm.conf may carry no PluginDir line at all (Kestrel relies on the
    compiled-in default). The sandbox cannot see that default path, and the
    clients then abort with 'Bad value ... for PluginDir' before doing
    anything -- the config file never mentions the path, so mount generation
    alone cannot fix it. `scontrol show config` prints the effective value.
    """
    if shutil.which("scontrol") is None:
        return None
    try:
        out = subprocess.run(
            ["scontrol", "show", "config"], capture_output=True, text=True
        ).stdout
    except OSError:
        return None
    m = re.search(r"^\s*PluginDir\s*=\s*(\S+)", out, re.M)
    if m and os.path.isdir(m.group(1)):
        return os.path.realpath(m.group(1))
    return None


def _write_client_conf(
    conf_path: str,
    out_path: Path,
    user: str,
    lib_map: dict[str, str] | None = None,
    plugin_dir_fallback: str | None = None,
) -> Path | None:
    """Write a client copy of the host conf, adapted to the sandbox.

    - SlurmUser -> the launching user. SlurmUser is controller-side config; a
      client conf only needs it to parse, and the host value ("slurm") has no
      passwd entry inside the image. Only a syntactically valid user name
      triggers the rewrite (plain name -- the "name(uid)" form is rejected).
    - PluginDir/Include paths are rewritten to where the sandbox can actually
      see them: a host dir that is also a mounted shim lib dir becomes its
      /opt/slurm-host/lib/<i> target, everything else becomes an absolute
      host path (bound at that exact path).
    - When the conf has NO PluginDir line (compiled-in default, e.g. Kestrel)
      and plugin_dir_fallback is given, a PluginDir line is appended pointing
      at the fallback's in-sandbox location -- without it every client dies
      with 'Bad value for PluginDir'.
    """
    lib_map = lib_map or {}
    try:
        text = Path(conf_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    conf_dir = os.path.dirname(os.path.realpath(conf_path))

    def resolve_host_path(token: str) -> str:
        p = token if token.startswith("/") else os.path.join(conf_dir, token)
        return p

    out_lines: list[str] = []
    saw_plugin_dir = False
    for line in text.splitlines():
        m = re.match(r"^(\s*)(SlurmUser|PluginDir|Include)\s*=\s*(.*?)\s*$", line)
        if not m or "#" in m.group(1):
            out_lines.append(line)
            continue
        lead, key, value = m.groups()
        if key == "SlurmUser":
            if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*\Z", user):
                out_lines.append(f"{lead}SlurmUser={user}")
            else:
                out_lines.append(line)
        elif key == "PluginDir":
            tokens = []
            for t in value.split():
                host = resolve_host_path(t)
                rp = os.path.realpath(host)
                tokens.append(lib_map.get(rp, host))
            out_lines.append(f"{lead}PluginDir={' '.join(tokens)}")
        else:  # Include
            tokens = [resolve_host_path(t) for t in value.split()]
            out_lines.append(f"{lead}Include={' '.join(tokens)}")
    if not saw_plugin_dir and plugin_dir_fallback:
        out_lines.append(f"PluginDir={plugin_dir_fallback}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return out_path


def _plugin_dirs(conf_path: str) -> list[str]:
    """Host Slurm plugin directories referenced by PluginDir= in slurm.conf.

    Client commands dlopen these plugins at runtime, so they never show up in
    ldd output and the shim would otherwise leave them unmounted. Without them
    every client fails immediately, e.g.
    "Couldn't load plugin accounting_storage/none: cannot open shared object".
    PluginDir may appear multiple times with space-separated absolute (or
    conf-relative) paths.
    """
    try:
        text = Path(conf_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    conf_dir = os.path.dirname(os.path.realpath(conf_path))
    out: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != "PluginDir":
            continue
        for p in value.split():
            if not p.startswith("/"):
                p = os.path.join(conf_dir, p)
            rp = os.path.realpath(p)
            if os.path.isdir(rp) and rp not in out:
                out.append(rp)
    return out


def setup(state_root: Path, env: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Build the shim; returns (mount_specs, env_pairs). No-op when the host
    is not Linux or has no Slurm client."""
    env = env if env is not None else os.environ
    if platform.system() != "Linux":
        return [], []
    if shutil.which("sbatch") is None:
        return [], []

    names: list[str] = []
    paths: list[str] = []
    dirs: list[str] = []
    for cmd in SLURM_CMDS:
        p = shutil.which(cmd)
        if not p:
            continue
        rp = os.path.realpath(p)
        if os.path.isfile(rp):
            p = rp
        names.append(cmd)
        paths.append(p)
        d = os.path.dirname(p)
        if d not in dirs:
            dirs.append(d)

    if not names:
        return [], []

    mounts = [f"{d}:/opt/slurm-host/real/{i}:ro" for i, d in enumerate(dirs)]

    # Per-command dynamic linking: union of host lib dirs + dynamic loader.
    lib_dirs: list[str] = []
    cmd_dyn: list[bool] = []
    interp: str | None = None
    for p in paths:
        if shutil.which("ldd") is None or not os.access(p, os.X_OK):
            cmd_dyn.append(False)
            continue
        try:
            out = subprocess.run(["ldd", p], capture_output=True, text=True).stdout
        except OSError:
            cmd_dyn.append(False)
            continue
        if "not a dynamic executable" in out or "statically linked" in out:
            cmd_dyn.append(False)
            continue
        cmd_dyn.append(True)
        for line in out.splitlines():
            m = re.search(r"=>\s*(/\S+)", line)
            if m:
                d = os.path.realpath(os.path.dirname(m.group(1)))
                if d not in lib_dirs:
                    lib_dirs.append(d)
            elif interp is None:
                m2 = re.match(r"\s*(/\S+)\s+\(0x", line)
                if m2:
                    r = os.path.realpath(m2.group(1))
                    interp = r if os.path.isfile(r) else m2.group(1)

    lib_path = ""
    lib_map: dict[str, str] = {}
    for i, d in enumerate(lib_dirs):
        target = f"/opt/slurm-host/lib/{i}"
        mounts.append(f"{d}:{target}:ro")
        lib_map[d] = target
        lib_path += (":" if lib_path else "") + target

    if interp and os.path.isfile(interp):
        mounts.append(f"{interp}:/opt/slurm-host/ld-linux.so.2:ro")

    # Per-command wrapper scripts (python one-liners, generated artifacts).
    # The whole shim dir is mounted at /opt/slurm-host and also carries the
    # generated client conf (see below) -- a file bind into the read-only
    # host conf dir is not possible under Apptainer (mount-point creation
    # inside a ro bind fails with "permission denied").
    shim_root = state_root / "slurm-shim"
    shim_bin = shim_root / "bin"
    shim_bin.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names):
        j = dirs.index(os.path.dirname(paths[i]))
        target = f"/opt/slurm-host/real/{j}/{os.path.basename(paths[i])}"
        if cmd_dyn[i] and interp:
            body = (
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                f"os.execv('/opt/slurm-host/ld-linux.so.2', "
                f"['ld-linux.so.2', '--library-path', {lib_path!r}, {target!r}, *sys.argv[1:]])\n"
            )
        else:
            body = (
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                f"os.execv({target!r}, [{target!r}, *sys.argv[1:]])\n"
            )
        w = shim_bin / name
        w.write_text(body)
        w.chmod(0o755)

    mounts.append(f"{shim_root}:/opt/slurm-host:ro")

    # Slurm config: $SLURM_CONF, /etc candidates, or the clients' compiled-in
    # path (clusters like Kestrel install into /nopt/slurm/etc). The conf dir
    # is bound at its own host path (absolute Include=/PluginDir= targets keep
    # resolving), and the generated client conf rides along with the shim at
    # /opt/slurm-host/slurm-client.conf, with PluginDir/Include rewritten to
    # their in-sandbox locations.
    env_pairs: list[str] = []
    conf_path = _find_host_conf(env)
    if conf_path:
        conf_dir = os.path.dirname(conf_path)
        mounts.append(f"{conf_dir}:{conf_dir}:ro")
        # Plugin dirs are dlopen'd at runtime (invisible to ldd above); bind
        # each at its host path so the host slurm.conf works verbatim inside
        # the sandbox. When the conf names none, the clients' compiled-in
        # default (from `scontrol show config`) is used instead -- the sandbox
        # cannot see that path, so it is mounted and written INTO the client
        # conf (the host conf never mentions it, mounts alone cannot help).
        plugin_dirs = _plugin_dirs(conf_path)
        compiled_dir = None
        if not plugin_dirs:
            compiled_dir = _compiled_plugin_dir()
            if compiled_dir:
                plugin_dirs = [compiled_dir]
        for pd_ in plugin_dirs:
            if pd_ not in lib_map:  # already visible via a shim lib mount
                mounts.append(f"{pd_}:{pd_}:ro")
        for inc in _include_targets(conf_path):
            mounts.append(f"{inc}:{inc}:ro")
        plugin_dir_fallback = None
        if compiled_dir:
            plugin_dir_fallback = lib_map.get(compiled_dir, compiled_dir)
        client_conf = _write_client_conf(
            conf_path, shim_root / CLIENT_CONF_NAME, _launch_user(), lib_map, plugin_dir_fallback
        )
        if client_conf:
            env_pairs.append(f"SLURM_CONF=/opt/slurm-host/{CLIENT_CONF_NAME}")
        else:
            env_pairs.append(f"SLURM_CONF={conf_path}")

    # Munge socket dir (Slurm authentication): standard candidates plus a
    # site-specific socket= path from AuthInfo.
    munge_dirs = list(MUNGE_CANDIDATES)
    if conf_path:
        d = _authinfo_socket_dir(conf_path)
        if d and d not in munge_dirs:
            munge_dirs.insert(0, d)
    for d in munge_dirs:
        if os.path.isdir(d):
            mounts.append(f"{os.path.realpath(d)}:/run/munge")
            break

    return mounts, env_pairs
