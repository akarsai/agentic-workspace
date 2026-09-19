"""Tests for the tool-version pin resolver (tool_versions.py).

Network lookups are faked: _fetch_json is monkeypatched per test, so the
suite asserts parsing, diffing, failure handling and file round-trips
without touching npm/GitHub/the Node index.
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


NODE_INDEX = [{"version": "v26.8.2"}]
GH_RELEASE = {"tag_name": "v2.100.0"}
NPM_RELEASE = {"version": "2.1.267"}


def test_resolve_node_github_npm(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert tv.resolve_one("NODE_VERSION") == "v26.8.2"
    assert tv.resolve_one("GH_VERSION") == "2.100.0"  # "v" stripped
    assert tv.resolve_one("BUN_VERSION") == "1.4.2"  # "bun-v" stripped
    assert tv.resolve_one("UV_VERSION") is None  # astral-sh/uv not faked -> failure
    assert tv.resolve_one("CLAUDE_CODE_VERSION") == "2.1.267"


def test_resolve_failure_is_none_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tv, "_fetch_json", fake_fetch({"nodejs.org": ConnectionError("offline")}))
    assert tv.resolve_one("NODE_VERSION") is None
    # a malformed payload must not leak junk either
    monkeypatch.setattr(tv, "_fetch_json", fake_fetch({"nodejs.org": [{"version": "  "}]}))
    assert tv.resolve_one("NODE_VERSION") is None


def test_refresh_writes_pins_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = tmp_path / "versions.json"
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
    assert on_disk["NODE_VERSION"] == "v26.8.2"
    assert "PI_VERSION" not in on_disk  # never pin a version we could not resolve


def test_refresh_failure_keeps_previous_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = tmp_path / "versions.json"
    pins.write_text(json.dumps({"PI_VERSION": "0.85.0"}))
    monkeypatch.setattr(tv, "_fetch_json", fake_fetch({"registry.npmjs.org": ConnectionError("down")}))
    result = tv.refresh_versions(pins)

    assert "PI_VERSION" in result.failed
    assert json.loads(pins.read_text())["PI_VERSION"] == "0.85.0"


def test_refresh_is_idempotent_when_latest_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = tmp_path / "versions.json"
    monkeypatch.setattr(
        tv,
        "_fetch_json",
        fake_fetch({"registry.npmjs.org/@anthropic-ai/claude-code/latest": {"version": "2.1.267"}}),
    )
    tv.refresh_versions(pins)  # pins 2.1.267
    second = tv.refresh_versions(pins)
    assert second.changed == {}
    assert "CLAUDE_CODE_VERSION" in second.unchanged
    assert "CLAUDE_CODE_VERSION" not in second.failed


def test_load_versions_roundtrip_and_tolerance(tmp_path: Path) -> None:
    pins = tmp_path / "versions.json"
    assert tv.load_versions(pins) == {}  # missing file: fine, builds go live

    pins.write_text(json.dumps({"PI_VERSION": "0.85.0", "_note": "human text"}))
    assert tv.load_versions(pins) == {"PI_VERSION": "0.85.0"}  # unknown keys dropped

    pins.write_text("{ not json")
    assert tv.load_versions(pins) == {}  # corrupt file: tolerated


def test_build_arg_flags(tmp_path: Path) -> None:
    pins = tmp_path / "versions.json"
    pins.write_text(json.dumps({"PI_VERSION": "0.85.1", "NODE_VERSION": "v26.8.2"}))
    flags = tv.build_arg_flags(pins)
    assert flags == [
        "--build-arg",
        "NODE_VERSION=v26.8.2",
        "--build-arg",
        "PI_VERSION=0.85.1",
    ]
