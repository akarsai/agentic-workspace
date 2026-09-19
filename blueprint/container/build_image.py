#!/usr/bin/env python3
"""build_image.py: build the container image for a child instance.

Uses the instance manifest (AGENT_ROOT/manifest.yaml) for identity and the
child's own container/Dockerfile (layered on the generic base image).

Standalone entry for direct use (the `build` subcommand calls the same logic):

  <name> --build                 # Docker (default)
  <name> --apptainer --build     # Apptainer SIF (Linux HPC, no Docker)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentic_workspace import container_build  # noqa: E402
from agentic_workspace.paths import resolve_agent_root  # noqa: E402


def main() -> int:
    runtime = "docker"
    for a in sys.argv[1:]:
        if a in ("--docker", "docker"):
            runtime = "docker"
        elif a in ("--apptainer", "apptainer"):
            runtime = "apptainer"
        else:
            print(f"Error: unknown runtime argument: {a}", file=sys.stderr)
            print("  Usage: <name> --build [--docker|--apptainer]", file=sys.stderr)
            return 1
    agent_root = resolve_agent_root()
    if agent_root is None or not (agent_root / "manifest.yaml").is_file():
        print("Error: AGENT_ROOT is not set to an instance directory.", file=sys.stderr)
        print("  Invoke this through the instance launcher (e.g. <name> --build).", file=sys.stderr)
        return 1
    return container_build.build_instance_image(agent_root, runtime)


if __name__ == "__main__":
    sys.exit(main())
