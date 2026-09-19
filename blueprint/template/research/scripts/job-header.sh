#!/bin/bash
# job-header.sh -- prologue for Slurm batch jobs; source it FIRST in every
# .sbatch script. Runs on a COMPUTE NODE, outside the container.
#
# sbatch exports the submitting container's environment to the job, and most
# of it is wrong on the node: /workspace does not exist there, HOME=/home is
# the container's config store, PATH holds container dirs, UV_CACHE_DIR points
# at the /uv-cache bind, and SLURM_CONF is the sandbox's client conf. This
# prologue repairs all of that, cds to the project's node-visible host path,
# and makes sure uv + the project venv work.
#
#   #!/bin/bash
#   #SBATCH ...
#   source "$(dirname "$0")/job-header.sh"
#   uv run --directory src python scripts/train.py
#
# Launcher-managed file: overwritten from the instance on every launch. Keep
# project-specific prologues in a separate file.

set -euo pipefail

# --- 1. Repair the environment exported from the submitting container ------
if [ "${HOME:-}" = "/home" ] || [ -z "${HOME:-}" ]; then
    HOME=$(getent passwd "$(id -u)" | cut -d: -f6)
    export HOME
fi
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset SLURM_CONF                       # the node's slurm finds its own conf
for _v in UV_CACHE_DIR UV_PYTHON_INSTALL_DIR UV_TOOL_DIR; do
    # container bind paths (/uv-cache, /uv-python, /uv-tools) don't exist here
    case "${!_v:-}" in /uv-cache|/uv-python|/uv-tools) unset "$_v" ;; esac
done
export UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}"

# --- 2. Resolve the project directory on node-visible storage --------------
PROJECT_DIR="${AGENTIC_WORKSPACE_HOST:-}"
if [ -z "$PROJECT_DIR" ] && [ -n "${APPTAINER_CONTAINER:-${SINGULARITY_CONTAINER:-}}" ]; then
    # SIF at <instance>/.apptainer/<name>.sif -> try <instance>/workspace
    _sif=${APPTAINER_CONTAINER:-$SINGULARITY_CONTAINER}
    _inst=$(cd "$(dirname "$(dirname "$_sif")")" 2>/dev/null && pwd) || _inst=""
    [ -n "$_inst" ] && [ -d "$_inst/workspace" ] && PROJECT_DIR="$_inst/workspace"
fi
if { [ -z "$PROJECT_DIR" ] || [ ! -d "$PROJECT_DIR" ]; } && { [ -f pyproject.toml ] || [ -d .git ]; }; then
    PROJECT_DIR=$PWD                   # submitted from a node-visible path
fi
if [ -z "$PROJECT_DIR" ] || [ ! -d "$PROJECT_DIR" ]; then
    echo "job-header.sh: cannot locate the project on node-visible storage." >&2
    echo "  AGENTIC_WORKSPACE_HOST=${AGENTIC_WORKSPACE_HOST:-<unset>}" >&2
    echo "  Compute nodes must see the project dir (shared home/Lustre)." >&2
    echo "  Submit from a session launched with an up-to-date launcher," >&2
    echo "  or set #SBATCH --chdir to the project's host path." >&2
    exit 1
fi
cd "$PROJECT_DIR"

# --- 3. uv + project venv ----------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    echo "job-header: installing uv to \$HOME/.local/bin" >&2
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
fi
command -v uv >/dev/null 2>&1 || {
    echo "job-header: uv unavailable (no network on this node?)" >&2
    echo "  pre-sync the venv on the login node so jobs can run offline" >&2
    exit 1
}
if [ -f src/pyproject.toml ]; then
    (cd src && uv sync)
elif [ -f pyproject.toml ]; then
    uv sync
fi
