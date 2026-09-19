# agre: the Research Agent

agre launches an **AI research agent** inside a sandboxed Docker container and
lets it work autonomously on your projects: filesystem isolation, GPU
passthrough, strict research instructions, and a persistent memory that
carries insights across sessions.

agre runs on [Pi](https://pi.dev) by default, and can also run
[OpenCode](https://opencode.ai), [Claude Code](https://claude.com/claude-code),
or [Codex CLI](https://github.com/openai/codex) (see the main README's
"Choosing an engine" section).

## agre is an instance of the agentic-workspace monorepo

agre does **not** reinvent the sandbox. It is one *instance* of
[`agentic-workspace`](https://github.com/akarsai/agentic-workspace), the
monorepo that defines the Docker environment and all interactions once (in
`blueprint/`).

Everything that makes agre *agre* is its **research personality**:

| Piece | What it is |
|-------|------------|
| `INSTRUCTIONS.md` | the research agent's rules: core principles, modules for math / GPU work, reporting discipline |
| `commands/` | slash commands: `/retro`, `/update_base`, `/subagent` |
| `manifest.yaml` | identity: image `agre:latest`, tool, commands |
| `container/Dockerfile` | research toolset layered on the blueprint's base image |

Everything else: the launcher, container base image, entrypoint, security,
and setup/cleanup tooling: is the **shared framework** in
the monorepo's `blueprint/`. When the framework improves, agre benefits
automatically (one repo, no re-sync).

Want your own flavor? The monorepo's `agentic-workspace scaffold` generates
new instances in seconds: research, minimal, or anything you write.

## Prerequisites

- **Docker** (or **Apptainer** on a Linux HPC)
- An API key for your model provider (e.g. `DEEPSEEK_API_KEY` for Pi,
  `OPENAI_API_KEY` for Codex CLI)
- GPU drivers on the host if you want GPU passthrough
- On a Slurm cluster: `sbatch`/`squeue` on `PATH` when launching `agre`, so
  the launcher can pass the host's Slurm client into the sandbox

## Quick Start

```bash
git clone git@github.com:akarsai/agentic-workspace.git
cd agentic-workspace

# One-time interactive installer (symlinks launchers + writes configs)
./agentic-workspace install --name agre

# Build the container (builds the blueprint base image first if missing)
agre --build

# Launch in your project directory
agre ~/my-project
```

State lives under `~/.cache/agre` by default.

On a Linux HPC without Docker:

```bash
./agentic-workspace install --name agre --apptainer   # records Apptainer + builds the .sif
agre --apptainer ~/my-project
```

## Configuration

Run `agre --settings` to create (or update) the config file at
`${XDG_CONFIG_HOME:-$HOME/.config}/agre/config.py` (a Python module. Legacy
`config.sh` files are migrated automatically). The wizard configures:

- **State/cache directory** (`AGENTIC_STATE_ROOT`): caches and tool state. Default `~/.cache/agre`
- **Extra environment variables** (`AGENTIC_EXTRA_ENV`): `KEY=VALUE` pairs forwarded into the container (e.g. `HF_TOKEN=...|WANDB_API_KEY=...`)
- **Network proxy**. HTTP/HTTPS proxy for inside the container
- **Extra bind directories**: additional host paths mounted into the sandbox (they appear under `/workspace/.mount/<name>`)

Re-run `agre --settings` any time, or set individual values with `agre --settings KEY=VALUE`.

`WANDB_API_KEY` and `HF_TOKEN` are also forwarded automatically when they are
exported in the host environment, so wandb/Hugging Face work inside the sandbox
without adding them to `AGENTIC_EXTRA_ENV`.

## Usage

```bash
agre                       # sandbox the current directory
agre ~/my-project          # sandbox a specific project
agre --test                # validate the sandbox environment
```

Inside a session:

- **`/retro`**: end-of-session reflection. Writes improvement notes to `REVISION.md`.
- **`/plan`**: toggle read-only plan mode (from the bundled `pi-plan-modus`
  extension) to explore without modifying files.
- **Notifications**: the bundled `pi-notify` extension emits a native desktop
  notification when the agent finishes and waits for input (Ghostty, iTerm2,
  WezTerm, Kitty, tmux passthrough, or Windows Terminal). Set
  `PI_NOTIFY_SOUND_CMD` to attach a custom sound hook.
- **Subagents**: delegate to child agents: `subagent` (synchronous) and
  `subagent_spawn` (background), managed with `subagent_list` /
  `subagent_result` / `subagent_stop` / `subagent_cleanup`. Built-in roles:
  `scout`, `worker`, `reviewer`, plus the GRAFT-ATHENA team (`formalizer`,
  `proposer`, `ranker`, `auditor`, `advisor`). Add your own in
  `.pi/agents/*.md`. Only the parent commits. Subagents share the workspace and
  run to completion.

### Slurm on HPC

On a Linux host with Slurm client commands on `PATH` (a login node), the
launcher automatically binds the **host's own Slurm client** into the sandbox:
`sbatch`, `squeue`, `scontrol`, `sacct`, `scancel`, `sinfo`, `srun`, and their
shared libraries, config, and munge socket. Agre can therefore submit and
monitor jobs from inside the container (e.g. Hydra + submitit) with the exact
Slurm version the cluster runs.

**Multi-node dispatch.** For multi-node Slurm allocations, start agre inside
the allocation with `--multi-node` (Apptainer only):

```bash
salloc --nodes=2 --gres=gpu:2 --time=04:00:00   # allocate 2 nodes × 2 GPUs
agre --apptainer --multi-node ~/my-project
agre --apptainer --multi-node --test          # validate the setup first
```

Inside the session, dispatch independent experiments with `remote-run`
(`remote-run <node> --bg -- <cmd>`, `remote-run --status`, `--logs`, `--tail`,
`--kill`). Remote jobs re-enter the same Apptainer image with `/workspace`
bind-mounted, so workspace paths resolve identically on compute nodes. Raw
`sbatch`/`srun` scripts that reference `/workspace` will fail, since compute
nodes do not mount the login-node-only bind.

## Sandbox security

| Layer | Detail |
|-------|--------|
| Filesystem isolation | the agent can only access `/workspace` |
| Path traversal protection | symlinks resolved. System/credential directories blocked |
| Scratch space | `/workspace/.tmp` is pre-created. Agents never write to `/tmp` |

