# bolt container image: web toolset layered on the generic base image.
FROM agentic-blueprint:base

# pnpm version comes from the shared pins (blueprint/container/versions.json,
# refreshed by `update`); empty -> latest at build time.
ARG PNPM_VERSION=""
RUN npm install -g "pnpm@${PNPM_VERSION:-latest}" \
    && npm cache clean --force
