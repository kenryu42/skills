---
name: ultra
description: >-
  USE PROACTIVELY for any implementation request that spans multiple files, has
  several independent work items, or needs new tests — "implement this plan",
  "add feature X", "fix all these findings". It delegates execution to cheaper
  models (Fable plans and reviews, Opus implements), so invoking it is the
  default for substantial builds, not the exception; doing a large
  implementation directly instead wastes the user's Fable usage. Do NOT use
  for: single-file or few-line changes, questions, opinions, design
  discussions, code review, doc/plan edits, or work still being iterated
  conversationally. Before invoking, state in one line that you're delegating
  to /ultra and why. When genuinely borderline, prefer invoking it if the task
  is a self-contained "build/change this" request; prefer direct work if the
  task depends heavily on this conversation's context.
disable-model-invocation: false
---

# /ultra — plan, build, and review with role-assigned models

Run the user's multi-agent build workflow on the task given in the arguments. This command is the user's explicit opt-in to multi-agent orchestration.

## Steps

1. Assemble the task description. Start from `$ARGUMENTS`. If this conversation already established relevant verified facts (investigation findings, reproduced behavior, confirmed commands, design decisions), append them verbatim to the task — later agents cannot see this conversation, so anything not in the task text is lost. If `$ARGUMENTS` is empty and no task is evident from the conversation, ask the user what to build instead of guessing.

2. Invoke the workflow:

   ```
   Workflow({
     scriptPath: "/Users/kenryu/.claude/skills/ultra/workflow.js",
     args: { task: "<assembled task text>" }
   })
   ```

   Do not rewrite or inline the script; always use `scriptPath` so the user's single canonical copy runs.

3. When the workflow completes, read its result and report: the plan summary, the acceptance criteria, work items implemented, the final review verdict, and any findings that remain unfixed. If the workflow ends with verdict `needs-fixes` after its fix rounds, say so plainly and list the outstanding findings — do not soften the outcome.

## Notes

- The workflow's phases are dynamic: a Fable planner designs the stage plan per task (which stages exist, how many agents each fans out), so do not pre-decide stage structure or counts in the task text. A review+fix loop always runs at the end regardless of the plan.
- Model assignment is fixed inside the script's role table and splits by responsibility: Fable owns judgment (planner, investigate, design, review, decisions on escalations), Opus owns execution (implement, fix). The planner picks roles, never models. Do not override them.
- An implementer that hits a decision the plan does not cover escalates it: a Fable decision agent resolves the question and the implementer continues. This is not a failure.
- The workflow fails fast: it throws if a critical agent dies, an implementer reports a genuine block (missing tooling or credentials), or an item still needs a decision after two escalations. Report the error and what completed; once the blocker is resolved, resume with `Workflow({scriptPath, resumeFromRunId})` so finished agents replay from cache.
