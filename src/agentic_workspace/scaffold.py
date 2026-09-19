"""The `scaffold` subcommand: create a new child instance.

Generates an instance directory (manifest.yaml, INSTRUCTIONS.md, commands/,
container/Dockerfile, launcher shim) from a template. The shared framework
lives in the repo's blueprint/; instances do NOT vendor a copy.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from .manifest import load_manifest
from .paths import framework_dir, instances_dir, repo_root
from .util import die, say

TEMPLATE_DIR = framework_dir() / "template"
VALID_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
VALID_TOOLS = ("opencode", "pi", "claude", "codex")


def show_help() -> None:
    print(
        "Usage: agentic-workspace scaffold [name] [OPTIONS]\n"
        "       agentic-workspace scaffold --list\n"
        "       (run bare, the scaffold asks for name, template, and tool)\n"
        "\n"
        "Options:\n"
        "  --template NAME   research | web | minimal   (default: minimal)\n"
        "  --dir PATH        parent directory for the new instance (default: instances/)\n"
        "  --tool NAME       opencode | pi | claude | codex   (default: pi)\n"
        "  --description TXT description for the manifest\n"
        "  --no-git          skip git init + initial commit\n"
        "  --help            show this help"
    )


def cmd_list() -> int:
    print("Available templates:")
    for t in sorted(TEMPLATE_DIR.iterdir()):
        if not t.is_dir():
            continue
        manifest = t / "manifest.yaml"
        desc = ""
        if manifest.is_file():
            desc = load_manifest(manifest).description
        print(f"  {t.name:<10} {desc}")
    return 0


def ask_template(default: str) -> str:
    """Interactive template menu. Enter keeps the default. EOF-safe."""
    templates = sorted(t.name for t in TEMPLATE_DIR.iterdir() if t.is_dir())
    if not templates:
        return default
    print("Template:")
    for i, tname in enumerate(templates, 1):
        mark = "*" if tname == default else " "
        desc = ""
        mfile = TEMPLATE_DIR / tname / "manifest.yaml"
        if mfile.is_file():
            desc = load_manifest(mfile).description
        print(f" {i:>2}){mark} {tname:<10} {desc}")
    while True:
        try:
            raw = input(f"Number [1-{len(templates)}, Enter = {default}]: ").strip()
        except EOFError:
            return default
        if raw == "":
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(templates):
            return templates[int(raw) - 1]
        print(f"Enter a number between 1 and {len(templates)}.")


def launcher_shim(name: str) -> str:
    return (
        "#!/bin/sh\n"
        f"# {name}: launch the {name} agent in a sandboxed container.\n"
        "#\n"
        "# Thin POSIX-sh launcher: sets AGENT_ROOT (this instance's directory) and\n"
        "# hands off to the shared 'agentic-workspace launch' command. Plain sh on\n"
        "# purpose, so only uv is required on the host.\n"
        "SELF=$0\n"
        "case $SELF in\n"
        "    */*) ;;\n"
        "    *) SELF=$(command -v -- \"$SELF\" 2>/dev/null || echo \"$SELF\") ;;\n"
        "esac\n"
        "while [ -L \"$SELF\" ]; do\n"
        "    LINK=$(readlink \"$SELF\") || break\n"
        "    case $LINK in\n"
        "        /*) SELF=$LINK ;;\n"
        "        *) SELF=$(dirname \"$SELF\")/$LINK ;;\n"
        "    esac\n"
        "done\n"
        "AGENT_ROOT=$(CDPATH= cd -- \"$(dirname -- \"$SELF\")\" && pwd)\n"
        "export AGENT_ROOT\n"
        "ROOT=$(CDPATH= cd -- \"$AGENT_ROOT/../..\" && pwd)\n"
        "\n"
        "exec \"$ROOT/agentic-workspace\" launch \"$@\"\n"
    )


def main(argv: list[str]) -> int:
    name = ""
    template = "minimal"
    parent_dir = instances_dir()
    tool = "pi"
    description = ""
    no_git = False
    template_given = False
    tool_given = False

    args = list(argv)
    if args and args[0] in ("--help", "-h"):
        show_help()
        return 0
    if args and args[0] == "--list":
        return cmd_list()

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--template":
            if i + 1 >= len(args):
                return die("--template requires a value")
            template = args[i + 1]
            template_given = True
            i += 1
        elif a == "--dir":
            if i + 1 >= len(args):
                return die("--dir requires a value")
            parent_dir = Path(args[i + 1])
            i += 1
        elif a == "--tool":
            if i + 1 >= len(args):
                return die("--tool requires a value")
            tool = args[i + 1]
            tool_given = True
            i += 1
        elif a == "--description":
            if i + 1 >= len(args):
                return die("--description requires a value")
            description = args[i + 1]
            i += 1
        elif a == "--no-git":
            no_git = True
        elif a in ("--help", "-h"):
            show_help()
            return 0
        elif a.startswith("-"):
            return die(f"unknown option: {a}")
        else:
            if not name:
                name = a
            else:
                return die(f"unexpected argument: {a}")
        i += 1

    # Bare scaffold: ask for name, template, and tool (Enter keeps defaults).
    interactive_mode = not name
    if not name:
        try:
            name = input("Instance name (lowercase letters, digits, - or _): ").strip()
        except EOFError:
            name = ""
    if not name:
        die("missing instance name")
        show_help()
        return 1
    if not VALID_NAME.match(name):
        return die(f"invalid name '{name}' (use lowercase letters, digits, - or _)")

    if interactive_mode and not template_given:
        template = ask_template(template)
    if interactive_mode and not tool_given:
        from .setup_wizard import ask_tool

        print()
        tool = ask_tool(tool)

    if not (TEMPLATE_DIR / template).is_dir():
        return die(f"unknown template '{template}'")
    if tool not in VALID_TOOLS:
        return die(f"unknown tool '{tool}' (supported: pi, opencode, claude, codex)")

    child_dir = parent_dir / name
    if child_dir.exists():
        return die(f"destination exists: {child_dir}")

    print(f"Scaffolding instance '{name}' (template: {template}, tool: {tool})")
    tpl = TEMPLATE_DIR / template
    child_dir.mkdir(parents=True)

    # Manifest: fill placeholders.
    if not description:
        m = load_manifest(tpl / "manifest.yaml")
        desc = m.description
        if not desc or desc.startswith("__"):
            desc = f"AI coding agent ({name})"
        description = desc
    manifest_text = (tpl / "manifest.yaml").read_text()
    manifest_text = (
        manifest_text.replace("__NAME__", name)
        .replace("__TOOL__", tool)
        # Quote for YAML: descriptions may contain colons or other chars
        # that break an unquoted scalar (e.g. "Web app builder: ...").
        .replace("__DESCRIPTION__", '"' + description.replace('"', '\\"') + '"')
    )
    (child_dir / "manifest.yaml").write_text(manifest_text)

    # Claude Code auto-loads CLAUDE.md (not AGENTS.md) from the workspace.
    if tool == "claude":
        p = child_dir / "manifest.yaml"
        p.write_text(p.read_text().replace("instruction_target: AGENTS.md", "instruction_target: CLAUDE.md"))

    for f in ("INSTRUCTIONS.md", ".gitignore"):
        src = tpl / f
        if src.is_file():
            shutil.copy2(src, child_dir / f)

    if (tpl / "commands").is_dir():
        # Copy the whole tree: command markdown plus per-command asset
        # subdirs (e.g. clone-writing-style/stylelint.py).
        shutil.copytree(tpl / "commands", child_dir / "commands")

    # Optional per-instance tooling (e.g. Slurm job scripts for the research
    # template); the launcher syncs instance scripts/ into every workspace.
    if (tpl / "scripts").is_dir():
        shutil.copytree(tpl / "scripts", child_dir / "scripts")

    (child_dir / "container").mkdir()
    src_dockerfile = tpl / "container" / "Dockerfile"
    if src_dockerfile.is_file():
        shutil.copy2(src_dockerfile, child_dir / "container" / "Dockerfile")

    # Launcher shim (python; sets AGENT_ROOT and hands off to the launch cmd).
    shim = child_dir / name
    shim.write_text(launcher_shim(name))
    shim.chmod(0o755)

    if not no_git:
        subprocess.run(["git", "-C", str(child_dir), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(child_dir), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(child_dir), "-c", f"user.name={name}", "-c", f"user.email={name}@localhost",
             "commit", "-q", "-m", f"chore: scaffold {name}"],
            check=True,
        )

    print()
    print(f"Created: {child_dir}")
    print()
    print("Next steps:")
    print(f"  1. Register the launcher:  ./agentic-workspace install --name {name}")
    print(f"  2. Build the container:    {name} --build")
    print(f"  3. Launch:                 {name} ~/your-project")
    print()
    if parent_dir.resolve() == instances_dir().resolve():
        print("Commit the new instance directory to the repo so it is part of the monorepo.")
    return 0
