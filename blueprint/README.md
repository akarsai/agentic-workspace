# blueprint: the agentic-workspace framework

**One harness, many agents.**

`blueprint/` is the framework part of the `agentic-workspace` monorepo. It
defines the Docker environment and **every interaction** of a sandboxed
AI-coding-agent harness exactly once (the launcher, the base container image,
filesystem isolation, security, and setup/cleanup tooling) so that individual
*instances* only have to provide their own personality.

Instances live in the monorepo's `instances/` directory and share this
framework. Nothing is vendored or copied per instance.

An instance adds, on top of the shared framework:

- **`manifest.yaml`**: identity (name, docker image, tool, commands),
- **`INSTRUCTIONS.md`**: the instructions the agent lives by (the personality),
- **`commands/`**: the slash commands the agent can run,
- **`container/Dockerfile`**: a thin toolset layer on the blueprint base image,
- **`<name>`**: a thin POSIX-sh launcher shim that sets `AGENT_ROOT` and
  hands off to `agentic-workspace launch` (sh only because it must run before
  any runtime exists).

## Repository layout

```
agentic-workspace        the CLI (root shim -> uv-run console script)
src/agentic_workspace/   the Python package (launch/install/build/update/...)
blueprint/
  container/
    Dockerfile.base      generic base image: ubuntu, node, uv, git, gosu,
                         jq, rg, yq, gh, bun, opencode, pi, claude, codex
    entrypoint.py        user setup + tool exec at session start (PID 1)
    build_base.py        build agentic-blueprint:base (standalone entry)
    build_image.py       build an instance image (standalone entry)
    dockerfile_to_def.py Dockerfile -> Apptainer recipe converter
    extensions/subagent.ts  baked subagent extension (pi instances)
    agents/              default subagent roles: scout, worker, reviewer,
                         formalizer, proposer, ranker, auditor, advisor
  scripts/
    test_sandbox.py      validate the sandbox from inside the container
    remote_run.py        multi-node job dispatch client (inside the container)
    dispatcher.py        multi-node dispatch daemon (host side)
  config/                config example + OpenCode template
  template/              personality templates (research, web, minimal)
instances/<name>/        one folder per agent (manifest + personality + toolset)
tests/test_cli.py        lean smoke-test suite (uv run pytest)
```

## Quick start (build the base image)

You only build the base image once per machine:

```bash
python3 container/build_base.py            # → agentic-blueprint:base
python3 container/build_base.py --apptainer  # → .apptainer/agentic-blueprint-base.sif (no Docker)
```

This image is shared by every instance. Instances build their own thin image
on top of it (via `<instance> --build`).

## Creating an instance

### 1. Scaffold

```bash
./agentic-workspace scaffold my-agent --template research
```

This creates `instances/my-agent/` with a populated manifest, instructions,
commands, a container layer, and a launcher wrapper. The framework is shared:
no copy is made.

### 2. Add your personality

Edit the instance's own files. The framework in `blueprint/` is generic and
shared:

```
instances/my-agent/
├── my-agent                # launcher shim (posix sh; sets AGENT_ROOT)
├── manifest.yaml           # identity — edit freely
├── INSTRUCTIONS.md         # your harness instructions
├── commands/               # your slash commands
└── container/Dockerfile    # your toolset (FROM agentic-blueprint:base)
```

### 3. Install, build, run

```bash
./agentic-workspace install --name my-agent   # symlink ~/.local/bin/my-agent + write config
my-agent --build               # build the instance image
my-agent ~/your-project        # launch
```

### 4. Commit it

The instance is part of the monorepo: commit `instances/my-agent/` to the
repo.

## Managing instances

| Task | Command |
|------|---------|
| List available templates | `agentic-workspace scaffold --list` |
| Create an instance | `agentic-workspace scaffold <name> --template <tpl>` |
| Register the launcher + config | `agentic-workspace install --name <name>` |
| Build the shared base image | `python3 container/build_base.py` |
| Rebuild an instance image | `<name> --build` |
| Build selected images (from install) | `agentic-workspace build` |
| Build with Apptainer (no Docker) | `<name> --apptainer --build` |
| Validate a sandbox | `<name> --test` |
| Update selected images | `agentic-workspace update` |

