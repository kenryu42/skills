---
name: arena
description: "Spawn N parallel candidates at the same task, pick a base, graft the strongest parts of the losers into it. Use for /arena, 'arena this', 'throw it in the arena', or when one attempt at a non-trivial artifact would lock in the wrong shape."
disable-model-invocation: true
---

# Arena

Open a todolist with one entry per phase (Frame, Fan out, Cross-judge, Pick, Graft, Verify) before launching anything.

## The three-model panel

Different models catch different real problems. The panel is fixed:

| Arm | Model | Effort |
| --- | --- | --- |
| A | `claude-fable-5.1` | `max` |
| B | `claude-opus-5` | `xhigh` |
| C | `gpt-6-astra` | `max` |

Run the arm you already are natively. Reach the other two through their CLI.

```sh
if   [ -n "$CLAUDECODE" ];       then HOST=claude
elif [ -n "$CODEX_SESSION_ID" ]; then HOST=codex
else                                  HOST=unknown; fi
```

Write the brief to one file first and feed the same bytes to every arm. Give each arm its own output path.

**From Claude Code** (`HOST=claude`) arms A and B are native. Spawn them as parallel subagents with the Agent tool on `fable` and `opus`. Reach arm C with:

```sh
codex exec -s read-only --skip-git-repo-check \
  -c model=gpt-6-astra -c model_reasoning_effort=max \
  -o "$OUT/arm-c.md" - < "$OUT/brief.md"
```

**From Codex** (`HOST=codex`) arm C is native. Reach arms A and B with:

```sh
claude -p --model claude-fable-5.1 --effort max \
  --disallowed-tools "Edit Write NotebookEdit" \
  --output-format text < "$OUT/brief.md" > "$OUT/arm-a.md"

claude -p --model claude-opus-5 --effort xhigh \
  --disallowed-tools "Edit Write NotebookEdit" \
  --output-format text < "$OUT/brief.md" > "$OUT/arm-b.md"
```

When `HOST=unknown`, run all three arms through the CLIs.

These forms are read-only. An arm that must write its artifact gets its own git worktree and a writable form: `-s workspace-write -C "$WORKTREE"` for Codex, and for Claude drop the `--disallowed-tools` guard, add `--permission-mode acceptEdits`, and run it from the worktree. A native Claude Code arm that writes takes `isolation: "worktree"`.

If a CLI is missing or an arm exits non-zero, proceed with the arms you have and name the dropout. Never fill a dropped arm's slot by guessing what it would have said.

## Phase A: Frame

Every candidate gets the same prompt, so the prompt is the contract.

1. State the artifact each candidate is producing.
2. Derive the rubric, 3-6 concrete gradeable criteria for what success looks like on *this* task. It is the picker's tool in Phase D. Candidates only see the task.
3. Pick the runners. Default to one runner per arm of the three-model panel above. Spawn more runners when the arena covers multiple design directions. Use the same model N times when the work is generation-bound rather than judgment-sensitive.
4. Give each candidate its own output location (a git worktree where possible, otherwise `candidate-<n>/` under a fresh `mktemp -d` directory), per the **principle-separate-before-serializing-shared-state** skill (`../principle-separate-before-serializing-shared-state/SKILL.md`).

## Phase B: Fan out

Launch all N runners at once, native arms as background subagents and CLI arms as background commands, each with the task, the path to the shared grounding, its own output path, and instructions to produce the artifact and a short rationale naming the alternatives it considered and what it rejected.

If a candidate produces no output, proceed with N-1 and note the dropout.

## Phase C: Cross-judge

After all Phase B candidates complete, choose one panel arm from a different model family than the parent's: arm C from Claude Code, arm A from Codex. Run one read-only judge on that arm. It sees the rubric and the candidates by path label, scores each criterion, and recommends a base with rationale. It runs in parallel with your reading in Phase D. Don't spawn it while candidates are still writing.

## Phase D: Pick a base

Read every candidate end to end before picking. Score each against the rubric criterion by criterion, not on holistic feel, and compare with the cross-judge. Agreement on the base confirms the pick. Disagreement means one of you is biased or the rubric was ambiguous. Read both rationales before deciding.

Pick the base a future maintainer can extend most easily without breaking invariants. When two feel tied, prefer the cleaner boundary or smaller API, per the **principle-laziness-protocol** skill (`../principle-laziness-protocol/SKILL.md`).

## Phase E: Graft

Walk each losing candidate once more for what is worth porting into the base, usually one or two things per candidate, not most of it. Fold each graft in by hand, per the **principle-redesign-from-first-principles** skill (`../principle-redesign-from-first-principles/SKILL.md`), so the result stays coherent under one mental model. Don't paste mechanically.

If the candidates converge on one shape, that is strong agreement. Note it and ship the consensus shape. No graft is needed. If they wildly diverge, Phase A was under-specified. Reframe and re-run rather than averaging the divergence.

## Phase F: Verify

Verify the synthesized artifact like any other output, per the **principle-prove-it-works** skill (`../principle-prove-it-works/SKILL.md`). If verification finds a problem the arena missed, either Phase A was wrong (re-frame and re-run) or a candidate caught it and you missed the graft (go back to Phase E). Don't paper over it.

## Outputs

One synthesized artifact, with a short synthesis note alongside naming the base and why, the cross-judge's verdict, each graft with its source candidate, what was rejected and why, any convergence or dropouts, and the verification result.
