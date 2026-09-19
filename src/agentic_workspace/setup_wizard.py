"""The `setup` subcommand: interactive setup wizard for a child instance.

Generates ${XDG_CONFIG_HOME:-~/.config}/<name>/config.py.

Usage:
  <instance> --setup                          # Full interactive wizard
  <instance> --setup KEY=VALUE [KEY=VALUE...]  # Set individual values
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import config as cfgmod
from .manifest import load_manifest
from .paths import resolve_agent_root
from .util import banner, bold, die, say


def _ask(prompt: str, current: str, fallback: str) -> str:
    """Ask for a value, keeping the current value on Enter (EOF-safe)."""
    try:
        val = input(f"{prompt} [{current or fallback}]: ").strip()
    except EOFError:
        val = ""
    if val:
        return val
    return current or fallback


def wizard_main(agent_root: Path) -> int:
    manifest = load_manifest(agent_root / "manifest.yaml")
    name = manifest.name
    config_file = cfgmod.config_path(name)

    banner(f"{name} - Setup Wizard")
    print()

    current = cfgmod.load(name)
    if config_file.is_file():
        print(f"Existing configuration found: {config_file}")
        print()
        print("Tip: To change individual settings without re-running the full wizard:")
        print(f"  {name} --setup KEY=VALUE")
        print(f"  e.g., {name} --setup AGENTIC_EXTRA_BIND_DIRS=\"/data/models,/shared/datasets\"")
        print()
        try:
            overwrite = input("Re-run full wizard? [y/N] ").strip()
        except EOFError:
            overwrite = "n"
        if not overwrite.lower().startswith("y"):
            print("Setup cancelled.")
            return 0
        print()

    runtime = current.get("AGENTIC_CONTAINER_RUNTIME", "docker")
    default_model = current.get("AGENTIC_DEFAULT_MODEL", "")
    auth_mode = current.get("AGENTIC_AUTH_MODE", "tool")

    # 1. CLI Tool
    print("─── CLI Tool ───")
    print("  The coding agent CLI executed inside the container (pi, opencode, claude, or codex).")
    tool = _ask("CLI tool (pi, opencode, claude, or codex)", current.get("AGENTIC_CLI_TOOL", "pi"), "pi")
    if tool not in ("pi", "opencode", "claude", "codex"):
        print(f"  Unknown tool '{tool}'; using pi.")
        tool = "pi"
    print()

    # 2. Model Subscription
    print("─── Model Subscription (leave empty if none) ───")
    print("  A flat-rate coding-plan provider used as the DEFAULT model everywhere:")
    print("  the main session AND all subagents (explicit settings still win).")
    print("  Known providers:")
    from . import subscription as sub

    for alias, p in sorted(sub.SUBSCRIPTION_PROVIDERS.items()):
        print(f"    {alias:<8} {p['label']} (export {p['api_key_env']})")
    print("  Format: <provider>:<model>   e.g.  zai:glm-5.3")
    print("  Enter 'none' to clear an existing subscription.")
    print()
    sub_current = current.get("AGENTIC_MODEL_SUBSCRIPTION", "")
    while True:
        val = _ask("Subscription", sub_current, "")
        if val.lower() in ("none", "off", "-"):
            sub_spec = ""
            break
        parsed = sub.parse(val)
        if not val:
            sub_spec = sub_current
            break
        if parsed is None:
            print("  Format is '<provider>:<model>' (or '<provider>/<model>'). Try again.")
            continue
        if sub.provider(parsed[0]) is None:
            known = ", ".join(sorted(sub.SUBSCRIPTION_PROVIDERS))
            print(f"  Unknown provider {parsed[0]!r} (known: {known}). Try again.")
            continue
        sub_spec = ":".join(parsed)
        break
    if sub_spec:
        alias, model = sub.parse(sub_spec) or ("", "")
        p = sub.provider(alias) or {}
        print(f"  → {p.get('label', alias)} -- {model} (default + subagent model)")
        key_envs = " or ".join(sub.api_key_envs(alias))
        print(f"  → export {key_envs} in your shell before launching")
    print()

    # 3. Network Proxy
    print("─── Network Proxy (leave empty if not needed) ───")
    https_proxy = _ask("HTTPS proxy (e.g., http://proxy:3128)", current.get("AGENTIC_HTTPS_PROXY", ""), "")
    http_proxy = current.get("AGENTIC_HTTP_PROXY", "")
    if https_proxy:
        http_proxy = _ask("HTTP proxy", http_proxy, https_proxy)
    print()

    # 4. Local State
    print("─── Local State ───")
    state_root = _ask(
        "State/cache directory",
        current.get("AGENTIC_STATE_ROOT", ""),
        f"{Path.home() / '.cache' / name}",
    )
    print(f"  → {state_root}")
    print()

    # 5. Extra sandbox directories
    print("─── Extra Sandbox Directories ───")
    print("  By default, only your project directory is accessible inside the sandbox.")
    print("  You can allow additional directories (e.g., datasets, shared storage).")
    print("  Separate paths with commas, colons, or spaces.")
    print()
    extra = _ask("Extra directories (e.g., /data/models, /shared/datasets)", current.get("AGENTIC_EXTRA_BIND_DIRS", ""), "")
    extra = re.sub(r"[,\s]+", ":", extra)
    extra = re.sub(r":{2,}", ":", extra).strip(":")
    if extra:
        print(f"  → {extra}")
    print()

    # Start from the current config so keys the wizard does not ask about
    # (subagent settings, extra env, GPU mode, subscription, ...) survive a
    # re-run instead of being dropped from the file.
    values = {k: v for k, v in current.items() if k in cfgmod.KNOWN_KEYS}
    values.update({
        "AGENTIC_CONTAINER_RUNTIME": runtime,
        "AGENTIC_AUTH_MODE": auth_mode,
        "AGENTIC_API_PROVIDER": current.get("AGENTIC_API_PROVIDER", ""),
        "AGENTIC_API_KEY_ENV": current.get("AGENTIC_API_KEY_ENV", ""),
        "AGENTIC_CUSTOM_ENDPOINT": current.get("AGENTIC_CUSTOM_ENDPOINT", ""),
        "AGENTIC_CUSTOM_ANTHROPIC_ENDPOINT": current.get("AGENTIC_CUSTOM_ANTHROPIC_ENDPOINT", ""),
        "AGENTIC_CLI_TOOL": tool,
        "AGENTIC_MODEL_SUBSCRIPTION": sub_spec,
        "AGENTIC_DEFAULT_MODEL": default_model,
        "AGENTIC_HTTPS_PROXY": https_proxy,
        "AGENTIC_HTTP_PROXY": http_proxy,
        "AGENTIC_STATE_ROOT": state_root,
        "AGENTIC_EXTRA_BIND_DIRS": extra,
    })
    cfgmod.write(name, values)

    print("════════════════════════════════════════════════════════════════")
    print(f"Configuration saved to: {cfgmod.config_path(name)}")
    print()
    print("Tip: Change individual settings later with:")
    print(f"  {name} --setup KEY=VALUE")
    print()
    print("Next steps:")
    print(f"  1. Build the container: {name} --build")
    print(f"  2. Launch: {name}")
    print("════════════════════════════════════════════════════════════════")
    return 0


def main(argv: list[str], agent_root: Path | None = None) -> int:
    agent_root = agent_root or resolve_agent_root()
    if agent_root is None:
        return die("cannot locate manifest.yaml (set AGENT_ROOT or run from an instance directory).")
    manifest = load_manifest(agent_root / "manifest.yaml")
    name = manifest.name

    if argv and argv[0].startswith("--"):
        return die(f"unknown option: {argv[0]}")

    # KEY=VALUE mode.
    if argv and "=" in argv[0]:
        config_file = cfgmod.config_path(name)
        if not config_file.is_file():
            return die(f"No config file found. Run '{name} --setup' first (without arguments).")
        cfg = cfgmod.load(name)
        for arg in argv:
            if "=" not in arg:
                return die(f"expected KEY=VALUE, got: {arg}")
            key, _, val = arg.partition("=")
            if key == "AGENTIC_EXTRA_BIND_DIRS":
                val = re.sub(r"[,\s]+", ":", val)
                val = re.sub(r":{2,}", ":", val).strip(":")
            cfg[key] = val
            print(f"Updated: {key}={val!r}")
        cfgmod.write(name, cfg)
        return 0

    return wizard_main(agent_root)
