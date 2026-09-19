#!/usr/bin/env python3
"""test_sandbox.py: validate the sandbox environment.

Runs inside the container to check CLI tools, filesystem, network, and GPU.
Exit 0 if all required checks pass; GPU failure is a warning only.

Pure stdlib (runs with the container's plain python3).
"""
import os
import shutil
import subprocess
import sys

PASS = 0
FAIL = 0
WARN = 0


def pass_(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


def warn(msg: str) -> None:
    global WARN
    WARN += 1
    print(f"  [WARN] {msg}")


def main() -> int:
    # --- CLI Tools ---
    print("=== CLI Tools ===")
    for tool in ("uv", "git", "gh", "jq", "rg", "yq", "python3"):
        path = shutil.which(tool)
        if path:
            pass_(f"{tool} found ({path})")
        else:
            fail(f"{tool} not found")
    active_tool = os.environ.get("AGENTIC_CLI_TOOL", "pi")
    if shutil.which(active_tool):
        pass_(f"{active_tool} found ({shutil.which(active_tool)})")
    else:
        fail(f"{active_tool} not found")
    print()

    # --- Filesystem ---
    print("=== Filesystem ===")
    if os.path.isdir("/workspace"):
        pass_("/workspace exists")
    else:
        fail("/workspace does not exist")

    testfile = f"/workspace/.sandbox_test_{os.getpid()}"
    try:
        with open(testfile, "w"):
            pass
        os.unlink(testfile)
        pass_("/workspace is writable")
    except OSError:
        fail("/workspace is not writable")

    if os.path.isdir("/workspace/.tmp"):
        pass_("/workspace/.tmp scratch dir exists")
    else:
        warn("/workspace/.tmp missing")

    if os.environ.get("HOME") == "/home":
        pass_("HOME is /home")
    else:
        fail(f"HOME is '{os.environ.get('HOME')}' (expected /home)")

    tmpfile = f"/tmp/.sandbox_test_{os.getpid()}"
    try:
        with open(tmpfile, "w"):
            pass
        os.unlink(tmpfile)
        pass_("/tmp is writable")
    except OSError:
        fail("/tmp is not writable")
    print()

    # --- Network ---
    print("=== Network ===")
    import urllib.request

    reachable = False
    for url in ("https://api.anthropic.com/", "https://www.google.com/"):
        try:
            with urllib.request.urlopen(url, timeout=10):
                reachable = True
                break
        except OSError:
            continue
    if reachable:
        pass_("Network connectivity (HTTPS)")
    else:
        fail("No network connectivity")
    print()

    # --- GPU (optional) ---
    print("=== GPU (optional) ===")
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=30,
            ).stdout
            if out.strip():
                pass_(f"GPU available: {out.strip().splitlines()[0]}")
            else:
                warn("nvidia-smi found but no GPU reported (no GPU allocated?)")
        except (subprocess.SubprocessError, OSError):
            warn("nvidia-smi found but failed to run (no GPU allocated?)")
    else:
        warn("nvidia-smi not found (no GPU support)")
    print()

    # --- Tool-specific checks ---
    active_tool = os.environ.get("AGENTIC_CLI_TOOL", "pi")
    print(f"=== Active Tool: {active_tool} ===")
    if active_tool == "opencode":
        if os.path.isfile(f"{os.environ.get('HOME', '/home')}/.config/opencode/opencode.json"):
            pass_("OpenCode config file is mounted")
        else:
            warn("OpenCode config file not found (may use defaults)")
    elif active_tool == "pi":
        if os.path.isdir(f"{os.environ.get('HOME', '/home')}/.pi"):
            pass_("Pi home directory is mounted")
        else:
            warn("Pi home directory not found (may use defaults)")
    elif active_tool == "claude":
        if os.path.isdir(f"{os.environ.get('HOME', '/home')}/.claude"):
            pass_("Claude Code home directory is mounted")
        else:
            warn("Claude Code home directory not found (may use defaults)")
    elif active_tool == "codex":
        if os.path.isdir(f"{os.environ.get('HOME', '/home')}/.codex"):
            pass_("Codex CLI home directory is mounted")
        else:
            warn("Codex CLI home directory not found (may use defaults)")
    print()

    # --- Summary ---
    print("================================")
    print(f"Results: {PASS} passed, {FAIL} failed, {WARN} warnings")
    if FAIL == 0:
        print("Status: ALL REQUIRED CHECKS PASSED")
        return 0
    print("Status: SOME CHECKS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
