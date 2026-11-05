#!/usr/bin/env python3
"""Determine read-only GitLab pipeline actions for Umpire candidates."""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

HOST = "code.cdso.army.mil"
DEFAULT_STATE_DIR = Path.home() / ".local/state/pipeline-repair"
SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
IN_PROGRESS = {
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "scheduled",
}
PIPELINE_STATES = {"passed", "failed", "in_progress", "inconclusive"}
ACTIONS = {
    "repair_default_branch",
    "wait_or_investigate",
    "release_review_no_tag",
    "no_action",
    "release_review_tag_behind",
    "investigate_tag_context",
    "release_review_tag_failed",
}
DEFAULT_WORKERS = 4
DEFAULT_REQUEST_TIMEOUT = 45
DEFAULT_RETRIES = 2
REQUEST_TIMEOUT = DEFAULT_REQUEST_TIMEOUT
REQUEST_RETRIES = DEFAULT_RETRIES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_STATE_DIR / "umpire-candidates.json",
        help="collector JSON input (default: %(default)s)",
    )
    parser.add_argument(
        "--repository",
        action="append",
        default=[],
        metavar="URL",
        help="triage one repository URL instead of reading --input; repeatable",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_STATE_DIR / "pipeline-actions.json",
        help="destination JSON file (default: %(default)s)",
    )
    parser.add_argument(
        "--only-repository",
        action="append",
        default=[],
        metavar="URL",
        help="classify only this URL from --input while preserving issue provenance; repeatable",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="concurrent repository classifications (default: %(default)s)",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT,
        help="seconds allowed per GitLab request (default: %(default)s)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help="retries per failed GitLab request (default: %(default)s)",
    )
    return parser.parse_args()


def api(path: str) -> Any:
    command = ["glab", "api", "--hostname", HOST, path]
    result = None
    for attempt in range(REQUEST_RETRIES + 1):
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=REQUEST_TIMEOUT,
            )
            break
        except FileNotFoundError as exc:
            raise RuntimeError("glab is not installed or is not available on PATH") from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = (
                (exc.stderr or exc.stdout or str(exc)).strip()
                if isinstance(exc, subprocess.CalledProcessError)
                else f"request timed out after {REQUEST_TIMEOUT} seconds"
            )
            if attempt == REQUEST_RETRIES:
                raise RuntimeError(
                    f"GitLab API request failed for {path} after {attempt + 1} attempts: {detail}"
                ) from exc
            time.sleep(min(2 ** attempt, 4))
    if result is None:
        raise RuntimeError(f"GitLab API request failed for {path}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GitLab API returned invalid JSON for {path}: {exc}") from exc


