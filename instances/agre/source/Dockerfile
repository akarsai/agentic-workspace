# ---------------------------------------------------------------------------
# Template Dockerfile for running Python research code.
# Built on the official astral-sh/uv image which bundles uv + Python.
#
# Before building, replace <python-version> with the latest stable Python
# (e.g., 3.12, 3.13) and <debian-release> with the base Debian release
# (e.g., bookworm, trixie).  Check available tags at:
#   https://github.com/astral-sh/uv/pkgs/container/uv
# ---------------------------------------------------------------------------

FROM ghcr.io/astral-sh/uv:python<python-version>-<debian-release>

WORKDIR /src

# Install dependencies in a separate layer to maximise cache reuse.
# The lockfile is optional but recommended for reproducibility.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen

# Copy the rest of the source tree.
COPY . .

# ---------------------------------------------------------------------------
# Usage (run from the *project root*, i.e. the directory containing src/):
#
#   docker build -t <project-name> -f src/Dockerfile src/
#   docker run --rm <project-name> uv run python experiments/<script>.py
#
# GPU passthrough (NVIDIA):
#   docker run --rm --gpus all <project-name> uv run python experiments/<script>.py
# ---------------------------------------------------------------------------
