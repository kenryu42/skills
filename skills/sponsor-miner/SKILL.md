---
name: sponsor-miner
description: "Find and verify potential sponsors for a GitHub repository by mining sponsor data from peer repos: README sponsor sections, sponsorkit SVGs, the GitHub Sponsors API, and Open Collective. Use when the user provides a target GitHub repo URL and wants sponsor leads, ranked company prospects, sponsor contact discovery, or a verified-leads.csv for outreach planning."
---

# Sponsor Miner

Find companies that already pay to sponsor open source projects whose audience matches the target repo. "Already sponsors a peer repo" is the strongest qualification signal a sponsorship prospect can have — the miner works from that signal, not from generic company lists.

Prerequisites:

- `python3` 3.9 or newer
- GitHub CLI installed and authenticated so `gh auth token` succeeds (no extra scopes needed; the Open Collective API needs no key)

## Workflow

### 1. Understand the target audience

Extract the single target repo URL from the user request (ask if missing; ask to choose if several). Fetch its metadata (`gh api repos/<owner>/<repo>`) and note description, topics, and stars. Write one sentence describing who the repo's users are — that sentence drives seed selection and fit judgments later.

### 2. Choose seed repos

Seeds are the repos whose sponsors get mined, so seed quality decides lead quality. Pick both:

- **Topics** (4–6): GitHub topics whose top repos share the target's audience. Start from the target repo's own topics and drop the ones that are too generic or off-audience. Pass via `--topics`; without the flag the script uses the target's first six topics.
- **Explicit seeds**: peer repos you know have visible sponsors (look for a "Sponsors" README section, a sponsorkit SVG, an active GitHub Sponsors profile, or an Open Collective). Pass via `--seeds`. Including one or two sponsor-rich ecosystems outside the exact niche (e.g. `antfu/unocss`) is fine — companies that repeatedly sponsor developer-tool OSS are good prospects even if the repo differs.

### 3. Run the miner

```bash
python3 <this-skill-dir>/scripts/sponsor_miner.py <target_repo_url> \
  --topics <t1,t2,...> --seeds <owner/repo,...> [--min-stars 300] [--max-seed-repos 40]
```

Resolve `<this-skill-dir>` from the path of this `SKILL.md`. The run takes several minutes (API rate-limit pacing); run it in the background. It prints `SESSION_PATH=<dir>` at the end.

The script mines each seed repo through four sources: README sponsor sections, sponsor SVG images (sponsorkit-style, links parsed out of the SVG), the GitHub Sponsors GraphQL API (public sponsors of the repo owner and FUNDING.yml accounts), and the Open Collective API (organization backers with donation totals). It then aggregates by company and ranks.

### 4. Review the ranking

Read `<session>/run-summary.md`, then work through `<session>/companies-ranked.csv` top-down. How to read it:

- `score` favors companies sponsoring **multiple** seed repos, confirmed through the Sponsors/Open Collective APIs, typed as organizations, and with larger donation totals. It is a triage heuristic, not a verdict.
- `entity_type` is `organization` or `individual` only when a typed source confirmed it; `unknown` means only a website link was seen — many unknowns are real companies, many are personal sites. Check the `links` URL.
- `total_donated` (Open Collective only) tells you what the company already pays peers — use it to calibrate your ask.
- `sponsor-edges.jsonl` holds every raw sponsorship observation when evidence needs auditing.

Verify each promising lead by opening `evidence_urls` and confirming real, current sponsorship, then judge audience overlap with the target repo. Reject: donation-platform-only links, badges/CDN artifacts, personal sites of individual backers (unless the person is a founder/decision-maker at a relevant company), and companies with no plausible audience overlap.

### 5. Find the right contact

Never settle for `support@`. Work this ladder per company:

1. **The sponsoring GitHub account.** When the sponsor is a GitHub org, open its profile: public members often include the DevRel or marketing person who runs sponsorships. When the sponsorship sits under a personal account, that person *is* the contact.
2. **A public OSS/sponsorship program.** Check `<domain>/open-source`, `/sponsorship`, `/oss`, and web-search `"<company>" sponsors open source`. Some companies publish a program page or a `sponsorships@`/`opensource@` address — that is the ideal channel.
3. **The person who owns developer marketing.** Look for DevRel / Developer Advocate / Head of Community / Developer Marketing on the company team page, LinkedIn, or X. Record name + role + profile URL. If you infer an email pattern (`first@`, `first.last@domain`), record it in `contact_email` but say in `fit_notes` that it is inferred, not confirmed.
4. **Sponsor-link UTM parameters** (visible in `links`/README) mean marketing runs the program — bias the search toward marketing rather than engineering.

Also remind the user of their **warm list**: companies that previously emailed them about sponsorship (including ones that ghosted) are named contacts at in-market companies, and those companies' direct competitors are the warmest cold prospects. Merge any the user mentions into the leads file.

### 6. Fill verified-leads.csv

Write final leads to `<session>/verified-leads.csv` (created with headers only):

```text
company,domain,evidence_url,fit_score,fit_notes,contact_name,contact_role,contact_url,contact_email,status
```

- `fit_score`: your judgment 1–5 (5 = sponsors multiple close peers and clearly targets this audience).
- `fit_notes`: one sentence of actual rationale — why this company would pay to reach the target repo's users. Never paste raw README snippets.
- `status`: `verified` (good lead, contact found) · `contact_missing` (good lead, no usable contact) · `needs_review` (evidence or fit uncertain) · `rejected` (not a real lead or no audience overlap).

### 7. Report

Summarize the top verified leads to the user with company, why they fit, and the contact. Point out that conversion depends on the pitch as much as the list: offer to draft a sponsorship one-pager / media kit (stars trajectory, traffic and clones from the repo's Insights, audience description, placement tiers with prices) and a `SPONSORS.md` + GitHub Sponsors tiers so inbound prospects can say yes at a known price.

Do not send any outreach unless the user explicitly asks.
