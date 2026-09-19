# Generic agentic-blueprint base container layer for the research template.
# Child-specific toolset: everything NOT in agentic-blueprint:base.

FROM agentic-blueprint:base

# LaTeX syntax checking (research reports use chktex; never compile LaTeX).
RUN apt-get update && apt-get install -y --no-install-recommends \
        chktex \
    && rm -rf /var/lib/apt/lists/*
