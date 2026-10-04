---
name: pr
description: Review GitHub pull requests with structured issue and code analysis
disable-model-invocation: true
---

# PR review

Review the GitHub pull request URL or URLs specified in `$ARGUMENTS`.

For each pull request:

1. Read the full pull request, including the description, all comments, all commits, and all changed files.
2. Identify any linked issues referenced in the body, comments, commit messages, or cross links. Read each issue in full, including all comments.
3. Analyze the diff without checking out or switching to the pull request branch.
   - Use `gh pr diff`, `gh pr view`, `gh api`, and local main-branch files.
   - If pull request file contents are needed, use fetched refs with `git show <ref>:<path>` or temporary files. Do not fetch file blobs unless a file is missing on main or the diff context is insufficient.
   - Read all relevant code files in full. Do not truncate them. Compare them against the diff.
   - Include related code paths outside the diff that are required to validate behavior.
4. Provide a structured review with these sections:
   - What it does: one short paragraph describing the change and its intent.
   - Good: solid choices or improvements.
   - Bad: concrete issues, regressions, missing tests, or risks.
   - Ugly: subtle or high-impact problems.
   - Tests: what is covered, what is missing, and whether existing tests are adequate.
   - Open questions for you: only things blocking a merge decision that need the user's input. Omit the section entirely if there are none.

Output format per pull request:

```text
PR: <url>
What it does:
- ...
Good:
- ...
Bad:
- ...
Ugly:
- ...
Tests:
- ...
Open questions for you:
- ...
```

If no issues are found, say so under Bad and Ugly.

Do not implement changes unless the user explicitly asks. Review only.
