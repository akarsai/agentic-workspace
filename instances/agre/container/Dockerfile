# agre container image: research toolset layered on the generic base image.
# The base image (agentic-blueprint:base) provides the common tools and the
# entrypoint; this file adds research-specific tools only.

FROM agentic-blueprint:base

# Slurm job tooling (job-header.sh, submit.sh, ...). Baked into the image and
# copied into a workspace by the agent only when the user asks for Slurm jobs
# -- it is NOT auto-synced into every workspace.
COPY slurm-scripts/ /opt/agre/slurm-scripts/

# just (command runner) via uv. Dirs are pinned under /opt: the launcher bind
# mounts the host over /home (image-baked files there would be shadowed) and
# points UV_TOOL_DIR at a host cache, so the default ~/.local locations would
# not survive at runtime. The `just` link goes to /usr/local/bin, which is on
# every user's PATH. The version comes from the shared pins
# (blueprint/container/versions.json, refreshed by `update`); empty -> latest.
ARG JUST_VERSION=""
RUN if [ -n "$JUST_VERSION" ]; then just_spec="rust-just==${JUST_VERSION}"; else just_spec="rust-just"; fi \
    && UV_TOOL_DIR=/opt/uv/tools \
        UV_TOOL_BIN_DIR=/usr/local/bin \
        UV_PYTHON_INSTALL_DIR=/opt/uv/python \
        uv tool install --no-cache "$just_spec"
