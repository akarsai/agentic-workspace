"""The `launch` subcommand: run an AI coding agent in a sandboxed container.

Python port of the old bin/agentic launcher. Instance identity (name, image,
tool, commands, instruction template) comes from the instance's manifest.yaml;
per-launch behaviour comes from CLI flags, environment, and the instance
config. AGENT_ROOT (set by the instance's launcher shim) selects the instance.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config as cfgmod
from . import oci, pty, skills, slurm, subscription
from .manifest import Manifest, load_manifest
from .paths import framework_dir, resolve_agent_root, scripts_dir
from .util import banner, bold, confirm, die, dim, error, info, say, warn

TOOL_DISPLAY = {
    "pi": "Pi",
    "claude": "Claude Code",
    "codex": "Codex CLI",
    "opencode": "OpenCode",
}

PASSTHROUGH_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "WANDB_API_KEY",
    "HF_TOKEN",
)

# The image pins the agent CLIs (blueprint/container/versions.json, refreshed
# by `agentic-workspace update`) and /opt/node is not writable by the runtime
# user, so a CLI's in-place self-update can only fail ("Auto-update failed:
# no write permission to npm prefix") -- and would fight the pin if it ever
# succeeded. Upgrades come from rebuilding the image, so every updater that
# can be switched off by env is.
PINNED_TOOL_ENV: tuple[tuple[str, str], ...] = (
    ("DISABLE_AUTOUPDATER", "1"),          # claude code
    ("OPENCODE_DISABLE_AUTOUPDATE", "1"),  # opencode
)

# API keys of known model-subscription providers (see subscription.py) are
# forwarded as well, so a subscription configured in the instance config can
# authenticate without any extra setup.
SUBSCRIPTION_KEY_ENVS = tuple(
    sorted(
        {
            e
            for p in subscription.SUBSCRIPTION_PROVIDERS.values()
            for e in (p["api_key_env"], *p.get("alt_api_key_envs", ()))
        }
    )
)

# Slurm settings forwarded from the launch shell so cluster-side requirements
# hold inside the sandbox (the container env is clean: --compat implies
# --cleanenv, host variables do not leak in). SBATCH_ACCOUNT is the one that
# bites on clusters without a default account: "sbatch: error: Account must be
# provided with job." -- export it once on the login node and every submission
# from inside the session is accounted.
SLURM_ENV_KEYS = (
    "SBATCH_ACCOUNT",
    "SALLOC_ACCOUNT",
    "SRUN_ACCOUNT",
    "SLURM_CLUSTERS",
)

# Directories the sandbox must never mount directly (security).
SENSITIVE_DIRS = ("/", "/etc", "/root", "/sys", "/proc", "/dev", "/boot")
# Credential dirs are blocked only under a user home (/home/*, /Users/*).
CREDENTIAL_DIRS = (".ssh", ".claude", ".gnupg", ".aws", ".kube", ".config/gcloud")


def host_identity() -> tuple[int, int, str, str]:
    """(uid, gid, user, group) matching `id -u/-g/-un/-gn`."""
    import grp
    import pwd

    uid = os.getuid()
    gid = os.getgid()
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = os.environ.get("USER", "agent")
    try:
        group = grp.getgrgid(gid).gr_name
    except KeyError:
        group = user
    return uid, gid, user, group


@dataclass
class Options:
    yolo: bool = False
    test: bool = False
    multi_node: bool = False
    debug_launch: bool = False
    workspace_dir: str | None = None
    tool_args: list[str] = field(default_factory=list)
    runtime_override: str | None = None
    tool_override: str | None = None
    model_specified: bool = False


@dataclass
class Dispatch:
    """A launcher flag that re-dispatches to another subcommand."""

    action: str
    args: list[str] = field(default_factory=list)


def parse_args(argv: list[str]) -> tuple[Options, Dispatch | None]:
    opts = Options()
    i = 0
    n = len(argv)
    while i < n:
        a = argv[i]
        if a == "--setup":
            return opts, Dispatch("setup", argv[i + 1 :])
        if a == "--build":
            return opts, Dispatch("build")
        if a == "--clean":
            return opts, Dispatch("clean", argv[i + 1 :])
        if a == "--uninstall":
            return opts, Dispatch("uninstall", argv[i + 1 :])
        if a == "--yolo":
            opts.yolo = True
        elif a == "--test":
            opts.test = True
        elif a == "--multi-node":
            opts.multi_node = True
        elif a == "--debug-launch":
            opts.debug_launch = True
        elif a == "--apptainer":
            opts.runtime_override = "apptainer"
        elif a == "--docker":
            opts.runtime_override = "docker"
        elif a == "--pi":
            opts.tool_override = "pi"
        elif a == "--codex":
            opts.tool_override = "codex"
        elif a == "--opencode":
            opts.tool_override = "opencode"
        elif a == "--claude":
            opts.tool_override = "claude"
        elif a == "--tool":
            if i + 1 >= n:
                return opts, die("--tool requires a value (pi, opencode, claude, or codex).")
            val = argv[i + 1]
            if val not in ("pi", "opencode", "claude", "codex"):
                return opts, die(f"Unsupported tool '{val}' (supported: pi, opencode, claude, codex).")
            opts.tool_override = val
            i += 1
        elif a in ("--help", "-h"):
            show_help()
            sys.exit(0)
        elif a in ("--resume", "-r"):
            if i + 1 < n and not argv[i + 1].startswith("-"):
                opts.tool_args += ["--resume", argv[i + 1]]
                i += 1
            else:
                opts.tool_args.append("--resume")
        elif a in ("--continue", "-c"):
            opts.tool_args.append("--continue")
        elif a == "--model":
            if i + 1 >= n or argv[i + 1].startswith("-"):
                return opts, die("--model requires a value")
            opts.model_specified = True
            opts.tool_args += ["--model", argv[i + 1]]
            i += 1
        elif a == "--context":
            if i + 1 >= n or argv[i + 1].startswith("-"):
                return opts, die("--context requires a value")
            opts.tool_args += ["--context", argv[i + 1]]
            i += 1
        elif a.startswith("-"):
            opts.tool_args.append(a)
        else:
            if opts.workspace_dir is None:
                opts.workspace_dir = a
            else:
                opts.tool_args.append(a)
        i += 1
    return opts, None


def show_help() -> None:
    print(
        """Usage: <instance> [OPTIONS] [DIRECTORY] [TOOL_OPTIONS...]

Options:
  --setup             Run the optional interactive setup wizard
  --yolo              Enable all read/write tools (skip permission prompts) for the selected tool
  --build             Build or rebuild the container image
  --apptainer         Use Apptainer runtime (build + launch on Linux HPC without Docker)
  --docker            Use Docker runtime (default)
  --clean             Remove launcher-managed local state
  --uninstall         Remove the installed launcher and optional local state
  --test              Quick validation of sandbox environment
  --multi-node        Enable multi-node dispatch (Apptainer + Slurm only)
  --debug-launch      Print extra launcher details and enable tool startup logs where supported
  --tool TOOL         Select CLI tool (pi, opencode, claude, or codex)
  --pi                Shorthand for --tool pi
  --codex             Shorthand for --tool codex
  --opencode          Shorthand for --tool opencode
  --claude            Shorthand for --tool claude
  --resume [ID]       Resume a session (interactive picker, or specify ID)
  --continue, -c      Continue the most recent conversation
  --model MODEL       Override default model
  DIRECTORY           Project directory to work in (default: current directory)
  TOOL_OPTIONS        Additional options passed to the CLI tool

