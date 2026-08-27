---
name: is
description: Analyze GitHub issues (bugs or feature requests)
disable-model-invocation: true
---

# Issue study

Analyze the GitHub issue or issues specified in `$ARGUMENTS`.

For each issue:

1. Add the `inprogress` label to the issue via GitHub CLI. If this action fails, report that explicitly and continue.
2. Read the full issue, including all comments and linked issues or pull requests.
3. Do not trust analysis in the issue. Verify the behavior independently and derive the analysis from the code and execution path.

For a bug:

- Ignore root cause analysis in the issue. It is likely wrong.
- Read all related code files in full. Do not truncate them.
- Trace the code path and identify the actual root cause.
- Propose a fix.

For a feature request:

- Do not trust implementation proposals in the issue without verification.
- Read all related code files in full. Do not truncate them.
- Propose the most concise implementation approach.
- List the affected files and required changes.

Do not implement changes unless the user explicitly asks. Analyze and propose only.
