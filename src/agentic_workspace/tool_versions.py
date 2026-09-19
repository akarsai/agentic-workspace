"""Resolve and pin upstream tool versions for the container images.

Every third-party tool in the images (claude code, pi, opencode, codex, node,
gh, yq, typst, uv, bun, pnpm, just) is installed at build time with "latest"
resolution. Docker's layer cache freezes a RUN layer as long as its command
text is unchanged, so rebuilding an image alone never moves a tool — it stays
at whatever was latest when the layer cache was created.

`update` therefore refreshes blueprint/container/versions.json (clone-local,
gitignored) and every build passes the pins as build args: a changed version
is the only thing that busts the layer cache, so an unchanged tool costs
nothing and a released one is picked up on the next `update`.

The file is intentionally untracked: writing a tracked file from `update`
would collide with `git pull --rebase`, and pins are per-clone anyway. With
no file present the Dockerfiles fall back to resolving "latest" at build
time — the old behaviour — so fresh clones keep building without it.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .paths import container_dir

VERSIONS_FILE = "versions.json"
TIMEOUT = 15
USER_AGENT = "agentic-workspace (tool pin refresh)"


@dataclass(frozen=True)
class Source:
    """Where the latest version of a tool comes from.

    kind: "github" (releases/latest tag), "npm" (registry /latest), or
    "node" (the Node.js dist index). strip is the tag prefix removed from
    GitHub tags ("v1.2.3" -> "1.2.3", "bun-v1.2.3" -> "1.2.3").
    """

    kind: str
    ref: str
    strip: str = ""


# ARG names double as versions.json keys, so no mapping table is needed
# anywhere: the same names flow into --build-arg and the def converter.
SOURCES: dict[str, Source] = {
    "NODE_VERSION": Source("node", ""),
    "YQ_VERSION": Source("github", "mikefarah/yq", "v"),
    "GH_VERSION": Source("github", "cli/cli", "v"),
    "TYPST_VERSION": Source("github", "typst/typst", "v"),
    "UV_VERSION": Source("github", "astral-sh/uv"),
    "BUN_VERSION": Source("github", "oven-sh/bun", "bun-v"),
    "OPENCODE_VERSION": Source("npm", "opencode-ai"),
    "CLAUDE_CODE_VERSION": Source("npm", "@anthropic-ai/claude-code"),
    "CODEX_VERSION": Source("npm", "@openai/codex"),
    "PI_VERSION": Source("npm", "@earendil-works/pi-coding-agent"),
    # instance-layer tools share the same file and the same refresh
    "PNPM_VERSION": Source("npm", "pnpm"),
    "JUST_VERSION": Source("github", "casey/just", "v"),
}

DISPLAY: dict[str, str] = {
    "NODE_VERSION": "node",
    "YQ_VERSION": "yq",
    "GH_VERSION": "gh",
    "TYPST_VERSION": "typst",
    "UV_VERSION": "uv",
    "BUN_VERSION": "bun",
    "OPENCODE_VERSION": "opencode",
    "CLAUDE_CODE_VERSION": "claude-code",
    "CODEX_VERSION": "codex",
    "PI_VERSION": "pi",
    "PNPM_VERSION": "pnpm",
    "JUST_VERSION": "just",
}


@dataclass
class RefreshResult:
    changed: dict[str, tuple[str, str]] = field(default_factory=dict)  # key -> (old, new)
    unchanged: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # keys whose lookup failed
    path: Path | None = None


def versions_file() -> Path:
    return container_dir() / VERSIONS_FILE


def load_versions(path: Path | None = None) -> dict[str, str]:
    """Read versions.json; missing file -> {} (builds then resolve latest)."""
    path = path or versions_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        from .util import warn

        warn(f"could not read {path} ({exc}); tools will resolve 'latest' at build time")
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if k in SOURCES and str(v).strip()}


def _fetch_json(url: str) -> object:
    headers = {"User-Agent": USER_AGENT}
    if "api.github.com" in url:
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    else:
        # the npm registry (Cloudflare) answers 406 to the GitHub accept type
        headers["Accept"] = "application/json"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def _clean(version: str) -> str | None:
    version = version.strip()
    if not version or any(c.isspace() for c in version):
        return None
    return version


def resolve_one(key: str) -> str | None:
    """Latest upstream version for an ARG name, or None if the lookup fails."""
    src = SOURCES[key]
    try:
        if src.kind == "node":
            data = _fetch_json("https://nodejs.org/dist/index.json")
            return _clean(data[0]["version"])  # type: ignore[index]
        if src.kind == "github":
            data = _fetch_json(f"https://api.github.com/repos/{src.ref}/releases/latest")
            tag = data["tag_name"]  # type: ignore[index]
            if src.strip and tag.startswith(src.strip):
                tag = tag[len(src.strip) :]
            return _clean(tag)
        if src.kind == "npm":
            data = _fetch_json(f"https://registry.npmjs.org/{src.ref}/latest")
            return _clean(data["version"])  # type: ignore[index]
    except Exception:
        return None
    return None


def refresh_versions(path: Path | None = None) -> RefreshResult:
    """Resolve every source, merge with the existing pins, write the file.

    A failed lookup keeps the previous pin (or leaves the tool unpinned, in
    which case the Dockerfile falls back to build-time 'latest' resolution)
    instead of failing the whole update.
    """
    path = path or versions_file()
    current = load_versions(path)
    result = RefreshResult(path=path)

    pins: dict[str, str] = dict(current)
    for key in SOURCES:
        latest = resolve_one(key)
        if latest is None:
            result.failed.append(key)
            continue
        if current.get(key) == latest:
            result.unchanged.append(key)
        else:
            result.changed[key] = (current.get(key, ""), latest)
        pins[key] = latest

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return result


def build_arg_flags(path: Path | None = None) -> list[str]:
    """versions.json as --build-arg flags: ["--build-arg", "KEY=VALUE", ...]."""
    flags: list[str] = []
    for key, value in sorted(load_versions(path).items()):
        if value:
            flags += ["--build-arg", f"{key}={value}"]
    return flags
