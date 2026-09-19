"""Per-instance configuration.

New format: a Python module at ${XDG_CONFIG_HOME:-~/.config}/<name>/config.py
that the launcher imports. A legacy shell config (config.sh, `KEY="value"`
lines) is read once and migrated to config.py automatically.

Precedence (highest wins): CLI flags > environment > config file > defaults.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

# Keys the framework understands. install/setup write these; the launcher reads
# them; update switches AGENTIC_CONTAINER_RUNTIME in place.
KNOWN_KEYS = [
    "AGENTIC_CONTAINER_RUNTIME",
    "AGENTIC_AUTH_MODE",
    "AGENTIC_API_PROVIDER",
    "AGENTIC_API_KEY_ENV",
    "AGENTIC_CUSTOM_ENDPOINT",
    "AGENTIC_CUSTOM_ANTHROPIC_ENDPOINT",
    "AGENTIC_CLI_TOOL",
    "AGENTIC_MODEL_SUBSCRIPTION",
    "AGENTIC_DEFAULT_MODEL",
    "AGENTIC_SUBAGENT_MODEL",
    "AGENTIC_SUBAGENT_TOOLS",
    "AGENTIC_SUBAGENT_THINKING",
    "AGENTIC_HTTPS_PROXY",
    "AGENTIC_HTTP_PROXY",
    "AGENTIC_STATE_ROOT",
    "AGENTIC_EXTRA_BIND_DIRS",
    "AGENTIC_EXTRA_ENV",
    "AGENTIC_DOCKER_GPUS",
]

# Legacy AR_* names (pre-AGENTIC_* config files) mapped to their new keys.
LEGACY_AR_MAP = {
    "AR_CONTAINER_RUNTIME": "AGENTIC_CONTAINER_RUNTIME",
    "AR_CLI_TOOL": "AGENTIC_CLI_TOOL",
    "AR_DEFAULT_MODEL": "AGENTIC_DEFAULT_MODEL",
    "AR_STATE_ROOT": "AGENTIC_STATE_ROOT",
    "AR_EXTRA_BIND_DIRS": "AGENTIC_EXTRA_BIND_DIRS",
    "AR_EXTRA_ENV": "AGENTIC_EXTRA_ENV",
    "AR_DOCKER_GPUS": "AGENTIC_DOCKER_GPUS",
    "AR_AUTH_MODE": "AGENTIC_AUTH_MODE",
    "AR_API_PROVIDER": "AGENTIC_API_PROVIDER",
    "AR_API_KEY_ENV": "AGENTIC_API_KEY_ENV",
    "AR_CUSTOM_ENDPOINT": "AGENTIC_CUSTOM_ENDPOINT",
    "AR_CUSTOM_ANTHROPIC_ENDPOINT": "AGENTIC_CUSTOM_ANTHROPIC_ENDPOINT",
    "AR_HTTPS_PROXY": "AGENTIC_HTTPS_PROXY",
    "AR_HTTP_PROXY": "AGENTIC_HTTP_PROXY",
}


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))


def config_dir(name: str) -> Path:
    return config_home() / name


def config_path(name: str) -> Path:
    return config_dir(name) / "config.py"


def legacy_config_path(name: str) -> Path:
    return config_dir(name) / "config.sh"


def load(name: str) -> dict[str, str]:
    """Load config for an instance: config.py, else migrate a legacy config.sh."""
    py = config_path(name)
    if py.is_file():
        return _load_py(py)
    sh = legacy_config_path(name)
    if sh.is_file():
        cfg = _parse_shell(sh)
        write(name, cfg)
        return cfg
    return {}


def _load_py(path: Path) -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("_agentic_config", path)
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return {k: v for k, v in vars(mod).items() if k.startswith("AGENTIC_") and isinstance(v, str)}


def _parse_shell(path: Path) -> dict[str, str]:
    cfg: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        key = LEGACY_AR_MAP.get(key, key)
        if key.startswith("AGENTIC_"):
            cfg[key] = val
    return cfg


def write(name: str, values: dict[str, str]) -> Path:
    path = config_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {name} configuration (managed by agentic-workspace)",
        "# Edit this file, or run: agentic-workspace setup",
        "",
    ]
    for key in sorted(values):
        lines.append(f"{key} = {values[key]!r}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def set_values(name: str, updates: dict[str, str]) -> dict[str, str]:
    """Load, apply updates, write; returns the new config."""
    cfg = load(name)
    for k, v in updates.items():
        if v is not None:
            cfg[k] = v
    write(name, cfg)
    return cfg


def set_runtime(name: str, runtime: str) -> None:
    set_values(name, {"AGENTIC_CONTAINER_RUNTIME": runtime})
