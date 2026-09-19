# agentic-workspace

A collection of tools to sandbox CLI coding agents by running them in containers.
Key features are:
- CLI coding agents are installed and run in containers (Docker, Apptainer).
- Multiple container environments can be installed in parallel, each with their own toolset and instructions.

The project is inspired by [The Agentic Researcher](https://github.com/ZIB-IOL/The-Agentic-Researcher).


## Quick start

**Requirements**
- **[uv](https://astral.sh/uv)**
- **Docker** or **Apptainer**
- Access to a CLI coding agent

**Install**

```bash
# clone
git clone git@github.com:akarsai/agentic-workspace.git && cd agentic-workspace # this folder is the install, it should remain on disk

# install
./agentic-workspace install
```

**Usage**

```bash
# go to any folder
cd any-folder

# call an instance, e.g. for agre
agre
```


### Instance commands

Here for the `agre` instance, others the same.

```bash
# pick the CLI engine for this launch
agre --pi
agre --codex
agre --opencode
agre --claude 

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

| Agent | Who it is | What it's for |
|-------|-----------|---------------|
| **`agre`** | the **researcher** | math, machine learning, GPU experiments, reading papers, running long experiments, writing up results |
| **`bolt`** | the **web app builder** | small full-stack apps in TypeScript: React/Next.js frontends, Node backends, Tailwind styling, Vitest + Playwright tests, conventional commits |
| **`worka`** | the **workspace services agent** | improving the agentic-workspace repo itself |
| **`lera`** | the **teacher** | university math courses: course profiles distilled from the textbook, exercise sheets, solutions and exams in the instructor's own style |
| **`<name>`** | your future agent | create more with `agentic-workspace scaffold` |
