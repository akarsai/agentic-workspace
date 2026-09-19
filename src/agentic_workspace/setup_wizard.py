"""The `settings` subcommand: interactive settings for a child instance.

Generates ${XDG_CONFIG_HOME:-~/.config}/<name>/config.py.

Usage:
  agentic-workspace settings [NAME] [KEY=VALUE...]  # bare: asks which instance
  <instance> --settings                            # Interactive settings menu
  <instance> --settings KEY=VALUE [KEY=VALUE...]   # Set individual values

With an existing config, the interactive mode opens a settings menu: pick a
setting, change it (Enter keeps the current value), repeat, then save. Without
a config, the full wizard asks every question once, top to bottom.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import config as cfgmod
from .manifest import load_manifest
from .paths import resolve_agent_root
from .util import banner, bold, die, say, warn

VALID_TOOLS = ("pi", "opencode", "claude", "codex")


def _ask(prompt: str, current: str, fallback: str) -> str:
    """Ask for a value, keeping the current value on Enter (EOF-safe)."""
    try:
        val = input(f"{prompt} [{current or fallback}]: ").strip()
    except EOFError:
        val = ""
    if val:
        return val
    return current or fallback


def ask_tool(current: str) -> str:
    """Interactive CLI-tool prompt with validation (Enter keeps current)."""
    print("  The coding agent CLI executed inside the container (pi, opencode, claude, or codex).")
    while True:
        tool = _ask("CLI tool", current, "pi")
        if tool in VALID_TOOLS:
            return tool
        print(f"  Unknown tool '{tool}' (supported: {', '.join(VALID_TOOLS)}). Try again.")


def ask_subscription(current: str) -> str:
    """Interactive model-subscription prompt with validation.

    Returns the '<provider>:<model>' spec, or '' for no subscription.
    """
    from . import subscription as sub

    print("  A flat-rate coding-plan provider used as the DEFAULT model everywhere:")
    print("  the main session AND all subagents (explicit settings still win).")
    print("  Known providers:")
    for alias, p in sorted(sub.SUBSCRIPTION_PROVIDERS.items()):
        print(f"    {alias:<8} {p['label']} (export {p['api_key_env']})")
    print("  Format: <provider>:<model>   e.g.  zai:glm-5.3")
    print("  Enter 'none' to clear an existing subscription.")
    while True:
        val = _ask("Subscription", current, "")
        if not val:
            return current
        if val.lower() in ("none", "off", "-"):
            return ""
        parsed = sub.parse(val)
        if parsed is None:
            print("  Format is '<provider>:<model>' (or '<provider>/<model>'). Try again.")
            continue
        if sub.provider(parsed[0]) is None:
            known = ", ".join(sorted(sub.SUBSCRIPTION_PROVIDERS))
            print(f"  Unknown provider {parsed[0]!r} (known: {known}). Try again.")
            continue
        return ":".join(parsed)


def describe_subscription(sub_spec: str) -> None:
    from . import subscription as sub

    alias, model = sub.parse(sub_spec) or ("", "")
    p = sub.provider(alias) or {}
    print(f"  → {p.get('label', alias)} -- {model} (default + subagent model)")
    key_envs = " or ".join(sub.api_key_envs(alias))
    print(f"  → export {key_envs} in your shell before launching")


def ask_https_proxy(current: str) -> str:
    return _ask("HTTPS proxy (e.g., http://proxy:3128)", current, "")


def ask_http_proxy(current: str, https_proxy: str) -> str:
    return _ask("HTTP proxy", current, https_proxy)


def ask_state_root(name: str, current: str) -> str:
    return _ask("State/cache directory", current, f"{Path.home() / '.cache' / name}")


def ask_extra_dirs(current: str) -> str:
    print("  By default, only your project directory is accessible inside the sandbox.")
    print("  You can allow additional directories (e.g., datasets, shared storage).")
    print("  Separate paths with commas, colons, or spaces.")
    extra = _ask("Extra directories (e.g., /data/models, /shared/datasets)", current, "")
    return normalize_extra_dirs(extra)


def normalize_extra_dirs(extra: str) -> str:
    extra = re.sub(r"[,\s]+", ":", extra)
    return re.sub(r":{2,}", ":", extra).strip(":")


def settings_menu(agent_root: Path) -> int:
    """Interactive settings menu: change one setting at a time, then save."""
    manifest = load_manifest(agent_root / "manifest.yaml")
    name = manifest.name

    # Start from the current config so keys the menu does not show
    # (subagent settings, extra env, GPU mode, ...) survive.
    values = {k: v for k, v in cfgmod.load(name).items() if k in cfgmod.KNOWN_KEYS}

    banner(f"{name} - Settings")
    print(f"Config: {cfgmod.config_path(name)}")
    print("Pick a setting to change. Enter keeps the current value.")

    while True:
        def show(label: str, key: str, none_mark: str = "(none)") -> None:
            val = values.get(key, "")
            print(f" {label:<24} {val if val else none_mark}")

        print()
        bold("Settings:")
        show("1) CLI tool", "AGENTIC_CLI_TOOL")
        show("2) Model subscription", "AGENTIC_MODEL_SUBSCRIPTION")
        show("3) HTTPS proxy", "AGENTIC_HTTPS_PROXY")
        show("4) HTTP proxy", "AGENTIC_HTTP_PROXY")
        show("5) State directory", "AGENTIC_STATE_ROOT")
        show("6) Extra sandbox dirs", "AGENTIC_EXTRA_BIND_DIRS")
        print(" 7) Run the full wizard")
        print(" q) Save and exit")
        try:
            choice = input("Setting [1-7, q]: ").strip().lower()
        except EOFError:
            choice = "q"
        if choice in ("q", "quit", "exit"):
            break
        if choice == "":
            continue
        if choice == "1":
            print()
            values["AGENTIC_CLI_TOOL"] = ask_tool(values.get("AGENTIC_CLI_TOOL", "pi"))
        elif choice == "2":
            print()
            spec = ask_subscription(values.get("AGENTIC_MODEL_SUBSCRIPTION", ""))
            values["AGENTIC_MODEL_SUBSCRIPTION"] = spec
            if spec:
                describe_subscription(spec)
            else:
                print("  → subscription cleared")
        elif choice == "3":
            print()
            values["AGENTIC_HTTPS_PROXY"] = ask_https_proxy(values.get("AGENTIC_HTTPS_PROXY", ""))
        elif choice == "4":
            print()
            values["AGENTIC_HTTP_PROXY"] = ask_http_proxy(
                values.get("AGENTIC_HTTP_PROXY", ""), values.get("AGENTIC_HTTPS_PROXY", "")
            )
        elif choice == "5":
            print()
            values["AGENTIC_STATE_ROOT"] = ask_state_root(name, values.get("AGENTIC_STATE_ROOT", ""))
            print(f"  → {values['AGENTIC_STATE_ROOT']}")
        elif choice == "6":
            print()
            values["AGENTIC_EXTRA_BIND_DIRS"] = ask_extra_dirs(values.get("AGENTIC_EXTRA_BIND_DIRS", ""))
            if values["AGENTIC_EXTRA_BIND_DIRS"]:
                print(f"  → {values['AGENTIC_EXTRA_BIND_DIRS']}")
        elif choice == "7":
            cfgmod.write(name, values)
            return wizard_main(agent_root)
        else:
            print("Enter a number 1-7, or q to save and exit.")
            continue

    cfgmod.write(name, values)
    print()
    say(f"Configuration saved to: {cfgmod.config_path(name)}")
    return 0


def wizard_main(agent_root: Path) -> int:
    """Full wizard: ask every question once, top to bottom."""
    manifest = load_manifest(agent_root / "manifest.yaml")
    name = manifest.name

    banner(f"{name} - Setup Wizard")
    print()

    current = cfgmod.load(name)
    runtime = current.get("AGENTIC_CONTAINER_RUNTIME", "docker")
    default_model = current.get("AGENTIC_DEFAULT_MODEL", "")
    auth_mode = current.get("AGENTIC_AUTH_MODE", "tool")

    print("─── CLI Tool ───")
    tool = ask_tool(current.get("AGENTIC_CLI_TOOL", "pi"))
    print()

    print("─── Model Subscription (leave empty if none) ───")
    sub_spec = ask_subscription(current.get("AGENTIC_MODEL_SUBSCRIPTION", ""))
    if sub_spec:
        describe_subscription(sub_spec)
    print()

    print("─── Network Proxy (leave empty if not needed) ───")
    https_proxy = ask_https_proxy(current.get("AGENTIC_HTTPS_PROXY", ""))
    http_proxy = current.get("AGENTIC_HTTP_PROXY", "")
    if https_proxy:
        http_proxy = ask_http_proxy(http_proxy, https_proxy)
    print()

    print("─── Local State ───")
    state_root = ask_state_root(name, current.get("AGENTIC_STATE_ROOT", ""))
    print(f"  → {state_root}")
    print()

    print("─── Extra Sandbox Directories ───")
    extra = ask_extra_dirs(current.get("AGENTIC_EXTRA_BIND_DIRS", ""))
    if extra:
        print(f"  → {extra}")
    print()

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
    print(f"  {name} --settings KEY=VALUE")
    print()
    print("Next steps:")
    print(f"  1. Build the container: {name} --build")
    print(f"  2. Launch:              {name}")
    print("════════════════════════════════════════════════════════════════")
    return 0


def main(argv: list[str], agent_root: Path | None = None) -> int:
    from . import interactive

    if any(a in ("--help", "-h") for a in argv):
        print(
            "Usage: agentic-workspace settings [NAME] [KEY=VALUE...]\n"
            "       <name> --settings [KEY=VALUE...]\n"
            "\n"
            "Interactive: asks which instance when bare, then opens the settings\n"
            "menu (or the full wizard when no config exists yet).\n"
            "KEY=VALUE sets individual values without the menu."
        )
        return 0

    agent_root = agent_root or resolve_agent_root()
    if agent_root is None:
        # Bare CLI: settings [NAME] [KEY=VALUE...]. Without a name, ask.
        name, argv = interactive.extract_instance_arg(argv)
        if name is None:
            name = interactive.pick_instance("Settings")
            if name is None:
                warn("No instance selected.")
                return 0
        if not interactive.is_instance(name):
            return die(f"instance '{name}' not found under instances/")
        agent_root = interactive.instance_root(name)
    manifest = load_manifest(agent_root / "manifest.yaml")
    name = manifest.name

    if argv and argv[0].startswith("--"):
        return die(f"unknown option: {argv[0]}")

    # KEY=VALUE mode.
    if argv and "=" in argv[0]:
        config_file = cfgmod.config_path(name)
        if not config_file.is_file():
            return die(f"No config file found. Run '{name} --settings' first (without arguments).")
        cfg = cfgmod.load(name)
        for arg in argv:
            if "=" not in arg:
                return die(f"expected KEY=VALUE, got: {arg}")
            key, _, val = arg.partition("=")
            if key == "AGENTIC_EXTRA_BIND_DIRS":
                val = normalize_extra_dirs(val)
            cfg[key] = val
            print(f"Updated: {key}={val!r}")
        cfgmod.write(name, cfg)
        return 0

    # Interactive: menu when a config exists, full wizard on first run.
    if cfgmod.config_path(name).is_file():
        return settings_menu(agent_root)
    return wizard_main(agent_root)
