#!/bin/bash
# smoke.sbatch -- 1-minute end-to-end check of batch jobs on this cluster.
#
#   scripts/submit.sh scripts/smoke.sbatch
#   cat /workspace/logs/smoke_<jobid>.out   # last line must read: SMOKE OK
#
# Launcher-managed file: overwritten from the instance on every launch.

#SBATCH --job-name=smoke
#SBATCH --time=00:01:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --output=logs/smoke_%j.out
#SBATCH --error=logs/smoke_%j.err

# Fail fast: a failed prologue (or any later step) must abort the script.
# Without this the script prints SMOKE OK even when everything before it
# failed -- a green smoke test that verified nothing.
set -euo pipefail

# $0 on the node is slurmd's spool copy (/var/spool/slurmd/<jobid>/slurm_script),
# so dirname "$0" is the spool dir, NOT the project. Resolve the prologue
# through the workspace host path; PWD fallback works because submit.sh's
# --chdir already landed the job in the project dir.
source "${AGENTIC_WORKSPACE_HOST:-$PWD}/scripts/job-header.sh"

echo "node:    $(hostname)"
echo "date:    $(date -Is)"
echo "project: $PWD"
echo "uv:      $(command -v uv)"
nvidia-smi -L 2>/dev/null || echo "gpus:    none on this node"

if [ -f src/pyproject.toml ]; then
    (cd src && uv run python -c 'import sys; print("python:  " + sys.version.split()[0])')
fi
echo "SMOKE OK"
