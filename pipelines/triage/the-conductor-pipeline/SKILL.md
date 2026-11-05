---
name: the-conductor-pipeline
description: "Orchestrate the AI2C GitLab pipeline-repair workflow from fresh Umpire collection through live action classification, explicit approval, and one local uncommitted repair. Use when asked to run, coordinate, or conduct the complete pipeline triage workflow across get-umpire-gitlab-pipelines, determine-pipeline-actions, and triage-pipeline."
---

# Conduct the Pipeline-Repair Workflow

Coordinate the existing pipeline skills in a safe, staged sequence. Collect a
fresh Umpire candidate queue, classify every candidate from current GitLab
state, present the results, obtain explicit approval for exactly one eligible
repository, and then repair only that repository locally.

This skill is an orchestrator. Invoke and follow the existing skills rather
than reproducing their GitLab queries, classification rules, scanner guidance,
or repair logic:

1. `get-umpire-gitlab-pipelines`
2. `determine-pipeline-actions`
3. `triage-pipeline`

One invocation may perform at most one local repair. Collection and
classification are read-only. The repair must end with uncommitted, unpushed
local changes for manual review.

## State files

Use the existing default handoff files unless the user supplies paths:

- Collector output:
	`~/.local/state/pipeline-repair/umpire-candidates.json`
- Classifier output:
	`~/.local/state/pipeline-repair/pipeline-actions.json`

Pass caller-supplied paths consistently between stages. Do not silently fall
back to a default file when a supplied path is missing or invalid.

Before each producing stage, record whether its destination exists and, when
it does, its modification time, size, and SHA-256 digest. These values are only
stale-output guards and must not be treated as pipeline evidence.

## Non-negotiable controls

- Run the stages in order. Never begin classification before collection ends
	successfully, and never begin repair before classification and approval.
- Wait for every helper to exit. Classification can take many minutes; an
	initial period without output is not permission to abandon it or read an old
	result.
- A nonzero exit, interruption, timeout, malformed output, or failed freshness
	check stops the workflow. Preserve any prior state file but label it stale;
	never present it as the result of the current run.
- Never infer approval from the user's request to run this orchestrator.
- Never select a repository automatically, even when only one is eligible.
- Require approval for exactly one repository after showing classifications.
- Never continue automatically to a second repository.
- Never commit, push, tag, create a merge request, retry a pipeline, or modify
	issues or other GitLab state.
- Never expose tokens, authorization headers, secrets, or complete sensitive
	scanner findings.

## Stage 1: collect a fresh Umpire queue

Load and follow `get-umpire-gitlab-pipelines`. Run its bundled helper to
completion and require a successful exit status.

After it exits, validate the produced file before using it:

1. It is readable JSON.
2. `schema_version` is exactly `2`.
3. `collected_at` is a parseable UTC ISO-8601 timestamp generated after this
	 stage began and is not unreasonably in the future.
4. `candidates` and `warnings` have the collector's documented shapes.
5. Every candidate contains only nonempty `repository_url` and
	 `umpire_issue_url` strings.
6. Repository URLs are unique.
7. The output's modification time or SHA-256 digest differs from the recorded
	 pre-run file state. A previously missing output must now exist.

An empty but valid candidate list is a successful collection. Report that
there is nothing to classify and stop.

If the helper fails or any validation fails, report the stage failure and stop.
Do not inspect or use a pre-existing candidate file as a fallback.

## Stage 2: classify current pipeline actions

Load and follow `determine-pipeline-actions`. Give it the freshly validated
collector output as its input and run its bundled helper synchronously to
completion. Do not replace the helper with ad hoc `glab` calls.

After it exits successfully, validate the produced file:

1. It is readable JSON and `schema_version` is exactly `1`.
2. `triaged_at` is a parseable UTC ISO-8601 timestamp generated after the
	 classifier stage began and after the collector's `collected_at` value.
3. The output changed from its recorded pre-run state.
4. `repositories` and top-level `warnings` have the classifier's documented
	 shapes.
5. The set of `(repository_url, umpire_issue_url)` pairs exactly matches the
	 fresh collector candidates. Reject missing, extra, or duplicate entries.
6. When the additive `input.sha256` provenance is present, it exactly matches
	the SHA-256 of the fresh collector file bytes. A mismatch stops the run.
7. Every action is one documented by `determine-pipeline-actions`.
8. Pipeline states, branch data, tag data, and warnings satisfy that skill's
	 output contract.

The pair comparison is the current schema's provenance check. Do not claim a
stronger cryptographic producer-to-consumer link unless the producer schemas
later add and verify a run ID or input digest.

