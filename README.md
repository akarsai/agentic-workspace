# agentic-workspace

> An **AI worker** you can hand a project to: it opens your files in a safe,
> sandboxed environment, and works on the task you describe.

Every agent runs in its own disposable container that only ever sees your
project folder. One repo acts as a shared "garage" that builds and houses
your personal AI helpers, each with its own personality, training manual,
and toolbox.

> **Note for AI agents:** the project instructions (`AGENTS.md`) are written
> into a workspace at launch. A fresh clone does not contain them.

## Features

- **Sandboxed by default.** Each agent runs in a disposable container
  that only ever sees your project folder.
- **One repo, many agents.** A shared garage for your AI workers: `agre`
  the researcher, `bolt` the web builder, `lera` the teacher, or agents you
  create yourself.
- **Your choice of engine.** Pi (default), OpenCode, Claude Code, and
  Codex CLI all ship in the base image. Switch per launch or per agent.
- **Laptop to HPC.** Docker at your desk, Apptainer + Slurm + GPUs on a
  Linux cluster, with multi-node dispatch.
- **Subagents.** Inside a Pi session, the agent delegates work to extra AI
  workers with their own context windows.
- **Make your own agent in one command.** Scaffold it from a template,
  shape its personality, commit the folder.

## How it works

An agent (an *instance*) is just a folder in this repo: a manifest, an
instructions file, a Dockerfile, and some slash-commands. A tiny launcher
shim (`agre`, `lera`, etc.) hands off to the shared engine, which runs the
container with your project mounted at `/workspace`.

```
agre                                    # launcher shim in ~/.local/bin
 └─ agentic-workspace launch            # shared engine (this repo)
     └─ container                       # instance image, your project at /workspace
```

The repo *is* the install: no copies, no snapshots.

## Quick start

**Requirements**

- **uv**: installs and manages the project's Python (≥ 3.10), so it is the
  only host requirement besides the container runtime (the launcher shims
  are plain POSIX `sh`). Install it with
  `curl -LsSf https://astral.sh/uv/install.sh | sh` if missing.