`build` and `update` (no instance args) operate only on the instances that
`install` recorded in the clone-local `.agentic-instances` marker (gitignored,
never committed). Name instances explicitly to build or update others. If the
marker is missing (install never ran), they warn and do nothing.

## Building with Apptainer (HPC clusters without Docker)

On Linux HPC systems that have Apptainer/Singularity but no Docker daemon, the
same images can be built as SIF files. Apptainer cannot consume a Dockerfile
whose `FROM` references a locally built base image, so the build flow
translates each layer's Dockerfile into an Apptainer recipe
(`dockerfile_to_def.py`) and layers it on a shared base SIF:

```bash
python3 container/build_base.py --apptainer  # → container/.apptainer/agentic-blueprint-base.sif
<name> --apptainer --build                   # → instances/<name>/.apptainer/<name>.sif
./agentic-workspace build --apptainer        # repo root: base + instances chosen by install
<name> --apptainer                           # launch the agent with Apptainer
```

- Apptainer builds and launches are Linux-only and require `apptainer` on
  `PATH`.
- Interactive launches go through the package's PTY wrapper so TUI tools get a
  real PTY.
- If Docker is pinned in the config but absent while Apptainer is present, the
  launcher falls back to Apptainer automatically (with a notice).
- `build`/`update` with no instance args build/update only what `install`
  recorded in the clone-local `.agentic-instances` marker. Name instances
  explicitly to build others.