If classification fails or validation does not prove that the output belongs
to the current collector result, report the failure and stop. Do not show an
older classifier file as current.

## Stage 3: present classifications

Before requesting approval, show a concise table with one row per repository:

- Repository
- Default branch pipeline state
- Latest tag, or `none`
- Tag pipeline state, or `n/a`
- Whether branch and tag resolve to the same SHA
- Classified action
- Eligibility for local repair

Group or summarize the rows using these operator-facing categories:

- **Repairable now**: repository `status` is absent or `ok`, action is
	`repair_default_branch`, default-branch name and SHA are present, and an
	exact-SHA failed default-branch pipeline with an ID and URL is present. A
	latest tag is optional: tagged repositories use `em/<latest-tag>` and
	untagged repositories use `em/pipeline_fix`.
- **Blocked repair**: action is `repair_default_branch`, but required branch or
	pipeline input is absent.
- **Classification error**: repository `status` is `error`. Show its redacted
	warning and never pass it to `triage-pipeline`; use a focused classifier
	rerun instead.
- **Release review**: `release_review_no_tag`,
	`release_review_tag_behind`, or `release_review_tag_failed`.
- **Wait or investigate**: `wait_or_investigate` or
	`investigate_tag_context`.
- **No action**: `no_action`.

Do not reinterpret or overwrite the classifier's action in its JSON. The
operator-facing eligibility category is an additional guard on whether
`triage-pipeline` has all required input.

Include all collector and classifier warnings without exposing sensitive data.
State clearly that only entries under **Repairable now** can be selected in
this run. Do not repair release-review, wait/investigate, no-action, or blocked
entries through this orchestrator.

## Approval gate

Stop and ask the user to approve exactly one **Repairable now** repository by
its full repository URL or an unambiguous displayed identifier. Approval must
occur after the classification report; approval from an earlier message does
not cross this gate unless it explicitly identifies one repository and was
given in response to the current fresh report.

Handle the response as follows:

- No selection: stop without modifying a checkout.
- More than one selection: ask the user to choose one; do not repair any.
- Ambiguous identifier: ask for clarification.
- Ineligible selection: explain the blocking category and ask for one eligible
	repository or stop.
- Exactly one eligible selection: restate the repository URL and proceed only
	with that entry.

Do not offer a default or choose the first, easiest, or highest-priority entry.

## Stage 4: repair one approved repository

Load and follow `triage-pipeline` for the approved classifier entry. Supply the
repository explicitly; do not let the repair skill infer a selection from a
multi-entry file.

The repair skill must repeat its required live checks. A successful
classification does not waive verification of the current default branch,
HEAD SHA, failed exact-SHA pipeline, or tag state. If live state differs, stop
and report that collection/classification must be rerun. Do not switch to a
different pipeline or tag within this stage.

Permit only the local operations allowed by `triage-pipeline`: locating or
cloning the checkout, read-only fetching, creating or reusing the local
`em/<latest-tag>` branch when tagged or `em/pipeline_fix` when untagged,
inspecting failure evidence, making minimal local edits, and focused validation.
Both new branches must be created from the verified default-branch SHA, not
from an assumed branch named `main`.

Before edits, preserve each target file's existing encoding, byte-order mark,
and newline convention. After edits, verify they remain unchanged unless the
failure specifically requires conversion and the user approved it. Treat a
whole-file CRLF/LF conversion or unrelated formatting as unintended change;
restore only the unintended representation change without discarding the
intended edit, then recheck the complete diff.

Never broaden the approved scope to another repository. If the selected repair
is blocked, inconclusive, infrastructure-related, or requires separate risk
acceptance, report that result and stop the orchestrator.

## Final review and report

After the repair skill stops, verify and report:

- The fresh collector and classifier output paths and timestamps.
- Candidate count and action counts.
- The approved repository and its Umpire issue URL.
- Local checkout path and active `em/<latest-tag>` or `em/pipeline_fix` branch.
- Verified default branch, base SHA, latest tag, and failed pipeline URL.
- Failed jobs inspected and concise evidence-backed causes.
- Files changed and the purpose of each change.
- Focused validation commands and results.
- Newline and encoding preservation result for changed files.
- `git status --short`, `git diff --stat`, and any unresolved failures.
- An explicit statement that this invocation processed no second repository.
- An explicit statement that the orchestrator did not commit or push.

Leave intended changes in the worktree for manual inspection. Stop after this
report. The user is responsible for reviewing, committing, and pushing any
accepted changes.
