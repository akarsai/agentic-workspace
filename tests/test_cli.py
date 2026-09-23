"""Lean smoke tests for the agentic-workspace CLI.

Run with: uv run pytest
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = REPO_ROOT / "instances" / "agre"


def run_cli(args: list[str], env: dict | None = None, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agentic_workspace.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        input=input,
    )


def sandbox_env(tmp_path: Path) -> dict:
    """Isolated HOME/XDG (and marker) so interactive tests touch nothing real."""
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / ".config"),
            "AGENTIC_INSTANCES_MARKER": str(tmp_path / "no-marker"),
            "NO_COLOR": "1",
        }
    )
    return env


def test_help_lists_all_subcommands() -> None:
    result = run_cli(["--help"])
    assert result.returncode == 0
    for sub in ("launch", "install", "build", "update", "scaffold", "settings", "clean", "uninstall"):
        assert sub in result.stdout


def test_manifest_parsing() -> None:
    from agentic_workspace.manifest import load_manifest

    m = load_manifest(AGENT_ROOT / "manifest.yaml")
    assert m.name == "agre"
    assert m.image == "agre:latest"
    assert m.tool == "pi"
    assert m.commands == ["retro", "update_base", "subagent", "clone-writing-style"]
    assert m.instruction_target == "AGENTS.md"
    # The removed brain keys must not come back.
    assert "brain" not in m.data


def test_lera_instance_integrity() -> None:
    """lera ships a consistent instance: manifest, commands, assets, shim,
    and the instruction sections the launch/update machinery keys off."""
    from agentic_workspace.manifest import load_manifest

    root = REPO_ROOT / "instances" / "lera"
    m = load_manifest(root / "manifest.yaml")
    assert m.name == "lera"
    assert m.image == "lera:latest"
    assert m.tool == "pi"
    assert m.instruction_target == "AGENTS.md"
    # Every command listed in the manifest exists as a commands/<name>.md file.
    for cmd in m.commands:
        assert (root / "commands" / f"{cmd}.md").is_file(), cmd
    # The style skill ships its mechanical linter as a per-command asset.
    assert (root / "commands" / "style" / "stylelint.py").is_file()
    # Launcher shim is executable and hands off to the shared engine.
    shim = root / m.name
    assert shim.stat().st_mode & stat.S_IXUSR
    assert "agentic-workspace\" launch" in shim.read_text()
    # Child image builds on the shared base image.
    assert (root / "container" / "Dockerfile").read_text().lstrip().startswith("#")
    assert "FROM agentic-blueprint:base" in (root / "container" / "Dockerfile").read_text()
    # The instruction file keeps its lean structure: the Typst-first
    # personality (compilable materials, Helvetica from assets/fonts) and
    # the honesty rules are what /update_base and the launch-time sync
    # preserve.
    instructions = (root / "INSTRUCTIONS.md").read_text()
    assert "Project Instructions" not in instructions
    assert "## 3. Honesty" in instructions
    # Typst-first personality: compilable materials, Helvetica from assets/fonts.
    assert "typst compile --font-path assets/fonts" in instructions
    assert 'Helvetica' in instructions and 'assets/fonts' in instructions
    sheet_cmd = (root / "commands" / "sheet.md").read_text()
    assert "typst compile --font-path assets/fonts" in sheet_cmd
    assert "unknown font family" in sheet_cmd  # the font-missing gate


def test_lera_skills_render_into_workspace(tmp_path: Path) -> None:
    """lera's commands render as Agent Skills, with the stylelint asset
    copied next to the style skill."""
    from agentic_workspace.manifest import load_manifest
    from agentic_workspace.skills import install_project_skills

    root = REPO_ROOT / "instances" / "lera"
    manifest = load_manifest(root / "manifest.yaml")
    ws = tmp_path / "ws"
    ws.mkdir()
    install_project_skills(ws, root, manifest.commands)
    # Skill names are sanitized ([a-z0-9-]): update_base -> update-base.
    for cmd in ("course", "sheet", "style", "subagent", "retro", "update-base"):
        assert (ws / ".agents" / "skills" / cmd / "SKILL.md").is_file(), cmd
    assert (ws / ".agents" / "skills" / "style" / "stylelint.py").is_file()
    sheet_skill = (ws / ".agents" / "skills" / "sheet" / "SKILL.md").read_text()
    assert "solvable" in sheet_skill


def test_subscription_parse() -> None:
    from agentic_workspace.subscription import parse

    assert parse("zai:glm-5.3") == ("zai", "glm-5.3")
    assert parse("zai/glm-5.3") == ("zai", "glm-5.3")
    assert parse("") is None
    assert parse("   ") is None
    assert parse("glm-5.3") is None  # no provider alias
    assert parse("zai:") is None
    assert parse("zai:glm:5.3") is None  # second separator in the model


def test_subscription_is_default_for_main_and_subagents() -> None:
    """A subscription fills AGENTIC_DEFAULT_MODEL and AGENTIC_SUBAGENT_MODEL
    when unset; explicit values win; unknown/malformed specs are no-ops."""
    from agentic_workspace.subscription import apply_to_config

    cfg = {"AGENTIC_MODEL_SUBSCRIPTION": "zai:glm-5.3", "AGENTIC_DEFAULT_MODEL": "", "AGENTIC_SUBAGENT_MODEL": ""}
    apply_to_config(cfg)
    assert cfg["AGENTIC_DEFAULT_MODEL"] == "zai/glm-5.3"
    assert cfg["AGENTIC_SUBAGENT_MODEL"] == "zai/glm-5.3"

    cfg = {"AGENTIC_MODEL_SUBSCRIPTION": "zai:glm-5.3", "AGENTIC_DEFAULT_MODEL": "deepseek-v4", "AGENTIC_SUBAGENT_MODEL": "deepseek-v4-flash"}
    apply_to_config(cfg)
    assert cfg["AGENTIC_DEFAULT_MODEL"] == "deepseek-v4"
    assert cfg["AGENTIC_SUBAGENT_MODEL"] == "deepseek-v4-flash"

    for spec in ("", "bogus", "nosuch:glm-5.3"):
        cfg = {"AGENTIC_MODEL_SUBSCRIPTION": spec, "AGENTIC_DEFAULT_MODEL": "", "AGENTIC_SUBAGENT_MODEL": ""}
        apply_to_config(cfg)
        assert cfg["AGENTIC_DEFAULT_MODEL"] == ""
        assert cfg["AGENTIC_SUBAGENT_MODEL"] == ""


def test_subscription_pi_models_json_upsert(tmp_path: Path) -> None:
    """The subscription provider is upserted into models.json; other providers
    and user-added models on the subscription provider survive rewrites."""
    import json

    from agentic_workspace.subscription import write_pi_models_json

    store = tmp_path / "store"
    out = write_pi_models_json(store, "zai:glm-5.3")
    assert out is not None and out.is_file()
    data = json.loads(out.read_text())
    zai = data["providers"]["zai"]
    assert zai["baseUrl"] == "https://api.z.ai/api/coding/paas/v4"
    assert zai["apiKey"] == "$ZAI_API_KEY"  # env reference, never a stored key
    assert [m["id"] for m in zai["models"]] == ["glm-5.3"]

    # A user provider and an extra user model on zai must survive rewrites.
    data["providers"]["my-ollama"] = {"baseUrl": "http://localhost:11434/v1", "models": [{"id": "llama3"}]}
    data["providers"]["zai"]["models"].append({"id": "glm-4.7"})
    out.write_text(json.dumps(data))

    out = write_pi_models_json(store, "zai:glm-5.3")
    data = json.loads(out.read_text())
    assert "my-ollama" in data["providers"]
    assert sorted(m["id"] for m in data["providers"]["zai"]["models"]) == ["glm-4.7", "glm-5.3"]

    # Malformed/empty specs leave the file untouched.
    before = out.read_text()
    assert write_pi_models_json(store, "") is None
    assert write_pi_models_json(store, "garbage") is None
    assert out.read_text() == before


def test_subscription_engine_env(tmp_path: Path) -> None:
    """Claude Code is routed through the subscription's Anthropic-compatible
    endpoint; pi reads models.json instead (no env); key never leaks into pi pairs."""
    from agentic_workspace.subscription import engine_env_pairs

    env = {"ZAI_API_KEY": "sk-test"}
    pairs = engine_env_pairs("zai:glm-5.3", "claude", env)
    assert dict(pairs)["ANTHROPIC_BASE_URL"] == "https://api.z.ai/api/anthropic"
    assert dict(pairs)["ANTHROPIC_AUTH_TOKEN"] == "sk-test"

    assert engine_env_pairs("zai:glm-5.3", "pi", env) == []
    assert engine_env_pairs("zai:glm-5.3", "codex", env) == []
    assert engine_env_pairs("", "claude", env) == []


def test_launch_oci_runtime_subscription_env(tmp_path: Path, monkeypatch) -> None:
    """Regression: launch_oci_runtime must take the CLI tool from the config
    (it has no `tool` binding) -- the subscription env pairs used to raise
    NameError on every docker/podman launch."""
    import io

    from agentic_workspace import launch
    from agentic_workspace.manifest import Manifest

    monkeypatch.setattr(launch.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(launch.oci, "gpu_arguments", lambda *a, **k: [])
    monkeypatch.setattr(launch, "workspace_host_env", lambda p: [])
    monkeypatch.setattr(launch, "host_identity", lambda: (1000, 1000, "user", "staff"))
    monkeypatch.setattr(launch.sys, "stdin", io.StringIO())
    monkeypatch.setattr(launch.sys, "stdout", io.StringIO())
    calls: list[list[str]] = []
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd: (calls.append(cmd), 0)[1])

    dirs = {k: tmp_path / k for k in (
        "uv_cache", "uv_python", "uv_tools", "state_root", "config_store",
        "hf_home", "triton_cache", "wandb",
    )}
    manifest = Manifest({"name": "lera"}, tmp_path / "manifest.yaml")
    opts = launch.Options(workspace_dir=str(tmp_path))

    cfg = {
        "AGENTIC_CONTAINER_RUNTIME": "docker",
        "AGENTIC_CLI_TOOL": "claude",
        "AGENTIC_MODEL_SUBSCRIPTION": "zai:glm-5.3",
    }
    rc = launch.launch_oci_runtime(
        "run", cfg, {"ZAI_API_KEY": "sk-test"}, manifest, opts, dirs, [], [], [], []
    )
    assert rc == 0
    assert "ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic" in calls[-1]
    assert "ANTHROPIC_AUTH_TOKEN=sk-test" in calls[-1]

    # pi reads models.json instead of env: no Anthropic pairs, and no crash.
    cfg["AGENTIC_CLI_TOOL"] = "pi"
    rc = launch.launch_oci_runtime(
        "run", cfg, {"ZAI_API_KEY": "sk-test"}, manifest, opts, dirs, [], [], [], []
    )
    assert rc == 0
    assert not any(str(a).startswith("ANTHROPIC_") for a in calls[-1])


def test_scaffold_creates_instance(tmp_path: Path) -> None:
    result = run_cli(
        ["scaffold", "testagent", "--template", "research", "--dir", str(tmp_path), "--no-git"]
    )
    assert result.returncode == 0, result.stderr

    child = tmp_path / "testagent"
    assert (child / "manifest.yaml").is_file()
    assert (child / "INSTRUCTIONS.md").is_file()
    assert (child / "commands" / "retro.md").is_file()
    assert (child / "commands" / "update_base.md").is_file()
    assert not (child / "commands" / "remember.md").exists()
    # Per-command asset subdirs (skill helpers) are scaffolded too.
    assert (child / "commands" / "clone-writing-style" / "stylelint.py").is_file()
    # Slurm job tooling ships with the research template (launcher syncs
    # instance scripts/ into every workspace).
    assert (child / "scripts" / "job-header.sh").is_file()
    assert (child / "scripts" / "submit.sh").stat().st_mode & stat.S_IXUSR
    assert (child / "container" / "Dockerfile").is_file()
    shim = child / "testagent"
    assert shim.stat().st_mode & stat.S_IXUSR
    assert "AGENT_ROOT" in shim.read_text()
    assert "launch" in shim.read_text()

    manifest_text = (child / "manifest.yaml").read_text()
    assert "name: testagent" in manifest_text
    assert "tool: pi" in manifest_text
    assert "brain" not in manifest_text


def test_config_migration_from_legacy_sh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_workspace import config as cfgmod

    xdg = tmp_path / "xdg"
    (xdg / "myagent").mkdir(parents=True)
    legacy = xdg / "myagent" / "config.sh"
    legacy.write_text(
        '# old config\n'
        'AGENTIC_CONTAINER_RUNTIME="apptainer"\n'
        'AGENTIC_CLI_TOOL="claude"\n'
        'AGENTIC_STATE_ROOT="/some/state"\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path))

    cfg = cfgmod.load("myagent")
    assert cfg["AGENTIC_CONTAINER_RUNTIME"] == "apptainer"
    assert cfg["AGENTIC_CLI_TOOL"] == "claude"
    assert cfg["AGENTIC_STATE_ROOT"] == "/some/state"
    # Migration wrote config.py and left the legacy file alone.
    assert cfgmod.config_path("myagent").is_file()
    assert legacy.is_file()


def test_launch_arg_translation() -> None:
    from agentic_workspace.launch import Options, translate_tool_args

    # opencode: --yolo maps to --auto; --dangerously-skip-permissions maps too.
    opts = Options(yolo=True)
    args = translate_tool_args(opts, "opencode", "OpenCode", {})
    assert "--auto" in args

    # pi has no permission system: --yolo warns and adds nothing.
    opts = Options(yolo=True)
    args = translate_tool_args(opts, "pi", "Pi", {})
    assert args == []

    # codex: --resume with ID becomes `resume <id>`.
    opts = Options(tool_args=["--resume", "abc123"])
    args = translate_tool_args(opts, "codex", "Codex CLI", {})
    assert args == ["resume", "abc123"]

    # default model is appended when set in config and not overridden.
    opts = Options(tool_args=[])
    args = translate_tool_args(opts, "pi", "Pi", {"AGENTIC_DEFAULT_MODEL": "deepseek-v4"})
    assert args == ["--model", "deepseek-v4"]


def test_skill_sanitize_and_render(tmp_path: Path) -> None:
    from agentic_workspace.skills import sanitize_skill_name, strip_frontmatter

    assert sanitize_skill_name("setup_research_plan") == "setup-research-plan"
    assert sanitize_skill_name("Retro") == "retro"
    body = strip_frontmatter("---\nname: x\ndescription: d\n---\nHello $ARGUMENTS\n")
    assert body == "Hello $ARGUMENTS\n"


def test_skill_assets_copied_next_to_skill(tmp_path: Path) -> None:
    """install_project_skills copies a command's asset subdir (e.g.
    commands/clone-writing-style/stylelint.py) into the rendered skill dir."""
    from agentic_workspace.manifest import load_manifest
    from agentic_workspace.skills import install_project_skills

    ws = tmp_path / "ws"
    ws.mkdir()
    manifest = load_manifest(AGENT_ROOT / "manifest.yaml")
    install_project_skills(ws, AGENT_ROOT, manifest.commands)
    skill = ws / ".agents" / "skills" / "clone-writing-style"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "stylelint.py").is_file()
    assert "no semicolons" in (skill / "SKILL.md").read_text()
    # assets must also survive a re-render (dirs_exist_ok overwrite path)
    install_project_skills(ws, AGENT_ROOT, ["clone-writing-style"])
    assert (skill / "stylelint.py").is_file()


def test_style_file_copied_into_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An instance's STYLE.md is copied into a fresh workspace, but never
    overwrites a style file that already exists there."""
    from agentic_workspace import launch
    from agentic_workspace.manifest import load_manifest

    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "manifest.yaml").write_text("name: demo\n")
    (instance / "STYLE.md").write_text("# Default style\nshort sentences.\n")
    manifest = load_manifest(instance / "manifest.yaml")

    fresh_ws = tmp_path / "fresh"
    fresh_ws.mkdir()
    launch.setup_style_file(manifest, fresh_ws)
    assert (fresh_ws / "STYLE.md").read_text() == "# Default style\nshort sentences.\n"

    existing_ws = tmp_path / "existing"
    existing_ws.mkdir()
    (existing_ws / "STYLE.md").write_text("# Per-project style\n")
    launch.setup_style_file(manifest, existing_ws)
    assert (existing_ws / "STYLE.md").read_text() == "# Per-project style\n"


