# agentic-workspace

A collection of tools to sandbox CLI coding agents by running them in containers.
Key features are:
- CLI coding agents are installed and run in containers (Docker, Apptainer).
- Multiple container environments can be installed in parallel, each with their own toolset and instructions.

The project is inspired by [The Agentic Researcher](https://github.com/ZIB-IOL/The-Agentic-Researcher).


## Quick start

**Requirements**
- Linux or macOS
- **[uv](https://astral.sh/uv)**
- **Docker** or **Apptainer**
- An API key for the coding agent you plan to use

**Install**

```bash
# clone
git clone git@github.com:akarsai/agentic-workspace.git && cd agentic-workspace # this folder is the install, it should remain on disk

# install
./agentic-workspace install
```

The installer asks which agents you want, creates their launchers in `~/.local/bin`, writes a per-agent config, and builds the container images.
It tells you exactly what is missing when Docker or an API key cannot be found.

**First launch**

```bash
# go to any folder
cd any-folder

# call an instance, e.g. for agre
agre
```

The agent sees exactly that folder, mounted at `/workspace`, and nothing else except the documented mounts (see [Security](#security)).
On first contact the agent asks for confirmation before it writes anything into the project.


## Platforms and runtimes

| Host | Runtime | Notes |
|------|---------|-------|
| Linux desktop/server | Docker (default) or Apptainer | Apptainer needs no daemon and no root |
| Linux HPC login node | Apptainer + Slurm | GPUs via `--nv`, multi-node via `--multi-node` |
| macOS | Docker Desktop | Linux containers |

On clusters where Apptainer is only available as an environment module, the launcher loads the module once and retries.


## Agent CLIs

Four coding agent CLIs ship preinstalled in the base image.
Pick per launch or per agent, there is nothing extra to build.

| CLI | Default auth | Notes |
|-----|--------------|-------|
| **pi** (default) | `DEEPSEEK_API_KEY` | subagents, plan mode, desktop notifications |
| opencode | provider keys | `--yolo` maps to `--auto` |
| claude code | `ANTHROPIC_API_KEY` | instructions synced to `CLAUDE.md` |
| codex | `OPENAI_API_KEY` | ChatGPT login also works |


### Instance commands

Here for the `agre` instance, others the same.

```bash
# pick the CLI engine for this launch
agre --pi
agre --codex
agre --opencode
agre --claude

# interactive settings menu
agre --settings

# validate the sandbox is healthy
agre --test

# resume the most recent session
agre --continue

# skip permission prompts
agre --yolo

# force Apptainer use
agre --apptainer

# dispatch work to the other nodes of a multi-node Slurm allocation
agre --multi-node
```


### agentic-workspace commands

Every command is interactive when run bare and asks for whatever it needs.

```bash
# interactive installer
./agentic-workspace install

# build images
./agentic-workspace build

# pull refresh tool pins + rebuild
./agentic-workspace update

# create a new instance
./agentic-workspace scaffold

# per-instance settings menu
./agentic-workspace settings

# remove launcher-managed local state
./agentic-workspace clean

# remove an installed instance
./agentic-workspace uninstall
```


## Available agents

The bundled instances are working examples, not a supported product surface.
Install picks whichever you want, and your own agents live next to them.

| Agent | Who it is | What it's for |
|-------|-----------|---------------|
| **`agre`** | the **researcher** | math, machine learning, GPU experiments, reading papers, running long experiments, writing up results |
| **`bolt`** | the **web app builder** | small full-stack apps in TypeScript: React/Next.js frontends, Node backends, Tailwind styling, Vitest + Playwright tests, conventional commits |
| **`worka`** | the **workspace services agent** | improving the agentic-workspace repo itself |
| **`lera`** | the **teacher** | university math courses: course profiles distilled from the textbook, exercise sheets, solutions and exams in the instructor's own style |
| **`<name>`** | your future agent | create more with `agentic-workspace scaffold` |


## Configuration

`agentic-workspace settings` (or `<name> --settings`) opens an interactive menu: CLI tool, model subscription, proxies, state directory, extra sandbox directories.
Individual values can be set directly:

```bash
# write one setting
./agentic-workspace settings agre AGENTIC_EXTRA_BIND_DIRS=/data/models

# the file behind it (a small Python module)
~/.config/agre/config.py
```

A flat-rate model subscription (e.g. a z.ai GLM coding plan) can be made the default for the main session and every subagent in one setting: `AGENTIC_MODEL_SUBSCRIPTION=zai:glm-5.3`.


## Uninstall

```bash
# remove state, config, launcher, and image of one agent
./agentic-workspace uninstall

# remove only the launcher-managed state directory
./agentic-workspace clean
```

Both ask which instance when run bare and confirm before deleting anything.


## Security

The agent runs in a disposable container and gets exactly one writable directory: your project.
Two caveats worth knowing before a first launch:

- Your `~/.ssh` and `~/.gitconfig` are mounted read-only into the sandbox so git works.
  Read-only prevents modification, not reading: the agent can read your SSH keys.
- Container images download tools from upstream registries at build time.
  After `./agentic-workspace update` the direct downloads are pinned and checksum-verified, but a fresh clone builds whatever is `latest` that day.

The full model and its limits are in [SECURITY.md](SECURITY.md).


## Troubleshooting

- **`agre: command not found`**: add `~/.local/bin` to `PATH` (`export PATH="$HOME/.local/bin:$PATH"`), the installer prints this hint too.
- **`docker not found on PATH`**: install Docker, or use Apptainer with `./agentic-workspace install --apptainer` (Linux, no daemon needed).
- **`FATAL: Building from a definition file requires root or some kind of fake root`** (Apptainer): the host allows neither fakeroot nor user namespaces for your user, so Apptainer cannot build images. Ask an admin to run `sudo apptainer config fakeroot --add $USER` (adds the `/etc/subuid` + `/etc/subgid` entries; needs the `uidmap` package), or to enable unprivileged user namespaces — or build on a host that allows it (e.g. with Docker) and copy the `.sif` files over. `update` keeps previously built images in the meantime (with a warning); explicit `--build` commands fail until the host allows builds.
- **`update` says `docker not found` although the instances use Apptainer**: fixed — a plain `update` now takes the runtime from the instances' config (or pass `--apptainer`, `--runtime apptainer` or `--tool apptainer` explicitly).
- **`build` says nothing to build**: no instance selection exists yet, run `./agentic-workspace install` once, or name an instance explicitly.
- **Tools in the image feel stale**: a plain rebuild never moves a tool, run `./agentic-workspace update` (refreshes version pins and rebuilds).
- **Jobs referencing `/workspace` fail on compute nodes**: the path only exists inside the container, use the shipped `scripts/submit.sh` on Slurm clusters.
