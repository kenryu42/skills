---
name: cleanup-comments
description: Plan the removal of a project's existing code comments, in any language. Classifies every comment with Comment Sicko, prepares a strip patch proven by a parser-based check to change no code, and writes a ship-stack plan for the strip and for the code the deleted comments were excusing. Stops at the plan for the user's approval.
disable-model-invocation: true
---

# Cleanup comments

Turn a project full of existing comments into an approved plan for a `ship-stack` delivery. Layer 1 strips the comments. The layers above it make the code say what the deleted comments said.

This skill only plans. It never changes the project's checkout, branches, or remote. All stripping happens in a temporary worktree, and the plan stops for approval before any PR exists.

Sibling skills live next to this one. `../no-comments/SKILL.md` means the `no-comments` skill in the directory that holds this skill. Read a sibling's `SKILL.md` and follow the step it names. `ship-stack` is a Claude Code skill; follow its plan rules and template.

## Before you start

- The working tree must be clean on an up-to-date trunk. Record the trunk commit as the plan's baseline.
- Find the project's check command from its agent instructions, package scripts, or CI, and the languages it contains. Only a language you can inventory (step 1) and verify (step 3) is in scope; report the rest.
- If the project has a comment lint, such as the `lint:comments` script `setup-code-quality` installs, it drives the inventory and layer 1 must turn it green. In a workspace, run it from the package directory that owns it.
- For a TypeScript project without a comment lint, stop and recommend running `setup-code-quality` (`../setup-code-quality/SKILL.md`) and merging it first. Its lint gives layer 1 a green target, its `fix-comment-lint` skill checks the agents fixing later layers, it translates old suppressions once instead of after the strip, and it installs the `oxc-parser` the verifier needs. Its `check` fails on the existing comments until layer 1 lands, so run this skill right after it merges, or hold its merge until layer 1 is ready. If the user declines, continue without a lint.
- Choose the plan file's location by `ship-stack`'s rule. Keep the inventory, the strip patch, and any verifier you write next to it, untracked.
- Keep the laptop responsive: at most 3 Comment Sicko batches at once, and run installs, checks, and scans under `nice -n 15`.

## 1. Take the inventory

With a comment lint, run it and save its output next to the plan. If it passes, there is nothing to clean up: stop. If it reports a file it cannot parse, stop and report the file, because the lint misses comments after a syntax error.

Without one, list comments with ast-grep for each language:

1. Find the grammar's comment node kinds: `ast-grep run --debug-query=cst -p '<a snippet with a line and a block comment>' --lang <language>`. They differ by grammar, such as `comment` in Python and Go, and `line_comment` and `block_comment` in Rust.
2. Run `ast-grep scan --inline-rules '<rule matching those kinds>' --json=stream` from the repository root. It skips files `.gitignore` covers.
3. Drop committed generated and vendored files, using what the project's linters and formatters already exclude.
4. Save the file, line, and text of every comment next to the plan.

For a language ast-grep does not support, list comments with the language's own parser or tokenizer. If neither exists, leave the language out of scope.

## 2. Strip in a temporary worktree

1. Create a detached worktree at the baseline in a temporary directory outside the checkout, and install dependencies there with the project's frozen lockfile so its checks run.
2. Split the inventory's files into batches of about 10 to 20 files, grouped by directory so related code stays together. Batches never share a file.
3. For each batch, spawn Comment Sicko as step 1 of `../no-comments/SKILL.md` describes, scoped to the batch's files by their paths in the worktree.
4. Audit each report as step 2 of `../no-comments/SKILL.md` describes. Do not run its later steps: fixes belong in the stack.
5. From the accepted reports, collect every `MUST KILL` flag (file, symbol, one-line reason) and every keep with the exception that saved it.

## 3. Prove the strip changes no code

Each language in scope needs a verifier that compares every changed file with the baseline using the language's own parser, and fails on:

- any change to the syntax tree, ignoring positions and comments, and normalizing only syntax that compiles the same way (such as JSX whitespace);
- any added or edited comment, except a reduction to an allowed directive form that the comment already contained;
- added, deleted, or renamed files, and changes to files not in the language;
- a deleted comment that a tool reads: lint and type suppressions, compiler or build directives, coverage and formatter pragmas, bundler or code-generation markers, and license markers.

