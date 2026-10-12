---
name: sync-pstack
description: Merge upstream pstack changes from cursor/plugins into the pstack skills ported in kenryu42/skills, port any new Cursor-specific text off Cursor, and open a PR. Use for the weekly pstack sync, when asked to sync or update the ported pstack skills, or to start tracking another pstack skill.
disable-model-invocation: true
---

# Sync pstack

`pstack.lock` at the root of the `kenryu42/skills` clone lists each ported pstack skill and the `cursor/plugins` commit it last synced with. A line with a third column tracks a single upstream file at another path, such as `no-comments/references/comment-sicko.md`, which is ported from `pstack/agents/comment-sicko.md`. `scripts/sync-pstack.sh` three-way merges every upstream change since that commit into `skills/<skill>/`. The merge base is upstream at the locked commit, so your port edits survive wherever upstream did not touch the same lines. You port what is left, following [`references/porting-guide.md`](references/porting-guide.md).

Run everything from the root of the `kenryu42/skills` clone. Paths to `scripts/` and `references/` are relative to this skill's directory.

## 1. Start clean

1. `git status --porcelain` must be empty. If it is not, stop and report.
2. `git switch main && git pull --ff-only origin main`.
3. `git switch -c sync-pstack/<YYYY-MM-DD>`.

## 2. Merge

Run `bash scripts/sync-pstack.sh`. It fetches upstream into `~/.cache/sync-pstack/plugins`, merges, updates `pstack.lock`, then checks the tracked skills for Cursor text and conflict markers. Its output lines:

- `up to date`: nothing changed upstream. Delete the branch and report that. Stop.
- `merged <skill>`, followed by the upstream commits that touched it.
- `updated` / `added` / `removed <skill>/<file>`: applied cleanly.
- `conflict <skill>/<file>`: both sides changed the same lines. The file has `<<<<<<< ours` / `>>>>>>> upstream` markers.
- `flag <skill>/<file>: <reason>`: a delete on one side against an edit on the other, a binary changed on both sides, or a skill gone upstream. Nothing was changed.
- `cursor <skill>/<file>:<line>: <text>` and `marker ...`: what the check found.

Exit 0 means the merge was clean and the check passed. Exit 1 means something needs you.

## 3. Port

Work through every merged skill, not just the ones with conflicts:

1. Read the guide, then read the upstream diff for the skill: `git -C ~/.cache/sync-pstack/plugins diff <old-lock-sha> <new-lock-sha> -- pstack/skills/<skill>` (old SHA from `git diff pstack.lock`).
2. Resolve each conflict by porting the upstream side per the guide. Keep a port edit only where the guide says to.
3. For each `flag`, decide and say why in the PR. A skill gone upstream stays here, and its lock line stays put until the user decides.
4. Review the clean hunks too. A clean merge can still bring in a bare skill reference without a `../<skill>/SKILL.md` path, a reference to a skill not installed here, or Cursor wording the check's patterns miss.
5. Keep `agents/openai.yaml` and the skill's README entry in step if upstream changed the skill's purpose or `disable-model-invocation`.

Rerun `bash scripts/sync-pstack.sh check` until it exits 0. Never weaken its patterns to get a pass. If it flags a legitimate non-Cursor use, reword the text.

## 4. Ship

1. Commit with a conventional message: `chore(skills): sync pstack to <short-sha>`.
2. `git push -u origin HEAD`, then open the PR with the REST API: `gh api repos/kenryu42/skills/pulls -f head=<branch> -f base=main -f title='chore(skills): sync pstack to <short-sha>' -f body='…'`.
3. The body lists each merged skill with its upstream commits, every conflict and flag with how you resolved it, and any upstream change you left out because it only makes sense in Cursor.
4. Never merge the PR. The user reviews it, then runs `npx skills update` to install the result.

## Tracking another pstack skill

`bash scripts/sync-pstack.sh add <skill>` copies the skill at upstream HEAD into `skills/<skill>/` and adds its lock line. Then port it end to end per the guide, add `agents/openai.yaml`, `LICENSE.txt`, and a README entry ending "Adapted from pstack.", and ship it as in step 4 with a `feat(<skill>):` message.

## Testing the script

`bash scripts/sync-pstack.test.sh` runs the script against throwaway git repos and never touches the network or this clone.
