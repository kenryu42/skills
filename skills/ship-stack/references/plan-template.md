# Plan file template

Use these sections in order. Drop any section that would be empty. Write for a fresh agent that has
only this file and the repo.

````markdown
# <Title> Plan

Status as of <YYYY-MM-DD>: <planned | in progress (layer N of M) | all layers green>.

<One paragraph: what this delivers and why.>

## Read first

- <Source of the work: an audit report, issue, design doc, or findings list, with its path. Say
  whether it is tracked, gitignored, or local-only.>
- <Repo rules file, for example AGENTS.md, and anything else a new agent must read.>
- <External sources the fixes depend on, such as host or library source at a pinned version.>

## Baseline

<Commit or release the plan starts from, and any versions the work targets.>

## Already done

<Work finished outside the stack, with commits or links. Omit if none.>

## Open decisions

<Numbered list of decisions for the user. Give each a recommended default. Mark each as resolved,
with the answer, once the user decides.>

## Working rules

<Only repo-specific rules that are easy to miss: the final check command, test conventions,
generated-artifact handling during a restack, commands that need approval, and file-deletion
quirks. Point to AGENTS.md rather than copying it.>

## Stack workflow

This plan is delivered with the ship-stack skill:

- Build bottom-up, one layer at a time.
- Each layer is implemented by its own Workflow, which stops at the commit.
- The manager session owns every `gh stack` operation, submits with
  `gh stack submit --auto --open`, and drives each PR to green with babysit.
- Start the next layer only when the current PR and every lower PR are green on their head commits.
- Never merge.

## The stack

Bottom (merges first) to top.

| # | Branch | Items | Main files |
|---|---|---|---|
| 1 | `<type>/<short-name>` | <item ids> | <files> |

<Why the order is what it is, when it isn't obvious; for example, "layer 1 sits below layer 3
because both edit X".>

### Layer 1: `<branch>`

- **<Item id>.** <The problem and its evidence.>
  - Fix: <the smallest fix, with file and symbol.>
  - Verify first: <anything to check before coding.>
- **Failing tests first:** <test files and the cases that must fail before the fix.>
- **Out of scope:** <anything deliberately excluded.>

## Not in the stack

- <Item: why it is excluded, and what would unblock it.>

## Layer notes

The manager appends each workflow report here: the implementation and every finding-fix
workflow. Later layers, fix workflows, and resumed sessions read this to learn why the code is the
way it is.

### Layer 1: `<branch>`

- <YYYY-MM-DD, implementation workflow: commits `<sha>` "<subject>"; decisions; surprises; open
  questions.>

## Progress log

| # | Branch | PR | CI | Reviews | Status |
|---|---|---|---|---|---|
| 1 | `<branch>` | — | — | — | not started |
````

Use these status values: `not started`, `in progress`, `in review`, `green`, `blocked: <reason>`.
Mark a layer `green` only when it passes the ship-stack gate.
