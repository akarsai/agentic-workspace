"""Path discovery for the agentic-workspace repo.

Everything derives from this package's location (src/agentic_workspace/), so
the same helpers work whether the code runs from the repo checkout or from an
editable install.
"""
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Absolute path of the repository root (the parent of src/)."""
    return Path(__file__).resolve().parents[2]


def framework_dir() -> Path:
    return repo_root() / "blueprint"


def scripts_dir() -> Path:
    return framework_dir() / "scripts"


def container_dir() -> Path:
    return framework_dir() / "container"


def instances_dir() -> Path:
    return repo_root() / "instances"


def resolve_agent_root() -> Path | None:
    """Locate the instance directory.

    Prefers the AGENT_ROOT environment variable (set by an instance's launcher
    shim); falls back to walking up from the current directory looking for a
    manifest.yaml, matching the old launcher's behaviour.
    """
    env = os.environ.get("AGENT_ROOT")
    if env:
        p = Path(env).resolve()
        if (p / "manifest.yaml").is_file():
            return p
        return None
    d = Path.cwd()
    while d != d.parent:
        if (d / "manifest.yaml").is_file():
            return d
        d = d.parent
    return None


def marker_file() -> Path:
    """Clone-local list of the instances selected by `install` (gitignored)."""
    return repo_root() / ".agentic-instances"
