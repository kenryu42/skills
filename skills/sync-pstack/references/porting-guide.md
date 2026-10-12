# Porting pstack text off Cursor

The ported skills run in Claude Code and Codex (and sometimes Amp). No Cursor-specific text survives a port: no Cursor tools, settings, paths, model slugs, or the word "Cursor". These rules come from the ports already in this repo. When upstream text changes, port the new text the way the old text was ported; read the current port of the same skill first and match it.

## Subagents

| Upstream | Port |
| --- | --- |
| `Task` tool, `subagent_type: generalPurpose`, "Task subagent" | "Spawn subagents with the host's subagent tool (the Agent tool in Claude Code)" |
| `readonly: true` | Tell the subagent not to edit files. |
| `readonly: false` because the subagent needs MCP access | A subagent type that keeps MCP tools (`general-purpose` in Claude Code), told not to edit files. |
| A named custom subagent such as `"Comment Sicko"` | One line per host: the Agent tool with the Claude Code agent name, Codex `spawn_agent` with its `agent_type`, otherwise a general-purpose subagent given the prompt file. See `no-comments`. |
| `run_in_background: true`, "in one message" | "Launch all at once", native arms as background subagents, CLI arms as background commands. |

## Models

Upstream reads model slugs from the `pstack-models.mdc` rule (`~/.cursor/rules/pstack-models.mdc`) and falls back to Cursor slugs such as `claude-opus-5-5-max` or `grok-4.7-xhigh-fast`. Drop all of it: the rule, the role lines, the default tables, and the "if the Task tool rejects a slug" fallbacks.

- A single-model role (explorer, explainer, investigator, synthesizer) runs on the parent model.
- A multi-model role (arena runners, architect runners, the cross-judge, interrogate reviewers, a cross-model reviewer) uses the fixed three-model panel in `arena`, section "The three-model panel". Refer to it by arm (A, B, C) and do not copy the CLI commands into another skill. `blast-radius` is the one exception: it carries its own copy of the panel.
- Cross-family choices: arm C when a Claude model did the work or is the parent, arm A when a GPT model did.
- When an arm drops out, proceed with the others and name the dropout.

## Skill references

Upstream names sibling skills in bold or as slash commands. Every reference gets a path so a host without a skill registry can follow it:

- `the **how** skill` or `/how` becomes `the **how** skill (`../how/SKILL.md`)`.
- Principle skills use their full directory name: `the **prove-it-works** principle skill` becomes `the **principle-prove-it-works** skill (`../principle-prove-it-works/SKILL.md`)`.
- Text in a prompt file handed to a subagent (`references/*-prompt.md`) uses `<skills-dir>/<skill>/SKILL.md`, and the prompt's intro says the orchestrator fills in `<skills-dir>`.
- A reference to a skill that is not installed here (`/recall`, `/setup-pstack`, `/poteto-mode`) gets cut, or replaced by the steps it stood for.

## Cursor environment

| Upstream | Port |
| --- | --- |
| `.cursor/skills/<x>` | `.agents/skills/<x>`, plus a `.claude/skills/<x>` symlink to `../../.agents/skills/<x>` where the skill creates one. The script rewrites the path; add the symlink step by hand. |
| `.cursor/rules/*.mdc` | Cut. Put any rule the skill needs into the skill body. |
| Transcripts under `~/.cursor/projects/*/agent-transcripts/` | The per-host table in `show-me-your-work`: Codex `CODEX_THREAD_ID`, Amp `AI_AGENT=amp`, Claude Code `CLAUDE_CODE_SESSION_ID`. Never glob other sessions. |
| "List the MCPs from the Cursor environment", the `mcps/` directory | "List the MCP servers available in this session. Their tools appear in your tool list, named `mcp__<server>__<tool>` in Claude Code." |
| "cursor location", Ask mode, Agent mode, worktree or Best-of-N UI | The host-neutral meaning: "the current selection", a read-only subagent, a writable one, `git worktree`. |
| `/tmp/<name>/` | A fresh `mktemp -d` directory. |
| Framework names the user does not use (for example "Solid versus React") | The general rule the example illustrates. |

## Files the port adds

Upstream never ships these, so a sync never touches them. Keep them in step with the skill:

- `agents/openai.yaml`: Codex metadata (`display_name`, `short_description`, and `allow_implicit_invocation: false` when the frontmatter sets `disable-model-invocation: true`).
- `LICENSE.txt`: pstack's MIT license.
- Anything under `references/` or `scripts/` that replaces a Cursor-only feature, such as `no-comments/scripts/install-agents.sh`.

## When upstream rewrites a section you ported

Port the new upstream wording; do not keep your old port of the old wording. Upstream changes are the reason to sync. Keep a port edit only where it removes something Cursor-specific or encodes a deliberate local choice (the fixed panel, the `.agents` layout). If an upstream change only makes sense inside Cursor, leave the port as it was and say so in the PR.
