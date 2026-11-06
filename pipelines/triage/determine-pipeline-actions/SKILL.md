---
name: determine-pipeline-actions
description: Inspect current default-branch and latest-tag GitLab pipelines and classify the next action. Use after collecting Umpire candidates, when comparing branch and release pipeline state, or when deciding whether a repository needs repair or release review.
---

# Determine Pipeline Actions

Consume repository candidates from the Umpire collector, query GitLab for a
current read-only snapshot of each repository, and classify the next action.
Do not repair pipelines in this skill.

## Inputs

By default, read:

`~/.local/state/pipeline-repair/umpire-candidates.json`

Accept a caller-supplied input path instead when provided. The expected input
has `schema_version` 2 and this candidate shape:

```json
{
  "repository_url": "https://code.cdso.army.mil/group/project",
  "umpire_issue_url": "https://code.cdso.army.mil/group/umpire/-/issues/123"
}
```

The caller may alternatively supply one repository URL. In that case,
`umpire_issue_url` may be `null` in the output.

Reject malformed input before making GitLab requests. Do not silently use an
old output file if the requested input cannot be read or validated.

## Invocation

Always use the bundled helper instead of reproducing the API calls, pagination,
tag ordering, comparisons, or JSON writing manually. Resolve the script relative
to this `SKILL.md` and run it with Python 3:

```sh
python3 <skill-directory>/scripts/determine_pipeline_actions.py
```

For this personal installation, `<skill-directory>` is normally
`$HOME/.agents/skills/determine-pipeline-actions`.

If the caller supplies an input or output path, pass each exactly as provided:

```sh
python3 <skill-directory>/scripts/determine_pipeline_actions.py \
	--input <caller-input-path> \
	--output <caller-output-path>
```

For one or more directly supplied repositories, do not create an intermediate
collector file. Pass each URL with a separate `--repository` option:

```sh
python3 <skill-directory>/scripts/determine_pipeline_actions.py \
	--repository <repository-url> \
	--output <caller-output-path>
```

Do not combine `--repository` with `--input`. When neither custom path is
provided, omit both options and let the helper use the documented defaults.
Run it from any working directory. If it fails, report the error without
exposing credentials and do not treat an older output file as a successful run.

For a focused rerun that preserves Umpire issue provenance, use repeatable
`--only-repository <repository-url>` with the normal input file and a distinct
output path. The helper also supports bounded `--workers` (default 4, maximum
8), `--request-timeout` (default 45 seconds), and `--retries` (default 2,
maximum 5).

## Prerequisites

Before triage:

1. Confirm that `glab` is installed.
2. Confirm that `glab` can authenticate to `code.cdso.army.mil`.
3. Confirm that the authenticated user can read every candidate project.

If authentication or authorization fails, stop and report the error without
printing tokens or authorization headers.

## Helper behavior

The helper uses the GitLab API through
`glab api --hostname code.cdso.army.mil` and URL-encodes project identifiers and
refs. The following procedure defines its required behavior.

For each candidate:

1. Normalize the repository URL by removing a trailing slash and optional
	`.git`, then obtain its namespace/project path.
2. Query the project and record its numeric project ID and `default_branch`.
3. Query the default branch endpoint and record its current commit SHA.
4. Retrieve all repository tags, following pagination.
5. Select one latest applicable tag using the policy below and record its name,
	dereferenced commit SHA, and commit timestamp.
6. Find the newest pipeline whose `ref` and `sha` exactly match the current
	default branch and its HEAD SHA.
7. Find the newest pipeline whose `ref` and `sha` exactly match the selected tag
	and its dereferenced commit SHA.
8. For each selected pipeline, retrieve pipeline details. When an exact-SHA
	default-branch pipeline passed, inspect only its `review`-stage jobs for a
	cDSO downstream-pipeline URL. Read the minimum trace needed to parse that
	URL, then inspect the downstream pipeline's failed, recognized security jobs
	(e.g. Syft/Grype, Semgrep, TruffleHog, or ZAP), even when its overall status
	is `success` because a job is allowed to fail. Record job metadata only; do
	not copy scanner findings into classifier output.
9. Compare the branch and tag SHAs and pipeline states, then assign exactly one
	action using the classification rules below.

Never select a pipeline by ref alone. A branch pipeline for an older commit is
not the current default-branch pipeline. Order matching pipelines by descending
pipeline ID and select the first exact match. Include all pipeline sources; do
not assume push pipelines are the only valid source.

Use the exact ref/SHA server filters and inspect the newest returned page first;
do not paginate through every historical pipeline after an exact match is
confirmed. If the default-branch state already determines the action, do not
query a tag pipeline that cannot alter that classification. Tags are still
collected so a failed branch can provide the repair branch name.

Classify repositories concurrently with a small fixed worker pool. If one
repository still fails after bounded retries, preserve the remaining results
and emit that repository with `status: error`, action `wait_or_investigate`,
null unresolved fields, and a redacted warning. Global input, authentication,
or output failures still stop the run.

## Latest-tag policy

Apply this deterministic policy:

1. A semantic-version tag is an optional `v` followed by
	`MAJOR.MINOR.PATCH`, with optional valid SemVer prerelease or build metadata.
2. If stable semantic-version tags exist, choose the highest stable semantic
	version. Ignore a leading `v` for comparison.
3. Otherwise, if prerelease semantic-version tags exist, choose the highest
	semantic version using SemVer precedence.
