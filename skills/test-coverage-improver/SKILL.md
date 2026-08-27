---
name: test-coverage-improver
description: 'Improve repository test coverage with autonomous, test-only quick wins. Run `bun run test:coverage`, inspect coverage artifacts, add tests that exercise real production behavior, repeat until no quick wins remain, validate the result, and report opportunities that require source changes, external APIs, mocks, or user approval.'
---

# Test Coverage Improver

## Objective

Improve test coverage with meaningful test-only changes. Continue without an approval phase until no quick wins remain.

Create or continue a goal for this coverage task. If no coverage goal is active, call `create_goal` with this objective: improve coverage with direct, test-only quick wins until none remain. Do not set a token budget unless the user gives one.

Call `update_goal` with `complete` only after no quick wins remain and all added tests and affected checks pass.

## Quick-win rules

A candidate is a quick win only when all these conditions are true:

- Change only tests or isolated test fixtures.
- Exercise a real production path.
- Assert an observable result, state, error, process effect, or cleanup.
- Run deterministically without an external API or network service.
- Require no production source change, new dependency, or broad environment setup.
- Add useful behavioral protection and a measured coverage improvement.

Do not add or expand mocks, stubs, or fakes. Do not write a test that only verifies a test double. Prefer real temporary files, repositories, subprocesses, parsers, and local services when they are safe and practical.

A console capture spy is permitted only as an observation mechanism. It must not change a return value or control the production path. Assert the captured output and restore the spy after the test.

Use isolated temporary directories for filesystem and log state. Never write tests to real user or project log locations.

## Workflow

1. Run `bun run test:coverage` from the repository root and record the baseline.
2. Use the console summary and `coverage/` artifacts to identify uncovered lines, branches, functions, and paths.
3. Rank candidates by behavioral value, risk, and test cost. Prioritize public APIs, shared utilities, recent fixes, error handling, boundary inputs, retries, timeouts, cancellation, and cleanup.
4. Select the best candidate that meets all quick-win rules.
5. Add one focused test or one small related test batch.
6. Run the focused test. Capture and assert expected console output so it does not leak to the test runner.
7. Run `bun run test:coverage` again. Confirm the improvement with covered counts when available, not only rounded percentages.
8. Repeat from candidate selection. Do not stop because one candidate is not eligible; defer it and inspect the other candidates.
9. Stop only when inspection shows that no remaining candidate meets all quick-win rules.
10. Run all affected checks and `bun run check` before completion.

Do not use a fixed iteration limit. The quick-win rules are the stop condition. Do not add low-value tests only to increase a percentage.

If a valid new test finds incorrect production behavior, do not change the source, weaken the test, delete the test, or skip the test. Stop the autonomous loop and report the source change that requires approval. Do not complete the goal while the required checks fail.

## Deferred opportunities

Defer a coverage opportunity when it requires one or more of these items:

- A production source or configuration change.
- A real external API, network service, account, credential, or paid resource.
- A new or expanded mock, stub, fake, or behavior-changing spy.
- A new dependency or large environment setup.
- A large output matrix or fixture that gives little behavioral value.

Do not stop to request approval during the autonomous pass. Record the opportunity and continue with other eligible candidates.

## Final report

Report these sections:

### Added tests

For each added test or test batch, report:

- Test file and test name.
- Real production behavior exercised.
- Observable result asserted.
- Uncovered path that the test now covers.
- Coverage before and after the change.
- Focused and aggregate validation commands and results.

### Deferred opportunities

For each deferred opportunity, report:

- Source file and uncovered behavior.
- Reason that it is not a quick win.
- Source change, external API, mock, dependency, or environment that it requires.
- Expected coverage or behavioral value. Mark an estimate as an estimate.
- The approval that is necessary before implementation.

Also report the final coverage summary and any remaining validation failure. Do not claim completion when the checks are not green.

## Notes

- If coverage artifacts are missing or stale, rerun `bun run test:coverage` instead of guessing.
- Do not create `scripts/`, `references/`, or `assets/` unless the workflow later needs them.
