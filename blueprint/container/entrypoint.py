#!/usr/bin/env python3
"""entrypoint.py: container entrypoint for agentic-workspace images.

Generic across child instances; identity is injected via env vars
(AGENT_NAME, SANDBOX_TOOL, ...). When started as root, it creates the host
user's uid/gid inside the container and re-executes itself under gosu before
running the rest of the setup and finally exec'ing the CLI tool.

Pure stdlib (runs with the image's plain python3; the image also ships gosu).
"""
import os
import re
import shutil
import subprocess
import sys

HOME = "/home"


def is_valid_linux_name(s: str) -> bool:
    return re.fullmatch(r"[a-z_][a-z0-9_-]*[$]?", s) is not None


def _passwd_user_by_uid(uid: int) -> str | None:
    try:
        with open("/etc/passwd") as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if len(parts) >= 3 and parts[2] == str(uid):
                    return parts[0]
    except OSError:
        pass
    return None


def _group_name_by_gid(gid: int) -> str | None:
    try:
        with open("/etc/group") as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if len(parts) >= 3 and parts[2] == str(gid):
                    return parts[0]
    except OSError:
        pass
    return None


def _group_exists(name: str) -> bool:
    try:
        with open("/etc/group") as f:
            for line in f:
                if line.split(":", 1)[0] == name:
                    return True
    except OSError:
        pass
    return False


def _user_exists(name: str) -> bool:
    try:
        with open("/etc/passwd") as f:
            for line in f:
                if line.split(":", 1)[0] == name:
                    return True
    except OSError:
        pass
    return False


def prepare_home_layout(uid: int, gid: int) -> None:
    dirs = [
        "/home", "/home/.config", "/home/.cache", "/home/.local", "/home/.local/bin",
        "/home/.local/share", "/home/.local/state", "/home/.claude", "/home/.claude/commands",
        "/home/.codex", "/home/.opencode", "/home/.opencode/bin", "/home/.pi", "/home/.pi/agent",
        "/home/.pi/bin",
    ]
    for d in dirs:
        try:
            os.makedirs(d, exist_ok=True)
            os.chown(d, uid, gid)
        except OSError:
            pass


def setup_container_user(argv: list[str]) -> None:
    if os.geteuid() != 0 or os.environ.get("AGENTIC_USER_READY") == "1":
        return
    host_uid = int(os.environ.get("HOST_UID", "1000"))
    host_gid = int(os.environ.get("HOST_GID", "1000"))
    requested_user = os.environ.get("HOST_USER", "agent")
    requested_group = os.environ.get("HOST_GROUP", requested_user)
    container_user = requested_user
    container_group = requested_group
    if not is_valid_linux_name(requested_group):
        container_group = f"hostgrp-{host_gid}"
    if not is_valid_linux_name(requested_user):
        container_user = f"hostuser-{host_uid}"

    if _group_name_by_gid(host_gid):
        container_group = _group_name_by_gid(host_gid)  # type: ignore[assignment]
    elif is_valid_linux_name(requested_group) and _group_exists(requested_group):
        container_group = f"hostgrp-{host_gid}"
        subprocess.run(["groupadd", "-g", str(host_gid), container_group], check=False)
    else:
        subprocess.run(["groupadd", "-g", str(host_gid), container_group], check=False)

    if _passwd_user_by_uid(host_uid):
        container_user = _passwd_user_by_uid(host_uid)  # type: ignore[assignment]
    elif is_valid_linux_name(requested_user) and _user_exists(requested_user):
        container_user = f"hostuser-{host_uid}"
        subprocess.run(
            ["useradd", "-l", "-u", str(host_uid), "-g", str(host_gid), "-d", "/home",
             "-M", "-N", "-s", "/bin/bash", container_user],
            check=False,
        )
    else:
        subprocess.run(
            ["useradd", "-l", "-u", str(host_uid), "-g", str(host_gid), "-d", "/home",
             "-M", "-N", "-s", "/bin/bash", container_user],
            check=False,
        )

    # Docker may auto-create bind-mount parents under /home as root.
    prepare_home_layout(host_uid, host_gid)

    os.environ["HOME"] = "/home"
    os.environ["USER"] = container_user
    os.environ["LOGNAME"] = container_user
    os.environ["AGENTIC_USER_READY"] = "1"

    gosu = shutil.which("gosu")
    if gosu is None:
        print("Error: gosu not found in the image.", file=sys.stderr)
        sys.exit(1)
    os.execv(gosu, [gosu, f"{host_uid}:{host_gid}", sys.executable, "/entrypoint.py", *argv])


