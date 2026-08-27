export const meta = {
  name: "ultra",
  description:
    "Ultra workflow: Fable owns judgment (plan, investigate, design, review, decide), Opus owns execution (implement, fix, validate). A Fable planner designs a task-specific stage plan, an interpreter executes it, then a Fable review + Opus fix loop closes it out.",
  phases: [
    {
      title: "Plan",
      detail:
        "fable planner scopes the task, captures baseline, defines acceptance criteria, emits the stage plan",
      model: "fable",
    },
    {
      title: "Review",
      detail: "autoreview skill + fable judge",
      model: "fable",
    },
    {
      title: "Fix",
      detail: "opus applies the corrections fable specified",
      model: "opus",
    },
  ],
};

const TASK = typeof args === "string" ? args : args?.task;
if (!TASK)
  throw new Error(
    "Pass the task as args.task — include any already-verified findings/context verbatim",
  );

const ensure = (result, what) => {
  if (!result) throw new Error(`${what} agent failed or was skipped`);
  return result;
};

const PROJECT_PREAMBLE = `Work in the repository at your current working directory. First read the project instruction files if they exist (CLAUDE.md, AGENTS.md, CONTRIBUTING.md, README.md) and follow their conventions exactly. Never commit, never push, never touch generated/build output directories.`;

// Two roles: Fable owns judgment (investigate/design/review, planning, decisions),
// Opus owns execution (implement, fix). This table — not the planner — picks models.
const ROLE_MODEL = {
  investigate: { model: "fable" },
  design: { model: "fable" },
  implement: { model: "opus" },
  review: { model: "fable" },
};

phase("Plan");
const plan = ensure(
  await agent(
    `${PROJECT_PREAMBLE}

You are the workflow planner. Design a stage plan tailored to this task; separate agents who cannot see this conversation will execute it.

## Task
${TASK}

Skim the codebase enough to judge its real complexity — do not deep-dive.

## How the plan is executed
- Stages run strictly in order. Prompts within a stage run in parallel — except in 'implement' stages, whose prompts run one at a time in order.
- Two model roles execute the stages. Fable owns judgment and runs 'investigate', 'design', and 'review' stages. Opus owns execution and runs 'implement' stages.
  - 'investigate' — read-only: locate files, read code, and answer a question precisely with file:line references and verbatim excerpts
  - 'design' — architect the implementation plan from the task and all earlier stage results (use when the change needs real design work)
  - 'implement' — execute the edits and their tests for one self-contained work item
  - 'review' — mid-flow verification of intermediate results (only when a later stage depends on an earlier one being right)
- The implementer executes decisions; it does not make them. Every 'implement' prompt (or the design output it defers to) must be concrete enough to execute without new design, scope, or behavior decisions.
- Every executing agent automatically receives the task, the acceptance criteria, your project notes, the baseline state, and the full results of all earlier stages. Each prompt therefore only needs to state that agent's specific job — but must be self-contained in stating it (name concrete files/areas/conventions where you know them).
- If you include a 'design' stage, later 'implement' prompts may defer their edit details to the design stage's output ("implement work item N from the design").
- Scale the plan to complexity: a small cohesive change may be a single 'implement' stage with one prompt; a sweeping task may need investigate fan-outs, a design stage, and several implement items. Do not pad the plan with stages the task does not need.
- Do NOT add a final review stage — a review-and-fix loop always runs automatically after your stages.

Also capture the scope facts:
- acceptanceCriteria: the concrete, checkable conditions that must hold for the task to be complete — implementers build to these and the final review judges against them.
- projectNotes: project facts every later agent needs (instruction files found and their key rules, language/toolchain, layout).
- verifyCommand: the single command that verifies changes (from project instructions or package scripts, e.g. 'bun run check', 'npm test'); empty string if none exists.
- baselineDirty: the verbatim output of 'git status --porcelain' right now (empty string if the tree is clean), so later agents can tell pre-existing uncommitted changes from their own.
- baselineVerify: run the verify command once, before anything changes: 'pass' if it succeeds, 'fail' if it does not, 'not-run' if there is no verify command or running it here is impractical.
- baselineVerifyDetail: when 'fail', the failing test names or error tail; otherwise empty string.`,
    {
      label: "plan",
      phase: "Plan",
      model: "fable",
      schema: {
        type: "object",
        properties: {
          stages: {
            type: "array",
            minItems: 1,
            items: {
              type: "object",
              properties: {
                title: { type: "string" },
                role: {
                  type: "string",
                  enum: ["investigate", "design", "implement", "review"],
                },
                prompts: {
                  type: "array",
                  minItems: 1,
                  items: { type: "string" },
                },
              },
              required: ["title", "role", "prompts"],
            },
          },
          acceptanceCriteria: { type: "string" },
          projectNotes: { type: "string" },
          verifyCommand: { type: "string" },
          baselineDirty: { type: "string" },
          baselineVerify: { type: "string", enum: ["pass", "fail", "not-run"] },
          baselineVerifyDetail: { type: "string" },
        },
        required: [
          "stages",
          "acceptanceCriteria",
          "projectNotes",
          "verifyCommand",
          "baselineDirty",
          "baselineVerify",
          "baselineVerifyDetail",
        ],
      },
    },
  ),
  "Plan",
);

