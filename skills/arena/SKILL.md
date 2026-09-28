---
name: arena
description: "Spawn N parallel candidates at the same task, pick a base, graft the strongest parts of the losers into it. Use for /arena, 'arena this', 'throw it in the arena', or when one attempt at a non-trivial artifact would lock in the wrong shape."
disable-model-invocation: true
---

# Arena

Fan out N parallel attempts at the same task. Read every candidate end to end. Pick the strongest as the base. Graft the best ideas from the others into it. Verify the synthesized result.

## Start

Open a todolist with one entry per phase before launching anything.

1. Frame
2. Fan out
3. Cross-judge
4. Pick
5. Graft
6. Verify

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

The N candidates will receive the same prompt, so the prompt is the contract.

1. State the artifact each candidate is producing.
2. Derive the rubric. State what success looks like for *this* task, then turn it into 3-6 concrete gradeable criteria. The rubric is the picker's tool in Phase D. Candidates only see the task.
3. Pick the runners. Default to one runner per arm of the three-model panel below. Spawn more when the arena covers multiple design directions. Same model N times when the work is generation-bound rather than judgment-sensitive.
4. Assign output paths. Each candidate writes to its own location (a git worktree where possible, otherwise `candidate-<n>/` under a fresh `mktemp -d` directory), per the **principle-separate-before-serializing-shared-state** skill (`../principle-separate-before-serializing-shared-state/SKILL.md`).

## Phase B: Fan out

Launch all N runners at once, native arms as background subagents and CLI arms as background commands, each with the task, the path to the shared grounding, its own output path, and instructions to produce both the artifact and a short rationale.

Each rationale names the alternatives the candidate considered and what it rejected.

If a candidate fails to produce output, proceed with N-1 and note the dropout in the synthesis record.

## Phase C: Cross-judge

After all Phase B candidates complete, choose one panel arm from a different model family than the parent's: arm C from Claude Code, arm A from Codex. Run one read-only judge on that arm. It sees the rubric and the candidates by path label, scores each criterion, and recommends a base with rationale. It runs in parallel with the parent's reading in Phase D, not with the candidates themselves. Don't spawn the judge while candidates are still writing.

## Phase D: Pick a base

Read every candidate end to end before picking.

Score each candidate against the rubric criterion by criterion, not on holistic feel. Compare against the cross-judge. Agreement on the base confirms the pick. Disagreement means one of you is biased or the rubric was ambiguous. Read both rationales before deciding.

Pick the base on which candidate a future maintainer can extend most easily without breaking invariants. Prefer the cleaner boundary or smaller API when two feel tied, per the **principle-laziness-protocol** skill (`../principle-laziness-protocol/SKILL.md`).

Record the pick and the reason in a short synthesis note alongside the base artifact, including the cross-judge's verdict.

## Phase E: Graft

Walk each losing candidate once more and identify what is worth porting into the base. The signal is usually one or two things per candidate, not most of it.

Fold each graft in by hand, per the **principle-redesign-from-first-principles** skill (`../principle-redesign-from-first-principles/SKILL.md`). Don't paste mechanically. The result has to remain coherent under one mental model.

Record what was grafted, from which candidate, and what was rejected and why.

When N candidates converge on the same shape, that is a strong agreement signal. Note the convergence in the record and ship the consensus shape. No graft is needed. When N candidates wildly diverge, Phase A was under-specified. Reframe and re-run rather than averaging the divergence.

## Phase F: Verify

The synthesized artifact has to hold up under the same scrutiny as any other output, per the **principle-prove-it-works** skill (`../principle-prove-it-works/SKILL.md`).

If verification surfaces a problem the arena did not catch, either Phase A was wrong (re-frame and re-run) or one candidate caught it and you missed the graft (go back to Phase E). Don't paper over.

## Outputs

One synthesized artifact. One short synthesis note alongside, naming the base, the grafts (with source candidate), the rejections, the dropouts if any, and the verification result.