Examples:
  <instance> --build                      # Build container
  <instance> --apptainer --build          # Build with Apptainer (no Docker needed)
  <instance> --apptainer                  # Launch with Apptainer
  <instance> --apptainer --multi-node     # Multi-node dispatch (Slurm, inside an allocation)
  <instance> --setup                      # Optional setup wizard
  <instance> --test                       # Validate environment
  <instance>                              # Current directory
  <instance> ~/my-project                 # Specific directory

What's Sandboxed:
  The agent can ONLY access your project directory.
  Cannot read/write files outside the project.
  Cannot access home directory, SSH keys, etc.

Security:
  The selected CLI tool has no built-in permission system.
  Review changes before committing to git."""
    )


def effective_config(name: str, manifest: Manifest, env: dict[str, str]) -> dict[str, str]:
    """Config file -> environment -> defaults (CLI overrides applied separately)."""
    file_cfg = cfgmod.load(name)
    defaults = {
        "AGENTIC_CONTAINER_RUNTIME": oci.detect_runtime(),
        "AGENTIC_CLI_TOOL": manifest.tool,
        "AGENTIC_API_KEY_ENV": oci.api_key_env_for(manifest.tool),
        "AGENTIC_STATE_ROOT": str(Path.home() / ".cache" / name),
        "AGENTIC_EXTRA_BIND_DIRS": "",
        "AGENTIC_EXTRA_ENV": "",
        "AGENTIC_DOCKER_GPUS": "auto",
        "AGENTIC_SUBAGENT_MODEL": "",
        "AGENTIC_SUBAGENT_TOOLS": "",
        "AGENTIC_SUBAGENT_THINKING": "",
        "AGENTIC_AUTH_MODE": "tool",
        "AGENTIC_MODEL_SUBSCRIPTION": "",
        "AGENTIC_API_PROVIDER": "",
        "AGENTIC_CUSTOM_ENDPOINT": "",
        "AGENTIC_CUSTOM_ANTHROPIC_ENDPOINT": "",
        "AGENTIC_HTTPS_PROXY": "",
        "AGENTIC_HTTP_PROXY": "",
        "AGENTIC_DEFAULT_MODEL": "",
    }
    cfg = {**defaults, **file_cfg}
    for key in cfgmod.KNOWN_KEYS:
        if env.get(key):
            cfg[key] = env[key]
    return cfg


def validate_workspace(path: str) -> Path:
    if not path:
        path = os.getcwd()
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        die(f"Directory does not exist: {path}")
        sys.exit(1)

    for d in SENSITIVE_DIRS:
        if real == d or real.startswith(d + "/"):
            die(f"Cannot sandbox system directories: {real}")
            sys.exit(1)
    # Credential dirs are blocked only under a user home (/home/*, /Users/*),
    # mirroring the original launcher's globs.
    for home in ("/home", "/Users"):
        if real.startswith(home + "/"):
            rest = real[len(home) + 1 :]
            segments = rest.split("/")
            if len(segments) >= 2 and segments[1] in CREDENTIAL_DIRS:
                die(f"Cannot sandbox credential directories: {real}")
                sys.exit(1)
    return Path(real)


def setup_storage(cfg: dict[str, str], name: str, tool: str) -> dict[str, Path]:
    state_root = Path(cfg["AGENTIC_STATE_ROOT"])
    dirs = {
        "state_root": state_root,
        "uv_cache": state_root / "uv" / "cache",
        "uv_python": state_root / "uv" / "python",
        "uv_tools": state_root / "uv" / "tools",
        "config_store": state_root / f"{name}-config",
        "hf_home": state_root / "hf_home",
        "triton_cache": state_root / "triton_cache",
        "wandb": state_root / "wandb",
    }
    store = dirs["config_store"]
    for d in [
        store,
        store / "commands",
        store / ".cache",
        store / ".local" / "bin",
        store / ".local" / "state",
        store / ".ssh",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    if tool == "pi":
        (store / ".pi" / "agent").mkdir(parents=True, exist_ok=True)
    elif tool == "claude":
        (store / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
    elif tool == "codex":
        (store / ".codex").mkdir(parents=True, exist_ok=True)
    else:  # opencode
        (store / ".config" / "opencode" / "commands").mkdir(parents=True, exist_ok=True)
        (store / ".local" / "share" / "opencode").mkdir(parents=True, exist_ok=True)
        dirs["opencode_config"] = state_root / "opencode" / "config"
        dirs["opencode_data"] = state_root / "opencode" / "data"
        dirs["opencode_config"].mkdir(parents=True, exist_ok=True)
        dirs["opencode_data"].mkdir(parents=True, exist_ok=True)

    (store / ".gitconfig").touch(exist_ok=True)
    for d in [dirs["uv_cache"], dirs["uv_python"], dirs["uv_tools"], dirs["hf_home"],
              dirs["triton_cache"], dirs["wandb"]]:
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def render_instruction_template(template: Path) -> str:
    """Strip <!-- DOCKER-OMIT-START --> .. <!-- DOCKER-OMIT-END --> blocks."""
    out = []
    skip = False
    for line in template.read_text(encoding="utf-8").splitlines(keepends=True):
        if "<!-- DOCKER-OMIT-START -->" in line:
            skip = True
            continue
        if "<!-- DOCKER-OMIT-END -->" in line:
            skip = False
            continue
        if not skip:
            out.append(line)
    return "".join(out)


def setup_instruction_file(manifest: Manifest, workspace: Path) -> None:
    """Sync the instance's default instructions into the workspace.

    - target (AGENTS.md) missing           -> write the rendered template
    - target exists, content identical     -> leave it alone
    - target exists, content differs       -> warn, show a colorful git diff,
      and ask before overwriting. The default answer is "no" when the
      existing file is newer than the template (it may hold edits from a
      previous session) and "yes" when the template is newer (the default
      instructions were updated since).
    """
    template = manifest.path.parent / manifest.instruction_template
    target = workspace / manifest.instruction_target
    if not template.is_file():
        return
    rendered = render_instruction_template(template)
    if not target.exists():
        target.write_text(rendered)
        return
    try:
        existing = target.read_text(encoding="utf-8")
    except OSError:
        warn(f"cannot read {target}; leaving it untouched.")
        return
    if existing == rendered:
        return
    if confirm_instruction_overwrite(template, target):
        try:
            target.write_text(rendered)
        except OSError:
            warn(f"cannot write {target}; leaving it untouched.")


def confirm_instruction_overwrite(template: Path, target: Path) -> bool:
    """Warn that `target` differs from the default instructions, show a
    colorful git diff, and ask whether to overwrite it with the template.

    Returns True when the caller should overwrite. The default answer is
    "no" if the existing target is newer than the template (project edits
    would be lost) and "yes" if the template is newer (the instructions
    were updated since the target was written). Equal mtimes count as
    "target newer" so the conservative default applies.
    """
    template_newer = template.stat().st_mtime > target.stat().st_mtime
    if template_newer:
        warn(
            f"{target.name} is older than the default instructions "
            f"({template.name}) and differs - the instructions were updated "
            "since it was written."
        )
    else:
        warn(
            f"{target.name} is newer than the default instructions "
            f"({template.name}) and differs - it may hold edits from a "
            "previous session."
        )
    show_instruction_diff(template, target)
    hint = "Y/n" if template_newer else "y/N"
    return confirm(f"Overwrite {target.name} with the default instructions?", hint)


def show_instruction_diff(template: Path, target: Path) -> None:
    """Print a colorful `git diff --no-index` between the rendered template
    (what would actually be written) and the existing target file.

    The rendered text is materialized in a scratch dir when the template
    contains DOCKER-OMIT blocks, so host-only lines don't show up as phantom
    changes. Falls back to a plain unified diff when git is unavailable or
    fails. Colors follow the util convention: on only when stdout is a TTY
    and NO_COLOR is not set.
    """
    rendered = render_instruction_template(template)
    src = template
    tmp_dir: str | None = None
    if template.read_text(encoding="utf-8") != rendered:
        tmp_dir = tempfile.mkdtemp(prefix="agentic-instructions-")
        src = Path(tmp_dir) / template.name
        src.write_text(rendered, encoding="utf-8")
    try:
        git = shutil.which("git")
        if git is None:
            warn("git not available - showing a plain diff instead.")
            _plain_diff(template.name, rendered, target)
            return
        color = "always" if (sys.stdout.isatty() and not os.environ.get("NO_COLOR")) else "never"
        try:
            proc = subprocess.run(
                [git, "diff", "--no-index", f"--color={color}", str(src), str(target)],
                capture_output=True,
                text=True,
            )
        except OSError:
            proc = None
        if proc is not None and proc.returncode in (0, 1) and proc.stdout:
            out = proc.stdout
            if tmp_dir:
                # git prints the scratch path as `a<abs-path>`; rewrite it to
                # `a/<name>` so the header shows the template's own name.
                out = out.replace(str(src), "/" + template.name)
            print(out, end="")
            return
        warn("git diff produced no output - showing a plain diff instead.")
        _plain_diff(template.name, rendered, target)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _plain_diff(name: str, rendered: str, target: Path) -> None:
    """Minimal unified diff fallback when git is unavailable."""
    a = rendered.splitlines()
    b = target.read_text(encoding="utf-8").splitlines()
    for line in difflib.unified_diff(
        a, b, fromfile=name, tofile=str(target), lineterm=""
    ):
        print(line)


def setup_workspace_scripts(manifest: Manifest, workspace: Path) -> None:
    """Sync the instance's scripts/ directory into the workspace.

    The scripts are launcher-managed tooling (e.g. the Slurm job prologue),
    not project data, so they are overwritten on every launch — fixes reach
    running projects without a manual copy. Files the project added to
    scripts/ are left alone.
    """
    src_dir = manifest.path.parent / "scripts"
    if not src_dir.is_dir():
        return
    dst_dir = workspace / "scripts"
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file()):
        target = dst_dir / src.relative_to(src_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def workspace_host_env(workspace: Path) -> list[tuple[str, str]]:
    """Env pairs exposing node-visible host paths to the sandbox.

    AGENTIC_WORKSPACE_HOST is the host path of the mounted /workspace. On
    clusters with a shared filesystem (Lustre/NFS home) compute nodes see the
    same path, so Slurm job scripts can cd there instead of the container-only
    /workspace (which does not exist on nodes).
    """
    return [("AGENTIC_WORKSPACE_HOST", str(workspace))]


def setup_style_file(manifest: Manifest, workspace: Path) -> None:
    """Copy the instance's default STYLE.md into the workspace if present.

    Mirrors the instruction-file setup: only when the workspace does not
    already have a STYLE.md (a per-project style file the agent built is never
    clobbered).
    """
    template = manifest.path.parent / "STYLE.md"
    target = workspace / "STYLE.md"
    if template.is_file() and not target.exists():
        target.write_text(template.read_text(encoding="utf-8"))


def setup_claude_memory(manifest: Manifest, workspace: Path, tool: str) -> None:
    """Point Claude Code's memory (CLAUDE.md) at the shared instructions.

    Claude Code reads CLAUDE.md; the other tools read AGENTS.md. Instead of
    duplicating the instructions (two copies to keep current, two to diff
    when they drift), CLAUDE.md just imports the instruction target with
    Claude's @path syntax. Created whenever the instance runs with claude
    as the tool and no CLAUDE.md exists yet -- an existing CLAUDE.md is the
    project's own and is never touched.
    """
    if tool != "claude":
        return
    memory = workspace / "CLAUDE.md"
    if memory.exists():
        return
    memory.write_text(f"@{manifest.instruction_target}\n", encoding="utf-8")
    say(f"Created {memory} (imports {manifest.instruction_target})")


def managed_agent_files(manifest: Manifest, workspace: Path, tool: str) -> list[str]:
    """Workspace entries the launcher owns in this workspace.

    Everything here is (re-)rendered per launch -- instructions, claude
    memory, skills, scratch space, the default style profile, instance
    scripts -- so none of it is project data. Only entries the instance
    actually produces are listed (scripts/ and STYLE.md only when the
    instance ships them), so the gitignore block stays honest.
    """
    managed = [Path(manifest.instruction_target).name]
    if tool == "claude":
        managed.append("CLAUDE.md")
    if (workspace / ".agents").is_dir():
        managed.append(".agents")
    managed.append(".tmp")
    if (manifest.path.parent / "STYLE.md").is_file():
        managed.append("STYLE.md")
    if (manifest.path.parent / "scripts").is_dir():
        managed.append("scripts")
    return list(dict.fromkeys(managed))  # dedupe, keep order


# Standard sandbox bycatch: python/uv artifacts agents regenerate on every
# run, Claude Code's project-local state, and generated documents. Written
# together with the managed files so a fresh workspace repo is solid from
# the get-go, not after the first accidental commit of a .venv or a build
# artifact.
IGNORE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python / uv (regenerated in the sandbox)",
     (".venv/", "venv/", "__pycache__/", "*.py[cod]", "*.egg-info/",
      "dist/", "build/", ".pytest_cache/", ".ruff_cache/", "*.lock")),
    ("claude code project state", (".claude/",)),
    ("generated output", ("*.pdf",)),
)

_CHAR_CLASS = re.compile(r"\[[^\]]*\]")


def ignore_probe(pattern: str) -> str:
    """A concrete relative path that `pattern` would match.

    `git check-ignore` answers for paths, not for patterns, so asking
    whether the repo already covers a candidate line means handing git a
    filename that line matches: the glob metacharacters collapse to a
    literal (`*`/`?` -> `x`, `[cod]` -> `c`) and a directory pattern gets a
    child, since `.venv/` only ever matches as a path component.
    """
    body = pattern.strip("/")
    body = _CHAR_CLASS.sub(lambda m: (m.group()[1:-1].lstrip("!^") or "x")[0], body)
    body = body.replace("*", "x").replace("?", "x")
    return f"{body}/probe" if pattern.endswith("/") else body


def already_ignored(repo_root: Path, workspace: Path, patterns: list[str]) -> set[str]:
    """The subset of `patterns` the repo's existing ignore rules already cover.

    The question is asked of git rather than matched textually, so a rule
    that differs in spelling (`*.pyc` for `*.py[cod]`, `.venv` for `.venv/`),
    a broader parent rule (`.tmp/` for a nested name), or a re-include
    (`!keep.pdf`) all count — the point is whether the file ends up ignored,
    not whether the line is there verbatim. `--no-index` is what keeps that
    true for a path the repo already tracks: without it git answers for the
    *file* (ignore rules never apply to a tracked path, so it reports "not
    ignored" however the rule is spelled) and a project that commits, say,
    its own `scripts/` would be offered the same line on every launch and
    grow a duplicate in .gitignore each time. One batched call; on any git
    error nothing is reported as covered and the caller falls back to asking.
    """
    if not patterns:
        return set()
    root = repo_root.resolve()
    try:
        probes = {
            p: str((workspace / ignore_probe(p)).resolve().relative_to(root))
            for p in patterns
        }
    except ValueError:
        return set()  # workspace outside the repo: nothing to judge, ask instead
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "-z", "--no-index", "--stdin"],
        input="\0".join(probes.values()) + "\0",
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        return set()  # git said something unexpected: treat nothing as covered
    matched = {m for m in proc.stdout.split("\0") if m}
    return {p for p, probe in probes.items() if probe in matched}


def pending_ignores(
    repo_root: Path, workspace: Path, managed: list[str]
) -> list[tuple[str, list[str]]]:
    """The ignore sections still worth appending, already-covered lines dropped.

    Sections that come out empty are dropped too, so an empty result means
    the repo needs nothing and there is nothing to prompt about -- which is
    what makes a second launch silent.
    """
    sections = [
        ("launcher-managed agent files (agentic-workspace)", list(dict.fromkeys(managed))),
        *((title, list(entries)) for title, entries in IGNORE_GROUPS),
    ]
    covered = already_ignored(
        repo_root, workspace, [p for _, entries in sections for p in entries]
    )
    return [
        (title, keep)
        for title, entries in sections
        if (keep := [p for p in entries if p not in covered])
    ]


def agent_ignore_block(sections: list[tuple[str, list[str]]]) -> str:
    """Render the pending sections as a .gitignore snippet."""
    lines: list[str] = []
    for title, entries in sections:
        lines.append(f"# {title}")
        lines.extend(entries)
    return "\n".join(lines) + "\n" if lines else ""


def ensure_agent_files_ignored(workspace: Path, managed: list[str]) -> None:
    """Prompt to gitignore the launcher-managed agent files in the workspace.

    These files are rendered into the workspace per launch (instructions,
    claude memory, skills, scratch, style profile, scripts); they are
    working state for the sandboxed agent, not project data, and committing
    them invites churn and drift. When the workspace sits inside a git repo,
    every line the launcher would write -- the managed names plus the
    standard sandbox ignores -- is checked against the repo's actual ignore
    rules, and only what is genuinely uncovered is offered, listed in full
    so the prompt says exactly what lands in the file. Nothing left to add
    means no prompt at all, so accepting once ends the question for good.
    Best-effort: without git, outside a repo, or on a declined prompt there
    is nothing to enforce -- just warn.
    """
    if not managed:
        return
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return  # no git on PATH: nothing to check
    if proc.returncode != 0:
        return  # not inside a git repo: nothing to prompt about
    repo_root = Path(proc.stdout.strip())
    sections = pending_ignores(repo_root, workspace, managed)
    if not sections:
        return  # every line is already covered by an existing rule
    gitignore = repo_root / ".gitignore"
    count = sum(len(entries) for _, entries in sections)
    say(f"\nNot ignored yet in {gitignore} ({count} "
        f"{'entry' if count == 1 else 'entries'}):")
    for title, entries in sections:
        dim(f"  # {title}")
        for entry in entries:
            say(f"    {entry}")
    if not confirm(
        "These are launcher-managed agent files and sandbox bycatch; add them?",
        "Y/n",
    ):
        warn("left unignored -- keep them out of commits; they are re-rendered on every launch")
        return
    body = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    if body and not body.endswith("\n"):
        body += "\n"
    try:
        gitignore.write_text(body + agent_ignore_block(sections), encoding="utf-8")
    except OSError as exc:
        warn(f"could not write {gitignore}: {exc}")
        return
    say(f"Added {count} ignore entries to {gitignore}")


def yolo_flag(tool: str) -> str | None:
    return {
        "claude": "--dangerously-skip-permissions",
        "codex": "--dangerously-bypass-approvals-and-sandbox",
        "opencode": "--auto",
    }.get(tool)


def translate_tool_args(opts: Options, tool: str, tool_display: str, cfg: dict[str, str]) -> list[str]:
    out: list[str] = []
    i = 0
    args = opts.tool_args
    while i < len(args):
        a = args[i]
        if a == "--dangerously-skip-permissions":
            flag = yolo_flag(tool)
            if flag:
                out.append(flag)
            else:
                warn(f"--dangerously-skip-permissions has no effect in {tool_display} mode (no permission system)")
        elif a == "--resume":
            nxt = args[i + 1] if i + 1 < len(args) else None
            if tool == "claude":
                if nxt and not nxt.startswith("-"):
                    out += ["--resume", nxt]
                    i += 1
                else:
                    out.append("--continue")
            elif tool == "codex":
                if nxt and not nxt.startswith("-"):
                    out += ["resume", nxt]
                    i += 1
                else:
                    out.append("resume")
            elif nxt and not nxt.startswith("-"):
                out += ["--session", nxt]
                i += 1
            elif tool == "pi":
                out.append("-r")
            else:
                warn("--resume without ID not supported in OpenCode")
        elif a == "--continue":
            out += ["resume", "--last"] if tool == "codex" else [a]
        elif a == "--context":
            i += 1
            warn(f"--context not supported in {tool_display}")
        else:
            out.append(a)
        i += 1

    if not opts.model_specified and cfg.get("AGENTIC_DEFAULT_MODEL"):
        out += ["--model", cfg["AGENTIC_DEFAULT_MODEL"]]

    if opts.debug_launch:
        if tool == "pi":
            out.append("--verbose")
        elif tool == "claude":
            out.append("--debug")
        elif tool == "opencode":
            out += ["--print-logs", "--log-level", "DEBUG"]

    if opts.yolo:
        flag = yolo_flag(tool)
        if flag:
            if flag not in out:
                out.append(flag)
        else:
            warn(f"--yolo has no effect in {tool_display} mode (no permission system)")
    return out


def normalize_bind_dirs(raw: str) -> list[str]:
    """Accept commas, spaces, or colons as separators -> colon-separated list."""
    s = re.sub(r"[,\s]+", ":", raw)
    s = re.sub(r":{2,}", ":", s).strip(":")
    return [d for d in s.split(":") if d]


def sanitize_mount_name(raw: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]", "_", raw)
    return s or "mount"


def setup_optional_binds(
    cfg: dict[str, str], workspace: Path, state_root: Path
) -> tuple[list[tuple[str, str, str | None]], list[str]]:
    """Returns (binds [(host, container, mode)], debug_lines)."""
    binds: list[tuple[str, str, str | None]] = []
    debug: list[str] = []
    raw = cfg.get("AGENTIC_EXTRA_BIND_DIRS") or ""
    if not raw:
        return binds, debug

    mount_state = state_root / "workspace-mounts" / hashlib.md5(
        str(workspace).encode()
    ).hexdigest()[:10]
    mount_state.mkdir(parents=True, exist_ok=True)
    binds.append((str(mount_state), "/workspace/.mount", None))

    used: list[str] = []
    for d in normalize_bind_dirs(raw):
        if d.startswith(str(workspace / ".mount")) or d == str(workspace / ".mount"):
            warn(f"Skipping extra bind inside reserved workspace mount root: {d}")
            continue
        if not os.path.isdir(d):
            warn(f"Extra bind directory not found: {d}")
            continue
        mount_name = sanitize_mount_name(os.path.basename(d))
        if mount_name in used:
            warn(f"Skipping extra bind with duplicate mount name '{mount_name}': {d}")
            continue
        used.append(mount_name)
        host_dir = mount_state / mount_name
        host_dir.mkdir(parents=True, exist_ok=True)
        container_dir = f"/workspace/.mount/{mount_name}"
        binds.append((d, container_dir, None))
        debug.append(f"{d} -> {container_dir}")
    return binds, debug


def build_env_args(
    cfg: dict[str, str], env: dict[str, str], dirs: dict[str, Path], name: str, tool: str
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = [
        ("UV_CACHE_DIR", "/uv-cache"),
        ("UV_PYTHON_INSTALL_DIR", "/uv-python"),
        ("UV_TOOL_DIR", "/uv-tools"),
        ("UV_LINK_MODE", "symlink"),
        ("HF_HOME", str(dirs["hf_home"])),
        ("TRITON_CACHE_DIR", str(dirs["triton_cache"])),
        ("WANDB_DIR", str(dirs["wandb"])),
        ("TERM", env.get("TERM", "xterm-256color")),
        ("USER", env.get("USER", os.environ.get("USER", "agent"))),
        ("GIT_SSH_COMMAND", "ssh"),
        ("SANDBOX_TOOL", tool),
        ("AGENT_NAME", name),
        ("AGENTIC_SUBAGENT_MODEL", cfg.get("AGENTIC_SUBAGENT_MODEL", "")),
        ("AGENTIC_SUBAGENT_TOOLS", cfg.get("AGENTIC_SUBAGENT_TOOLS", "")),
        ("AGENTIC_SUBAGENT_THINKING", cfg.get("AGENTIC_SUBAGENT_THINKING", "")),
        *PINNED_TOOL_ENV,
    ]
    pairs += subscription.engine_env_pairs(cfg.get("AGENTIC_MODEL_SUBSCRIPTION", ""), tool, env)
    for key in PASSTHROUGH_KEYS + SUBSCRIPTION_KEY_ENVS:
        if env.get(key):
            pairs.append((key, env[key]))
    for key in SLURM_ENV_KEYS:
        if env.get(key):
            pairs.append((key, env[key]))
    proxy = cfg.get("AGENTIC_HTTPS_PROXY") or env.get("HTTPS_PROXY")
    if proxy:
        http_proxy = cfg.get("AGENTIC_HTTP_PROXY") or env.get("HTTP_PROXY") or proxy
        pairs.append(("https_proxy", proxy))
        pairs.append(("http_proxy", http_proxy))
    for entry in (cfg.get("AGENTIC_EXTRA_ENV") or "").split("|"):
        if entry and "=" in entry:
            k, _, v = entry.partition("=")
            pairs.append((k.strip(), v))
    return pairs


def setup_tool_config(
    cfg: dict[str, str], env: dict[str, str], manifest: Manifest, dirs: dict[str, Path]
) -> None:
    tool = cfg["AGENTIC_CLI_TOOL"]
    store = dirs["config_store"]
    if tool == "pi":
        (store / ".pi" / "agent").mkdir(parents=True, exist_ok=True)
        # A model subscription registers its provider in pi's models.json
        # (mounted as /home/.pi/agent); no-ops for unset/invalid specs.
        subscription.write_pi_models_json(store, cfg.get("AGENTIC_MODEL_SUBSCRIPTION", ""))
        return
    if tool == "claude":
        commands_dir = store / ".claude" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        for cmd in manifest.commands:
            src = manifest.path.parent / "commands" / f"{cmd}.md"
            if src.is_file():
                shutil.copy2(src, commands_dir / f"{cmd}.md")
        return
    if tool == "codex":
        return

    # opencode
    oc_config = dirs["opencode_config"]
    oc_data = dirs["opencode_data"]
    oc_config.mkdir(parents=True, exist_ok=True)
    oc_data.mkdir(parents=True, exist_ok=True)
    commands_dir = oc_config / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)

    if cfg.get("AGENTIC_CUSTOM_ENDPOINT"):
        template = framework_dir() / "config" / "opencode_config.template.json"
        if template.is_file():
            api_key = env.get(cfg.get("AGENTIC_API_KEY_ENV") or "OPENAI_API_KEY", "")
            rendered = (
                template.read_text()
                .replace("__AGENTIC_CUSTOM_ENDPOINT__", cfg["AGENTIC_CUSTOM_ENDPOINT"])
                .replace("__AGENTIC_API_KEY__", api_key)
            )
            (oc_config / "opencode.json").write_text(rendered)

    for cmd in manifest.commands:
        src = manifest.path.parent / "commands" / f"{cmd}.md"
        if src.is_file():
            shutil.copy2(src, commands_dir / f"{cmd}.md")


def print_debug_launch(
    opts: Options, cfg: dict[str, str], dirs: dict[str, Path], binds_debug: list[str], tool_args: list[str]
) -> None:
    if not opts.debug_launch:
        return
    print("Launch debug:")
    print(f"  Runtime:        {cfg['AGENTIC_CONTAINER_RUNTIME']}")
    print(f"  Tool:           {cfg['AGENTIC_CLI_TOOL']}")
    print(f"  Workspace:      {opts.workspace_dir}")
    print(f"  State root:     {dirs['state_root']}")
    print(f"  Tool args:      {' '.join(tool_args) if tool_args else '(none)'}")
    for line in binds_debug:
        print(f"    {line}")


def launch_apptainer(
    mode: str,
    cfg: dict[str, str],
    env: dict[str, str],
    manifest: Manifest,
    opts: Options,
    dirs: dict[str, Path],
    extra_binds: list[tuple[str, str, str | None]],
    slurm_mounts: list[str],
    slurm_env: list[str],
    mn_binds: list[str],
    mn_env: list[tuple[str, str]],
    tool_args: list[str],
) -> int:
    name = manifest.name
    image_sif = manifest.path.parent / ".apptainer" / f"{name}.sif"
    if not image_sif.is_file():
        error(f"Apptainer image not found: {image_sif}")
        print()
        print("Build it first:")
        print(f"  {name} --apptainer --build")
        return 1

    bind_args: list[str] = [
        "--compat",
        "--no-mount", "home",
        "--home", "/home",
        "--bind", f"{opts.workspace_dir}:/workspace",
        "--bind", f"{dirs['uv_cache']}:/uv-cache",
        "--bind", f"{dirs['uv_python']}:/uv-python",
        "--bind", f"{dirs['uv_tools']}:/uv-tools",
        "--bind", f"{dirs['apptainer_tmp']}:/tmp",
        "--bind", f"{dirs['state_root']}:{dirs['state_root']}",
        "--bind", f"{dirs['config_store']}:/home",
        "--pwd", "/workspace",
    ]
    bind_args += oci.gpu_arguments("apptainer", cfg.get("AGENTIC_DOCKER_GPUS", "auto"))
    for host, container, mode in extra_binds:
        spec = f"{host}:{container}" + (f":{mode}" if mode else "")
        bind_args += ["--bind", spec]
    for spec in slurm_mounts:
        bind_args += ["--bind", spec]
    if opts.multi_node:
        bind_args += mn_binds

    if dirs.get("opencode_config") and dirs["opencode_config"].is_dir():
        bind_args += ["--bind", f"{dirs['opencode_config']}:/home/.config/opencode"]
    if dirs.get("opencode_data") and dirs["opencode_data"].is_dir():
        bind_args += ["--bind", f"{dirs['opencode_data']}:/home/.local/share/opencode"]
    template = manifest.path.parent / manifest.instruction_template
    if template.is_file():
        tpl_out = dirs["config_store"] / "INSTRUCTIONS.md.template"
        tpl_out.write_text(render_instruction_template(template))
        bind_args += ["--bind", f"{tpl_out}:/home/.claude/INSTRUCTIONS.md.template:ro"]
    if os.path.isdir(os.path.expanduser("~/.ssh")):
        bind_args += ["--bind", f"{os.path.expanduser('~/.ssh')}:/home/.ssh:ro"]
    if os.path.isfile(os.path.expanduser("~/.gitconfig")):
        bind_args += ["--bind", f"{os.path.expanduser('~/.gitconfig')}:/home/.gitconfig:ro"]

    app_env: list[tuple[str, str]] = [
        # NB: no HOME here -- Apptainer rejects --env HOME ("Overriding HOME
        # ... is not permitted") and --home /home above already sets it.
        ("HOST_UID", str(host_identity()[0])),
        ("HOST_GID", str(host_identity()[1])),
        ("HOST_USER", host_identity()[2]),
        ("HOST_GROUP", host_identity()[3]),
        ("AGENTIC_CLI_TOOL", cfg["AGENTIC_CLI_TOOL"]),
    ]
    app_env += workspace_host_env(Path(opts.workspace_dir))
    app_env += build_env_args(cfg, env, dirs, name, cfg["AGENTIC_CLI_TOOL"])
    for pair in slurm_env:
        k, _, v = pair.partition("=")
        app_env.append((k, v))
    if opts.multi_node:
        app_env += mn_env

    env_args: list[str] = []
    for k, v in app_env:
        env_args += ["--env", f"{k}={v}"]

    if mode == "test":
        bind_args += ["--bind", f"{scripts_dir() / 'test_sandbox.py'}:/test_sandbox.py:ro"]
        return subprocess.call(
            ["apptainer", "exec", *bind_args, *env_args, str(image_sif), "/test_sandbox.py"]
        )
    return pty.run_with_pty(
        ["apptainer", "run", *bind_args, *env_args, str(image_sif), *tool_args]
    )


def launch_oci_runtime(
    mode: str,
    cfg: dict[str, str],
    env: dict[str, str],
    manifest: Manifest,
    opts: Options,
    dirs: dict[str, Path],
    extra_binds: list[tuple[str, str, str | None]],
    slurm_mounts: list[str],
    slurm_env: list[str],
    tool_args: list[str],
) -> int:
    runtime = cfg["AGENTIC_CONTAINER_RUNTIME"]
    if shutil.which(runtime) is None:
        error(f"{runtime} runtime selected, but '{runtime}' is not installed or not on PATH.")
        print()
        print(f"Install {runtime} on the host, then rerun:")
        print(f"  {manifest.name} --{runtime} --build")
        return 1

    oci_args: list[str] = [
        runtime, "run", "--rm", "--init",
        "-v", f"{opts.workspace_dir}:/workspace",
        "-v", f"{dirs['uv_cache']}:/uv-cache",
        "-v", f"{dirs['uv_python']}:/uv-python",
        "-v", f"{dirs['uv_tools']}:/uv-tools",
        "-v", f"{dirs['state_root']}:{dirs['state_root']}",
        "-v", f"{dirs['config_store']}:/home",
        "-w", "/workspace",
    ]
    if sys.stdin.isatty() and sys.stdout.isatty():
        oci_args += ["-it"]
    oci_args += oci.gpu_arguments("docker", cfg.get("AGENTIC_DOCKER_GPUS", "auto"))
    for host, container, mode in extra_binds:
        spec = f"{host}:{container}" + (f":{mode}" if mode else "")
        oci_args += ["-v", spec]
    for spec in slurm_mounts:
        oci_args += ["-v", spec]

    env_pairs: list[tuple[str, str]] = [
        ("UV_CACHE_DIR", "/uv-cache"),
        ("UV_PYTHON_INSTALL_DIR", "/uv-python"),
        ("UV_TOOL_DIR", "/uv-tools"),
        ("UV_LINK_MODE", "symlink"),
        ("HF_HOME", str(dirs["hf_home"])),
        ("TRITON_CACHE_DIR", str(dirs["triton_cache"])),
        ("WANDB_DIR", str(dirs["wandb"])),
        ("TERM", env.get("TERM", "xterm-256color")),
        ("HOME", "/home"),
        ("HOST_UID", str(host_identity()[0])),
        ("HOST_GID", str(host_identity()[1])),
        ("HOST_USER", host_identity()[2]),
        ("HOST_GROUP", host_identity()[3]),
        ("SANDBOX_TOOL", cfg["AGENTIC_CLI_TOOL"]),
        ("AGENT_NAME", manifest.name),
        ("AGENTIC_SUBAGENT_MODEL", cfg.get("AGENTIC_SUBAGENT_MODEL", "")),
        ("AGENTIC_SUBAGENT_TOOLS", cfg.get("AGENTIC_SUBAGENT_TOOLS", "")),
        ("AGENTIC_SUBAGENT_THINKING", cfg.get("AGENTIC_SUBAGENT_THINKING", "")),
        *PINNED_TOOL_ENV,
    ]
    env_pairs += subscription.engine_env_pairs(
        cfg.get("AGENTIC_MODEL_SUBSCRIPTION", ""), cfg["AGENTIC_CLI_TOOL"], env
    )
    env_pairs += workspace_host_env(Path(opts.workspace_dir))
    for key in PASSTHROUGH_KEYS + SUBSCRIPTION_KEY_ENVS:
        if env.get(key):
            env_pairs.append((key, env[key]))
    for key in SLURM_ENV_KEYS:
        if env.get(key):
            env_pairs.append((key, env[key]))
    proxy = cfg.get("AGENTIC_HTTPS_PROXY") or env.get("HTTPS_PROXY")
    if proxy:
        http_proxy = cfg.get("AGENTIC_HTTP_PROXY") or env.get("HTTP_PROXY") or proxy
        env_pairs.append(("https_proxy", proxy))
        env_pairs.append(("http_proxy", http_proxy))
    for entry in (cfg.get("AGENTIC_EXTRA_ENV") or "").split("|"):
        if entry and "=" in entry:
            k, _, v = entry.partition("=")
            env_pairs.append((k.strip(), v))
    for pair in slurm_env:
        k, _, v = pair.partition("=")
        env_pairs.append((k, v))

    for k, v in env_pairs:
        oci_args += ["-e", f"{k}={v}"]

    if dirs.get("opencode_config") and dirs["opencode_config"].is_dir():
        oci_args += ["-v", f"{dirs['opencode_config']}:/home/.config/opencode"]
    if dirs.get("opencode_data") and dirs["opencode_data"].is_dir():
        oci_args += ["-v", f"{dirs['opencode_data']}:/home/.local/share/opencode"]
    template = manifest.path.parent / manifest.instruction_template
    if template.is_file():
        tpl_out = dirs["config_store"] / "INSTRUCTIONS.md.template"
        tpl_out.write_text(render_instruction_template(template))
        oci_args += ["-v", f"{tpl_out}:/home/.claude/INSTRUCTIONS.md.template:ro"]
    if os.path.isdir(os.path.expanduser("~/.ssh")):
        oci_args += ["-v", f"{os.path.expanduser('~/.ssh')}:/home/.ssh:ro"]
    if os.path.isfile(os.path.expanduser("~/.gitconfig")):
        oci_args += ["-v", f"{os.path.expanduser('~/.gitconfig')}:/home/.gitconfig:ro"]

    if mode == "test":
        oci_args += ["--entrypoint", "/test_sandbox.py"]
        oci_args += ["-v", f"{scripts_dir() / 'test_sandbox.py'}:/test_sandbox.py:ro"]
        oci_args += ["-e", f"AGENTIC_CLI_TOOL={cfg['AGENTIC_CLI_TOOL']}"]
        return subprocess.call([*oci_args, manifest.image])
    return subprocess.call([*oci_args, manifest.image, *tool_args])


def validate_multi_node(opts: Options, cfg: dict[str, str], env: dict[str, str]) -> dict | None:
    if not opts.multi_node:
        return None
    if cfg["AGENTIC_CONTAINER_RUNTIME"] != "apptainer":
        die("--multi-node requires the Apptainer runtime (--apptainer).")
        sys.exit(1)
    if not env.get("SLURM_JOB_ID"):
        error("--multi-node requires an active multi-node Slurm allocation.")
        print()
        print("First allocate multiple nodes, e.g.:")
        print("  salloc --nodes=2 --gres=gpu:2 --time=04:00:00")
        print()
        print("For single-node use, omit --multi-node.")
        sys.exit(1)
    nodelist = env.get("SLURM_JOB_NODELIST")
    if not nodelist:
        die("SLURM_JOB_NODELIST not set.")
        sys.exit(1)

    try:
        nodes = subprocess.run(
            ["scontrol", "show", "hostnames", nodelist], capture_output=True, text=True, check=True
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        die("could not run 'scontrol show hostnames'.")
        sys.exit(1)
    head_node = os.uname().nodename.split(".")[0]
    num_nodes = len(nodes)
    if num_nodes < 2:
        warn(f"--multi-node requested but the allocation has only 1 node ({nodelist}).")
        warn("  No remote nodes to dispatch to; running WITHOUT multi-node dispatch.")
        opts.multi_node = False
        return None

    gpus = env.get("SLURM_GPUS_ON_NODE")
    if not gpus:
        try:
            out = subprocess.run(
                ["scontrol", "show", "job", env["SLURM_JOB_ID"]],
                capture_output=True, text=True, check=True,
            ).stdout
            m = re.search(r"TresPerNode=gres/gpu(:[a-zA-Z0-9]+)?:(\d+)", out)
            if m:
                gpus = m.group(2)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    if not gpus:
        try:
            out = subprocess.run(
                ["nvidia-smi", "-L"], capture_output=True, text=True, check=True
            ).stdout
            gpus = str(len(out.strip().splitlines()))
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    if not gpus or gpus == "0":
        warn("Could not detect GPUs per node, defaulting to 4")
        gpus = "4"
    return {
        "nodelist": nodelist,
        "nodes": nodes,
        "head_node": head_node,
        "num_nodes": num_nodes,
        "gpus_per_node": gpus,
    }


def setup_multi_node(
    mn: dict, cfg: dict[str, str], env: dict[str, str], manifest: Manifest, dirs: dict[str, Path], workspace: Path
) -> tuple[list[str], list[tuple[str, str]], Path, str]:
    name = manifest.name
    image_sif = manifest.path.parent / ".apptainer" / f"{name}.sif"
    dispatch_dir = dirs["state_root"] / f"dispatch-{env['SLURM_JOB_ID']}"
    jobs_dir = dispatch_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    conf = {
        "dispatch_dir": str(dispatch_dir),
        "container_image": str(image_sif),
        "workspace_host": str(workspace),
        "state_root": str(dirs["state_root"]),
        "uv_cache_dir": str(dirs["uv_cache"]),
        "uv_python_install_dir": str(dirs["uv_python"]),
        "uv_tool_dir": str(dirs["uv_tools"]),
        "hf_home": str(dirs["hf_home"]),
        "triton_cache_dir": str(dirs["triton_cache"]),
        "wandb_dir": str(dirs["wandb"]),
        "https_proxy": cfg.get("AGENTIC_HTTPS_PROXY") or env.get("HTTPS_PROXY", ""),
        "http_proxy": cfg.get("AGENTIC_HTTP_PROXY") or env.get("HTTP_PROXY", ""),
        "head_node": mn["head_node"],
        "slurm_job_id": env["SLURM_JOB_ID"],
        "slurm_job_nodelist": mn["nodelist"],
    }
    import json

    (dispatch_dir / "dispatch.json").write_text(json.dumps(conf, indent=2))

    remote_run = scripts_dir() / "remote_run.py"
    mn_binds = [
        f"{dispatch_dir}:{dispatch_dir}",
        f"{remote_run}:/usr/local/bin/remote-run:ro",
    ]
    mn_env = [
        ("AGENTIC_DISPATCH_DIR", str(dispatch_dir)),
        ("AGENTIC_GPUS_PER_NODE", mn["gpus_per_node"]),
        ("AGENTIC_NUM_NODES", str(mn["num_nodes"])),
        ("AGENTIC_HEAD_NODE", mn["head_node"]),
        ("SLURM_JOB_NODELIST", mn["nodelist"]),
    ]
    return mn_binds, mn_env, dispatch_dir, name


def start_dispatcher(dispatch_dir: Path) -> subprocess.Popen | None:
    print("Starting dispatcher daemon...")
    log_file = open(dispatch_dir / "dispatcher.log", "ab")
    proc = subprocess.Popen(
        [sys.executable, str(scripts_dir() / "dispatcher.py"), str(dispatch_dir / "dispatch.json")],
        stdout=log_file,
        stderr=log_file,
    )
    time.sleep(2)
    if proc.poll() is not None:
        error(f"Dispatcher failed to start. Check {dispatch_dir / 'dispatcher.log'}")
        return None
    print(f"Dispatcher running (pid {proc.pid})")
    print()
    print("Inside the container, use 'remote-run' to dispatch to other nodes:")
    print("  remote-run --nodes                              # List nodes")
    print("  remote-run htc-gpuXXX -- uv run python train.py # Run on remote node")
    print("  remote-run htc-gpuXXX --bg -- command           # Run in background")
    print("  remote-run --status                             # Check all jobs")
    print()
    return proc


def cleanup_multi_node(dispatcher: subprocess.Popen | None, dispatch_dir: Path) -> None:
    print()
    print("Shutting down dispatcher...")
    if dispatcher and dispatcher.poll() is None:
        dispatcher.terminate()
        try:
            dispatcher.wait(timeout=5)
        except subprocess.TimeoutExpired:
            dispatcher.kill()
    for pid_file in (dispatch_dir / "jobs").glob("*.pid"):
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 15)
        except (ValueError, ProcessLookupError, OSError):
            pass
    print(f"Dispatch directory preserved at: {dispatch_dir}")
    print("  (delete manually when done reviewing logs)")


def test_multi_node(mn: dict, cfg: dict[str, str], manifest: Manifest, dirs: dict[str, Path], workspace: Path) -> int:
    image_sif = manifest.path.parent / ".apptainer" / f"{manifest.name}.sif"
    print("Testing multi-node setup...")
    print()
    print("Nodes in allocation:")
    for node in mn["nodes"]:
        marker = " (head)" if node == mn["head_node"] else " (remote)"
        print(f"  {node}{marker}")
    print()
    print("Testing container on head node...")
    subprocess.run(
        [
            "apptainer", "exec", "--nv", "--no-mount", "home", "--home", "/home",
            "--bind", f"{workspace}:/workspace",
            "--bind", f"{dirs['apptainer_tmp']}:/tmp",
            "--pwd", "/workspace",
            str(image_sif),
            "bash", "-c", 'echo "  Container OK: $(python3 --version), GPUs: $(nvidia-smi -L 2>/dev/null | wc -l)"',
        ]
    )
    print()
    print("Testing dispatch to remote nodes...")
    for node in mn["nodes"]:
        if node == mn["head_node"]:
            continue
        print(f"  {node}: ", end="")
        rc = subprocess.call(
            [
                "srun", "--overlap", "--nodes=1", "--ntasks=1", f"--nodelist={node}",
                f"--gres=gpu:{mn['gpus_per_node']}", "--cpu-bind=none",
                "apptainer", "exec", "--nv", "--no-mount", "home", "--home", "/home",
                "--bind", f"{workspace}:/workspace",
                "--bind", f"{dirs['state_root']}:{dirs['state_root']}",
                "--bind", f"{dirs['apptainer_tmp']}:/tmp",
                "--pwd", "/workspace",
                str(image_sif),
                "bash", "-c", 'echo "OK - $(nvidia-smi -L 2>/dev/null | wc -l) GPUs"',
            ]
        )
        print("OK" if rc == 0 else "FAILED (see error above)")
    print()
    print("Multi-node test complete.")
    return 0


def main(argv: list[str]) -> int:
    from . import build as build_mod
    from . import cleanup as cleanup_mod
    from . import setup_wizard as wizard_mod
    from . import uninstall as uninstall_mod

    env = os.environ.copy()
    opts, dispatch = parse_args(argv)

    agent_root = resolve_agent_root()
    if agent_root is None:
        return die("cannot locate manifest.yaml (set AGENT_ROOT or run from an instance directory).")

    manifest = load_manifest(agent_root / "manifest.yaml")
    name = manifest.name

    # Dispatch flags: --setup/--build/--clean/--uninstall re-dispatch to the
    # matching subcommand (preserving the old launcher's UX).
    if dispatch is not None:
        if dispatch.action == "setup":
            return wizard_mod.main(dispatch.args, agent_root=agent_root)
        if dispatch.action == "build":
            cfg = effective_config(name, manifest, env)
            if opts.runtime_override:
                cfg["AGENTIC_CONTAINER_RUNTIME"] = opts.runtime_override
            cfg["AGENTIC_CONTAINER_RUNTIME"] = oci.effective_runtime(cfg, env)
            return build_mod.main([f"--{cfg['AGENTIC_CONTAINER_RUNTIME']}"], agent_root=agent_root)
        if dispatch.action == "clean":
            return cleanup_mod.main(dispatch.args, agent_root=agent_root)
        if dispatch.action == "uninstall":
            return uninstall_mod.main(dispatch.args, agent_root=agent_root)

    # Effective config: file + env + CLI overrides.
    cfg = effective_config(name, manifest, env)
    if opts.runtime_override:
        cfg["AGENTIC_CONTAINER_RUNTIME"] = opts.runtime_override
    if opts.tool_override:
        cfg["AGENTIC_CLI_TOOL"] = opts.tool_override
    cfg["AGENTIC_CONTAINER_RUNTIME"] = oci.effective_runtime(cfg, env)
    tool = cfg["AGENTIC_CLI_TOOL"]
    tool_display = TOOL_DISPLAY.get(tool, "OpenCode")
    cfg["AGENTIC_API_KEY_ENV"] = cfg.get("AGENTIC_API_KEY_ENV") or oci.api_key_env_for(tool)

    # Model subscription: one provider+model as the default everywhere
    # (main session and subagents). Explicit config/flags still win.
    sub_label = ""
    sub_spec = cfg.get("AGENTIC_MODEL_SUBSCRIPTION", "")
    if sub_spec:
        parsed = subscription.parse(sub_spec)
        if parsed is None:
            warn(
                f"malformed AGENTIC_MODEL_SUBSCRIPTION {sub_spec!r} "
                "(expected '<provider>:<model>', e.g. 'zai:glm-5.3') -- ignoring"
            )
        elif subscription.provider(parsed[0]) is None:
            known = ", ".join(sorted(subscription.SUBSCRIPTION_PROVIDERS))
            warn(f"unknown subscription provider {parsed[0]!r} (known: {known}) -- ignoring")
        else:
            subscription.apply_to_config(cfg)
            sub_label = f"{subscription.provider(parsed[0])['label']} -- {parsed[1]}"
            if not subscription.api_key(parsed[0], env):
                key_envs = " or ".join(subscription.api_key_envs(parsed[0]))
                warn(f"subscription {parsed[0]}: no API key found -- export {key_envs} before launching")

    workspace = validate_workspace(opts.workspace_dir or "")
    opts.workspace_dir = str(workspace)

    mn = validate_multi_node(opts, cfg, env)
    if opts.multi_node and mn is None:
        opts.multi_node = False

    dirs = setup_storage(cfg, name, tool)
    if cfg["AGENTIC_CONTAINER_RUNTIME"] == "apptainer":
        dirs["apptainer_tmp"] = dirs["state_root"] / "apptainer_tmp"
        dirs["apptainer_tmp"].mkdir(mode=0o700, parents=True, exist_ok=True)
        cache = dirs["state_root"] / "apptainer_cache"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        env["APPTAINER_CACHEDIR"] = env.get("APPTAINER_CACHEDIR", str(cache))

    slurm_mounts, slurm_env = slurm.setup(dirs["state_root"], env)

    setup_instruction_file(manifest, workspace)
    setup_style_file(manifest, workspace)
    setup_workspace_scripts(manifest, workspace)

    tool_args = translate_tool_args(opts, tool, tool_display, cfg)

    extra_binds, binds_debug = setup_optional_binds(cfg, workspace, dirs["state_root"])

    # Scratch space inside the workspace (agents must not use /tmp).
    (workspace / ".tmp").mkdir(parents=True, exist_ok=True)

    print_debug_launch(opts, cfg, dirs, binds_debug, tool_args)
    setup_tool_config(cfg, env, manifest, dirs)
    if tool in ("pi", "codex"):
        skills.install_project_skills(workspace, agent_root, manifest.commands)

    # Claude Code gets a CLAUDE.md that imports the shared AGENTS.md; the
    # launcher-managed agent files are then offered for gitignoring.
    setup_claude_memory(manifest, workspace, tool)
    ensure_agent_files_ignored(workspace, managed_agent_files(manifest, workspace, tool))

    # Banner
    banner(manifest.description)
    print()
    print(f"Instance:       {name}")
    if sub_label:
        print(f"Subscription:   {sub_label}")
    print(f"Workspace:      {opts.workspace_dir}")
    print("Sandboxed:      Only /workspace is accessible")
    print(f"UV Cache:       {dirs['uv_cache']}")
    if opts.multi_node and mn:
        print(f"Slurm Job:      {env['SLURM_JOB_ID']}")
        print(f"Nodes:          {mn['num_nodes']} ({mn['nodelist']})")
        print(f"Head node:      {mn['head_node']}")
        print(f"GPUs/node:      {mn['gpus_per_node']}")
        print(f"Dispatch dir:   {dirs['state_root'] / ('dispatch-' + env['SLURM_JOB_ID'])}")
    print()
    print(f"Starting {tool_display}...")
    print("================================================================")
    print()

    mode = "test" if opts.test else "run"

    if opts.multi_node and mn:
        if opts.test:
            return test_multi_node(mn, cfg, manifest, dirs, workspace)
        mn_binds, mn_env, dispatch_dir, _name = setup_multi_node(mn, cfg, env, manifest, dirs, workspace)
        dispatcher = start_dispatcher(dispatch_dir)
        if dispatcher is None:
            return 1
        try:
            rc = _do_launch(
                mode, cfg, env, manifest, opts, dirs, extra_binds, slurm_mounts, slurm_env,
                mn_binds, mn_env, tool_args,
            )
        finally:
            cleanup_multi_node(dispatcher, dispatch_dir)
        return rc

    return _do_launch(
        mode, cfg, env, manifest, opts, dirs, extra_binds, slurm_mounts, slurm_env, [], [], tool_args
    )


def _do_launch(
    mode, cfg, env, manifest, opts, dirs, extra_binds, slurm_mounts, slurm_env, mn_binds, mn_env, tool_args
) -> int:
    if cfg["AGENTIC_CONTAINER_RUNTIME"] == "apptainer":
        return launch_apptainer(mode, cfg, env, manifest, opts, dirs, extra_binds, slurm_mounts, slurm_env, mn_binds, mn_env, tool_args)
    return launch_oci_runtime(mode, cfg, env, manifest, opts, dirs, extra_binds, slurm_mounts, slurm_env, tool_args)
