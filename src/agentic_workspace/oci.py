"""Docker/Apptainer runtime detection and argument builders."""
from __future__ import annotations

import os
import platform
import shutil

from .util import which


def is_linux() -> bool:
    return platform.system() == "Linux"


def docker_available() -> bool:
    return which("docker") is not None


def apptainer_available() -> bool:
    return which("apptainer") is not None


def nvidia_available() -> bool:
    return is_linux() and which("nvidia-smi") is not None


def detect_runtime() -> str:
    if docker_available():
        return "docker"
    if apptainer_available():
        return "apptainer"
    return "docker"


def gpu_arguments(runtime: str, docker_gpus: str) -> list[str]:
    """GPU passthrough args for docker/apptainer, honouring the gpus policy
    (all | auto | none). Mirrors the old launcher gating."""
    if runtime == "apptainer":
        if docker_gpus == "all" or (docker_gpus == "auto" and nvidia_available()):
            return ["--nv"]
        return []
    # docker
    if docker_gpus == "all" or (docker_gpus == "auto" and nvidia_available()):
        return ["--gpus", "all"]
    return []


def effective_runtime(config: dict[str, str], env: dict[str, str] | None = None) -> str:
    """Resolve the container runtime with the old launcher's fallback: if
    docker was selected but is not installed and apptainer is available on
    Linux, switch to apptainer with a warning."""
    env = env or os.environ
    runtime = config.get("AGENTIC_CONTAINER_RUNTIME") or detect_runtime()
    if (
        runtime == "docker"
        and not docker_available()
        and is_linux()
        and apptainer_available()
    ):
        print("Warning: docker not found; falling back to Apptainer.", file=__import__("sys").stderr)
        print("  Apptainer requires an apptainer-built image (SIF):", file=__import__("sys").stderr)
        print("  run '<name> --apptainer --build' first.", file=__import__("sys").stderr)
        runtime = "apptainer"
    return runtime


def api_key_env_for(tool: str) -> str:
    """Preferred API key env var per CLI tool."""
    return {
        "pi": "DEEPSEEK_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "codex": "OPENAI_API_KEY",
    }.get(tool, "DEEPSEEK_API_KEY")  # opencode: any provider, default deepseek
