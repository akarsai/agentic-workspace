# lera: the Teaching Agent

lera launches a **university teaching agent** for mathematical courses inside
a sandboxed Docker container. She builds the course profile from the
textbook you follow, distills the main topics into a week-by-week plan, and
writes exercise sheets, solutions, and exams in your own writing style --
cloned from the exercise sheets you already have. Materials are **Typst by
default**, so lera compiles the PDFs herself (default font: **Helvetica**,
from font files you drop into `assets/fonts/`). LaTeX remains supported for
legacy courses.

lera runs on [Pi](https://pi.dev) by default, and can also run
[OpenCode](https://opencode.ai), [Claude Code](https://claude.com/claude-code),
or [Codex CLI](https://github.com/openai/codex) (see the main README's
"Available CLI tools" section).

## lera is an instance of the agentic-workspace monorepo

lera does **not** reinvent the sandbox. It is one *instance* of
[`agentic-workspace`](https://github.com/akarsai/agentic-workspace), the
monorepo that defines the Docker environment and all interactions once (in
`blueprint/`).

Everything that makes lera *lera* is her **teaching personality**:

| Piece | What it is |
|-------|------------|
| `INSTRUCTIONS.md` | the teaching agent's rules: the ten Teaching Commandments, course profiles, sheet workflow, verification ladder |
| `commands/` | slash commands: `/course`, `/sheet`, `/style`, `/subagent`, `/retro`, `/update_base` |
| `manifest.yaml` | identity: image `lera:latest`, tool, commands |
| `container/Dockerfile` | teaching toolset on the blueprint base: chktex (legacy LaTeX) + poppler-utils (book PDFs). Typst ships in the base image |

Everything else: the launcher, container base image, entrypoint, security,
and setup/cleanup tooling: is the **shared framework** in the monorepo's
`blueprint/`. When the framework improves, lera benefits automatically.

## Quick Start

```bash
git clone git@github.com:akarsai/agentic-workspace.git
cd agentic-workspace
./agentic-workspace install --name lera   # or pick lera in the installer
./agentic-workspace build lera
mkdir ~/teaching/linear-algebra && cd ~/teaching/linear-algebra
lera
```

Prerequisites: Docker (or Apptainer) and an API key for your model provider
(e.g. `DEEPSEEK_API_KEY` for Pi). See the main README for details.

## The teaching workflow

1. **Set up the course**: `/course`. Lera asks for the
   book (a PDF in `books/`, a pasted TOC, or author+title for a web lookup),
   the term shape, and your sheet conventions. She distills the main topics
   from the book into a week-by-week plan (in `COURSE.md` at
   the workspace root), proposes it, and you edit it into shape. One course
   per workspace: you launch lera from the course directory, so there is
   no course selection.

2. **Clone your style**: `/style` pointed at your existing exercise sheets.
   lera measures your voice *and* your sheet craft (phrasing openers, points
   placement, hint formatting, template and helpers) and records it in
   `STYLE.md`. Your layout
   is ported to a Typst template (`template.typ`). Everything
   she writes afterwards matches it: checked at compile time and by a
   style-reviewer subagent.

3. **Produce sheets**: `/sheet 4` generates sheet 4:
   topics from the plan, notation from the book, phrasing from your style.
   Every exercise is solved (full solutions in a separate file),
   computationally verified where applicable, and compiled to PDF with Typst
   (Helvetica via `assets/fonts/`) before you see it. Exams and solution-only
   rewrites are variants of the same loop.

## Customization points

| What | Where |
|------|-------|
| Book to follow, topic plan, progress | `COURSE.md` |
| Your writing style | `STYLE.md` |
| Sheet conventions (points, hints, solutions) | course profile, or `## 8. Project Instructions` in `AGENTS.md` |
| Preamble / macros reused in sheets | `template.typ` (Typst. Fonts in `assets/fonts/`) |

Edit any of these directly: lera treats your edits as authoritative and
never overwrites them silently.

## Commandments, in short

Never fabricate book references. Every exercise must be solvable: lera
writes the solution before you see the problem. No forward references past
the current week. Difficulty is stated honestly. Everything is verified
before delivery: Typst sheets compile cleanly with Helvetica from
`assets/fonts/`, computational claims run through sympy checks, and legacy
LaTeX gets chktex + stylelint. The full set lives in `INSTRUCTIONS.md`.
