---
name: get-umpire-gitlab-pipelines
description: Collect repositories reported by Umpire as having pipeline failures. Use when starting the AI2C GitLab pipeline triage workflow or when asked to retrieve the current Umpire failure queue.
---

# Get Umpire GitLab Pipelines

Collect the repositories represented by open Umpire pipeline-failure issues and
produce a minimal JSON candidate list for downstream live triage.

## Source

Use the GitLab API for the Umpire project:

- GitLab host: `https://code.cdso.army.mil`
- Umpire project ID: `29112`
- Issue state: `opened`
- Required title prefix: `Umpire:`
- Required label: `TM::Pipelines`

Although these records appear in the GitLab Work Items interface, Umpire creates
and manages them through the GitLab Issues API.

Do not scrape the Umpire Pages HTML unless the GitLab API is unavailable and the
user explicitly requests that fallback.

## Prerequisites

Before collecting data:

1. Confirm that `glab` is installed.
2. Confirm that `glab` can authenticate to `code.cdso.army.mil`.
3. Confirm that the authenticated user can read project `29112`.

If authentication or authorization fails, stop and report the error. Do not ask
the user to expose a token in chat or store credentials in this skill.

## Collection procedure

Always use the bundled helper instead of reproducing the API calls manually.
Resolve the script relative to this `SKILL.md`, then run it with Python 3:

```sh
python3 <skill-directory>/scripts/get_umpire_gitlab_pipelines.py
```

For this personal skill installation, `<skill-directory>` is normally
`$HOME/.agents/skills/get-umpire-gitlab-pipelines`.

If the caller supplies an output path, pass it exactly as provided:

```sh
python3 <skill-directory>/scripts/get_umpire_gitlab_pipelines.py \
  --output <caller-supplied-path>
```

Do not add `--output` when the caller does not supply a path. The helper will
then write to `~/.local/state/pipeline-repair/umpire-candidates.json`.

The helper uses six concurrent note requests by default, a 45-second request
timeout, and two retries. Keep those defaults unless the environment requires
different bounded values. Optional controls are `--workers` (1-16),
`--request-timeout`, and `--retries` (0-5).

Run the command from any working directory; do not assume the current directory
is the skill directory. Capture the command's exit status and output. If it
fails, stop, report the error without exposing credentials, and do not use a
previously generated candidate file as though this collection succeeded.

The helper performs this procedure:

1. Query all pages of open issues in project `29112`, applying the required
  state and label server-side and rechecking them locally.
2. Request up to 100 issues per page and continue until no next page remains.
3. Filter the returned records locally. Retain only issues that:
   - Have a title beginning with `Umpire:`.
   - Have the `TM::Pipelines` label.
   - Remain open.
4. For every retained issue:
   - Use the issue description as the initial Umpire observation.
   - Request notes in bounded parallel across issues, with each issue's notes
     ordered newest first and up to 100 notes per page.
   - Use the first note whose body begins with
     `Umpire update: pipeline still failing` instead of the description.
   - If those notes contain no matching update, request the next page and stop
     as soon as a matching update is found. Do not retrieve or analyze older
     notes after finding one.
   - Extract the reported main pipeline URL from the selected observation.
   - Derive the repository URL by removing `/-/pipelines/<id>` from the main
     pipeline URL.
   - If note retrieval fails after retries, use the issue description and add
     an issue-specific warning rather than discarding all other candidates.
5. If no main pipeline URL can be found, omit that candidate and add a warning
  containing the issue IID and reason.
6. Deduplicate candidates by normalized repository URL. If duplicate issues
  refer to one repository, keep the observation with the newest matching
  Umpire note timestamp; use the issue creation timestamp when both candidates
  came from descriptions. Add a warning listing the omitted issue IIDs.
7. Sort candidates by repository URL.
8. Write the result to the path supplied by the caller. If no path is supplied,
   use `~/.local/state/pipeline-repair/umpire-candidates.json`.

Create the output directory if necessary. Write the complete result to a
temporary sibling file, validate that it contains valid JSON conforming to the
output contract, and then atomically rename it over the destination. This should
replace the previous output on every successful collection. If collection,
serialization, or validation fails, remove the temporary file and preserve the
previous valid output.

Do not use `issue.updated_at` to select an observation. Labels, assignments,
human notes, and other unrelated activity can change that field. Prefer the
matching Umpire note's `created_at` timestamp.

## Output contract

Write valid JSON with the following top-level structure:

{
  "schema_version": 2,
  "collected_at": "<UTC ISO-8601 timestamp>",
  "candidates": [
    {
      "repository_url": "<normalized repository URL>",
      "umpire_issue_url": "<issue URL>"
    }
  ],
  "warnings": []
}

An open Umpire issue is a candidate signal and does not prove that the repository
or pipeline is currently failing. Downstream triage must resolve the current
default branch, latest applicable tag, commit SHAs, and relevant pipelines
directly from GitLab in one live snapshot.

## Safety constraints

This skill is read-only with respect to GitLab and repositories.

Do not:

- Create, update, comment on, close, or reopen issues.
- Retry or cancel pipelines.
- Create branches or tags.
- Clone or modify candidate repositories.
- Commit or push changes.
- Treat an open issue as authoritative current pipeline status.
- Print authentication tokens or authorization headers.

Writing the local JSON output file is permitted.

## Completion report

After writing the JSON file, report:

- Output path.
- Number of open Umpire issues examined.
- Number of candidates collected.
- Issues omitted because no main pipeline URL was found.
- Duplicate repositories or issues omitted.
- Any API, parsing, or pagination warnings.

Do not perform downstream pipeline triage. Return the output path so the
triage skill or orchestrator can consume it.
