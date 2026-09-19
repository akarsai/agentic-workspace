"""agentic-workspace CLI: one command, many subcommands.

Usage:
  agentic-workspace launch [OPTS] [DIR]    # run an agent in a sandboxed container
  agentic-workspace install [OPTS]         # one-time interactive installer
  agentic-workspace build [OPTS]           # build base + instance images
  agentic-workspace update [OPTS]          # pull + rebuild
  agentic-workspace scaffold NAME [OPTS]   # create a new instance
  agentic-workspace setup [KEY=VALUE...]   # per-instance setup wizard
  agentic-workspace clean [OPTS]           # remove launcher-managed local state
  agentic-workspace uninstall [OPTS]       # remove an installed instance
"""
from __future__ import annotations

import sys

from . import __version__
from . import build, cleanup, install, launch, scaffold, setup_wizard, uninstall, update

SUBCOMMANDS = {
    "launch": launch.main,
    "install": install.main,
    "build": build.main,
    "update": update.main,
    "scaffold": scaffold.main,
    "setup": setup_wizard.main,
    "clean": cleanup.main,
    "uninstall": uninstall.main,
}


def show_help() -> None:
    print(
        "agentic-workspace: sandboxed AI coding agent harness.\n"
        "\n"
        "Usage:\n"
        "  agentic-workspace <subcommand> [options]\n"
        "\n"
        "Subcommands:\n"
        "  launch      Run an AI coding agent in a sandboxed container\n"
        "  install     One-time interactive installer (launchers + configs + builds)\n"
        "  build       Build the base image and instance images\n"
        "  update      Pull the repo, refresh tool pins, rebuild images\n"
        "  scaffold    Create a new child instance from a template\n"
        "  setup       Per-instance setup wizard (config.py)\n"
        "  clean       Remove launcher-managed local state\n"
        "  uninstall   Remove an installed instance\n"
        "\n"
        "Run 'agentic-workspace <subcommand> --help' for details.\n"
        "\n"
        "Instances (agre, worka, ...) are tiny launchers that hand off to\n"
        "'agentic-workspace launch' with their identity set.\n"
    )


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("--help", "-h"):
        show_help()
        return 0 if argv else 1
    if argv[0] in ("--version", "-V"):
        print(f"agentic-workspace {__version__}")
        return 0
    cmd, rest = argv[0], argv[1:]
    handler = SUBCOMMANDS.get(cmd)
    if handler is None:
        print(f"Error: unknown subcommand: {cmd}", file=sys.stderr)
        show_help()
        return 1
    return handler(rest)


if __name__ == "__main__":
    sys.exit(main())
