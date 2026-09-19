---
description: Reflect on the current teaching session and collect improvement suggestions for lera's instructions
argument-hint: "[optional feedback, e.g. 'the sheets came out too hard']"
---

You are reflecting on the current teaching session to identify improvements
for lera's base instructions. This is a retrospective -- not about what went
wrong, but about what can be better for future sessions on this course.

User feedback (may be empty): $ARGUMENTS

## Step 1: Gather Context

Read the following files (skip any that don't exist):

1. `/workspace/REVISION.md` -- previous retrospective entries
2. Course profile: `COURSE.md` in the workspace root --
   plan quality, progress recording
3. Recent sheets and solutions in `sheets/` -- style fidelity,
   difficulty, LaTeX hygiene
4. `/workspace/AGENTS.md` -- the instructions governing this session
   (especially the Teaching Commandments and §8: Project Instructions)
5. `/workspace/TODO.md` -- open questions left behind
6. `git log --oneline -30` -- commit discipline and what was actually done

## Step 2: Commandment Compliance

Review compliance with each Teaching Commandment (Section 1 of the
instruction file). For each, assess: **followed**, **partially followed**, or
**violated**, with evidence.

| # | Commandment | Status | Evidence |
|---|-------------|--------|----------|
| I | Never break a promise | ? | Did the agent follow through on stated intentions? |
| II | Never fabricate book references | ? | Were all chapter/theorem references verified against the book or profile? |
| III | Every exercise must be solvable | ? | Did every shipped exercise have a complete, checked solution? |
| IV | Respect the syllabus | ? | Did sheets use only covered definitions/notation? Bonus marked? |
| V | Write in the instructor's style | ? | Was the style profile loaded and followed? Preamble/macros reused? |
| VI | Be honest about difficulty | ? | Was the difficulty mix stated and calibrated? |
| VII | Verify before claiming | ? | typst compile + font check (or chktex/stylelint for legacy LaTeX), solutions, verification scripts all run? |
| VIII | Record everything | ? | Were COURSE.md, TODO.md kept current? |
| IX | Complete autonomous work | ? | Was work handed back half-finished? |
| X | Make it work before moving on | ? | Were lint/verify failures footnoted instead of fixed? |

This table goes into the REVISION.md entry.

## Step 3: Analyze the Session

### A. Course profile quality
- Did the distilled topic plan match how the course actually moved?
- Was notation tracking useful and correct (cumulative list up to date)?
- Did the plan need ad-hoc patches the profile should have anticipated?

### B. Sheet quality
- Style fidelity: would the instructor ship these without edits? What gave
  lera away (phrasing, points style, hint placement, difficulty)?
- Difficulty calibration: too hard / too easy vs. the book's exercises?
- Solutions: complete, correct, at the right level of detail for graders?
- Typst/LaTeX hygiene: compiled cleanly with the right font (Typst), or
  chktex-clean (legacy LaTeX)?

### C. Workflow
- Was plan-before-LaTeX followed? Did it catch design problems early?
- Verification ladder (typst compile -> font check -> solutions -> scripts.
  legacy LaTeX: chktex -> stylelint -> solutions -> scripts): run in
  order? What escaped?

### D. Git discipline
- Commit format `course(...)` / `sheet(...)` followed? Atomic commits?

### E. User-specific feedback
- Address the user's feedback directly. This is the most important input.
- If the user pointed out something specific, propose a concrete
  instruction file change for it.

## Step 4: Write to REVISION.md

Update `/workspace/REVISION.md` (append-only. Never rewrite old entries):

```markdown
## Revision N -- [DATE]

**Session context:** [course, what was produced]

**User feedback:** [what the user said, or "None provided"]

### Commandment Compliance
[table from Step 2]

### Proposed Changes
#### [Section of instruction file or command file]
- **Issue:** [what was suboptimal]
- **Suggestion:** [concrete wording change]
- **Rationale:** [why it helps]

### Things That Worked Well
- [keep doing these]
```

## Step 5: Propose instruction changes

For each proposed change, decide where it belongs:

- **Style profile** (STYLE.md): recurring voice violations → advisory rule
  via the distillation loop, not an instruction change.
- **Course profile** (COURSE.md): course-specific conventions.
- **Base instructions** (AGENTS.md §0--7) or a command file: only for
  cross-course, repeated failures. Present the diff. The instructor decides.
- If the user asked for this specific change, apply it directly after they
  confirm.

## Step 6: Report

Summarize for the instructor: compliance table, top issues, proposed
changes (with where each belongs), and what to reuse next session. Commit
REVISION.md with `retro: <one-line summary>`.