def setup_home_layout() -> None:
    dirs = [
        "$HOME/.config", "$HOME/.cache", "$HOME/.local/bin", "$HOME/.local/share",
        "$HOME/.local/state", "$HOME/.claude/commands", "$HOME/.config/opencode/commands",
        "$HOME/.local/share/opencode", "$HOME/.codex", "$HOME/.opencode/bin", "$HOME/.pi",
        "$HOME/.pi/agent", "$HOME/.pi/bin",
    ]
    for d in dirs:
        try:
            os.makedirs(os.path.expandvars(d), exist_ok=True)
        except OSError:
            pass
    os.environ.setdefault("XDG_CONFIG_HOME", f"{HOME}/.config")
    os.environ.setdefault("XDG_CACHE_HOME", f"{HOME}/.cache")
    os.environ.setdefault("XDG_DATA_HOME", f"{HOME}/.local/share")
    os.environ.setdefault("XDG_STATE_HOME", f"{HOME}/.local/state")


def link_tool_binary(tool_name: str, target_path: str) -> None:
    tool_bin = shutil.which(tool_name)
    if tool_bin:
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            os.symlink(tool_bin, target_path)
        except OSError:
            pass  # cosmetic convenience symlink; never abort the entrypoint


def setup_pi_extensions() -> None:
    """Copy image-baked pi extensions into ~/.pi/agent/extensions (best-effort)."""
    ext_dir = f"{HOME}/.pi/agent/extensions"
    try:
        os.makedirs(ext_dir, exist_ok=True)
    except OSError:
        return

    # pi-plan-modus: read-only plan mode extension.
    plan_ext = "/opt/pi-extensions/plan-modus/node_modules/pi-plan-modus/extensions/plan-mode.ts"
    if os.path.isfile(plan_ext):
        if not os.path.isfile(f"{ext_dir}/plan-mode.ts"):
            try:
                shutil.copy2(plan_ext, f"{ext_dir}/plan-mode.ts")
            except OSError:
                pass
        # Link the baked node_modules so just-bash resolves. Skip if a real
        # node_modules already exists (user-managed).
        nm = f"{ext_dir}/node_modules"
        if not os.path.isdir(nm) or os.path.islink(nm):
            try:
                if os.path.lexists(nm):
                    os.unlink(nm)
                os.symlink("/opt/pi-extensions/plan-modus/node_modules", nm)
            except OSError:
                pass

    # pi-notify: native desktop notifications (vendored source, no runtime deps).
    notify_src = "/opt/pi-extensions/pi-notify.ts"
    if os.path.isfile(notify_src) and not os.path.isfile(f"{ext_dir}/pi-notify.ts"):
        try:
            shutil.copy2(notify_src, f"{ext_dir}/pi-notify.ts")
        except OSError:
            pass


def setup_pi_subagent() -> None:
    """Install the baked subagent extension + default agent definitions."""
    ext_src = "/opt/pi-extensions/subagent.ts"
    agents_src = "/opt/pi-extensions/agents"
    ext_dir = f"{HOME}/.pi/agent/extensions"
    agents_dir = f"{HOME}/.pi/agent/agents"
    try:
        os.makedirs(ext_dir, exist_ok=True)
        os.makedirs(agents_dir, exist_ok=True)
    except OSError:
        return
    if os.path.isfile(ext_src):
        try:
            shutil.copy2(ext_src, f"{ext_dir}/subagent.ts")
        except OSError:
            pass
    if os.path.isdir(agents_src):
        try:
            for f in os.listdir(agents_src):
                if f.endswith(".md") and not os.path.isfile(os.path.join(agents_dir, f)):
                    shutil.copy2(os.path.join(agents_src, f), os.path.join(agents_dir, f))
        except OSError:
            pass


def main() -> int:
    argv = sys.argv[1:]
    setup_container_user(argv)

    # Scratch space inside the workspace (agents must not use /tmp).
    try:
        os.makedirs("/workspace/.tmp", exist_ok=True)
    except OSError:
        pass

    setup_home_layout()

    # Expose the host Slurm client shim (bind-mounted by the launcher) if present.
    if os.path.isdir("/opt/slurm-host/bin"):
        os.environ["PATH"] = f"/opt/slurm-host/bin:{os.environ.get('PATH', '')}"

    link_tool_binary("opencode", f"{HOME}/.opencode/bin/opencode")
    link_tool_binary("codex", f"{HOME}/.local/bin/codex")
    link_tool_binary("pi", f"{HOME}/.pi/bin/pi")

    tool = os.environ.get("SANDBOX_TOOL", "pi")
    if tool == "pi":
        setup_pi_extensions()
        setup_pi_subagent()

    tool_bin = shutil.which(tool)
    if tool_bin is None:
        print(
            f"Error: Unsupported SANDBOX_TOOL '{tool}' (supported: pi, opencode, claude, codex)",
            file=sys.stderr,
        )
        return 1
    os.execv(tool_bin, [tool, *argv])


if __name__ == "__main__":
    sys.exit(main())