4. Otherwise, choose the tag with the newest commit `committed_date`. Resolve
	ties by tag name in ascending lexical order.

Record the selection method as one of:

- `stable-semantic-version`
- `prerelease-semantic-version-fallback`
- `commit-date-fallback`
- `no-tags`

Do not use lexical ordering as semantic-version ordering. An annotated tag must
be dereferenced to the commit represented by the tag API's `commit.id`.

## Pipeline-state normalization

Retain GitLab's original pipeline status and additionally classify it as:

- `passed`: `success`
- `failed`: `failed`
- `in_progress`: `created`, `waiting_for_resource`, `preparing`, `pending`,
  `running`, or `scheduled`
- `inconclusive`: `canceled`, `skipped`, `manual`, missing pipeline, or any
  unknown status

Do not reinterpret a canceled, skipped, or missing pipeline as a failure.

## Action classification

Apply these rules in order:

1. If the default-branch pipeline is `failed`, use `repair_default_branch`
	regardless of whether a latest tag exists. The repair skill uses
	`em/pipeline_fix` when no tag exists.
2. If the default-branch pipeline passed and its downstream security review
	has recognized failed security jobs, use `repair_downstream_security_findings`.
3. If the default-branch pipeline is not `passed`, use `wait_or_investigate`.
4. If no tag exists, use `release_review_no_tag`.
5. If the tag pipeline is `in_progress` or `inconclusive`, use
	`wait_or_investigate`.
6. If both pipelines passed and the tag SHA equals default-branch HEAD, use
	`no_action`.
7. If both pipelines passed but the SHAs differ, use `release_review_tag_behind`.
8. If the default branch passed, the tag failed, and both refs resolve to the
	same SHA, use `investigate_tag_context`.
9. If the default branch passed, the tag failed, and the SHAs differ, use
	`release_review_tag_failed`.
10. For any unhandled combination, use `wait_or_investigate` and add a warning.

These classifications are recommendations only. Do not create or move tags,
retry pipelines, or begin repairs. A later repair skill may consume repositories
classified as `repair_default_branch` or `investigate_tag_context` after user
review.

## Output

Write to the caller-supplied output path or, by default:

`~/.local/state/pipeline-repair/pipeline-actions.json`

Use this structure:

```json
{
  "schema_version": 2,
  "triaged_at": "<UTC ISO-8601 timestamp>",
  "repositories": [
	 {
		"repository_url": "https://code.cdso.army.mil/group/project",
		"umpire_issue_url": "https://code.cdso.army.mil/group/umpire/-/issues/123",
		"project_id": 123,
		"default_branch": {
		  "name": "main",
		  "sha": "<commit SHA>",
		  "pipeline": {
			 "id": 100,
			 "url": "https://code.cdso.army.mil/group/project/-/pipelines/100",
			 "ref": "main",
			 "sha": "<commit SHA>",
			 "status": "success",
			 "state": "passed",
			 "source": "push",
			 "created_at": "<GitLab timestamp>",
			 "updated_at": "<GitLab timestamp>",
			 "failed_jobs": []
		  }
		},
		"latest_tag": {
		  "name": "v1.2.3",
		  "sha": "<commit SHA>",
		  "committed_at": "<GitLab timestamp>",
		  "selection_method": "stable-semantic-version",
		  "pipeline": null
		},
		"comparison": {
		  "same_commit": false,
		  "default_branch_pipeline_state": "passed",
		  "tag_pipeline_state": "inconclusive"
		},
		"action": "wait_or_investigate",
	"security_review": {
	  "status": "clean",
	  "parent_job": null,
	  "downstream_pipeline": null,
	  "failed_security_jobs": [],
	  "warnings": []
	},
		"warnings": [],
		"status": "ok"
	 }
  ],
  "warnings": [],
  "input": {
	"path": "<collector input path>",
	"sha256": "<SHA-256 of exact collector input bytes>"
  }
}
```

Use `null` for a missing pipeline or tag. When `latest_tag` is `null`, set
`comparison.same_commit` and `comparison.tag_pipeline_state` to `null`.
Preserve each candidate's `umpire_issue_url` for traceability. Sort repositories
by `repository_url`.

`security_review` contains only pipeline and job metadata. Trace contents,
scanner findings, and sensitive values must never be emitted. `status` is `ok` for a complete repository snapshot and `error` when that
repository could not be classified after retries. Error entries include a
redacted `error` object and must never be considered repairable. The additive
`input` object provides provenance without changing schema version 1.

Create the destination directory when necessary. Write and validate the entire
JSON document in a temporary sibling file, then atomically replace the output.
If collection, serialization, or validation fails globally, remove the temporary
file and preserve any previous valid output.

## Safety constraints

This skill is read-only with respect to GitLab and repositories. Do not:

- Create, update, comment on, close, or reopen issues.
- Retry, cancel, delete, or create pipelines.
- Create, move, or delete branches or tags.
- Clone or modify repositories.
- Create repair branches.
- Commit or push changes.
- Download or expose credentials.

Writing the local JSON result is permitted.

## Completion report

After writing the output, report:

- Input and output paths.
- Number of repositories examined.
- Repository count for each action.
- Repositories with missing tags or exact-SHA pipelines.
- Authentication, authorization, pagination, or consistency warnings.

Provide a concise table containing repository, default-branch pipeline state,
latest tag, tag pipeline state, SHA equality, and action. Stop after reporting;
do not invoke a repair workflow automatically.
