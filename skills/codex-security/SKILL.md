---
name: codex-security
description: "Use when the user asks for Codex Security workflows: repository-wide or scoped-path security scans, security reviews of pull requests, commits, branch diffs, or working-tree patches, deep exhaustive repository scans, fixing validated or plausible security findings, or individual threat-modeling, finding-discovery, validation, and attack-path-analysis phases."
---

# Codex Security

Use this as the single local entrypoint for Codex Security. Keep the public invocation as `$codex-security`; the upstream multi-skill workflows are packaged here as internal references.

## Route The Request

Choose exactly one mode reference first:

- Repository-wide or scoped-path security scan: `references/modes/security-scan.md`
- Pull request, commit, branch diff, working-tree patch, or other Git-backed change-set review: `references/modes/security-diff-scan.md`
- Deep, exhaustive, multi-pass, variance-reducing repository-wide scan: `references/modes/deep-security-scan.md`
- Fix and verify a validated or plausible security finding: `references/modes/fix-finding.md`

For explicit phase-only requests, read only the requested phase reference:

- Threat modeling: `references/phases/threat-model.md`
- Finding discovery: `references/phases/finding-discovery.md`
- Validation: `references/phases/validation.md`
- Attack-path analysis: `references/phases/attack-path-analysis.md`

Do not read every mode up front. Load the selected mode, then load phase and supporting references only when that mode requires them.

## Shared Resources

Use these local resources when the selected mode asks for them:

- Artifact paths: `references/scan-artifacts.md`
- Shared hard rules: `references/shared-hard-rules.md`
- Final report format and HTML rendering: `references/final-report.md`
- Scoped worklist and candidate ledger rules: `references/scan-artifacts-and-ledger.md`
- Worklist helper: `scripts/generate_rank_input.py`
- Report validator: `scripts/validate_report_format.py`
- HTML renderer: `scripts/render_report_html.py`
- HTML template: `assets/report_template_inlined.html`

Treat `<plugin_dir>` in references as this skill directory, `skills/codex-security`.

## Hard Rules

- Keep scan phases separate and in the order required by the selected mode.
- Preserve candidate, file, coverage, validation, and attack-path receipts required by `references/scan-artifacts.md`.
- Use the local artifact root from `references/scan-artifacts.md`; do not fall back to the upstream `/tmp` default.
- Do not emit a finding unless it survives the selected workflow's validation, attack-path, and final policy gates.
- When a selected reference conflicts with this file about public skill names or resource paths, this file wins.
