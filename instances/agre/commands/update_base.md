---
description: Update project instruction file with the latest base template while preserving project-specific instructions
---

You need to update the project's instruction file with the latest base template while keeping the project-specific content intact.

## Steps

1. **Read** the latest base template from `/home/.claude/INSTRUCTIONS.md.template`
2. **Use** `AGENTS.md` as `$INSTRUCTION_FILE`.
3. **Read** the current project file at `/workspace/$INSTRUCTION_FILE`
4. **Extract** from the current file: everything starting from `## 8. Project Instructions` (inclusive) to the end of the file. This is the project-specific content that must be preserved exactly as-is.
5. **Combine**: take the template content up to (but NOT including) `## 8. Project Instructions`, then append the extracted project-specific content from step 4.
6. **Write** the combined result to `/workspace/$INSTRUCTION_FILE`
7. **Show** the user a brief summary of what changed (e.g., "Updated base sections 0-7 from template, preserved your Project Instructions")
8. **Commit** with message: `chore: update instruction file base template`
