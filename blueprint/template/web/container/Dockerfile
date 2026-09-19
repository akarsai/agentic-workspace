# Web-development toolset layer on the generic base image.
# Base already provides: node, npm, npx, bun, git, jq, rg, yq, gh.

FROM agentic-blueprint:base

# pnpm via npm (corepack was removed from Node.js >= 25). The version comes
# from the shared pins (blueprint/container/versions.json, refreshed by
# `update`); empty -> latest at build time.
ARG PNPM_VERSION=""
RUN npm install -g "pnpm@${PNPM_VERSION:-latest}" \
    && npm cache clean --force

# Playwright is NOT installed here by default (large). When a project needs
# e2e tests, run inside the sandbox:
#   npx playwright install --with-deps chromium
