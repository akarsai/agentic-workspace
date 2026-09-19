"""Tests for the tool-version pin resolver (tool_versions.py).

Network lookups are faked: _fetch_json/_fetch_text are monkeypatched per
test, so the suite asserts parsing, diffing, checksum resolution, failure
handling and file round-trips without touching npm/GitHub/the Node index.
"""
import json
from pathlib import Path

import pytest

from agentic_workspace import tool_versions as tv


def fake_fetch(mapping: dict[str, object]):
    """_fetch_json stand-in keyed by URL substring."""

    def fetch(url: str):
        for needle, payload in mapping.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected url: {url}")

    return fetch


def fake_text(mapping: dict[str, object]):
    """_fetch_text stand-in keyed by URL substring."""

    def fetch(url: str):
        for needle, payload in mapping.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected url: {url}")

    return fetch


NODE_INDEX = [{"version": "v26.8.2"}]
GH_RELEASE = {"tag_name": "v2.100.0"}
NPM_RELEASE = {"version": "2.1.267"}

GH_RELEASE_WITH_DIGESTS = {
    "tag_name": "v4.47.1",
    "assets": [
        {"name": "yq_linux_amd64", "digest": "sha256:" + "a" * 64},
        {"name": "yq_linux_arm64", "digest": "sha256:" + "b" * 64},
        {"name": "checksums", "digest": "sha512:ignored"},  # wrong asset, wrong algo
        {"name": "yq_darwin_arm64", "digest": "not-a-digest"},
    ],
}

NODE_SHASUMS = (
    f"{'c' * 64}  node-v26.8.2-linux-x64.tar.xz\n"
    f"{'d' * 64}  node-v26.8.2-linux-arm64.tar.xz\n"
    f"{'e' * 64}  node-v26.8.2-darwin-x64.tar.gz\n"
)


def test_resolve_node_github_npm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tv, "_fetch_text", fake_text({}))  # no SHASUMS: tolerated
    monkeypatch.setattr(
        tv,
        "_fetch_json",
        fake_fetch(
            {
                "nodejs.org/dist/index.json": NODE_INDEX,
                "repos/cli/cli/releases/latest": GH_RELEASE,
                "repos/oven-sh/bun/releases/latest": {"tag_name": "bun-v1.4.2"},
                "@anthropic-ai/claude-code/latest": NPM_RELEASE,
            }
        ),
    )
    assert tv.resolve_one("NODE_VERSION") == {"version": "v26.8.2"}
    assert tv.resolve_one("GH_VERSION") == {"version": "2.100.0"}  # "v" stripped
    assert tv.resolve_one("BUN_VERSION") == {"version": "1.4.2"}  # "bun-v" stripped
    assert tv.resolve_one("UV_VERSION") is None  # astral-sh/uv not faked -> failure
    assert tv.resolve_one("CLAUDE_CODE_VERSION") == {"version": "2.1.267"}


def test_resolve_github_digests_become_sha256_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tv, "_fetch_json", fake_fetch({"repos/mikefarah/yq/releases/latest": GH_RELEASE_WITH_DIGESTS})
    )
    pin = tv.resolve_one("YQ_VERSION")
    assert pin == {
        "version": "4.47.1",
        "sha256": {"amd64": "a" * 64, "arm64": "b" * 64},
    }


def test_resolve_node_shasums_pin_both_arches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tv, "_fetch_json", fake_fetch({"nodejs.org/dist/index.json": NODE_INDEX})
    )
    monkeypatch.setattr(
        tv, "_fetch_text", fake_text({"nodejs.org/dist/v26.8.2/SHASUMS256.txt": NODE_SHASUMS})
    )
    pin = tv.resolve_one("NODE_VERSION")
    assert pin == {"version": "v26.8.2", "sha256": {"x64": "c" * 64, "arm64": "d" * 64}}


def test_resolve_node_shasums_unreachable_is_version_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tv, "_fetch_json", fake_fetch({"nodejs.org/dist/index.json": NODE_INDEX}))
    monkeypatch.setattr(tv, "_fetch_text", fake_text({"SHASUMS": ConnectionError("down")}))
    assert tv.resolve_one("NODE_VERSION") == {"version": "v26.8.2"}