const baselineSections = [];
if (plan.baselineDirty)
  baselineSections.push(
    `The tree already had uncommitted changes BEFORE this workflow started (git status --porcelain):\n${plan.baselineDirty}\nThose changes are not part of this task — do not revert, absorb, extend, or review them.`,
  );
if (plan.baselineVerify === "fail")
  baselineSections.push(
    `The verify command was already failing BEFORE this workflow started:\n${plan.baselineVerifyDetail}\nPre-existing failures are not yours to fix — only make sure no NEW failures appear.`,
  );
const BASELINE = baselineSections.length
  ? `## Baseline state (before this workflow)\n${baselineSections.join("\n\n")}\n\n`
  : "";

let verifyStep;
if (!plan.verifyCommand) {
  verifyStep = `Then verify your change compiles/passes whatever checks the project provides and report how you verified it.`;
} else if (plan.baselineVerify === "fail") {
  verifyStep = `Then run '${plan.verifyCommand}'. Failures already present at baseline are not yours to fix — make sure no NEW failures appear, and include the verbatim tail of the output in your report.`;
} else {
  verifyStep = `Then run '${plan.verifyCommand}' and fix failures until it passes; include the verbatim tail of its passing output in your report.`;
}

const IMPL_SCHEMA = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["done", "blocked", "needs-decision"] },
    blockedReason: { type: "string" },
    decisionRequest: { type: "string" },
    filesChanged: { type: "array", items: { type: "string" } },
    deviations: { type: "string" },
    verification: { type: "string" },
  },
  required: [
    "status",
    "blockedReason",
    "decisionRequest",
    "filesChanged",
    "deviations",
    "verification",
  ],
};

log(
  `Plan: ${plan.stages.map((s) => `${s.title} (${s.role}×${s.prompts.length})`).join(" → ")}`,
);

const stageContext = (record) => `${PROJECT_PREAMBLE}

## Task
${TASK}

## Acceptance criteria
${plan.acceptanceCriteria}

## Project notes
${plan.projectNotes}

${BASELINE}${record ? `## Results from earlier stages\n${record}\n\n` : ""}`;

