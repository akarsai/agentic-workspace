---
description: Produce exercise sheet n for the course, in the instructor's style, verified solvable
argument-hint: "[n] [topic or free-form instructions, e.g. '3: focus on diagonalization']"
---

You are producing an **exercise sheet** for the course. The sheet must
follow the book, respect the syllabus, read as if the instructor wrote it,
and contain only exercises you have solved yourself.

Arguments: $ARGUMENTS

## Step 0: Resolve the context

1. Course profile: `COURSE.md` at the workspace root.
   No profile yet → run the `/course` flow first.
2. Sheet number $n$: from the arguments, else next free number
   (`max(issued) + 1` from the profile's progress table).
3. Topics: from the topic plan in `COURSE.md` for week $n$'s units.
   Free-form arguments may
   narrow or extend the topic -- but anything beyond the week's coverage is
   bonus material and must be marked as such.
4. Style: load `STYLE.md`. **If it does not exist**, ask for sample sheets
   and run `/style` first. Do not write in your own default voice.
5. Template: reuse `template.typ`. If missing but sample sheets
   exist, port their layout to a Typst template (header block, exercise
   function, points display, hint placement -- and
   `#set text(font: "Helvetica")`. Fonts come from `assets/fonts/`). Only
   bootstrap a new minimal, boring template when there is truly nothing to
   reuse. (Legacy LaTeX course: reuse/port `preamble.tex` instead. LaTeX is
   never compiled.)

## Step 1: Plan before LaTeX

Write the plan in prose first (this is the cheap tier -- catch design
problems before typesetting):

- exercise list: one line each -- task type (compute / prove / apply /
  model / true-false-with-justification), the exact content, the book
  section it drills, the notation it uses.
- difficulty mix vs. the course profile's targets (and stated honestly).
- points allocation against the sheet's total.
- hints (per the conventions policy) and planned bonus problems.
- what each exercise *verifies* about the week's definitions/theorems.

Every exercise must pass the syllabus check: only definitions, theorems, and
notation from the topic plan in `COURSE.md`, weeks $\le n$.

## Step 2: Draft

- File: `sheets/sheet-<NN>.typ` (naming per conventions).
- Build on the course template. The default font is Helvetica from
  `assets/fonts/` -- compile with
  `typst compile --font-path assets/fonts <file>` early and often.
- Notation and terminology follow the book (references: verified
  section/theorem numbers only, else generic phrasing).
- Phrasing follows the style profile: typical openers ("Show that...",
  "Compute...", "Let ... Prove..."), points placement, sub-item style,
  hint formatting, German/English forms -- these are measured facts from
  the samples, apply them mechanically.
- Legacy LaTeX course: same rules in `.tex`. The style profile's source-
  formatting rules (sentence-per-line, `~` before inline math, `\Cref`)
  apply while writing, not as cleanup afterwards.

## Step 3: Solve (the solvability gate)

For **every** exercise, write the complete solution now, into
`sheets/sheet-<NN>-solutions.typ` (per conventions):

- proofs: full argument you would sign your name under.
- computations: a verification script `scripts/verify_sheet_<NN>.py`
  (sympy/numpy via `uv run`) that checks each computational claim, including
  edge cases. Record pass/fail.
- if you cannot solve an exercise, it is not ready: rewrite it, lower the
  difficulty, or drop it -- and note the change in your report.

## Step 4: Verify

In order -- fix, never footnote:

1. `typst compile --font-path assets/fonts sheets/sheet-<NN>.typ` -- zero
   errors and NO `unknown font family` warning (Helvetica resolved).
   compile the solutions file likewise
2. `typst fonts --font-path assets/fonts | grep -i helvetica` -- once after
   font files are added, to confirm discovery
3. Solution completeness: every exercise has a solution
4. `uv run python scripts/verify_sheet_<NN>.py` -- all checks pass
5. Difficulty honesty: re-read the mix against the profile targets

Legacy LaTeX course: skip the compile steps (LaTeX is never compiled);
steps 3--5 still apply.

## Step 5: Style pass

For a full sheet (not a quick single exercise), delegate:

- `stylist` subagent: polish the draft against the style profile +
  few-shot examples.
- `style-reviewer` subagent: check the result and report violations. Fix
  what it flags.
- re-run the mechanical gate afterwards: `typst compile` (Typst) or
  stylelint (legacy LaTeX) -- mechanics after voice.

## Step 6: Record and commit

- Update the course profile: add the sheet to the progress table,
  notation introduced, any convention decisions made along the way.
- Tick related TODO.md items. Add new ones (unverified claims, ideas for
  later sheets).
- Commit by name, source and compiled PDF together (never solution PDFs to
  a student-visible repository): `sheet(<n>): <topics covered>`.

## Variants

- `/sheet 4 exam` → same loop, exam conventions (points, time, no hints,
  cover page) -- ask for exam-specific settings first.
- `/sheet 3 solution only` → (re)write solutions for an existing sheet.
  Steps 3--4 still apply in full.
- `/sheet draft` → plan + exercise sketches only, no LaTeX. Instructor
  approves before typesetting.