For JavaScript and TypeScript, use `node <this skill's directory>/scripts/verify-strip-js.mjs <baseline>` from the package directory in the worktree. It resolves `oxc-parser` from the project, as `setup-code-quality` installs it. If the project lacks it, install it in a scratch directory outside the worktree and run the verifier with `NODE_PATH=<scratch>/node_modules`, so the project's manifest and lockfile stay untouched.

For another language, write the verifier before you strip, and save it next to the plan. Build it on the language's parser, such as Python's `ast` module, which drops comments, or Go's `go/parser` without `ParseComments`. Research the language's tool-read comments, such as Python's `# type: ignore`, `# noqa`, and `# pragma: no cover`, or Go's `//go:` directives, `//nolint`, and cgo preambles. Where comments are part of the tree, such as Rust's `///` doc comments, deleting them is a code change the verifier must report. Before trusting it, run it on a small hand-made strip: a pure deletion passes, and a code change, an added comment, and a deleted tool-read comment each fail. If the language has no usable parser, require identical build output instead. If neither is possible, leave the language out of scope rather than strip it unproven.

Run the verifiers. Restore each listed comment exactly as the baseline has it (`git show <baseline>:<path>`), and revert any code edit. Never change code to satisfy a verifier. Format the changed files with the project's formatter, then rerun until every verifier passes.

A restored suppression that Comment Sicko condemned stays in layer 1 and becomes a `MUST KILL` item: its layer fixes the code, then removes the suppression.

## 4. Make layer 1 green

With a comment lint, run it in the worktree. Every comment it still reports gets an exact entry in its allowlist, such as the `allow` map in `scripts/comment-lint.json`, in one of two classes:

- **Keep:** a comment Comment Sicko kept with a proven exception, such as a fact about an external tool, a license header, or a published package's public API docs. It is permanent once the maintainer approves it.
- **Temporary:** a tool-read comment the lint rejects that a `MUST KILL` layer will remove, such as a suppression without a reason. The layer that removes the comment deletes its entry, and the lint's stale-entry check fails until it does.

Without a comment lint, kept comments stay in place and need no entries.

Run the project's check command in the worktree. Fix anything the strip caused by restoring comments, never by editing code. Record failures that already exist at the baseline in the plan.

Commit the worktree's changes on its detached HEAD with a Conventional Commit message, such as `chore: strip comments that narrate or excuse code`. Save the commit as `strip.patch` next to the plan with `git format-patch -1 --stdout`. Then remove the worktree with `git worktree remove`, without `--force`.

## 5. Write the plan

Write the plan with `ship-stack`'s template.

- **Layer 1, `chore/strip-comments`:** apply `strip.patch` with `git am`. Spec: every verifier passes against the layer's base, with the command to run each; the comment lint, if any, and the check command pass. If trunk moved past the baseline and `git am` conflicts, redo steps 2 to 4 for the conflicting files from the new trunk. With a comment lint, list its allowlist entries in a table with their class and, for keeps, the exception and its source.
- **Layers 2 and up:** the `MUST KILL` items, split by `ship-stack`'s rules, with correctness and safety items (condemned suppressions, claimed constraints) at the bottom. Each item gives the file, the symbol, Comment Sicko's reason, and the deleted comment's text. Each gets the smallest fix for its kind:
  - surprising behavior in the project's own code: a clearer name, a named value, or a type;
  - a claimed constraint: check it with `git blame`, `git log -S`, and the tests, then pin it with a test that fails when the workaround is removed, before the workaround changes;
  - a fact about an external tool: the PR description, with its source.

  Behavior-preserving items keep every existing test and assertion unchanged. Name the temporary allowlist entries each layer deletes.
- **Working rules:** agents add no comments, follow the repository's comment-fix skill if it has one (such as `.agents/skills/fix-comment-lint/SKILL.md`), and recover a deleted comment's text with `git show <layer 1 commit> -- <file>`.
- **Open decisions:** approval of the permanent allowlist entries, with a recommended default for each; any `MUST KILL` item that needs a design choice; and, without a comment lint, adding one so comments do not return, through `setup-code-quality` for TypeScript. Items that cannot be fixed yet go under "Not in the stack".

## 6. Stop for approval

Report:

- per language, the inventory count, comments deleted, comments reduced to a directive, and keep and temporary entries;
- each verifier used, and whether it is bundled or written for this run;
- the languages left out of scope, and why;
- the layer table;
- the open decisions;
- existing check failures at the baseline;
- the paths of the plan, inventory, verifiers, and `strip.patch`.

Then stop. After the user approves, deliver the plan with `ship-stack`, starting at its Phase 2.
