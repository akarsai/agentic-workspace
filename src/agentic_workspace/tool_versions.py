"""Resolve and pin upstream tool versions (and checksums) for container images.

Every third-party tool in the images (claude code, pi, opencode, codex, node,
gh, yq, typst, uv, bun, pnpm, just) is installed at build time with "latest"
resolution. Docker's layer cache freezes a RUN layer as long as its command
text is unchanged, so rebuilding an image alone never moves a tool. It stays
at whatever was latest when the layer cache was created.

`update` therefore refreshes blueprint/container/versions.json (clone-local,
gitignored) and every build passes the pins as build args: a changed version
is the only thing that busts the layer cache, so an unchanged tool costs
nothing and a released one is picked up on the next `update`.

Checksums: tools installed by a package manager (npm CLIs, pnpm, rust-just
via uv) are integrity-verified by npm/uv against registry metadata. The
direct curl downloads (node, yq, gh, typst, uv, bun) are not, so their pins
carry a sha256 per architecture resolved from the same upstream source
(GitHub release asset digests, the Node.js SHASUMS256.txt). Builds verify
the download when a checksum is present and warn loudly when it is not.

The file is intentionally untracked: writing a tracked file from `update`
would collide with `git pull --rebase`, and pins are per-clone anyway. With
no file present the Dockerfiles fall back to resolving "latest" at build
time (the old behaviour, no checksum verification), so fresh clones keep
building without it.

Schema (v2): {"KEY": {"version": "1.2.3", "sha256": {"amd64": "<hex>"}}}.
Plain-string values from v1 files are read as {"version": value}.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .paths import container_dir

VERSIONS_FILE = "versions.json"
TIMEOUT = 15
USER_AGENT = "agentic-workspace (tool pin refresh)"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Source:
    """Where the latest version of a tool comes from.

    kind: "github" (releases/latest tag), "npm" (registry /latest), or
    "node" (the Node.js dist index). strip is the tag prefix removed from
    GitHub tags ("v1.2.3" -> "1.2.3", "bun-v1.2.3" -> "1.2.3").

    assets: for direct-download tools, dpkg-architecture -> release-asset
    name template ({version} and {arch} substituted). When present, the
    release-asset digests become per-arch sha256 pins.
    """

    kind: str
    ref: str
    strip: str = ""
    assets: tuple[tuple[str, str], ...] = ()


# ARG names double as versions.json keys, so no mapping table is needed
# anywhere: the same names flow into --build-arg and the def converter.
SOURCES: dict[str, Source] = {
    "NODE_VERSION": Source("node", ""),
    "YQ_VERSION": Source(
        "github", "mikefarah/yq", "v",
        assets=(("amd64", "yq_linux_{arch}"), ("arm64", "yq_linux_{arch}")),
    ),
    "GH_VERSION": Source(
        "github", "cli/cli", "v",
        assets=(("amd64", "gh_{version}_linux_{arch}.tar.gz"), ("arm64", "gh_{version}_linux_{arch}.tar.gz")),
    ),
    "TYPST_VERSION": Source(
        "github", "typst/typst", "v",
        assets=(("x86_64", "typst-{arch}-unknown-linux-musl.tar.xz"), ("aarch64", "typst-{arch}-unknown-linux-musl.tar.xz")),
    ),
    "UV_VERSION": Source(
        "github", "astral-sh/uv", "",
        assets=(("x86_64", "uv-{arch}-unknown-linux-gnu.tar.gz"), ("aarch64", "uv-{arch}-unknown-linux-gnu.tar.gz")),
    ),
    "BUN_VERSION": Source(
        "github", "oven-sh/bun", "bun-v",
        assets=(("x64", "bun-linux-{arch}.zip"), ("aarch64", "bun-linux-{arch}.zip")),
    ),
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


def _normalize(value: object) -> dict:
    """versions.json entry -> pin dict (v1 strings become {"version": s})."""
    if isinstance(value, dict):
        pin = dict(value)
        if str(pin.get("version", "")).strip():
            return pin
        return {}
    if isinstance(value, str) and value.strip():
        return {"version": value.strip()}
    return {}


def load_pins(path: Path | None = None) -> dict[str, dict]:
    """Read versions.json as pin dicts; missing file -> {}."""
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
    pins = {k: _normalize(v) for k, v in data.items() if k in SOURCES}
    return {k: v for k, v in pins.items() if v}


def load_versions(path: Path | None = None) -> dict[str, str]:
    """versions.json as {ARG: version} (what Dockerfiles reference)."""
    return {k: p["version"] for k, p in load_pins(path).items() if str(p.get("version", "")).strip()}


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


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def _clean(version: str) -> str | None:
    version = version.strip()
    if not version or any(c.isspace() for c in version):
        return None
    return version


def _asset_digest(assets: list, name: str) -> str | None:
    """sha256 hex of a release asset, from GitHub's asset digest field."""
    for asset in assets:
        if asset.get("name") != name:
            continue
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            hexd = digest[len("sha256:"):]
            if SHA256_RE.match(hexd):
                return hexd
    return None


