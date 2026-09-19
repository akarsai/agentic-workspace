---
description: Clone the instructor's writing style from exercise sheets and other samples into a style profile (STYLE.md) that all lera output must match
argument-hint: "[named style | 'samples are in <dir>']"
---

You are cloning a writing style. Goal: every sheet, solution, and exam lera
produces reads as if the instructor wrote it. The primary samples are the
instructor's **existing exercise sheets** (LaTeX source preferred).

Arguments: $ARGUMENTS

## Step 0: Choose the target file

- **The instructor's voice** (the default) → `/workspace/STYLE.md`. This is
  the course's style: one course per workspace, so there is exactly one
  profile, and it holds both the voice and the sheet craft of the course.
- **Named style** (someone else, e.g. `style peherstorfer`) →
  `/workspace/STYLE-<name>.md`.

If the target exists, extend it -- never discard recorded rules or examples.

## Step 1: Collect samples

1. **Ask for samples** -- the instructor points at files or a directory
   (e.g. `sheets/*.tex` from previous semesters), or pastes text. Use
   whatever they provide. Samples may be LaTeX (`.tex`) or Typst (`.typ`).
2. **Prefer the raw source over rendered text.** Source-level habits
   (line breaks, punctuation, macros/functions, template structure) *are*
   the style. Analyze the source directly. Never work from PDF-to-text when
   the source exists.
3. **Offer web search** if local samples are thin: course pages, the
   university's material archive, or URLs the instructor provides. Save
   fetched text under `/workspace/.tmp/style-samples/` (gitignored scratch).
4. **Clean** -- drop book-license boilerplate, solution keys, and template
   artifacts so the analysis reflects the instructor, not the template.

## Step 2: Analyze -- two layers

### Layer A: the voice (linguistic signature)

> Study the tone, word choice, sentence structure, pacing, punctuation
> habits, and rhetorical devices in the samples. Output a concise,
> bullet-point style guide another writer could follow: do/don't rules,
> sentence-length ranges, preferred connectors, formatting tendencies.

**Measure, don't guess.** Count from the samples: semicolons, em-dashes,
banned connectives ("whereas", "Therefore,"), sentence-length stats (mean,
median, share over 25/35 words), `~`-before-math density. Record the counts
-- a measured rule is checkable, a vague one is not.

### Layer B: the sheet craft (exercise-sheet-specific)

Extract and quantify, because sheets have conventions prose does not:

- **Structure**: header block (course, sheet number, due date, name fields),
  exercise environment (`Aufgabe`/`Exercise`/`Problem`), numbering style,
  sub-item style (a) b) i.), points placement (`[10 Punkte]` vs `(10 pts)`
  vs `\\hfill 10/40`), bonus marking (`*`, `(Bonus)`).
- **Phrasing openers**: how tasks begin -- "Show that", "Compute", "Let
  $A \in \mathbb{R}^{n \times n}$. Prove...", "Give an example of...",
  "True or false: justify". List the actual openers with frequencies.
- **Hints**: where they live (footnotes, italics after the problem, separate
  part (d)), their register ("You may use...").
- **Preamble, macros, and the Typst port**: custom theorem environments,
  points macros, enumitem settings, header/footer machinery -- for LaTeX
  samples, record them verbatim. Then **port the measured structure to a
  Typst template** at `template.typ` (workspace root), since new materials
  are Typst by default: exercise function (`#let exercise(...)`), points
  display, sub-item style, hint placement, header block, and
  `#set text(font: "Helvetica")` (fonts from `assets/fonts/`). Never
  invent structures the instructor does not use.
- **Difficulty signals**: how (whether) difficulty is communicated. Language
  of the materials (German/English/bilingual).

## Step 3: Codify -- write the style file

A compact, actionable profile:

- **Voice & tone**, **sentence rhythm**, **lexicon**, **formatting**,
  **rhetorical moves** (as in Layer A).
- **Sheet craft rules** (from Layer B) -- numbered and verifiable.
- **Mathematical style**: notation habits, formula density in prose, how
  much guidance multi-part exercises give.
- **Source formatting** (LaTeX): hard mechanical rules (sentence-per-line,
  no semicolons, `~` before math, `\Cref`) plus the command that checks
  them (Step 4).
- **2--3 verbatim example exercises** (with source intact, 80--150 words
  each) as few-shot demonstrations: one routine computation, one proof
  task, one multi-part guided exercise. Keep the instructor's quirks --
  they are the fingerprint. Clean template artifacts only.

If samples include prose (course overview, emails to students), fold it in
under a "prose" section -- sheets first.

## Step 4: Verify -- mechanical lint (LaTeX) / compile gate (Typst)

**Typst deliverables** (the default): the mechanical check is a clean
compile with the right font --

```bash
typst compile --font-path assets/fonts <file.typ>   # zero errors,
                                                    # no 'unknown font family'
```

Structure conformance comes from the course template: if the draft deviates
from `template.typ` (points display, hint placement, header), that is a
style violation.

**LaTeX legacy files** run the bundled linter:
```bash
python3 .agents/skills/style/stylelint.py check <file.tex>
python3 .agents/skills/style/stylelint.py reflow <file.tex>   # optional normalizer
```

- `check` reports semicolons, em-dashes, and inline math without a
  preceding `~`, with line numbers.
- `reflow` rewrites prose to one-sentence-per-line and asserts display math
  is byte-identical before/after (aborts if any math changed). Idempotent
  on a correctly formatted file.
- **Sanity-check on the instructor's own sample**: `reflow` on their file
  must not change content (only leading whitespace may differ). If it does,
  the recorded line rule is wrong for this instructor -- adjust the rule,
  not their file.

Fix every reported violation in lera's output before delivering. If a rule
is not lintable (banned connectives), grep for it.

## Agentic workflow

- **stylist subagent** -- for substantial pieces (a full sheet), delegate
  the writing: it loads the style file + few-shot examples and writes in
  that voice. Name the target file in the task.
- **style-reviewer subagent** -- final pass against the same style file:
  checks voice, sheet-craft rules, and example fidelity. The reviewer
  checks *voice*. The compile gate (Typst) or stylelint (legacy LaTeX)
  checks *mechanics*. Both are required.
- **Distillation loop** -- when output violates the style, diagnose which
  step was missing and add a short advisory rule (50--60 tokens) to the
  style file. When stylelint catches a recurring violation, promote it to a
  numbered hard rule. The profile improves over time without fine-tuning.