- On hosts without fakeroot/subuid (Apptainer's root-mapped namespace), the
  generated `%post` runs `apt-get` as root (`APT::Sandbox::User=root`) so
  package installs still work. This is build-time only. The SIF matches a
  Docker build.

### Multi-node dispatch (Slurm + Apptainer)

`<name> --apptainer --multi-node` (inside an active multi-node Slurm
allocation) starts a host-side dispatcher (`scripts/dispatcher.py`) that
re-enters the same SIF on remote nodes with the workspace bind-mounted at
`/workspace`. The agent dispatches jobs from inside the container via
`remote-run` (bind-mounted from `scripts/remote-run`):

```bash
remote-run <node> --bg -- <cmd>     # background job on a remote node
remote-run --status | --logs | --tail | --kill
```

Because every remote job runs inside the container image, `/workspace` paths
resolve identically on compute nodes. Raw `sbatch`/`srun` scripts that
reference `/workspace` do not work, because compute nodes never see the
login-node-only bind mount.
`--multi-node --test` validates the setup before launching.

## The manifest

The manifest is a small flat-YAML file that tells the generic launcher who the
instance is:

```yaml
name: my-agent                  # launcher name, config/state dirs
description: A research agent
image: my-agent:latest          # docker image tag for this instance
tool: pi                      # CLI tool executed inside the container (pi, opencode, claude, or codex)
instruction_template: INSTRUCTIONS.md
instruction_target: AGENTS.md   # file the instructions are synced to at launch
commands: retro, subagent   # slash commands
```

**Instruction sync at launch**: the rendered `instruction_template` is
compared with the target file in the project (`AGENTS.md`, or `CLAUDE.md` for
Claude Code instances): a missing target is written, an identical one is
left alone, and when they differ you get a warning plus a colorful diff and
a prompt, defaulting to *no* if the project file is newer (it may hold
edits from a previous session) and *yes* if the template is newer (the
instance's instructions were updated).

## The launcher

`agentic-workspace launch` is fully generic: every identity concern is read
from the instance's `manifest.yaml` (name, image tag, config path, state root,
command list, instruction target). Security lives here and therefore applies
to **every** instance:

- workspace is validated and system/credential directories are blocked
  (`.ssh`, `.aws`, `.kube`, GPG, cloud configs, `/etc`, `/proc`, etc.),
- the agent can only read/write the project directory,
- SSH keys and git config are mounted read-only,
- `/workspace/.tmp` is pre-created as scratch space: agents are told never to
  write to `/tmp`.

## Subagents (in-session delegation)

Every `pi` instance ships a **subagent extension** baked into the base image
(`container/extensions/subagent.ts`) plus a set of default agent roles
(`container/agents/*.md`: `scout`, `worker`, `reviewer`, and the GRAFT-ATHENA
team `formalizer`, `proposer`, `ranker`, `auditor`, `advisor`). The entrypoint's `setup_pi_subagent` copies the
extension into `~/.pi/agent/extensions/` and the roles into
`~/.pi/agent/agents/` at session start, so Pi auto-discovers them.

Inside a session the agent delegates work to **subagents**: child `pi`
processes with isolated context windows that share the workspace and run to
completion. Tools: `subagent` (synchronous single/parallel), `subagent_spawn`
(fire-and-forget background), and `subagent_list` / `subagent_result` /
`subagent_stop` / `subagent_cleanup` for management.

- Subagents are ordinary child processes: `session_shutdown` kills every
  running one, so ending the session ends its subagents. No per-task timeout.
- Each run gets a **mailbox** under `<workspace>/.tmp/subagents/<id>/`:
  `task.md`, `system-prompt.md`, `stdout.jsonl`, `stderr.log`,
  `session.jsonl`, `status`, `result.md`, `meta.json`.
- Subagents never commit to git. The parent commits.
- Model/tools/thinking are configured with `AGENTIC_SUBAGENT_MODEL`
  (default `deepseek-v4-flash`), `AGENTIC_SUBAGENT_TOOLS`, and
  `AGENTIC_SUBAGENT_THINKING`: passed into the container by the launcher.
- A model subscription (`AGENTIC_MODEL_SUBSCRIPTION`, e.g. `"zai:glm-5.3"`)
  makes one flat-rate provider the default for the main session **and** all
  subagents, and wires the provider into the CLI tool (pi `models.json`,
  Claude Code endpoint env). See the main README's "Model subscription"
  section.
- Custom roles: drop `<project>/.pi/agents/*.md` (or
  `~/.pi/agent/agents/*.md`) with `name`, `description`, `tools`, `model`,
  and a system-prompt body.

## Writing your own template

Templates are just directories under `template/`:

```
template/my-template/
├── manifest.yaml          # with __NAME__ / __TOOL__ / __DESCRIPTION__ placeholders
├── INSTRUCTIONS.md
├── commands/              # *.md slash commands
├── container/Dockerfile   # toolset layer
└── .gitignore
```

Placeholders `__NAME__`, `__TOOL__`, and `__DESCRIPTION__` are substituted by
the scaffold. Then: `agentic-workspace scaffold my-agent --template my-template`.

## FAQ

**Why a monorepo?** Solo, private use: one history, one push, no submodule
pointer bumps, no vendored `framework/` copies. The framework is still
cleanly separated from instances.

**Can two instances share state?** They share the base docker image. Each
instance keeps its own config and state root.

**Does every instance have to use Pi?** No. Instances may use `pi`,
the OpenCode coding agent (`opencode`, https://opencode.ai), Claude Code
(`claude`, https://claude.com/claude-code), or Codex CLI (`codex`,
https://github.com/openai/codex). Set `tool:` in the manifest, pass
`--tool pi|opencode|claude|codex` to the launcher, or set `AGENTIC_CLI_TOOL` in the
instance config. The base image ships all four CLIs. The entrypoint execs
whichever `SANDBOX_TOOL` is injected. Pi keeps its state under `~/.pi`, which
the config-store mount maps to a persistent host directory. Claude Code keeps
its state (sessions, slash commands) under `~/.claude`, and Codex keeps its
state (config, auth, sessions) under `~/.codex`. Pi instances also
ship the `pi-plan-modus` read-only plan mode extension baked into the base
image (use `/plan` or `--plan` to explore without writes) and a subagent
dispatch extension (see [Subagents](#subagents-in-session-delegation)).

