# Contributing

## Setup

```bash
git clone git@github.com:akarsai/agentic-workspace.git
cd agentic-workspace
uv sync          # creates .venv, installs pyyaml + pytest
uv run pytest    # the whole suite, no Docker needed
```

uv is the only host requirement.
Never use pip directly, inside the sandbox and on the host alike.

## Writing conventions

- One sentence per line in markdown and docs.
  It keeps diffs sentence-level.
- No em-dashes and no semicolons in prose.
  Use a period or a colon.
- Run `./agentic-workspace --help` after touching the CLI and keep the help text in sync.

## Adding or changing an instance

Instances are folders under `instances/` (manifest, INSTRUCTIONS.md, commands/, container/Dockerfile, launcher shim).
Start from a template:

```bash
uv run python -m agentic_workspace.cli scaffold myagent --template minimal
```

Then edit the INSTRUCTIONS.md to shape the agent and commit the folder.
The framework lives in `blueprint/` and is shared, so an instance only carries what makes it that agent.

## Adding a tool to the base image

1. Add the install step to `blueprint/container/Dockerfile.base` (or an instance Dockerfile for toolset layers).
2. If the tool is a direct download, add a version pin and a `*_SHA256` verification step, and register the source in `src/agentic_workspace/tool_versions.py` so `update` can pin and verify it.
3. Declare every build arg you reference.
   The Apptainer converter substitutes `${NAME}` and `${NAME:-fallback}` forms only.
4. Rebuild and run the test suite:
   `./agentic-workspace update --no-tools && ./agentic-workspace build`.

## Release process

Releases are cut from `main`.

1. Update `version` in `pyproject.toml`.
2. Run the full check:
   `uv run pytest && uv build`.
3. Commit, tag `v<version>`, push the tag.
4. Create the GitHub release from the tag with the changelog in the body.
5. Upload `dist/*` artifacts to the release.

The container images are built per clone, so there is no image publication step.

## Bundled instances

The instances shipped in this repo (agre, bolt, lera, worka) are working examples maintained by the author, not a supported product surface.
Install picks whichever you want, and removing an instance folder from your clone does not affect the framework.