def test_resolve_failure_is_none_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tv, "_fetch_json", fake_fetch({"nodejs.org": ConnectionError("offline")}))
    assert tv.resolve_one("NODE_VERSION") is None
    # a malformed payload must not leak junk either
    monkeypatch.setattr(tv, "_fetch_json", fake_fetch({"nodejs.org": [{"version": "  "}]}))
    monkeypatch.setattr(tv, "_fetch_text", fake_text({}))
    assert tv.resolve_one("NODE_VERSION") is None


def test_refresh_writes_pins_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = tmp_path / "versions.json"
    monkeypatch.setattr(tv, "_fetch_text", fake_text({}))
    monkeypatch.setattr(
        tv,
        "_fetch_json",
        fake_fetch(
            {
                "nodejs.org/dist/index.json": [{"version": "v26.8.2"}],
                "repos/cli/cli/releases/latest": {"tag_name": "v2.100.0"},
                "@anthropic-ai/claude-code/latest": {"version": "2.1.267"},
            }
        ),
    )
    result = tv.refresh_versions(pins)

    assert result.changed["NODE_VERSION"] == ("", "v26.8.2")
    assert result.changed["CLAUDE_CODE_VERSION"] == ("", "2.1.267")
    assert "GH_VERSION" in result.unchanged or result.changed.get("GH_VERSION")
    # every source that could not be resolved is reported as failed
    assert "PI_VERSION" in result.failed

    on_disk = json.loads(pins.read_text())
    assert on_disk["NODE_VERSION"] == {"version": "v26.8.2"}
    assert "PI_VERSION" not in on_disk  # never pin a version we could not resolve


def test_refresh_failure_keeps_previous_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = tmp_path / "versions.json"
    pins.write_text(json.dumps({"PI_VERSION": "0.85.0"}))  # v1 schema on disk
    monkeypatch.setattr(tv, "_fetch_json", fake_fetch({"registry.npmjs.org": ConnectionError("down")}))
    result = tv.refresh_versions(pins)

    assert "PI_VERSION" in result.failed
    assert json.loads(pins.read_text())["PI_VERSION"] == {"version": "0.85.0"}


def test_refresh_is_idempotent_when_latest_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = tmp_path / "versions.json"
    monkeypatch.setattr(tv, "_fetch_text", fake_text({}))
    monkeypatch.setattr(
        tv, "_fetch_json", fake_fetch({"registry.npmjs.org/@anthropic-ai/claude-code/latest": {"version": "2.1.267"}})
    )
    tv.refresh_versions(pins)  # pins 2.1.267
    second = tv.refresh_versions(pins)
    assert second.changed == {}
    assert "CLAUDE_CODE_VERSION" in second.unchanged
    assert "CLAUDE_CODE_VERSION" not in second.failed


def test_load_versions_roundtrip_and_tolerance(tmp_path: Path) -> None:
    pins = tmp_path / "versions.json"
    assert tv.load_versions(pins) == {}  # missing file: fine, builds go live
    assert tv.load_pins(pins) == {}

    pins.write_text(json.dumps({"PI_VERSION": "0.85.0", "_note": "human text"}))
    # v1 strings are migrated on read; unknown keys dropped
    assert tv.load_versions(pins) == {"PI_VERSION": "0.85.0"}
    assert tv.load_pins(pins) == {"PI_VERSION": {"version": "0.85.0"}}

    pins.write_text("{ not json")
    assert tv.load_versions(pins) == {}  # corrupt file: tolerated


def test_build_arg_flags(tmp_path: Path) -> None:
    pins = tmp_path / "versions.json"
    pins.write_text(
        json.dumps(
            {
                "PI_VERSION": "0.85.1",
                "NODE_VERSION": "v26.8.2",
                "YQ_VERSION": {
                    "version": "4.47.1",
                    "sha256": {"amd64": "a" * 64, "arm64": "b" * 64},
                },
            }
        )
    )
    flags = tv.build_arg_flags(pins)
    assert flags == [
        "--build-arg",
        "NODE_VERSION=v26.8.2",
        "--build-arg",
        "PI_VERSION=0.85.1",
        "--build-arg",
        "YQ_VERSION=4.47.1",
        "--build-arg",
        f"YQ_SHA256=amd64={'a' * 64},arm64={'b' * 64}",
    ]
