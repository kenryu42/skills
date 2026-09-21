# Personal Skills

## Skills

### `autoreview`

Runs a second-model code review using Codex (`gpt-5.6-sol`) by default, with optional Claude (`claude-fable-5`) review. Pair with `behavior-validator` for user-visible behavior checks.

### `babysit`

Watches an open pull request, fixes failing CI checks, waits out review bots (CodeRabbit, Greptile, Pullfrog), handles straightforward findings, and drives the PR to a mergeable state without re-prompting.

### `blast-radius`

Finds what a change breaks beyond its own diff, then proves the one fact its safety rests on by running real code instead of writing it up. Grades every safety claim on a five-step evidence ladder and marks anything it could not run as unproven. Wide changes go through a fixed three-model panel (`claude-fable-5`, `claude-opus-5`, `gpt-5.6-sol`) that runs the host's own model natively and reaches the rest through the Claude or Codex CLI.

### `bro`

Restates the last message in plain human language without jargon.

### `codex-first`

Delegates substantial implementation, fixing, code exploration, rebasing, and PR landing to Codex CLI while Claude handles design, decisions, review, and verification.

### `codex-security`

Runs Codex Security scan, diff-scan, deep-scan, finding-fix, and phase workflows for repository-wide or scoped-path security reviews.

### `commit-with-context`

Creates coherent Conventional Commits with concise, high-signal context. Scopes the commit from the staged index and avoids splitting one task into artificial micro-commits.

### `create-verification-skill`

Generates a project-local verification skill that launches and drives the real app, captures evidence, and maps user-facing features for later verification.

### `grill-me`

Stress-tests plans and designs through relentless direct questioning until reaching shared understanding across every branch of the decision tree.

### `improve`

Surveys any codebase as a senior advisor and produces prioritized, self-contained implementation plans for other models/agents to execute. Strictly read-only on source code — never implements, fixes, or refactors anything itself.

### `is`

Analyzes GitHub issues (bugs or feature requests) by verifying actual behavior against the codebase and execution paths, proposing concise fixes or implementation plans without making premature changes.

### `maintain-verification-skill`

Keeps a project's verification skill and feature map current through source review and live verification of every mapped feature.

### `orchestrate`

Coordinates multiple agents on large-scope tasks by delegating substantive work, running narrow read-only scouts in parallel, maintaining distinct ownership, and integrating results while keeping approvals with the user.

### `repo-explorer`

Clones and inspects external repositories in a reusable local exploration cache (`~/.explore/repos`) without cluttering the active workspace.

### `release-notes`

Generates concise, evidence-based notes and updates only the body of the latest existing GitHub Release.

### `setup-code-quality`

Sets up or migrates compatible JavaScript and TypeScript projects to Oxlint, type-aware linting, Oxfmt, jscpd, Knip, and TypeScript 7 through a shared check script. Replaces overlapping tools while preserving unique checks and leaves incompatible projects unchanged.

### `sponsor-miner`

Finds and verifies potential sponsors for a GitHub repository by mining peer repos' sponsor data (README sponsor sections, sponsorkit SVGs, the GitHub Sponsors API, and Open Collective), ranking companies by cross-repo sponsorship frequency. Outputs `companies-ranked.csv` and a `verified-leads.csv` for outreach planning.

### `test-coverage-improver`

Runs coverage suites and adds direct, test-only quick wins until no eligible quick wins remain. Reports added tests and opportunities that require approval.

### `ultra`

Runs a Claude Code's manual multi-agent build workflow: Fable plans, investigates, designs, reviews, and makes decisions; Opus implements, fixes, and validates. A Fable review and Opus fix loop closes the workflow.