- **Docker**, or [Apptainer](#hpc-apptainer-slurm--gpus) on a Linux HPC.
- **An API key**: e.g. `DEEPSEEK_API_KEY` (Pi), `ANTHROPIC_API_KEY`
  (Claude Code), or `OPENAI_API_KEY` (Codex CLI). `WANDB_API_KEY` and
  `HF_TOKEN` are also forwarded for experiment tracking.

**Install**

```bash
git clone git@github.com:akarsai/agentic-workspace.git
cd agentic-workspace
./agentic-workspace install   # asks which agents you want, creates their launchers
```

If anything is missing (no Docker/Apptainer, no API key), the installer and
the agent tell you exactly what it is.

<details>
<summary>The installer didn't build the container images</summary>

```bash
./agentic-workspace build
```

</details>

<details>
<summary>Linux HPC without Docker (Apptainer)</summary>

```bash
./agentic-workspace install --apptainer   # records Apptainer as the runtime + builds .sif images
./agentic-workspace build --apptainer     # (or rebuild later)
```

On clusters where Apptainer is only available through environment modules,
both commands run `module load apptainer` once and retry if the first
invocation fails.

</details>

<details>
<summary>Which agents get built, and what the installer does</summary>

`install` records your selection in a clone-local `.agentic-instances` file
(gitignored, never committed). `build` and `update` default to only those
instances. Name one explicitly (e.g. `./agentic-workspace build agre`) to
build another. If the marker is missing (install never ran), they warn and
build nothing.

The installer creates launchers in `~/.local/bin/` (so `agre` and friends
work as commands) and writes a small config file per agent. The repo *is* the
install: no copies, no snapshots.

</details>

## The cast of agents

| Agent | Who it is | What it's good at |
|-------|-----------|-------------------|
| **`agre`** | the **Researcher** | math, machine learning, GPU experiments, reading papers, running long experiments, writing up results |
| **`bolt`** | the **Web app builder** | small full-stack apps in TypeScript: React/Next.js frontends, Node backends, Tailwind styling, Vitest + Playwright tests, conventional commits |
| **`worka`** | the **Workspace services agent** | improving the agentic-workspace repo itself |
| **`lera`** | the **Teacher** | university math courses: course profiles distilled from the textbook, exercise sheets, solutions and exams in the instructor's own style |
| **`<name>`** | your future agent | create more with one command (see [Making your own agent](#making-your-own-agent)) |

## Using an agent

```bash
cd ~/my-project
agre
```

The agent can only see and touch that folder (mounted as `/workspace`). The
rest of your machine stays out of reach.

**Launch flags**

| Flag | What it does |
|------|--------------|
| `--setup` | Re-run the config wizard / change settings (engine, model, keys) |
| `--test` | Validate the sandbox is healthy |
| `--tool <pi\|opencode\|claude\|codex>` | Pick the CLI engine for this launch (see [Choosing the AI engine](#choosing-the-ai-engine)) |
| `--pi` / `--codex` / `--opencode` / `--claude` | Shorthand for `--tool <pi\|codex\|opencode\|claude>` |
| `--yolo` | Skip every permission prompt (engine mapping below) |
| `--apptainer` | Use the Apptainer runtime (Linux HPC without Docker) |
| `--multi-node` | Dispatch work to the other nodes of a multi-node Slurm allocation (see [HPC](#hpc-apptainer-slurm--gpus)) |
| `--resume [ID]` / `--continue` | Resume a previous session |
| `--model MODEL` | Override the default model |

**`--yolo`** maps to each engine's native skip-permissions flag:

| Engine | `--yolo` maps to |
|--------|------------------|
| Pi | none: Pi has no permission system, all tools are enabled by default |
| Claude Code | `--dangerously-skip-permissions` |
| Codex CLI | `--dangerously-bypass-approvals-and-sandbox` |
| OpenCode | `--auto` |

<details>
<summary>Keeping the tools fresh: how <code>update</code> works</summary>

```bash
./agentic-workspace update              # pull, refresh tool pins, rebuild
./agentic-workspace update --apptainer  # same, with Apptainer on a Linux HPC
```

Rebuilding alone never moves a tool inside an image: Docker freezes a `RUN`
layer until its command text changes. `update` therefore first refreshes
`blueprint/container/versions.json` (clone-local, gitignored) from upstream:

- npm registry: claude code, pi, opencode, codex and pnpm
- GitHub releases: gh, yq, typst, uv, bun and just
- Node.js index: node

Every build consumes those pins as build args. A changed pin is the only
thing that busts a tool's layer cache, so `update` picks up new releases
without paying for a full rebuild.

Details worth knowing:

- a failed lookup (offline, rate-limited) keeps the previous pin and warns
  instead of failing the update. `--no-tools` skips the refresh entirely.
- the sandbox runs with the CLI self-updaters off
  (`DISABLE_AUTOUPDATER=1` for claude code, `OPENCODE_DISABLE_AUTOUPDATE=1`
  for opencode): the tools are pinned by the image and `/opt/node` is not
  writable by the runtime user, so an in-place self-update could only
  fail. An `update` + rebuild is how they move.
- with no `versions.json` the Dockerfiles resolve `latest` at build time
  (exactly the old behaviour), so fresh clones build fine without it.
- `update` builds with `--pull`, so the `ubuntu:26.04` base image refreshes
  too (plain `build` never pulls and stays offline-friendly).
- the first `update` after upgrading to pin-based builds rebuilds every tool
  layer once (the recipe text changed). After that, only changed pins.
- Apptainer has no layer cache, so every build resolves pins fresh. The
  fingerprint covers `versions.json`, so a changed pin marks a stale SIF.

`update` and `build` both **rebuild the base image** every time. Docker
reuses cached layers, so an up-to-date base costs only a few seconds.

Apptainer re-runs the whole recipe: an `--force` flag alone only replaces
the output file, so the build commands rebuild from scratch instead of
serving a stale SIF. Without the rebuild, a stale `agentic-blueprint:base`
would keep serving old code to every instance.

Manual base rebuild: `python3 blueprint/container/build_base.py`.

</details>

## The `agentic-workspace` command

Everything is one Python CLI managed with [uv](https://docs.astral.sh/uv/).
`pyyaml` is the only dependency:

```bash
./agentic-workspace launch [OPTS] [DIR]   # what the instance launchers (agre, ...) call
./agentic-workspace install [OPTS]        # one-time interactive installer
./agentic-workspace build [OPTS]          # base + instance images
./agentic-workspace update [OPTS]         # pull + refresh tool pins + rebuild
./agentic-workspace scaffold NAME [OPTS]  # create a new instance
./agentic-workspace setup [KEY=VALUE...]  # per-instance config wizard
./agentic-workspace clean [OPTS]          # remove launcher-managed local state
./agentic-workspace uninstall [OPTS]      # remove an installed instance
```

The first run auto-syncs the project (`uv run`). Afterwards you can also
call `.venv/bin/agentic-workspace` directly.

Each agent (`agre`, `worka`, etc.) is a tiny POSIX-sh launcher that sets
`AGENT_ROOT` and hands off to `agentic-workspace launch`. The shims are
shell only because they must run before any runtime exists. Everything
else is Python.

Configuration lives in `~/.config/<name>/config.py` (a Python module).
Legacy `config.sh` files from older versions are migrated automatically on
first use.

## Choosing the AI engine

All four engines ship in the base image, so there is nothing extra to
build: **Pi** (default), **OpenCode**, **Claude Code**, **Codex CLI**.

**Per launch** (one-off, no setup):

```bash
agre --tool pi          # or --tool opencode / claude / codex
agre --pi               # shorthands: --pi, --codex, --opencode, --claude
```

**Per agent** (persistent): pick any one

- the config wizard: `agre --setup`, then choose the tool at the *CLI tool*
  prompt.
- the config file: `AGENTIC_CLI_TOOL = "opencode"` (or `"claude"`, `"codex"`)
  in `~/.config/agre/config.py`.
- the manifest: `tool: opencode` (or `tool: claude`, `tool: codex`) in
  `instances/<name>/manifest.yaml`, or
  `agentic-workspace scaffold myagent --tool codex` when creating an agent.

The change takes effect on the next launch. `agre --test` reports which
engine is currently the default.

### Pi

- **Auth**: prefers **DeepSeek** by default: export `DEEPSEEK_API_KEY`
  (the harness also passes `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `GOOGLE_API_KEY`, `GEMINI_API_KEY` if you switch providers). Inside a
  session, `/login` or `/model` picks a different provider/model.
- **Slash commands become skills**: the agent's `commands/` (like `/retro`,
  `/subagent`) are installed as [Agent Skills](https://agentskills.io) under
  `.agents/skills/` in your project, so you invoke them as `/skill:retro`,
  etc. The first time Pi meets a project with skills it asks you to
  **trust** the project (that's expected).
- **Sessions**: `agre --tool pi --resume` opens the session picker.
  `--resume <id>` continues a specific one. `--continue` resumes the most
  recent. Sessions live under `~/.pi`.
- **Instructions**: Pi auto-loads the same `AGENTS.md` the agents install
  into your project, so the personality and rules are unchanged.
- **Plan mode**: the `pi-plan-modus` extension ships with every pi
  instance. `/plan` (or `--plan`) starts a read-only exploration session:
  write tools and destructive bash commands are blocked until you toggle it
  off.
- **Notifications**: the `pi-notify` extension emits a native desktop
  notification when the agent finishes a turn (Ghostty, iTerm2, WezTerm,
  Kitty, tmux passthrough, or Windows Terminal). Set `PI_NOTIFY_SOUND_CMD`
  to attach a custom sound hook.
- **Subagents**: see [Subagents (Pi only)](#subagents-pi-only).

### OpenCode

- **Auth**: works with many providers. The harness passes through the
  standard API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`).
- **Sessions**: config and history live under `~/.config/opencode` and
  `~/.local/share/opencode`.
- **Slash commands**: the agent's `commands/` are installed under
  `~/.config/opencode/commands`.
- **Approvals**: OpenCode has its own permission system. `--yolo` maps to
  `--auto` (auto-approve everything that isn't explicitly denied).

### Claude Code

- **Auth**: authenticates via **Anthropic** by default: export
  `ANTHROPIC_API_KEY` (the harness also passes the other standard keys).
- **Sessions**: `agre --tool claude --resume <id>` (or `--continue`) resumes
  a conversation. Sessions live under `~/.claude`.
- **Slash commands**: the agent's `commands/` are installed under
  `~/.claude/commands/`, so `/retro`, `/subagent`, etc. are available
  natively.
- **Instructions**: launch creates a `CLAUDE.md` in the project containing
  just `@AGENTS.md`, so Claude Code auto-loads the same instructions every
  other tool reads (one copy, no drift). Running
  `agentic-workspace scaffold ... --tool claude` instead sets the
  instruction target itself to `CLAUDE.md`. An existing `CLAUDE.md` is
  never overwritten.
- **Approvals**: prompts before running commands by default. Pass `--yolo`
  (or `--dangerously-skip-permissions`) to bypass every permission check.

### Codex CLI

- **Auth**: authenticates via **OpenAI** by default (ChatGPT login or
  `OPENAI_API_KEY`). The harness also passes the other standard keys.
- **Sessions**: `agre --tool codex --resume` opens the session picker.
  `--resume <id>` continues a specific one. `--continue` resumes the most
  recent (mapped to `codex resume --last`). Sessions live under `~/.codex`.
- **Slash commands become skills**: like Pi, Codex auto-discovers Agent
  Skills under `.agents/skills/` in your project.
- **Instructions**: Codex auto-loads `AGENTS.md`, the same file Pi and
  OpenCode use.
- **Approvals**: Codex has its own approval + sandbox system on top of the
  container. By default it prompts before running commands. `--yolo` (or
  `--dangerously-skip-permissions`) bypasses it. Both map to Codex's
  `--dangerously-bypass-approvals-and-sandbox`.

## Making your own agent

```bash
./agentic-workspace scaffold myagent --template research   # or web, or minimal
./agentic-workspace install --name myagent
./agentic-workspace build myagent
myagent ~/my-project
```

Each instance is a folder containing the pieces that *make it that agent*:

| File / folder | What it is |
|---------------|------------|
| `manifest.yaml` | The agent's ID card: name, image, CLI tool, commands |
| `INSTRUCTIONS.md` | The agent's personality: the rules and habits it lives by (synced into the project at launch) |
| `commands/` | The slash-command definitions (`/plan`, `/retro`, etc.) |
| `container/Dockerfile` | The extra tools that agent needs on top of the base image |
| `STYLE.md` | Optional default writing-style profile, copied into each project at launch (not overwriting an existing one) |
| `<name>` | A tiny launcher that hands off to the shared engine |

Edit `instances/myagent/INSTRUCTIONS.md` to shape its personality, then
commit the new folder to the repo.

**Templates** live in `blueprint/template/`: `research`, `web`, and
`minimal`. Each uses placeholders (`__NAME__`, `__TOOL__`,
`__DESCRIPTION__`) that `agentic-workspace scaffold` fills in. To create
your own: copy an existing one to `blueprint/template/<name>/`, adapt it,
then run `agentic-workspace scaffold myagent --template <name>`.

<details>
<summary>Instruction sync, Claude Code memory, and agent files vs. git</summary>

**Instruction sync at launch:** when an agent starts, its
`INSTRUCTIONS.md` is compared with the target file in your project
(`AGENTS.md`, or `CLAUDE.md` for Claude Code agents). A missing file is
written. An identical one is left alone.

If the two differ, you get a warning, a colorful diff, and a prompt,
defaulting to **no** when your project file is newer (it may hold edits
from a previous session) and **yes** when the instance's instructions are
newer.

**Claude Code memory:** when an instance runs with claude as the tool and
the instruction target is `AGENTS.md`, launch creates a one-line
`CLAUDE.md` containing `@AGENTS.md` (Claude Code's import syntax), so there
is a single copy of the instructions to keep current. An existing
`CLAUDE.md` is never touched.

**Agent files & git:** the launcher-managed agent files (the instruction
sync target, `CLAUDE.md`, `.agents/`, `.tmp/`, and `STYLE.md` / `scripts/`
when the instance ships them) are working state for the sandboxed agent,
re-rendered on every launch, not project data.

If the workspace is inside
a git repo, launch offers to append them to the repo-root `.gitignore`
together with the standard sandbox bycatch (`.venv/`, `__pycache__/`,
`*.egg-info/`, `dist/`, `.pytest_cache/`, `.ruff_cache/`, `*.lock`,
`.claude/`, `*.pdf`), so a fresh workspace repo is solid from the get-go
instead of after the first accidental `git add .`.

Every candidate line is first put to `git check-ignore`, so a rule that
already covers it counts even when it is spelled differently (`*.pyc` for
`*.py[cod]`, `.venv` for `.venv/`, an ignored parent directory for
everything under it).

Only what is genuinely uncovered is offered, listed
in full before the prompt so you can see exactly what lands in the file.
When nothing is left there is no prompt at all. Declining just warns.
Nothing is enforced.

</details>

## Subagents (Pi only)

Inside a Pi session, the agent can spawn **subagents**: extra AI workers
that run as separate processes with their own context window, share the
same workspace, and keep going until done (no timeouts, no pausing to ask
questions).

- **Tools**: `subagent` (delegate and wait), `subagent_spawn` (background),
  `subagent_list` / `subagent_result` (check on them), `subagent_stop` /
  `subagent_cleanup` (manage them).
- **Built-in roles**: `scout` (read-only recon), `worker` (implement/run),
  `reviewer` (read-only code review), `stylist` (writes in the user's voice
  from STYLE.md) and `style-reviewer` (checks drafts against the style
  rules), plus the GRAFT-ATHENA team (`formalizer`, `proposer`, `ranker`,
  `auditor`, `advisor`) for the formalize → propose → rank → audit → advise
  pipeline.
- **Custom roles**: add markdown files in `.pi/agents/` inside the project
  (frontmatter: `name`, `description`, `tools`, `model`, plus a
  system-prompt body).
- **Full transparency**: every subagent has a **mailbox** under
  `.tmp/subagents/<id>/` with its task, live event log, and final result.
- **Only you commit**: subagents write code and report back. They never
  touch git. The parent agent commits.
- **Lifetime**: ending the session kills its subagents (they are child
  processes). Cancelling the current turn does *not* interrupt a running
  background subagent.
- **Model**: subagents default to a fast model:
  `AGENTIC_SUBAGENT_MODEL` (default `deepseek-v4-flash`). Also tunable:
  `AGENTIC_SUBAGENT_TOOLS`, `AGENTIC_SUBAGENT_THINKING`.

### Model subscription: one model everywhere

With a flat-rate **model subscription** (e.g. the z.ai GLM coding plan), one
setting makes it the default for *everything*: the main session and every
subagent.

```bash
lera --setup                                # wizard: "Model Subscription" step
lera --setup AGENTIC_MODEL_SUBSCRIPTION=zai:glm-5.3   # or set it directly
export ZAI_API_KEY=...                      # the key lives in your env, never in the config
lera
```

With `AGENTIC_MODEL_SUBSCRIPTION = "zai:glm-5.3"` set, the launcher:

- fills `AGENTIC_DEFAULT_MODEL` and `AGENTIC_SUBAGENT_MODEL` with
  `zai/glm-5.3` (so `--model` is passed to the CLI and every spawned
  subagent uses the same model).
- **Pi**: registers the provider in `models.json` inside the instance's
  config store (OpenAI-compatible coding endpoint, `thinkingFormat: zai`,
  key read from `$ZAI_API_KEY`).
- **Claude Code**: routes through the provider's Anthropic-compatible
  endpoint (`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`).
- forwards the provider's API key (`ZAI_API_KEY`, also `Z_AI_API_KEY`)
  into the sandbox like the other passthrough keys.

Explicit choices still win: `AGENTIC_DEFAULT_MODEL` /
`AGENTIC_SUBAGENT_MODEL` in the config, the `--model` launch flag, and
per-call subagent `model:` overrides all take precedence over the
subscription.

Known providers: `zai` (z.ai GLM Coding Plan). The registry lives in
`src/agentic_workspace/subscription.py`. Adding another subscription
provider is one dict entry.

## HPC: Apptainer, Slurm & GPUs

### GPU usage

GPUs are passed straight into the agent's container, so the agent can run
experiments and check availability with `nvidia-smi`:

- **Docker**: `--gpus all` when the host has an NVIDIA runtime
  (`AGENTIC_DOCKER_GPUS=all|auto|none`).
- **Apptainer**: `--nv` under the same conditions.

You need a GPU-capable host: typically an interactive Slurm allocation
(see below). A plain login node usually has none.

### Slurm passthrough (submitting & monitoring)

On a Slurm login node (`sbatch`/`squeue` already on `PATH`), the launcher
binds the **host's own Slurm client** into the container: `sbatch`,
`squeue`, `scontrol`, `sacct`, `scancel`, `sinfo`, `srun`, and friends,
with their shared libraries, plugins, config, and the munge socket. The
agent submits and monitors jobs from inside the sandbox with the exact
Slurm version the cluster runs.

`SBATCH_ACCOUNT`, `SALLOC_ACCOUNT`, `SRUN_ACCOUNT` and `SLURM_CLUSTERS`
from the launch shell are forwarded too, so `export SBATCH_ACCOUNT=<account>`
once on the login node covers clusters that reject unaccounted jobs.

> **Caveat:** jobs referencing `/workspace` paths (submitit/Hydra generate
> them by default inside the container) will **not** run on compute nodes:
> `/workspace` only exists inside the container.

<details>
<summary>How the Slurm client gets into the sandbox</summary>

`slurm.conf` is located via `$SLURM_CONF`, `/etc`, or the clients'
compiled-in path (site installs like `/nopt/slurm/etc`). A **client copy
with `SlurmUser` rewritten to the sandbox user** is generated on every
launch: the host value has no passwd entry inside the image, which
otherwise breaks every client command.

</details>

Instances ship job tooling, synced into every workspace at launch:
`scripts/job-header.sh` (repairs the exported container env and cds to the
node-visible host path from `$AGENTIC_WORKSPACE_HOST`),
`scripts/submit.sh` (sbatch with the right `--chdir`), and a smoke job.
For multi-node allocations, use the dispatcher below.

### Multi-node dispatch (Slurm + Apptainer)

```bash
salloc --nodes=2 --gres=gpu:2 --time=04:00:00   # or your cluster's allocator
agre --apptainer --multi-node ~/my-project
agre --apptainer --multi-node --test           # validate the setup first
```

`--multi-node` starts a host-side dispatcher that re-enters the same
Apptainer image on remote nodes with the workspace bind-mounted at
`/workspace`, so `/workspace` paths resolve identically on compute nodes.
Inside the session, the agent dispatches independent experiments with
`remote-run`:

- `remote-run --nodes`: list the allocated nodes
- `remote-run <node> --bg -- <cmd>`: run a background job on a remote node
- `remote-run --status` / `--logs <id>` / `--tail <id>` / `--kill <id>`:
  manage jobs

Requires Apptainer and an active multi-node Slurm allocation. Single-node
workflows are unaffected (a 1-node allocation just warns and runs without
dispatch).
