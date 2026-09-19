---
description: Self-review the current changes against the web-dev checklist
argument-hint: "[optional focus, e.g. 'focus on accessibility']"
---

Review your current changes before handing off or committing. Run through the
checklist below and fix anything that fails. Use `$ARGUMENTS` to focus the
review if provided.

## Steps

1. **Diff**: run `git diff` (and `git status`) to see what changed. Read every
   changed file, not just the summary.
2. **Typecheck**: run the project's typecheck command. Zero errors.
3. **Lint + format**: run lint and format check. Zero warnings. No new
   suppressions.
4. **Tests**: run unit tests. New behavior has tests. Bug fixes have
   regression tests. All green.
5. **Review checklist** (fix what fails):
   - **Types**: no `any`, no unsafe casts, boundaries typed.
   - **Correctness**: edge cases, empty/loading/error states, race conditions.
   - **Accessibility**: semantic HTML, keyboard nav, focus, `aria-*`, contrast,
     reduced motion.
   - **Responsiveness**: mobile → desktop, no horizontal overflow.
   - **Performance**: no heavy deps added, lazy loading where warranted, no
     obvious re-render regressions.
   - **Security**: no secrets committed, no unsanitized user content rendered,
     no injection. Check `.env*` did not change with real values.
   - **Docs**: README/CHANGELOG updated if behavior changed.
6. **Run the build** (`pnpm build` or equivalent) to confirm production output
   works.
7. **Report**: table of checklist item → pass/fail, list of fixes made, and
   anything still open.

## Rules

- Be honest. If something is not verified, say so.
- Do not mark items pass without running the relevant command.
- Do not fix unrelated code in the same pass -- note it separately.
