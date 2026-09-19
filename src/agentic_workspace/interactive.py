"""Interactive instance selection shared by the CLI subcommands.

Every subcommand still accepts explicit names and flags for scripted use.
When a command is run bare and information is missing, it asks:

  pick_instance   numbered single-choice menu (one instance)
  pick_instances  install-style all-or-per-name selection (several)

Both are EOF-safe: piped or empty stdin means "nothing selected", so the
commands stay scriptable.
"""
from __future__ import annotations

from pathlib import Path

from .manifest import list_instances
from .paths import instances_dir, marker_file
from .util import confirm, warn


def read_marker() -> list[str]:
    """Instances selected by `install` (the clone-local .agentic-instances marker)."""
    marker = marker_file()
    if not marker.is_file():
        return []
    names = []
    for line in marker.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def available_instances() -> list[str]:
    return list_instances(instances_dir())


def instance_root(name: str) -> Path:
    return instances_dir() / name


def is_instance(name: str) -> bool:
    return (instance_root(name) / "manifest.yaml").is_file()


def extract_instance_arg(argv: list[str], value_opts: set[str] = frozenset()) -> tuple[str | None, list[str]]:
    """Split a positional instance name out of argv, option-aware.

    Options listed in `value_opts` consume the following argument, so their
    values are never mistaken for an instance name. Returns (name, rest).
    """
    name: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in value_opts:
            if i + 1 < len(argv):
                rest += [a, argv[i + 1]]
                i += 2
                continue
            rest.append(a)
            i += 1
            continue
        if a.startswith("-"):
            rest.append(a)
        elif name is None:
            name = a
        else:
            rest.append(a)
        i += 1
    return name, rest


def pick_instance(action: str, *, candidates: list[str] | None = None) -> str | None:
    """Ask which single instance to act on. Returns the name, or None when
    the user declines or stdin is at EOF (nothing selected)."""
    names = candidates if candidates is not None else available_instances()
    names = [n for n in names if n]
    if not names:
        warn("no instances found under instances/")
        return None
    if len(names) == 1:
        return names[0]
    installed = [n for n in read_marker() if n in names]
    default = installed[0] if len(installed) == 1 else None
    print(f"{action}: pick an instance")
    for i, n in enumerate(names, 1):
        mark = "*" if n == default else " "
        print(f" {i:>2}){mark} {n}")
    hint = f" (Enter = {default})" if default else ""
    while True:
        try:
            raw = input(f"Number{hint}: ").strip()
        except EOFError:
            return None
        if raw == "" and default is not None:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        print(f"Enter a number between 1 and {len(names)}.")


def pick_instances(action: str, *, candidates: list[str] | None = None) -> list[str]:
    """Ask which instances to act on: all, or a per-name selection."""
    names = candidates if candidates is not None else available_instances()
    names = [n for n in names if n]
    if not names:
        warn("no instances found under instances/")
        return []
    if len(names) == 1:
        return names
    if confirm(f"{action} all instances?", "Y/n"):
        return names
    return [n for n in names if confirm(f"{action} {n}?", "y/N")]