def resolve_one(key: str) -> dict | None:
    """Latest pin for an ARG name, or None if the lookup fails.

    The pin is {"version": str} plus, for direct-download tools,
    {"sha256": {arch: hex}} when the upstream source exposes digests.
    """
    src = SOURCES[key]
    try:
        if src.kind == "node":
            data = _fetch_json("https://nodejs.org/dist/index.json")
            version = _clean(data[0]["version"])  # type: ignore[index]
            if version is None:
                return None
            pin = {"version": version}
            try:
                shasums = _fetch_text(f"https://nodejs.org/dist/{version}/SHASUMS256.txt")
            except Exception:
                shasums = ""  # checksum lookup down: version-only pin, warn at build
            sha: dict[str, str] = {}
            for line in shasums.splitlines():
                digest, _, name = line.strip().partition("  ")
                name = name.strip()
                for arch in ("x64", "arm64"):
                    if name == f"node-{version}-linux-{arch}.tar.xz" and SHA256_RE.match(digest):
                        sha[arch] = digest
            if sha:
                pin["sha256"] = sha
            return pin
        if src.kind == "github":
            data = _fetch_json(f"https://api.github.com/repos/{src.ref}/releases/latest")
            tag = data["tag_name"]  # type: ignore[index]
            if src.strip and tag.startswith(src.strip):
                tag = tag[len(src.strip):]
            version = _clean(tag)
            if version is None:
                return None
            pin = {"version": version}
            assets = data.get("assets", []) if isinstance(data, dict) else []  # type: ignore[assignment]
            sha = {}
            for arch, tpl in src.assets:
                digest = _asset_digest(assets, tpl.format(version=version, arch=arch))
                if digest:
                    sha[arch] = digest
            if sha:
                pin["sha256"] = sha
            return pin
        if src.kind == "npm":
            data = _fetch_json(f"https://registry.npmjs.org/{src.ref}/latest")
            version = _clean(data["version"])  # type: ignore[index]
            if version is None:
                return None
            # npm verifies tarball integrity against registry metadata itself.
            return {"version": version}
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
    current = load_pins(path)
    result = RefreshResult(path=path)

    pins: dict[str, dict] = dict(current)
    for key in SOURCES:
        latest = resolve_one(key)
        if latest is None:
            result.failed.append(key)
            continue
        old_version = current.get(key, {}).get("version", "")
        new_version = latest.get("version", "")
        if old_version == new_version and bool(current.get(key, {}).get("sha256")) == bool(latest.get("sha256")):
            # same version and same checksum coverage: keep the resolved pin
            result.unchanged.append(key)
            pins[key] = latest
        else:
            result.changed[key] = (old_version, new_version)
            pins[key] = latest

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return result


def _sha_arg(pin: dict) -> str | None:
    """Per-arch sha256 pins as one build-arg value: "amd64=<hex>,arm64=<hex>"."""
    sha = pin.get("sha256")
    if not isinstance(sha, dict) or not sha:
        return None
    return ",".join(f"{arch}={sha[arch]}" for arch in sorted(sha))


def build_arg_flags(path: Path | None = None) -> list[str]:
    """versions.json as --build-arg flags: ["--build-arg", "KEY=VALUE", ...].

    Version pins become KEY=version and, when checksums are known,
    KEY_SHA256="arch=hex,arch=hex" for the build-time verification steps.
    """
    flags: list[str] = []
    for key, pin in sorted(load_pins(path).items()):
        version = str(pin.get("version", "")).strip()
        if not version:
            continue
        flags += ["--build-arg", f"{key}={version}"]
        sha = _sha_arg(pin)
        if sha:
            # NODE_VERSION -> NODE_SHA256: the Dockerfiles declare *_SHA256
            flags += ["--build-arg", f"{key.removesuffix('_VERSION')}_SHA256={sha}"]
    return flags