def test_dockerfile_to_def_converts_new_base(tmp_path: Path) -> None:
    """The Apptainer converter must still handle the base Dockerfile with the
    python entrypoint."""
    converter = REPO_ROOT / "blueprint" / "container" / "dockerfile_to_def.py"
    dockerfile = REPO_ROOT / "blueprint" / "container" / "Dockerfile.base"
    result = subprocess.run(
        [sys.executable, str(converter), "--mode", "base", str(dockerfile)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Bootstrap: docker" in result.stdout
    assert "entrypoint.py" in result.stdout


def test_dockerfile_to_def_substitutes_build_args(tmp_path: Path) -> None:
    """--build-arg folds pin values into RUN lines the way Docker would
    expose them as env vars: ${KEY} and $KEY become the value, the
    ${KEY:-fallback} form drops the fallback (a pin never wants it)."""
    converter = REPO_ROOT / "blueprint" / "container" / "dockerfile_to_def.py"
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:26.04\n"
        "ARG PI_VERSION=\"\"\n"
        "ARG PNPM_VERSION=\"\"\n"
        "RUN npm install -g \"@earendil-works/pi-coding-agent@${PI_VERSION:-latest}\"\n"
        "RUN echo \"$PI_VERSION and $PNPM_VERSION\"\n"
    )
    result = subprocess.run(
        [sys.executable, str(converter), "--mode", "base",
         "--build-arg", "PI_VERSION=0.85.1", "--build-arg", "PNPM_VERSION=10.5.2",
         str(dockerfile)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"@earendil-works/pi-coding-agent@0.85.1"' in result.stdout
    assert "latest" not in result.stdout
    assert "0.85.1 and 10.5.2" in result.stdout


def test_dockerfile_to_def_build_args_on_real_base(tmp_path: Path) -> None:
    """The real Dockerfile.base must convert with the pins textually pinned:
    provided args resolve, unprovided ones keep their live-resolution
    fallback (Apptainer builds must not silently pin 'latest')."""
    converter = REPO_ROOT / "blueprint" / "container" / "dockerfile_to_def.py"
    dockerfile = REPO_ROOT / "blueprint" / "container" / "Dockerfile.base"
    result = subprocess.run(
        [sys.executable, str(converter), "--mode", "base",
         "--build-arg", "NODE_VERSION=v26.8.2",
         "--build-arg", "CLAUDE_CODE_VERSION=2.1.267",
         str(dockerfile)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert 'node_version="v26.8.2"' in result.stdout
    assert '"@anthropic-ai/claude-code@2.1.267"' in result.stdout
    assert "${NODE_VERSION" not in result.stdout
    assert "${CLAUDE_CODE_VERSION" not in result.stdout
    # unprovided tools keep the build-time fallback
    assert '"opencode-ai@${OPENCODE_VERSION:-latest}"' in result.stdout


def test_dockerfile_to_def_longest_key_first(tmp_path: Path) -> None:
    """$PI must not eat the front of $PI_VERSION."""
    converter = REPO_ROOT / "blueprint" / "container" / "dockerfile_to_def.py"
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:26.04\n"
        "RUN echo \"$PI $PI_VERSION\"\n"
    )
    result = subprocess.run(
        [sys.executable, str(converter), "--mode", "base",
         "--build-arg", "PI=3.14", "--build-arg", "PI_VERSION=0.85.1",
         str(dockerfile)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "3.14 0.85.1" in result.stdout


def test_dockerfile_to_def_rejects_malformed_build_arg(tmp_path: Path) -> None:
    converter = REPO_ROOT / "blueprint" / "container" / "dockerfile_to_def.py"
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ubuntu:26.04\n")
    result = subprocess.run(
        [sys.executable, str(converter), "--mode", "base", "--build-arg", "NOVALUE", str(dockerfile)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "KEY=VALUE" in result.stderr


def test_dockerfile_to_def_workarounds_gated_on_chgrp_probe(tmp_path: Path) -> None:
    """The root-mapped-namespace workarounds (group rewrite to GID 0, the
    base image's _ssh statoverride) must only run when chgrp to an unmapped
    gid actually fails. Under the fakeroot command — how locked-down hosts
    build — chgrp is faked and /etc/group is bind-mounted, so running them
    anyway dies (`sed -i` renames the file: EBUSY) instead of being the
    no-op it was meant to be."""
    converter = REPO_ROOT / "blueprint" / "container" / "dockerfile_to_def.py"
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ubuntu:26.04\nRUN true\n")
    for mode, extra in (("base", []), ("instance", ["--base-sif", "/base.sif"])):
        result = subprocess.run(
            [sys.executable, str(converter), "--mode", mode, *extra, str(dockerfile)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        probe = next(i for i, l in enumerate(lines) if l.strip().startswith("if ! chgrp 1 "))
        sed = next(i for i, l in enumerate(lines) if "sed -i -E" in l and "/etc/group" in l)
        close = next(i for i, l in enumerate(lines) if i > sed and l.strip() == "fi")
        assert probe < sed < close, f"{mode}: workaround must sit inside the chgrp gate"
        if mode == "base":
            assert any("dpkg-statoverride --add" in l for l in lines[probe:close])
        else:
            assert not any("dpkg-statoverride" in l for l in lines)
        # and the whole %post must stay valid shell
        post = result.stdout.split("%post\n", 1)[1]
        body = "\n".join(l[4:] for l in post.splitlines() if l.startswith("    "))
        assert subprocess.run(["sh", "-n"], input=body, text=True).returncode == 0


def test_dockerfile_to_def_precreates_ssh_group_without_groupadd(tmp_path: Path) -> None:
    """openssh-client's postinst creates the _ssh group with groupadd, which
    renames /etc/group — impossible when Apptainer's fakeroot engine
    bind-mounts it ('failure while writing changes', EBUSY on the rename).
    The base recipe must pre-create the group itself, in every build mode,
    via append (opens the file, never renames it) with a dynamically chosen
    free gid, and without ever invoking groupadd/addgroup."""
    converter = REPO_ROOT / "blueprint" / "container" / "dockerfile_to_def.py"
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ubuntu:26.04\nRUN true\n")
    result = subprocess.run(
        [sys.executable, str(converter), "--mode", "base", str(dockerfile)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert not any("groupadd" in l or " addgroup " in l for l in lines)
    guard = next(i for i, l in enumerate(lines) if "getent group _ssh" in l)
    append = next(i for i, l in enumerate(lines) if l.strip().startswith('echo "_ssh:x:'))
    assert append > guard
    assert ">> /etc/group" in lines[append]
    # dynamic free-gid scan: the loop reads getent, not a hardcoded gid
    assert any("getent group \"$__agentic_gid\"" in l for l in lines)
    # instance recipes have no openssh-client and must not carry the block
    result = subprocess.run(
        [sys.executable, str(converter), "--mode", "instance", "--base-sif", "/b.sif", str(dockerfile)],
        capture_output=True, text=True,
    )
    assert "_ssh" not in result.stdout


# --- interactive subcommands -------------------------------------------


def test_extract_instance_arg_splits_positional() -> None:
    from agentic_workspace.interactive import extract_instance_arg

    name, rest = extract_instance_arg(["--yes", "agre", "--keep-state"])
    assert name == "agre"
    assert rest == ["--yes", "--keep-state"]


def test_extract_instance_arg_keeps_option_values() -> None:
    from agentic_workspace.interactive import extract_instance_arg

    name, rest = extract_instance_arg(
        ["--install-dir", "/tmp/x", "--yes"], value_opts={"--install-dir", "--bin-dir"}
    )
    assert name is None
    assert rest == ["--install-dir", "/tmp/x", "--yes"]


def test_read_marker_env_override(tmp_path: Path, monkeypatch) -> None:
    from agentic_workspace import interactive

    marker = tmp_path / "marker"
    marker.write_text("# comment\nagre\nlera\n")
    monkeypatch.setenv("AGENTIC_INSTANCES_MARKER", str(marker))
    assert interactive.read_marker() == ["agre", "lera"]


def test_settings_bare_picks_instance_then_wizard(tmp_path: Path) -> None:
    """`settings` with no arguments asks which instance, then (no config yet)
    runs the full wizard. Piped EOF answers every wizard question with the
    default, so a config gets written."""
    env = sandbox_env(tmp_path)
    result = run_cli(["settings"], env=env, input="1\n")
    assert result.returncode == 0, result.stderr
    assert "pick an instance" in result.stdout
    config = tmp_path / ".config" / "agre" / "config.py"
    assert config.is_file()


def test_setup_alias_still_works(tmp_path: Path) -> None:
    env = sandbox_env(tmp_path)
    result = run_cli(["setup"], env=env, input="1\n")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".config" / "agre" / "config.py").is_file()


def test_settings_menu_edits_single_value(tmp_path: Path) -> None:
    env = sandbox_env(tmp_path)
    # First run: wizard with EOF defaults creates the config.
    assert run_cli(["settings"], env=env, input="1\n").returncode == 0
    config = tmp_path / ".config" / "agre" / "config.py"
    # Second run: pick instance 1, then menu: change item 3, save and exit.
    result = run_cli(["settings"], env=env, input="1\n3\nhttp://proxy:3128\nq\n")
    assert result.returncode == 0, result.stderr
    assert "Settings:" in result.stdout
    assert "http://proxy:3128" in config.read_text()


def test_settings_keyvalue_with_explicit_name(tmp_path: Path) -> None:
    env = sandbox_env(tmp_path)
    assert run_cli(["settings"], env=env, input="1\n").returncode == 0
    result = run_cli(["settings", "agre", "AGENTIC_HTTP_PROXY=http://p:1"], env=env)
    assert result.returncode == 0, result.stderr
    assert "AGENTIC_HTTP_PROXY" in (tmp_path / ".config" / "agre" / "config.py").read_text()


def test_clean_bare_picks_instance_and_confirms(tmp_path: Path) -> None:
    env = sandbox_env(tmp_path)
    result = run_cli(["clean"], env=env, input="1\n")
    assert result.returncode == 0, result.stderr
    assert "pick an instance" in result.stdout
    assert "Aborted." in result.stdout  # EOF at Continue? [y/N] means no


def test_clean_named_instance_skips_menu(tmp_path: Path) -> None:
    env = sandbox_env(tmp_path)
    result = run_cli(["clean", "agre", "--yes"], env=env)
    assert result.returncode == 0, result.stderr
    assert "Cleanup complete." in result.stdout


def test_uninstall_bare_picks_instance_and_confirms(tmp_path: Path) -> None:
    env = sandbox_env(tmp_path)
    result = run_cli(["uninstall"], env=env, input="1\n")
    assert result.returncode == 0, result.stderr
    assert "pick an instance" in result.stdout
    assert "Aborted." in result.stdout


def test_scaffold_interactive_bare(tmp_path: Path) -> None:
    """Bare scaffold asks for name, template (Enter = minimal), tool (Enter = pi)."""
    env = sandbox_env(tmp_path)
    result = run_cli(
        ["scaffold", "--dir", str(tmp_path / "out"), "--no-git"],
        env=env,
        input="testagent\n\n\n",
    )
    assert result.returncode == 0, result.stderr
    child = tmp_path / "out" / "testagent"
    assert (child / "manifest.yaml").is_file()
    assert (child / "testagent").is_file()
    assert "minimal" in result.stdout
