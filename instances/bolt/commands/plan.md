---
description: Plan a feature or task before implementing it
argument-hint: "what you want to build or fix"
---

You are planning implementation work. Produce a concrete, reviewable plan
before touching code. If the user supplied an argument, use it as the task.
otherwise ask what the task is.

## Steps

1. **Understand**: read the task, `package.json` scripts, and
   the relevant code. Identify the files the change will touch.
2. **Restate** the task in one or two sentences and confirm with the user if
   the scope is ambiguous.
3. **Acceptance criteria**: list verifiable checks (typecheck, lint, tests,
   build, manual behavior) that must pass for "done".
4. **Design**: outline the approach -- new components/modules/functions, data
   flow, state, edge cases. Note alternatives you considered and why you chose
   this one.
5. **Break down** into small steps, each independently committable:
   `1. setup → 2. implement core → 3. wire UI → 4. tests → 5. docs`.
6. **Risks**: what could go wrong, and how you will de-risk (spike, test
   early, ask the user).
7. **Show** the plan to the user and ask for confirmation before implementing,
   unless the user already authorized autonomous work.

## Format

Present as a short markdown plan with: Task, Files, Approach, Steps,
Acceptance criteria, Risks.
