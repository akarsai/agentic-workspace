"""Tests for the launch-time instruction sync (setup_instruction_file).

Covers the decision table:
  - target (AGENTS.md) missing              -> write the template
  - target exists, same content             -> skip (no prompt, no write)
  - target exists, differs, target newer    -> warn + diff + prompt, default no
  - target exists, differs, target older    -> warn + diff + prompt, default yes
"""
import os
import subprocess
from pathlib import Path

import pytest

from agentic_workspace import launch
from agentic_workspace.manifest import load_manifest


def make_manifest(tmp_path: Path, instructions: str = "# Default instructions\n"):
    inst = tmp_path / "instance"
    inst.mkdir(exist_ok=True)
    (inst / "manifest.yaml").write_text("name: demo\n")
    (inst / "INSTRUCTIONS.md").write_text(instructions)
    return load_manifest(inst / "manifest.yaml")


def set_mtime(path: Path, t: float) -> None:
    os.utime(path, (t, t))


class Recorder:
    """Fake `confirm` that records (prompt, hint) and returns `answer`."""

    def __init__(self, answer: bool):
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def __call__(self, prompt: str, hint: str = "y/N") -> bool:
        self.calls.append((prompt, hint))
        return self.answer


def test_missing_target_is_written(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    launch.setup_instruction_file(manifest, ws)
    assert (ws / "AGENTS.md").read_text() == "# Default instructions\n"


def test_identical_content_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("confirm must not be called when content matches")

    monkeypatch.setattr(launch, "confirm", boom)
    manifest = make_manifest(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("# Default instructions\n")
    # Make the target *older* so an uncareful implementation would prompt.
    set_mtime(ws / "AGENTS.md", 1_000_000_000)
    launch.setup_instruction_file(manifest, ws)
    assert (ws / "AGENTS.md").read_text() == "# Default instructions\n"


def test_rendered_template_is_compared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCKER-OMIT blocks are stripped before comparing, so a target that
    matches the *rendered* template counts as identical."""
    def boom(*args, **kwargs):
        raise AssertionError("confirm must not be called when content matches")

    monkeypatch.setattr(launch, "confirm", boom)
    tpl = "# Rules\n<!-- DOCKER-OMIT-START -->\nhost-only note\n<!-- DOCKER-OMIT-END -->\nbe nice.\n"
    manifest = make_manifest(tmp_path, tpl)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("# Rules\nbe nice.\n")
    launch.setup_instruction_file(manifest, ws)
    assert (ws / "AGENTS.md").read_text() == "# Rules\nbe nice.\n"


def test_newer_target_prompts_with_default_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = make_manifest(tmp_path)
    set_mtime(tmp_path / "instance" / "INSTRUCTIONS.md", 1_000_000_000)
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "AGENTS.md"
    target.write_text("# Project's own instructions\n")
    set_mtime(target, 1_000_000_500)  # newer than the template

    rec = Recorder(answer=False)
    monkeypatch.setattr(launch, "confirm", rec)
    launch.setup_instruction_file(manifest, ws)

    assert len(rec.calls) == 1
    prompt, hint = rec.calls[0]
    assert "Overwrite AGENTS.md" in prompt
    assert hint == "y/N"  # default: no — project edits win
    assert target.read_text() == "# Project's own instructions\n"


def test_newer_target_overwritten_when_user_confirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path)
    set_mtime(tmp_path / "instance" / "INSTRUCTIONS.md", 1_000_000_000)
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "AGENTS.md"
    target.write_text("# Project's own instructions\n")
    set_mtime(target, 1_000_000_500)

    monkeypatch.setattr(launch, "confirm", Recorder(answer=True))
    launch.setup_instruction_file(manifest, ws)
    assert target.read_text() == "# Default instructions\n"


def test_older_target_prompts_with_default_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path)
    set_mtime(tmp_path / "instance" / "INSTRUCTIONS.md", 1_000_000_500)
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "AGENTS.md"
    target.write_text("# Stale instructions\n")
    set_mtime(target, 1_000_000_000)  # older than the template

    rec = Recorder(answer=False)
    monkeypatch.setattr(launch, "confirm", rec)
    launch.setup_instruction_file(manifest, ws)

    assert rec.calls, "expected a prompt when content differs"
    assert rec.calls[0][1] == "Y/n"  # default: yes — the template is newer
    assert target.read_text() == "# Stale instructions\n"  # declined -> untouched


def test_older_target_overwritten_when_user_accepts_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path)
    set_mtime(tmp_path / "instance" / "INSTRUCTIONS.md", 1_000_000_500)
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "AGENTS.md"
    target.write_text("# Stale instructions\n")
    set_mtime(target, 1_000_000_000)

    monkeypatch.setattr(launch, "confirm", Recorder(answer=True))
    launch.setup_instruction_file(manifest, ws)
    assert target.read_text() == "# Default instructions\n"


def test_equal_mtime_counts_as_newer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Equal mtimes are ambiguous -> conservative default (no overwrite)."""
    manifest = make_manifest(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "AGENTS.md"
    target.write_text("# Different\n")
    set_mtime(tmp_path / "instance" / "INSTRUCTIONS.md", 1_000_000_000)
    set_mtime(target, 1_000_000_000)

    rec = Recorder(answer=False)
    monkeypatch.setattr(launch, "confirm", rec)
    launch.setup_instruction_file(manifest, ws)
    assert rec.calls[0][1] == "y/N"


def test_diff_shown_and_git_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The colorful git diff runs when git exists; a plain difflib fallback
    covers hosts without git."""
    manifest = make_manifest(tmp_path)
    set_mtime(tmp_path / "instance" / "INSTRUCTIONS.md", 1_000_000_000)
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "AGENTS.md"
    target.write_text("# Changed\n")
    set_mtime(target, 1_000_000_500)

    monkeypatch.setattr(launch, "confirm", Recorder(answer=False))
    launch.setup_instruction_file(manifest, ws)
    out = capsys.readouterr().out
    assert "diff --git" in out  # git is available in the test env
    assert "-# Default instructions" in out
    assert "+# Changed" in out

    # Without git: plain unified diff fallback, no crash, same decision flow.
    monkeypatch.setattr("shutil.which", lambda name: None)
    launch.setup_instruction_file(manifest, ws)
    out2 = capsys.readouterr().out
    assert "---" in out2 and "+++" in out2
    assert "-# Default instructions" in out2
    assert target.read_text() == "# Changed\n"


def test_diff_shows_rendered_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """DOCKER-OMIT lines never appear in the diff - they would not be
    written, so they must not show up as phantom changes."""
    tpl = (
        "# Rules\n"
        "<!-- DOCKER-OMIT-START -->\n"
        "host-only note\n"
        "<!-- DOCKER-OMIT-END -->\n"
        "be nice.\n"
    )
    manifest = make_manifest(tmp_path, tpl)
    set_mtime(tmp_path / "instance" / "INSTRUCTIONS.md", 1_000_000_000)
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "AGENTS.md"
    target.write_text("# Rules\nbe mean.\n")
    set_mtime(target, 1_000_000_500)

    monkeypatch.setattr(launch, "confirm", Recorder(answer=False))
    launch.setup_instruction_file(manifest, ws)
    out = capsys.readouterr().out
    assert "host-only note" not in out
    assert "diff --git a/INSTRUCTIONS.md" in out  # clean header, no scratch path
    assert "agentic-instructions-" not in out
    assert target.read_text() == "# Rules\nbe mean.\n"


def test_workspace_scripts_synced_and_overwritten(tmp_path: Path) -> None:
    """Instance scripts/ (Slurm job tooling) is copied into the workspace on
    every launch and refreshed when the instance updates it; files the project
    added on its own are left alone."""
    inst = tmp_path / "instance"
    (inst / "scripts").mkdir(parents=True)
    (inst / "manifest.yaml").write_text("name: demo\n")
    (inst / "scripts" / "job-header.sh").write_text("# v1\n")
    manifest = load_manifest(inst / "manifest.yaml")

    ws = tmp_path / "ws"
    ws.mkdir()
    launch.setup_workspace_scripts(manifest, ws)
    assert (ws / "scripts" / "job-header.sh").read_text() == "# v1\n"

    # project added a file, instance fixed its script -> refresh keeps both
    (ws / "scripts" / "my-job.sbatch").write_text("# mine\n")
    (inst / "scripts" / "job-header.sh").write_text("# v2\n")
    launch.setup_workspace_scripts(manifest, ws)
    assert (ws / "scripts" / "job-header.sh").read_text() == "# v2\n"
    assert (ws / "scripts" / "my-job.sbatch").read_text() == "# mine\n"


def test_workspace_host_env(tmp_path: Path) -> None:
    """AGENTIC_WORKSPACE_HOST exposes the node-visible host path of /workspace
    (job scripts on compute nodes cd there)."""
    pairs = launch.workspace_host_env(tmp_path / "proj")
    assert pairs == [("AGENTIC_WORKSPACE_HOST", str(tmp_path / "proj"))]


def test_slurm_account_env_passthrough(tmp_path: Path) -> None:
    """SBATCH_ACCOUNT & co. flow from the launch shell into the sandbox (the
    container env is clean under --compat/--cleanenv, so without this a
    cluster without a default account rejects every submission)."""
    dirs = {
        "hf_home": tmp_path / "hf",
        "triton_cache": tmp_path / "triton",
        "wandb": tmp_path / "wandb",
    }
    cfg = {"AGENTIC_SUBAGENT_MODEL": "", "AGENTIC_SUBAGENT_TOOLS": "",
           "AGENTIC_SUBAGENT_THINKING": "", "AGENTIC_HTTPS_PROXY": "",
           "AGENTIC_HTTP_PROXY": "", "AGENTIC_EXTRA_ENV": ""}
    pairs = launch.build_env_args(
        cfg, {"SBATCH_ACCOUNT": "extremedata", "SLURM_CLUSTERS": "kestrel"},
        dirs, "demo", "pi",
    )
    d = dict(pairs)
    assert d["SBATCH_ACCOUNT"] == "extremedata"
    assert d["SLURM_CLUSTERS"] == "kestrel"
    # unset keys are not passed
    pairs2 = launch.build_env_args(cfg, {}, dirs, "demo", "pi")
    assert "SBATCH_ACCOUNT" not in dict(pairs2)
    assert "SLURM_ENV_KEYS" in dir(launch)  # mirrored into the docker path too


def test_self_updaters_are_disabled_in_the_sandbox(tmp_path: Path) -> None:
    """The image pins the CLIs and /opt/node is read-only to the runtime
    user, so the in-place updater can only print a failure."""
    dirs = {"hf_home": tmp_path / "hf", "triton_cache": tmp_path / "t", "wandb": tmp_path / "w"}
    cfg = {"AGENTIC_SUBAGENT_MODEL": "", "AGENTIC_SUBAGENT_TOOLS": "",
           "AGENTIC_SUBAGENT_THINKING": "", "AGENTIC_HTTPS_PROXY": "",
           "AGENTIC_HTTP_PROXY": "", "AGENTIC_EXTRA_ENV": ""}
    pairs = dict(launch.build_env_args(cfg, {}, dirs, "demo", "claude"))
    assert pairs["DISABLE_AUTOUPDATER"] == "1"          # claude code
    assert pairs["OPENCODE_DISABLE_AUTOUPDATE"] == "1"  # opencode


# --- Claude Code memory + agent-file gitignore prompts ----------------------


def test_claude_memory_created_for_claude_only(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()

    launch.setup_claude_memory(manifest, ws, "claude")
    assert (ws / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    launch.setup_claude_memory(manifest, ws, "claude")  # idempotent
    assert (ws / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    other = tmp_path / "other"
    other.mkdir()
    launch.setup_claude_memory(manifest, other, "pi")
    launch.setup_claude_memory(manifest, other, "codex")
    launch.setup_claude_memory(manifest, other, "opencode")
    assert not (other / "CLAUDE.md").exists()


def test_claude_memory_never_clobbers_existing(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("project's own memory\n")
    launch.setup_claude_memory(manifest, ws, "claude")
    assert (ws / "CLAUDE.md").read_text() == "project's own memory\n"


def make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    (path / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        check=True,
    )
    return path


def test_agent_files_gitignore_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unignored agent files in a git repo prompt; accepting appends them
    plus the standard sandbox ignores (*.lock, *.pdf, .venv/, ...) to the
    repo-root .gitignore (bare names match at any depth); a second run
    finds them ignored and does not prompt again."""
    repo = make_git_repo(tmp_path / "repo")
    ws = repo / "sub" / "ws"
    ws.mkdir(parents=True)
    (ws / "AGENTS.md").write_text("# instructions\n")

    recorder = Recorder(answer=True)
    monkeypatch.setattr(launch, "confirm", recorder)
    launch.ensure_agent_files_ignored(ws, ["AGENTS.md", "CLAUDE.md", ".tmp"])

    assert len(recorder.calls) == 1
    gitignore = (repo / ".gitignore").read_text()
    for entry in ("AGENTS.md", "CLAUDE.md", ".tmp", ".venv/", "*.lock", "*.pdf", ".claude/"):
        assert entry in gitignore, f"missing {entry}"
    for ignored in ("CLAUDE.md", "report.pdf", "uv.lock", ".claude/settings.local.json"):
        check = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", "--", str(ws / ignored)],
            capture_output=True,
        )
        assert check.returncode == 0, f"nested {ignored} not covered by root patterns"

    monkeypatch.setattr(launch, "confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no re-prompt")))
    launch.ensure_agent_files_ignored(ws, ["AGENTS.md", "CLAUDE.md", ".tmp"])


