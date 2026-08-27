---
name: commit-with-context
description: Create a coherent Conventional Commit with concise, high-signal context. Use when the user explicitly asks to commit current work or invokes $commit-with-context. Keep unrelated changes out and avoid splitting one task into artificial micro-commits.
---

## Context

- Current status: !`git status --short`
- Staged changes: !`git diff --cached`
- Unstaged changes: !`git diff`
- Current branch: !`git branch --show-current`
- Recent commit messages: !`git log -5 --format='%h%n%s%n%b%n---'`

## Scope the commit

- Treat a non-empty index as the intended commit scope. Do not stage additional paths unless the user explicitly asks you to select changes from the worktree.
- When the index is empty, stage only paths that belong to the requested task after inspecting their contents, including relevant untracked files.
- Do not include unrelated or pre-existing changes. If the index mixes unrelated work, stop and report the conflict instead of silently restaging it.
- Do not modify source files while performing the commit workflow.

## Choose commit boundaries

- Default to one commit for one coherent task.
- Keep implementation, tests, documentation, and migrations for the same behavior together.
- Split only when changes are unrelated, independently reversible, or independently useful for cherry-picking or backporting.
- Do not split a task merely by file type, implementation step, or diff size.
- When splitting is justified, ensure every commit is meaningful and valid on its own.

## Write the message

Use a Conventional Commit subject in imperative form:

```text
type(scope): concise summary
```

Follow the repository's established types and scopes. Use `feat` for a new feature and `fix` for a bug fix. Add issue references or `BREAKING CHANGE` footers when applicable.

For non-trivial changes, add a concise body:

```text
Why:
- State the problem, motivation, or user-visible intent.

What:
- Summarize the important behavioral changes or design decisions.

Validation:
- List the exact verification commands that passed.
```

After staging the intended scope, calculate the LOC changes for `src/`, `tests/`, and the Git pathspec `*.md`. Append this aligned plain-text block to every commit message:

```text
LOC:
  src/    +128 / -36 = net +92
  tests/   +91 /  -8 = net +83
  *.md     +27 /  -4 = net +23
  Total   +246 / -48 = net +198
```

Replace the example values with the staged values. Use `net = added - deleted`, and add the three rows for `Total`.

Keep the body high-signal:

- Prefer one `Why` bullet and one to four `What` bullets.
- Record important boundaries or deliberate omissions when they help future readers.
- Do not invent validation results. If verification was not run, say `Not run` and give the reason.
- Do not list changed files or narrate implementation details that are obvious from the diff.
- Do not repeat the subject in the body or add empty sections.

## Commit and verify

- Create the commit with the exact subject and body you prepared.
- Do not bypass commit hooks. Read and respond to their complete output if they fail.
- Do not push or amend unless the user explicitly requests it.
- Verify the result with `git status --short` and `git log -1 --format=fuller`.
- Report the commit SHA, subject, and any remaining worktree changes.