def pages(path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page_number in range(1, 1001):
        separator = "&" if "?" in path else "?"
        page = api(f"{path}{separator}per_page=100&page={page_number}")
        if not isinstance(page, list):
            raise RuntimeError(f"Expected an array from paginated endpoint {path}")
        items.extend(page)
        if len(page) < 100:
            return items
    raise RuntimeError(f"Pagination limit exceeded for {path}")


def pipeline_state(status: Any) -> str:
    if status == "success":
        return "passed"
    if status == "failed":
        return "failed"
    if status in IN_PROGRESS:
        return "in_progress"
    return "inconclusive"


def semver_key(name: str) -> tuple[Any, ...] | None:
    match = SEMVER.fullmatch(name)
    if match is None:
        return None

    version = tuple(int(value) for value in match.group(1, 2, 3))
    prerelease = match.group(4)
    if prerelease is None:
        return version + (1, ())

    identifiers = tuple(
        (0, int(item)) if item.isdigit() else (1, item)
        for item in prerelease.split(".")
    )
    return version + (0, identifiers)


def parse_gitlab_timestamp(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def latest_tag(tags: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    if not tags:
        raise ValueError("latest_tag requires at least one tag")

    semantic = [(tag, semver_key(str(tag.get("name", "")))) for tag in tags]
    stable = [(tag, key) for tag, key in semantic if key is not None and key[3] == 1]
    if stable:
        selected, _ = max(stable, key=lambda item: (item[1], item[0]["name"]))
        return selected, "stable-semantic-version"

    prerelease = [(tag, key) for tag, key in semantic if key is not None]
    if prerelease:
        selected, _ = max(prerelease, key=lambda item: (item[1], item[0]["name"]))
        return selected, "prerelease-semantic-version-fallback"

    newest_timestamp = max(
        parse_gitlab_timestamp(tag.get("commit", {}).get("committed_date"))
        for tag in tags
    )
    newest = [
        tag
        for tag in tags
        if parse_gitlab_timestamp(tag.get("commit", {}).get("committed_date"))
        == newest_timestamp
    ]
    return min(newest, key=lambda tag: str(tag.get("name", ""))), "commit-date-fallback"


def exact_pipeline(project_id: int, ref: str, sha: str) -> dict[str, Any] | None:
    encoded_ref = quote(ref, safe="")
    encoded_sha = quote(sha, safe="")
    pipelines = api(
        f"/projects/{project_id}/pipelines?ref={encoded_ref}&sha={encoded_sha}"
        "&order_by=id&sort=desc&per_page=20&page=1"
    )
    if not isinstance(pipelines, list):
        raise RuntimeError("Expected an array from the filtered pipelines endpoint")
    matches = [
        pipeline
        for pipeline in pipelines
        if pipeline.get("ref") == ref and pipeline.get("sha") == sha
    ]
    if not matches:
        return None

    selected = max(matches, key=lambda pipeline: int(pipeline["id"]))
    pipeline_id = int(selected["id"])
    detail = api(f"/projects/{project_id}/pipelines/{pipeline_id}")
    if detail.get("ref") != ref or detail.get("sha") != sha:
        raise RuntimeError(
            f"Pipeline {pipeline_id} changed or did not match expected ref and SHA"
        )
    status = detail.get("status")
    failed_jobs: list[str] = []
    if status == "failed":
        jobs = pages(
            f"/projects/{project_id}/pipelines/{pipeline_id}/jobs?scope%5B%5D=failed"
        )
        failed_jobs = sorted(
            {str(job["name"]) for job in jobs if job.get("status") == "failed"}
        )
    return {
        "id": pipeline_id,
        "url": detail.get("web_url"),
        "ref": detail.get("ref"),
        "sha": detail.get("sha"),
        "status": status,
        "state": pipeline_state(status),
        "source": detail.get("source"),
        "created_at": detail.get("created_at"),
        "updated_at": detail.get("updated_at"),
        "failed_jobs": failed_jobs,
    }


def normalize_repository_url(repository_url: str) -> tuple[str, str]:
    normalized = repository_url.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    parsed = urlparse(normalized)
    project_path = parsed.path.strip("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname != HOST
        or parsed.port is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not project_path
        or "/-/" in f"/{project_path}/"
    ):
        raise ValueError(f"invalid repository URL: {repository_url}")
    return normalized, project_path


def classify_action(
    branch_state: str,
    has_tag: bool,
    tag_state: str | None,
    same_commit: bool | None,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if branch_state == "failed":
        return "repair_default_branch", warnings
    if branch_state != "passed":
        return "wait_or_investigate", warnings
    if not has_tag:
        return "release_review_no_tag", warnings
    if tag_state in {"in_progress", "inconclusive"}:
        return "wait_or_investigate", warnings
    if tag_state == "passed" and same_commit:
        return "no_action", warnings
    if tag_state == "passed":
        return "release_review_tag_behind", warnings
    if tag_state == "failed" and same_commit:
        return "investigate_tag_context", warnings
    if tag_state == "failed":
        return "release_review_tag_failed", warnings
    warnings.append("Unhandled pipeline-state combination")
    return "wait_or_investigate", warnings


def triage(candidate: dict[str, Any]) -> dict[str, Any]:
    repository_url, project_path = normalize_repository_url(candidate["repository_url"])
    project = api("/projects/" + quote(project_path, safe=""))
    if not isinstance(project.get("id"), int):
        raise RuntimeError(f"Project response has no numeric ID for {repository_url}")

    project_id = project["id"]
    branch_name = project.get("default_branch")
    if not isinstance(branch_name, str) or not branch_name:
        raise RuntimeError(f"Project has no default branch: {repository_url}")

    branch = api(
        f"/projects/{project_id}/repository/branches/{quote(branch_name, safe='')}"
    )
    branch_sha = branch.get("commit", {}).get("id")
    if not isinstance(branch_sha, str) or not branch_sha:
        raise RuntimeError(f"Default branch has no commit SHA: {repository_url}")
    branch_pipeline = exact_pipeline(project_id, branch_name, branch_sha)

    tags = pages(f"/projects/{project_id}/repository/tags")
    tag_data: dict[str, Any] | None = None
    tag_pipeline: dict[str, Any] | None = None
    if tags:
        tag, method = latest_tag(tags)
        tag_name = tag.get("name")
        tag_sha = tag.get("commit", {}).get("id")
        if not isinstance(tag_name, str) or not tag_name:
            raise RuntimeError(f"Selected tag has no name: {repository_url}")
        if not isinstance(tag_sha, str) or not tag_sha:
            raise RuntimeError(f"Selected tag has no commit SHA: {repository_url}")
        branch_state = branch_pipeline["state"] if branch_pipeline else "inconclusive"
        if branch_state == "passed":
            tag_pipeline = exact_pipeline(project_id, tag_name, tag_sha)
        tag_data = {
            "name": tag_name,
            "sha": tag_sha,
            "committed_at": tag.get("commit", {}).get("committed_date"),
            "selection_method": method,
            "pipeline": tag_pipeline,
        }

    branch_state = branch_pipeline["state"] if branch_pipeline else "inconclusive"
    tag_state = (
        tag_pipeline["state"]
        if tag_pipeline
        else ("inconclusive" if tag_data and branch_state == "passed" else None)
    )
    same_commit = branch_sha == tag_data["sha"] if tag_data else None
    action, warnings = classify_action(
        branch_state, tag_data is not None, tag_state, same_commit
    )
    if branch_pipeline is None:
        warnings.append("No pipeline exactly matched the default branch HEAD SHA")
    if tag_data is not None and tag_pipeline is None and branch_state == "passed":
        warnings.append("No pipeline exactly matched the selected tag SHA")

    return {
        "repository_url": repository_url,
        "umpire_issue_url": candidate.get("umpire_issue_url"),
        "project_id": project_id,
        "default_branch": {
            "name": branch_name,
            "sha": branch_sha,
            "pipeline": branch_pipeline,
        },
        "latest_tag": tag_data,
        "comparison": {
            "same_commit": same_commit,
            "default_branch_pipeline_state": branch_state,
            "tag_pipeline_state": tag_state,
        },
        "action": action,
        "warnings": warnings,
        "status": "ok",
    }


def failed_result(candidate: dict[str, Any], exc: Exception) -> dict[str, Any]:
    repository_url, _ = normalize_repository_url(candidate["repository_url"])
    message = str(exc).replace("\n", " ").strip()
    return {
        "repository_url": repository_url,
        "umpire_issue_url": candidate.get("umpire_issue_url"),
        "project_id": None,
        "default_branch": {"name": None, "sha": None, "pipeline": None},
        "latest_tag": None,
        "comparison": {
            "same_commit": None,
            "default_branch_pipeline_state": "inconclusive",
            "tag_pipeline_state": None,
        },
        "action": "wait_or_investigate",
        "warnings": [f"Classification failed: {message}"],
        "status": "error",
        "error": {"stage": "repository_classification", "message": message},
    }


def validate_candidates(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("input schema_version must be 2")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("input candidates must be an array")

    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {index} must be an object")
        if set(candidate) != {"repository_url", "umpire_issue_url"}:
            raise ValueError(
                f"candidate {index} must contain only repository_url and umpire_issue_url"
            )
        repository_url = candidate.get("repository_url")
        issue_url = candidate.get("umpire_issue_url")
        if not isinstance(repository_url, str) or not repository_url:
            raise ValueError(f"candidate {index}.repository_url must be nonempty")
        if not isinstance(issue_url, str) or not issue_url:
            raise ValueError(f"candidate {index}.umpire_issue_url must be nonempty")
        normalized, _ = normalize_repository_url(repository_url)
        if normalized in seen:
            raise ValueError(f"duplicate repository URL in input: {normalized}")
        seen.add(normalized)
        validated.append(candidate)
    return validated


def validate_pipeline(pipeline: Any, context: str) -> None:
    if pipeline is None:
        return
    required = {
        "id",
        "url",
        "ref",
        "sha",
        "status",
        "state",
        "source",
        "created_at",
        "updated_at",
        "failed_jobs",
    }
    if not isinstance(pipeline, dict) or set(pipeline) != required:
        raise ValueError(f"{context} has an invalid pipeline object")
    if pipeline["state"] not in PIPELINE_STATES:
        raise ValueError(f"{context} has an invalid normalized pipeline state")
    if not isinstance(pipeline["failed_jobs"], list) or not all(
        isinstance(job, str) for job in pipeline["failed_jobs"]
    ):
        raise ValueError(f"{context}.failed_jobs must be an array of strings")


def validate_output(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("output schema_version must be 1")
    if not isinstance(payload.get("triaged_at"), str):
        raise ValueError("triaged_at must be a string")
    if not isinstance(payload.get("warnings"), list) or not all(
        isinstance(warning, str) for warning in payload["warnings"]
    ):
        raise ValueError("top-level warnings must be an array of strings")
    repositories = payload.get("repositories")
    if not isinstance(repositories, list):
        raise ValueError("repositories must be an array")

    for index, repository in enumerate(repositories):
        if not isinstance(repository, dict):
            raise ValueError(f"repository {index} must be an object")
        if repository.get("action") not in ACTIONS:
            raise ValueError(f"repository {index} has an invalid action")
        branch = repository.get("default_branch")
        comparison = repository.get("comparison")
        if not isinstance(branch, dict) or not isinstance(comparison, dict):
            raise ValueError(f"repository {index} has invalid branch or comparison data")
        validate_pipeline(branch.get("pipeline"), f"repository {index}.default_branch")
        tag = repository.get("latest_tag")
        if tag is not None:
            if not isinstance(tag, dict):
                raise ValueError(f"repository {index}.latest_tag must be an object or null")
            validate_pipeline(tag.get("pipeline"), f"repository {index}.latest_tag")
        if comparison.get("default_branch_pipeline_state") not in PIPELINE_STATES:
            raise ValueError(f"repository {index} has an invalid branch comparison state")
        tag_state = comparison.get("tag_pipeline_state")
        if tag_state is not None and tag_state not in PIPELINE_STATES:
            raise ValueError(f"repository {index} has an invalid tag comparison state")
        if not isinstance(repository.get("warnings"), list):
            raise ValueError(f"repository {index}.warnings must be an array")


def atomic_write(payload: dict[str, Any], destination: Path) -> None:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temporary = output_file.name
            json.dump(payload, output_file, indent=2)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        with open(temporary, encoding="utf-8") as input_file:
            validate_output(json.load(input_file))
        os.replace(temporary, destination)
    except BaseException:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
        raise


def main() -> None:
    global REQUEST_TIMEOUT, REQUEST_RETRIES
    args = parse_args()
    if not 1 <= args.workers <= 8:
        raise ValueError("--workers must be between 1 and 8")
    if args.request_timeout < 1:
        raise ValueError("--request-timeout must be positive")
    if not 0 <= args.retries <= 5:
        raise ValueError("--retries must be between 0 and 5")
    if args.repository and args.only_repository:
        raise ValueError("--repository and --only-repository cannot be combined")
    REQUEST_TIMEOUT = args.request_timeout
    REQUEST_RETRIES = args.retries
    if shutil.which("glab") is None:
        raise RuntimeError("glab is not installed or is not available on PATH")

    if args.repository:
        candidates = [
            {"repository_url": repository, "umpire_issue_url": None}
            for repository in args.repository
        ]
        seen: set[str] = set()
        for candidate in candidates:
            normalized, _ = normalize_repository_url(candidate["repository_url"])
            if normalized in seen:
                raise ValueError(f"duplicate repository URL: {normalized}")
            seen.add(normalized)
        input_label = "direct repository argument"
    else:
        input_path = args.input.expanduser().resolve()
        input_bytes = input_path.read_bytes()
        candidates = validate_candidates(json.loads(input_bytes))
        if args.only_repository:
            requested = {
                normalize_repository_url(repository)[0]
                for repository in args.only_repository
            }
            available = {
                normalize_repository_url(candidate["repository_url"])[0]
                for candidate in candidates
            }
            missing = requested - available
            if missing:
                raise ValueError(
                    "--only-repository not present in input: " + ", ".join(sorted(missing))
                )
            candidates = [
                candidate
                for candidate in candidates
                if normalize_repository_url(candidate["repository_url"])[0] in requested
            ]
        input_label = str(input_path)

    repositories = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(triage, candidate): candidate for candidate in candidates}
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                repositories.append(future.result())
            except Exception as exc:
                repositories.append(failed_result(candidate, exc))
    repositories.sort(key=lambda item: item["repository_url"])
    input_provenance = None
    if not args.repository:
        input_provenance = {
            "path": str(input_path),
            "sha256": hashlib.sha256(input_bytes).hexdigest(),
        }
    output = {
        "schema_version": 1,
        "triaged_at": dt.datetime.now(dt.timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "repositories": repositories,
        "warnings": [],
        "input": input_provenance,
    }
    validate_output(output)
    atomic_write(output, args.output)

    destination = args.output.expanduser().resolve()
    print(f"Input: {input_label}")
    print(f"Output path: {destination}")
    print(f"Repositories examined: {len(repositories)}")
    errors = sum(repository.get("status") == "error" for repository in repositories)
    print(f"Repository classification errors: {errors}")
    counts = Counter(repository["action"] for repository in repositories)
    print("Actions:")
    for action in sorted(counts):
        print(f"- {action}: {counts[action]}")
    print("\nRepository | branch state | latest tag | tag state | same SHA | action")
    for repository in repositories:
        comparison = repository["comparison"]
        tag = repository["latest_tag"]
        print(
            f"- {repository['repository_url']} | "
            f"{comparison['default_branch_pipeline_state']} | "
            f"{tag['name'] if tag else 'none'} | "
            f"{comparison['tag_pipeline_state'] or 'n/a'} | "
            f"{comparison['same_commit'] if comparison['same_commit'] is not None else 'n/a'} | "
            f"{repository['action']}"
        )
        for warning in repository["warnings"]:
            print(f"  warning: {warning}")


if __name__ == "__main__":
    main()
