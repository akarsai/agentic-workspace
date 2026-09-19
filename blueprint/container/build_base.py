#!/usr/bin/env python3
"""build_base.py: build the generic base image shared by all child instances.

Standalone entry for direct use (the `build` subcommand calls the same logic):

  python3 build_base.py            # Docker (default)
  python3 build_base.py --apptainer  # Apptainer SIF (Linux HPC, no Docker)

Always forces a rebuild — for Apptainer that means re-running the whole recipe
even when the fingerprint is unchanged (the README documents this file as the
manual force-rebuild path).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentic_workspace import container_build  # noqa: E402


def main() -> int:
    runtime = "docker"
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("--docker", "docker"):
            runtime = "docker"
        elif arg in ("--apptainer", "apptainer"):
            runtime = "apptainer"
        else:
            print(f"Error: unknown runtime argument: {arg}", file=sys.stderr)
            print("  Usage: build_base.py [--docker|--apptainer]", file=sys.stderr)
            return 1
    return container_build.build_base_image(runtime, force=True)


if __name__ == "__main__":
    sys.exit(main())
