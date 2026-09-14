---
name: blast-radius
description: "Find what a change could break somewhere else before it ships, beyond the diff, and prove the one fact it's safe because of by running real code instead of writing it up. Use for 'blast radius of X', 'what could this break', or reviewing a small diff you don't trust."
disable-model-invocation: true
---

# Blast radius

Find what a change breaks somewhere else, before it ships. Use for "blast radius of X", "what could this break", or reviewing a small diff you don't trust yet.

Listing the callers is not the job. The agent can grep those in a second. The job is the breakage grep won't show you.

## Don't trust your own writeup

A blast-radius writeup that sounds right is worthless. It reads as convincing whether or not it's true, and that is the trap you are walking into. So don't hand back the writeup. Find the one or two facts the whole thing depends on and prove them by running code. Words are where you start, not what you ship.

### How sure are you

For each fact the change's safety depends on, get it as far down this list as is cheap, and say where it stopped.

1. You said so. Worthless on its own.
2. You pointed at the line. A real `file:line`, or the library's own source.
3. You showed the bad case can't happen. You walked the failure step by step and it doesn't reach.
4. You ran it. A script or test that calls the real code and fails loud if you're wrong.
5. You reproduced it in the running app.

Any safety fact you can't get to step 4, say so out loud. Don't write it up as settled. Step 4 is usually one small script that imports the same library the app ships and calls the exact function you're worried about.

## Steps

1. Read the change. The diff, the symbols it adds, changes, and deletes, and what it now does differently, including the part the diff doesn't spell out. Pull the surrounding intent from the PR and commits: `gh pr view --json title,body,comments`, `git log -p <base>..HEAD`, `git blame` on the lines that changed.
2. Find the one fact it's safe because of. Most changes that look scary are safe because of a single fact, like "this call only drops already-dead cache entries and does nothing else". Find that fact. If it holds, most of the scary cases die at once. Spend your time here, not on a long list of maybes.
3. Look where grep stops. Read the source of the library you call, and check its pinned version and any local patch. Work out when things run: async scheduling, teardown and cleanup order, lifecycle differences between one framework version or runtime and another. Follow what a symbol search misses: the JSON an API returns, a DB column, a wire format, another language reading the same bytes, a feature flag, code three hops downstream.
4. Be honest about each risk. Give it a real chance of happening and a real cost if it does. Keep the risks you confirmed; list the ones you checked and cleared separately. Separate what you found and cited from what you are inferring from what you don't know, and say which is which. Cite a real `file:line`, a search that finds nothing is still an answer, and never make up a caller or an API.
5. Prove the one fact. Write a script or test that runs the real code, run it, and paste what happened. If you can't prove it cheaply, mark it unproven. Don't round up.
6. For a big or wide change, run the three-model panel below and merge the answers before you write anything up.

## The three-model panel

Different models catch different real bugs. A risk all three name is real. A risk one names is a lead you go check yourself, not a finding.

The panel is fixed:

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

Write the brief to one file first and feed the same bytes to every arm. The panel means nothing unless the arms answered the same question. Give each arm its own output path; two arms writing one file is shared mutable state.

**From Claude Code** (`HOST=claude`) arms A and B are native — spawn them as parallel subagents on `fable` and `opus`. Reach arm C with:

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

Both forms run their arm read-only, so an arm can read code and run existing commands but cannot write a proof script. That is deliberate. The panel produces hypotheses; step 5 is still yours. If you want an arm to prove its own claim, give it `-s workspace-write` (Codex) or drop the `--disallowed-tools` guard (Claude), and give that arm its own git worktree.

If a CLI is missing or an arm exits non-zero, proceed with the arms you have and name the dropout in the writeup. Never fill a dropped arm's slot by guessing what it would have said.

## What to hand back

- **What it does.** What changed, including the part that isn't obvious.
- **The one fact it's safe because of.** State it, say which step you got it to, and show the proof. If you couldn't prove it, write unproven.
- **Risks.** Only the real ones. Each names how it breaks, the `file:line`, how likely and how bad, and how to check. Paste the proof for the ones that matter.
- **Cleared.** What you checked and why it's fine.
- **Before you merge.** The cheapest test or repro that catches the real bug, including the script you wrote.

If you ran the panel, say which arms ran, which dropped out, and which risks all three agreed on.

Write it through `unslop`, cite real code, and strip anything private before it goes anywhere public.

**Reply:** the writeup above, with the one safety fact either proven or marked unproven.
