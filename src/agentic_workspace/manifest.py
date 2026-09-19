"""Read instance manifests (flat YAML) via PyYAML.

Replaces the old grep/sed/awk `manifest_get` parsing in the bash launcher.
"""
from __future__ import annotations

from pathlib import Path

import yaml


class Manifest:
    def __init__(self, data: dict, path: Path):
        self.data = data or {}
        self.path = path

    def get(self, key: str, default: str | None = None) -> str | None:
        v = self.data.get(key)
        if v is None:
            return default
        return str(v)

    @property
    def name(self) -> str:
        return self.get("name") or "agentic"

    @property
    def description(self) -> str:
        return self.get("description") or "AI coding agent"

    @property
    def image(self) -> str:
        return self.get("image") or f"{self.name}:latest"

    @property
    def tool(self) -> str:
        return (self.get("tool") or "pi").lower()

    @property
    def instruction_template(self) -> str:
        return self.get("instruction_template") or "INSTRUCTIONS.md"

    @property
    def instruction_target(self) -> str:
        return self.get("instruction_target") or "AGENTS.md"

    @property
    def commands(self) -> list[str]:
        raw = self.get("commands")
        if not raw:
            return []
        return [c.strip() for c in raw.split(",") if c.strip()]


def load_manifest(path: Path) -> Manifest:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Manifest(data, path)


def list_instances(instances_dir: Path) -> list[str]:
    """Names of all instance directories (sorted), from their manifests."""
    names = []
    for d in sorted(instances_dir.iterdir()):
        if not d.is_dir():
            continue
        manifest = d / "manifest.yaml"
        if not manifest.is_file():
            continue
        m = load_manifest(manifest)
        if m.name:
            names.append(m.name)
    return names
