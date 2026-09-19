#!/bin/bash
# submit.sh -- sbatch wrapper that runs jobs from node-visible storage.
#
# sbatch inherits the submit directory as the job's working directory, and
# from inside the container that is /workspace -- which does not exist on
# compute nodes, so the job dies before its script starts. The wrapper points
# --chdir at the project's host path instead and forwards every argument to
# sbatch. AGENTIC_WORKSPACE_HOST is that host path: it is resolved by the
# Slurm controller on the HOST, so it must NOT be tested for visibility here
# (inside the sandbox the path does not exist -- that is the whole point).
#
#   scripts/submit.sh scripts/smoke.sbatch
#   scripts/submit.sh --array=1-36 scripts/run.sbatch
#
# Run from the workspace root: .sbatch templates write their output to
# logs/ relative to --chdir, so that directory is created here first (CWD --
# the same directory as the node-side host path, bind-mounted at /workspace).
#
# Job account: sbatch applies $SBATCH_ACCOUNT automatically; export it in the
# host shell before launching the session (the launcher passes it through) or
# put #SBATCH --account=<account> in the script.
#
# Launcher-managed file: overwritten from the instance on every launch.

set -euo pipefail

DIR="${AGENTIC_WORKSPACE_HOST:-}"
if [ -z "$DIR" ]; then
    echo "submit.sh: AGENTIC_WORKSPACE_HOST is not set." >&2
    echo "  Submit from a session launched with an up-to-date launcher," >&2
    echo "  or use plain sbatch with an explicit --chdir=<host path>." >&2
    exit 1
fi

mkdir -p logs
exec sbatch --chdir="$DIR" "$@"
