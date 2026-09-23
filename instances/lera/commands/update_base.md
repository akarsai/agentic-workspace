---
description: Update project instruction file with the latest base template while preserving project-specific instructions
---

You need to update the project's instruction file with the latest base template while keeping the project-specific content intact.

## Steps

1. **Read** the latest base template from `/home/.claude/INSTRUCTIONS.md.template`
2. **Use** `AGENTS.md` as `$INSTRUCTION_FILE`.
3. **Read** the current project file at `/workspace/$INSTRUCTION_FILE`
4. **Diff** the template against the current file (`diff /workspace/$INSTRUCTION_FILE /home/.claude/INSTRUCTIONS.md.template`). If the current file holds local edits beyond the template, show them and ask whether to keep or discard.
5. **Write** the template over `/workspace/$INSTRUCTION_FILE` (keeping any local content the user chose to preserve).
6. **Show** the user a brief summary of what changed.
7. **Commit** with message: `chore: update instruction file base template`
