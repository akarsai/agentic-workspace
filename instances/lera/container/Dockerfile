# lera container image: teaching toolset layered on the generic base image.
# The base image (agentic-blueprint:base) provides the common tools and the
# entrypoint; this file adds teaching-specific tools only.

FROM agentic-blueprint:base

# chktex: LaTeX syntax checking (sheets are read/edit only -- never compiled).
# poppler-utils: pdftotext to extract tables of contents and sections from
# the textbooks the instructor provides (course distillation reads the book).
RUN apt-get update && apt-get install -y --no-install-recommends \
        chktex \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*