let record = "";
const implReports = [];
for (const [si, stage] of plan.stages.entries()) {
  const opts = ROLE_MODEL[stage.role];
  const stageLabel = `${si + 1}:${stage.title}`;

  if (stage.role === "implement") {
    const itemReports = [];
    for (const [i, prompt] of stage.prompts.entries()) {
      const decisions = [];
      let report;
      for (;;) {
        report = ensure(
          await agent(
            `${stageContext(record)}You are the implementer for item ${i + 1}/${stage.prompts.length} of stage "${stage.title}". Work directly on the current branch. You execute a plan the planner already decided: choose how to carry out the item, but do not change its intended design, scope, or behavior. Fix straightforward implementation mistakes yourself. Follow the project's style conventions strictly and make the smallest change that satisfies the item.

## Your work item
${prompt}

${decisions.length > 0 ? `## Decisions from the planner for this item\nAn earlier attempt at this item stopped to escalate these questions; its partial edits may already be in the working tree.\n${decisions.join("\n\n")}\n\n` : ""}${itemReports.length > 0 ? `## Reports from items already completed in this stage\n${itemReports.join("\n\n")}\n\n` : ""}Apply the edits and their planned tests. ${verifyStep} Return structured output: status 'done' when the item is fully applied and verified. Status 'needs-decision' (with decisionRequest) when you discover a decision the plan does not cover — materially different possible approaches, a wrong assumption in the plan, or a conflict with the acceptance criteria; decisionRequest must state what you discovered, why the plan is insufficient, the available options, and the evidence. Status 'blocked' (with blockedReason) ONLY when the item genuinely cannot be completed — missing tooling or credentials — never for difficulty you can work through. filesChanged: each changed file followed by ' — ' and a one-line rationale. deviations: departures from the item's instructions with reasons, empty string if none. verification: how the item was verified, quoting the output tail when a verify command was run.`,
            {
              label: `implement:${stageLabel}:${i + 1}`,
              phase: stage.title,
              ...opts,
              schema: IMPL_SCHEMA,
            },
          ),
          `Implement (stage ${si + 1}, item ${i + 1})`,
        );
        if (report.status !== "needs-decision") break;
        if (decisions.length >= 2)
          throw new Error(
            `Stage ${si + 1} "${stage.title}" item ${i + 1} still needs a decision after ${decisions.length} escalations: ${report.decisionRequest}`,
          );
        log(
          `Stage ${si + 1} item ${i + 1}: implementer escalated a decision to the planner`,
        );
        const decision = ensure(
          await agent(
            `${stageContext(record)}You are the planner resolving a decision the implementer escalated from stage "${stage.title}". Read-only — do not modify anything; investigate as needed and decide.

## Work item
${prompt}

## Implementer's decision request
${report.decisionRequest}

Return the decision: which option to take and the concrete instructions the implementer needs to execute it without further decisions. If the work item itself must change, restate the changed instructions in full.`,
            {
              label: `decide:${stageLabel}:${i + 1}`,
              phase: stage.title,
              model: "fable",
            },
          ),
          `Decision (stage ${si + 1}, item ${i + 1})`,
        );
        decisions.push(
          `### Decision ${decisions.length + 1}\nQuestion: ${report.decisionRequest}\nDecision: ${decision}`,
        );
      }
      if (report.status === "blocked")
        throw new Error(
          `Stage ${si + 1} "${stage.title}" item ${i + 1} blocked: ${report.blockedReason}`,
        );
      const itemReport = `### Item ${i + 1}\nFiles changed:\n${report.filesChanged.map((f) => `- ${f}`).join("\n") || "- (none)"}\nDeviations: ${report.deviations || "none"}\n${decisions.length ? `Escalated decisions:\n${decisions.join("\n")}\n` : ""}Verification: ${report.verification}`;
      itemReports.push(itemReport);
      implReports.push(`Stage "${stage.title}" ${itemReport}`);
    }
    record += `\n\n## Stage ${si + 1}: ${stage.title} (implement)\n${itemReports.join("\n\n")}`;
  } else {
    const results = (
      await parallel(
        stage.prompts.map(
          (prompt, i) => () =>
            agent(
              `${stageContext(record)}Read-only — do not modify anything. Your job in stage "${stage.title}":

${prompt}

Return raw structured notes for another agent, not prose for a human. When reporting on code, cite file:line.`,
              {
                label: `${stage.role}:${stageLabel}:${i + 1}`,
                phase: stage.title,
                ...opts,
              },
            ),
        ),
      )
    ).filter(Boolean);
    if (results.length < stage.prompts.length)
      log(
        `Stage ${si + 1} "${stage.title}": ${stage.prompts.length - results.length} agent(s) failed or were skipped`,
      );
    if (results.length === 0)
      throw new Error(`Stage ${si + 1} "${stage.title}": every agent failed`);
    record += `\n\n## Stage ${si + 1}: ${stage.title} (${stage.role})\n${results.map((r, i) => `### Result ${i + 1}\n${r}`).join("\n\n")}`;
  }
}

