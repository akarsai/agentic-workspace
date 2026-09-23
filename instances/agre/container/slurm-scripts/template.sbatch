#!/bin/bash
# template.sbatch -- starting point for batch jobs. Copy it, adjust the
# #SBATCH block (discover partitions/limits with `sinfo`), and keep the
# job-header.sh source as the first line of the script body.
#
#   cp scripts/template.sbatch scripts/<experiment>.sbatch
#   scripts/submit.sh scripts/<experiment>.sbatch
#
# Launcher-managed file: overwritten from the instance on every launch.

#SBATCH --job-name=<experiment>
#SBATCH --time=04:00:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
## Account: required on clusters without a default account — check with
## `sacctmgr show assoc -u "$(id -un)"` (or export SBATCH_ACCOUNT once on
## the host; sbatch applies it automatically):
##SBATCH --account=<account>
## One GPU (uncomment and adapt to the cluster; check `sinfo -o "%P %a %l %D %t %N"`):
##SBATCH --gres=gpu:1
#SBATCH --output=logs/<experiment>_%j.out
#SBATCH --error=logs/<experiment>_%j.err

# Fail fast (a failed prologue must abort, not be reported as success).
set -euo pipefail

# $0 on the node is slurmd's spool copy, so dirname "$0" is NOT the project.
# Resolve the prologue through the workspace host path; PWD fallback works
# because submit.sh's --chdir already landed the job in the project dir.
source "${AGENTIC_WORKSPACE_HOST:-$PWD}/scripts/job-header.sh"

# The venv lives in src/ (uv project); logs belong in logs/, not stdout spam.
(cd src && uv run python -m <entrypoint> "$@") 2>&1 | tee -a "logs/<experiment>_$SLURM_JOB_ID.run"
