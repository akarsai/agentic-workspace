---
description: Initialize, update, or inspect the course profile. Distills the main topics from the book the instructor follows
argument-hint: "[init | update | book <new book>]"
---

You are managing the **course profile** -- the state that makes lera
customizable: which book to follow, the distilled topic plan, conventions,
and progress. One course lives in one workspace, in a single file at the
root -- `COURSE.md` (see AGENTS.md §2 for the layout). No course ids. A
second course is a second workspace.

Arguments: $ARGUMENTS

## Step 1: Decide the mode

- no arguments, and `COURSE.md` exists → **status**: print a one-screen
  status (book, week, next topics, open TODOs), and stop.
- no arguments, `init`, or no `COURSE.md` yet → **initialize** (Step 2).
- `update` → **update** (Step 4).
- `book <reference>` → **re-base**: swap the course's book, re-distill
  topics (Step 3), and propose a mapping from old plan to new book.

## Step 2: Initialize the course profile

Ask the instructor (interactive, one topic at a time. Propose sensible
defaults where you can):

1. **Identity**: title, term, level/audience, language of the materials.
2. **The book to follow** -- this is the core of the customization. Accept
   any of:
   - a PDF in the workspace (`books/*.pdf` or a path they name) → extract
     the table of contents with `pdftotext -f 1 -l 20 <pdf> -` (walk
     forward until the TOC ends. Some books put it late).
   - a pasted TOC or a chapter list.
   - author + title → look the TOC up on the web (publisher page, author's
     page, Google Books preview) and **verify chapter and section titles
     verbatim** before trusting them. Never reconstruct a TOC from memory
     (Commandment II).
3. **Term shape**: number of teaching weeks, sheets per week, lecture
   rhythm (so the plan can leave room for review and exam weeks).
4. **Conventions**: materials format (Typst by default, compiled to PDF
   with font Helvetica from `assets/fonts/`. LaTeX only if the course's
   existing materials must stay LaTeX), sheet naming, total points, hints
   policy, solutions policy (full/sketches/none), bonus problems, due-date
   format, German or English forms of "Exercise"/"Problem"/"Hint" if the
   materials are bilingual.
5. **Samples** (optional here, but ask): existing exercise sheets (raw
   `.typ` or `.tex` sources preferred) to measure the instructor's style.
   If provided, run the `/style` flow on them -- it
   ports their layout to `template.typ` (or extracts
   `preamble.tex` for legacy LaTeX courses) -- before writing any sheet.

## Step 3: Distill the main topics from the book

Work from the **verified TOC only** (Commandment II):

1. **Group** the book's sections into teachable units (a unit = one sheet's
   worth of material, typically 1--3 book sections).
2. **Sequence** the units week by week across the term. Respect the book's
   own dependency order. Leave review/exam weeks as the instructor's term
   shape suggests.
3. **For each unit record**:
   - title, week number, book sections (verified numbers + titles).
   - key definitions and theorems (names only at this stage).
   - notation introduced (symbols and their meaning -- cumulative list).
   - prerequisites (earlier units or assumed prior knowledge).
   - exercise targets: how many exercises, difficulty mix
     (routine / practice / challenging / bonus).
4. **Write the profile**: one file, `COURSE.md` -- identity, book,
   conventions, the distilled plan, and progress together:

```markdown
# Linear Algebra (WS25) -- course profile

- Term / level / audience / language: WS25, 1st semester BSc math, German
- Book: G. Strang, *Introduction to Linear Algebra*, 5th ed.
  (source: books/strang-ila.pdf, TOC verified 2025-09-04)
- Conventions: [materials format, sheet naming, total points, hints
  policy, solutions policy, bonus problems, due-date format]

## Topic plan

### Week 1 -- Vectors and linear combinations
- Sections: 1.1 Vectors and Linear Combinations. 1.2 Lengths and Dot
  Products
- Definitions/theorems: dot product, length, angle between vectors
- Notation introduced: R^n, v · w
- Prerequisites: none
- Exercise targets: 6 -- routine 4 / practice 2 / challenging 0 / bonus 0

## Progress

| Sheet | Week | Topics | Issued |
|-------|------|--------|--------|
```

5. **Propose, don't impose**: present the distilled plan as a table (week,
   unit, sections) and ask the instructor to confirm or edit. Record their
   decision in COURSE.md.

## Step 4: Update an existing profile

- Re-read the book source (PDF/TOC) and diff against the recorded plan.
- Incorporate the instructor's changes (they may edit COURSE.md
  directly between sessions -- their edits always win, never overwrite
  them silently).
- Re-map progress: which weeks are done (from the progress table and
  the actual files in `sheets/`), what moved.
- Report the diff, update the profile, commit `course: <what changed>`.

## Rules

- Never invent chapter numbers, section titles, or theorem names
  (Commandment II). Unverified → generic reference + TODO entry.
- The profile is the source of truth: after any change, the topic plan,
  the cumulative notation list, and the progress table must agree -- and
  the progress table must match the actual files in `sheets/`
  (Commandment VIII).
- Keep the cumulative notation list up to date -- `/sheet` relies on it to
  respect the syllabus (Commandment IV).