def test_managed_agent_files_reflect_the_instance(tmp_path: Path) -> None:
    """Only entries the instance actually produces are managed: scratch
    always, STYLE.md/scripts only when the instance ships them, CLAUDE.md
    for claude runs, skills when rendered."""
    manifest = make_manifest(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    assert launch.managed_agent_files(manifest, ws, "pi") == ["AGENTS.md", ".tmp"]

    claude = launch.managed_agent_files(manifest, ws, "claude")
    assert claude == ["AGENTS.md", "CLAUDE.md", ".tmp"]

    inst = manifest.path.parent
    (inst / "STYLE.md").write_text("# style\n")
    (inst / "scripts").mkdir()
    (ws / ".agents").mkdir()
    got = launch.managed_agent_files(manifest, ws, "pi")
    assert got == ["AGENTS.md", ".agents", ".tmp", "STYLE.md", "scripts"]


def test_agent_ignore_block_renders_sections(tmp_path: Path) -> None:
    """Each section keeps its comment header; empty input renders nothing."""
    text = launch.agent_ignore_block([("agent files", ["AGENTS.md", ".tmp"])])
    assert text == "# agent files\nAGENTS.md\n.tmp\n"
    assert launch.agent_ignore_block([]) == ""


def test_ignore_probe_turns_patterns_into_matchable_paths() -> None:
    """Globs collapse to a literal and directory patterns get a child, so
    git has a concrete path to judge."""
    assert launch.ignore_probe("*.py[cod]") == "x.pyc"
    assert launch.ignore_probe(".venv/") == ".venv/probe"
    assert launch.ignore_probe("*.lock") == "x.lock"
    assert launch.ignore_probe("CLAUDE.md") == "CLAUDE.md"
    assert launch.ignore_probe("/STYLE.md") == "STYLE.md"


def test_pending_ignores_honours_equivalent_existing_rules(tmp_path: Path) -> None:
    """A rule that differs in spelling still counts as covered: the question
    is whether the file ends up ignored, not whether the line matches."""
    repo = make_git_repo(tmp_path / "repo")
    ws = repo / "ws"
    ws.mkdir()
    (repo / ".gitignore").write_text(
        # deliberately none of these are spelled the way the launcher would
        "*.pyc\n.venv\nvenv\n__pycache__\n*.egg-info\ndist\nbuild\n"
        ".pytest_cache\n.ruff_cache\n*.lock\n.claude\n*.pdf\nws/\n"
    )
    assert launch.pending_ignores(repo, ws, ["AGENTS.md", ".tmp"]) == []


def test_pending_ignores_covers_lines_the_repo_tracks(tmp_path: Path) -> None:
    """A tracked path whose line is already in .gitignore counts as covered.

    git check-ignore consults the index by default and calls a tracked path
    unignored no matter what the rules say, which had the launcher re-offer
    `scripts` on every launch of a project that commits its own scripts/.
    """
    repo = make_git_repo(tmp_path / "repo")
    ws = repo / "ws"
    ws.mkdir()
    (ws / "scripts").mkdir()
    (ws / "scripts" / "run.sh").write_text("echo hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "-f", "ws/scripts/run.sh"], check=True)
    (repo / ".gitignore").write_text("scripts\n")
    assert "scripts" not in [
        e for _, section in launch.pending_ignores(repo, ws, ["scripts"]) for e in section
    ]


def test_pending_ignores_lists_only_what_is_missing(tmp_path: Path) -> None:
    repo = make_git_repo(tmp_path / "repo")
    ws = repo / "ws"
    ws.mkdir()
    (repo / ".gitignore").write_text("AGENTS.md\n*.pdf\n")
    sections = launch.pending_ignores(repo, ws, ["AGENTS.md", ".tmp"])
    entries = [e for _, section in sections for e in section]
    assert "AGENTS.md" not in entries and "*.pdf" not in entries
    assert ".tmp" in entries and "*.lock" in entries
    assert all(section for _, section in sections)  # no empty sections


def test_no_prompt_when_everything_is_already_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the check: a repo that already ignores everything
    is never asked again."""
    repo = make_git_repo(tmp_path / "repo")
    ws = repo / "ws"
    ws.mkdir()
    (repo / ".gitignore").write_text("ws/\n")  # the workspace itself is ignored
    monkeypatch.setattr(
        launch, "confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    launch.ensure_agent_files_ignored(ws, ["AGENTS.md", "CLAUDE.md", ".tmp"])


def test_gitignore_prompt_names_every_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The listing printed before the prompt is exactly what gets appended."""
    repo = make_git_repo(tmp_path / "repo")
    ws = repo / "ws"
    ws.mkdir()
    monkeypatch.setattr(launch, "confirm", Recorder(answer=True))
    launch.ensure_agent_files_ignored(ws, ["AGENTS.md"])
    printed = capsys.readouterr().out
    appended = [
        line.strip()
        for line in (repo / ".gitignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert appended
    for entry in appended:
        assert f"    {entry}" in printed, f"{entry} appended but not shown"


def test_agent_files_gitignore_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_git_repo(tmp_path / "repo")
    ws = repo / "ws"
    ws.mkdir(parents=True)
    monkeypatch.setattr(launch, "confirm", Recorder(answer=False))
    launch.ensure_agent_files_ignored(ws, ["AGENTS.md"])
    assert not (repo / ".gitignore").exists()


def test_agent_files_ignored_outside_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "plain"
    ws.mkdir()
    monkeypatch.setattr(launch, "confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt")))
    launch.ensure_agent_files_ignored(ws, ["AGENTS.md"])  # no repo: silent no-op
