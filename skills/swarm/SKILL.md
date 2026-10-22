---
name: swarm
description: "Fan out N parallel workers, drain them, and return one report. Use for /swarm, 'swarm this', or parallel coverage, races, gauntlets, and exploration."
disable-model-invocation: true
---

# Swarm

## Start

Open a todolist with one entry per phase before launching anything.

1. Frame
2. Fan out
3. Aggregate
4. Report

## Phase A: Frame

1. State the done predicate and the artifact or report the swarm must return.
2. Choose the shape. Partition into slices, race N workers on identical briefs, or mix both. For a race or mixed shape, declare `first pass`, `rank all`, or `best-of` before spawning.
3. Workers run on the parent model. For a model race, use one worker per arm of the three-model panel in the **arena** skill (`../arena/SKILL.md`), section "The three-model panel", and name each arm's model up front. Set each worker's reasoning effort for its job where the host's subagent tool takes one (`effort` on the Agent tool in Claude Code). Start at `xhigh`, raise it to `max` for the hardest or subtlest slices, and lower it one or two steps for routine checks. A race arm keeps the effort the panel gives it.
4. Give each worker its own writable output when it writes. When workers verify or measure commits, each brief names the exact SHAs. A measurement brief also names the method (sample count, what one sample is, order). The worker records both in its result.

## Phase B: Fan out

Launch all N workers at once, native workers as background general-purpose subagents with the host's subagent tool (the Agent tool in Claude Code) and other panel arms as background commands. A worker that writes or checks out commits gets its own git worktree (`isolation: "worktree"` in Claude Code), and its brief names the branch or commit to check out. A worker that only reads runs in the current checkout.

Every brief stands alone. Include the goal, scope, exact slice or race arm, how to verify, and what to report. Reports use `PASS`, `ISSUES`, or `BLOCKED` with evidence. A worker that can prove a defect reports `ISSUES` and lists every issue it can prove, not only the first.

If a worker drops out, proceed with N-1 and note it.

## Phase C: Aggregate

Read the terminal results. Drop a result that does not record the SHAs and method its brief names, and respawn that worker once. After a second miss, record a gap. A gap does not count as a pass. For coverage, every required slice needs a result. For a race, apply the selection rule declared up front. Use first pass, rank all, or best-of. Do not paste raw worker dumps.

## Phase D: Report

Return one consolidated in-chat report with the table, issue one-liners, gaps or dropouts, and the race rule when used.