const REVIEW_SCHEMA = {
  type: "object",
  properties: {
    findings: {
      type: "array",
      items: {
        type: "object",
        properties: {
          file: { type: "string" },
          summary: { type: "string" },
          failure_scenario: { type: "string" },
          required_fix: { type: "string" },
          must_fix: { type: "boolean" },
        },
        required: [
          "file",
          "summary",
          "failure_scenario",
          "required_fix",
          "must_fix",
        ],
      },
    },
    verdict: { type: "string", enum: ["approve", "needs-fixes"] },
  },
  required: ["findings", "verdict"],
};

let reviewVerifyStep = "";
if (plan.verifyCommand) {
  reviewVerifyStep =
    plan.baselineVerify === "fail"
      ? `4. Run '${plan.verifyCommand}' yourself and treat any NEW failure beyond the pre-existing baseline failures as a must-fix finding.`
      : `4. Run '${plan.verifyCommand}' yourself and treat any failure as a must-fix finding.`;
}

const reviewPrompt = (extra) => `${PROJECT_PREAMBLE}

You are the reviewer and judge for the uncommitted changes on the current branch. Apply any review criteria files the project defines (e.g. REVIEW.md).

## What the change must accomplish
${TASK}

## Acceptance criteria
${plan.acceptanceCriteria}

${BASELINE}## Work record (all stages that produced the changes)
${record}

${extra}

Review procedure:
1. Invoke the 'autoreview' skill via the Skill tool to review the uncommitted changes. If the Skill tool or the autoreview skill is unavailable in your environment, review them yourself instead: run 'git diff' and 'git status' and read every changed file with surrounding context.
2. Whichever path step 1 took, also hunt yourself for: correctness bugs, scope creep beyond the task, style violations, and tests that would not fail if the mistake they guard were made.
3. Acting as judge, verify each candidate finding against the actual code — no speculative findings — and decide which genuinely must be fixed (real defects, unmet acceptance criteria, or clear scope violations only).
${reviewVerifyStep}
Return every verified finding with must_fix set per your judgment. For each must_fix finding, write required_fix as the concrete correction — specific enough that an executor can apply it without making design decisions (empty string for findings that are not must_fix). Verdict 'approve' only if nothing must be fixed and the acceptance criteria are met.`;

phase("Review");
let review = ensure(
  await agent(reviewPrompt(""), {
    label: "review+judge",
    phase: "Review",
    model: "fable",
    schema: REVIEW_SCHEMA,
  }),
  "Review",
);

let round = 0;
while (
  review.verdict === "needs-fixes" &&
  review.findings.some((f) => f.must_fix) &&
  round < 3
) {
  round += 1;
  const mustFix = review.findings.filter((f) => f.must_fix);
  log(`Review round ${round}: ${mustFix.length} finding(s) to fix`);
  const fixReport = ensure(
    await agent(
      `${PROJECT_PREAMBLE}

You are the executor fixing confirmed review findings on the current branch. The reviewer already decided each correction — apply each finding's required_fix with the smallest change that resolves it. Do not redesign, expand scope, or change intended behavior; a finding is never a mandate to build a framework.

${BASELINE}## Findings to fix
${JSON.stringify(mustFix, null, 2)}

## Work record for context
${record}

Apply the fixes. ${verifyStep} Return what you changed per finding.`,
      { label: `fix:round${round}`, phase: "Fix", model: "opus" },
    ),
    `Fix (round ${round})`,
  );

  review = ensure(
    await agent(
      reviewPrompt(
        `## Findings that were supposed to be fixed\n${JSON.stringify(mustFix, null, 2)}\n\n## Fixer's report\n${fixReport}\n\nConfirm each finding is actually resolved and no regression or new scope creep was introduced — verify against real code, not the fixer's claims.`,
      ),
      {
        label: `re-review:round${round}`,
        phase: "Review",
        model: "fable",
        schema: REVIEW_SCHEMA,
      },
    ),
    `Re-review (round ${round})`,
  );
}

if (
  review.verdict === "needs-fixes" &&
  !review.findings.some((f) => f.must_fix)
) {
  log(
    "Normalizing verdict to approve: review said needs-fixes but marked nothing must-fix",
  );
  review = { ...review, verdict: "approve" };
}

return {
  stages: plan.stages.map((s) => `${s.title} (${s.role}×${s.prompts.length})`),
  acceptanceCriteria: plan.acceptanceCriteria,
  implReports,
  review,
  fixRounds: round,
};
