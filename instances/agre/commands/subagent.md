---
description: Spawn subagents for long-running or isolated research tasks
argument-hint: "optional guidance, otherwise see the protocol below"
---

You can delegate work to **subagents**: separate `pi` processes with isolated
context windows that share this workspace and run to completion without
interruption (no timeout, no human-in-the-loop).

## When to spawn a subagent

- **Long-running independent tasks** (experiments, large searches, literature
  sweeps) that should keep running while you do other work.
- **Parallelizable work** (e.g. explore several hypotheses at once).
- **Isolated context** for a focused sub-problem, keeping your main context clean.

## Tools

- `subagent_spawn { task, agent?, model?, tools? }`: fire-and-forget background
  subagent. Returns an id immediately. A follow-up message arrives on completion.
- `subagent { agent?, task }` or `subagent { tasks: [{agent, task}, ...] }`:
  synchronous delegation. Blocks until the subagent finishes (streams progress).
- `subagent_list`: list background subagents and their status.
- `subagent_result { id }`: read status/final result of a background subagent.
- `subagent_stop { id }`: kill a background subagent.
- `subagent_cleanup { purge? }`: drop finished subagents (optionally delete their mailboxes).

## Built-in agents

| Agent | Purpose |
|-------|---------|
| `scout` | read-only recon. Returns compact structured findings |
| `worker` | general-purpose implement/run (full tools) |
| `reviewer` | read-only code/plan review with actionable findings |
| `stylist` | writes in the user's voice, loading STYLE.md + few-shot examples |
| `style-reviewer` | checks a draft against STYLE.md and reports violations |

Omit `agent` for a generic subagent. Custom agents: `.pi/agents/*.md` in the
workspace (project-local) or `~/.pi/agent/agents/*.md`.

## Rules

- **Only you commit.** Subagents produce work and report it. They must not run
  git write operations. Commit their results yourself.
- **Model**: subagents default to `AGENTIC_SUBAGENT_MODEL` (fast/flash-class,
  configurable in the instance config). Override per call with `model:`.
- **Mailbox**: every subagent writes to `/workspace/.tmp/subagents/<id>/`
  (`task.md`, `result.md`, `status`, `stdout.jsonl`, `session.jsonl`, `meta.json`).
  Inspect these to debug a long run.
- **Kill semantics**: ending the session kills all running subagents. A running
  background subagent is NOT killed by cancelling your current turn.
- Long-running jobs write their own logs (Principle C2). The subagent's
  mailbox keeps its full JSON event stream out of your context window.
