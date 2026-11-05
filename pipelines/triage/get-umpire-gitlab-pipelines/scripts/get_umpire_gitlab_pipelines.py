#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


GITLAB_HOST = "code.cdso.army.mil"
PROJECT_ID = "29112"
TITLE_PREFIX = "Umpire:"
REQUIRED_LABEL = "TM::Pipelines"
PER_PAGE = 100
DEFAULT_WORKERS = 6
DEFAULT_REQUEST_TIMEOUT = 45
DEFAULT_RETRIES = 2


def run_glab_api(path, params=None, request_timeout=DEFAULT_REQUEST_TIMEOUT, retries=DEFAULT_RETRIES):
    if params is None:
        params = []
    cmd = [
        "glab",
        "api",
        f"projects/{PROJECT_ID}/{path}",
        "--hostname",
        GITLAB_HOST,
    ]
    if params:
        cmd.extend(["-X", "GET"])
        cmd.extend(params)
    result = None
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=request_timeout,
            )
            break
        except FileNotFoundError as exc:
            raise RuntimeError(
                "glab is not installed or is not available on PATH"
            ) from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = (
                (exc.stderr or exc.stdout or str(exc))
                if isinstance(exc, subprocess.CalledProcessError)
                else f"request timed out after {request_timeout} seconds"
            )
            if attempt == retries:
                raise RuntimeError(
                    f"glab api failed for {path} after {attempt + 1} attempts: "
                    f"{str(detail).strip()}"
                ) from exc
            time.sleep(min(2 ** attempt, 4))
    if result is None:
        raise RuntimeError(f"glab api failed for {path}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from glab api for {path}: {exc}") from exc


PIPELINE_URL_RE = re.compile(
    r"https://" + re.escape(GITLAB_HOST) + r"/[^\s)]+?/-/pipelines/\d+"
)
MAIN_PIPELINE_RE = re.compile(
    r"^\s*-?\s*Latest pipeline:\s*(" + PIPELINE_URL_RE.pattern + r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)
def extract_main_pipeline_url(text):
    match = MAIN_PIPELINE_RE.search(text or "")
    return match.group(1) if match else None


def derive_repo_url(pipeline_url):
    if not pipeline_url:
        return None
    return re.sub(r"/-/pipelines/\d+.*$", "", pipeline_url)


def get_best_observation(issue, request_timeout, retries):
    iid = issue["iid"]
    description = issue.get("description") or ""
    best_body = description
    best_created_at = issue.get("created_at")

    page = 1
    while True:
        notes = run_glab_api(f"issues/{iid}/notes", [
            "-F",
            f"page={page}",
            "-F",
            f"per_page={PER_PAGE}",
            "-F",
            "order_by=created_at",
            "-F",
            "sort=desc",
        ], request_timeout=request_timeout, retries=retries)
        if not notes:
            break
        found = False
        for note in notes:
            body = note.get("body") or ""
            if body.startswith("Umpire update: pipeline still failing"):
                best_body = body
                best_created_at = note.get("created_at")
                found = True
                break
        if found:
            break
        page += 1

    return best_body, best_created_at


def collect_issues(request_timeout, retries):
    issues = []
    page = 1
    while True:
        page_issues = run_glab_api(
            "issues",
            [
                "-F",
                f"page={page}",
                "-F",
                f"per_page={PER_PAGE}",
                "-F",
                "state=opened",
                "-F",
                f"labels={REQUIRED_LABEL}",
            ],
            request_timeout=request_timeout,
            retries=retries,
        )
        if not page_issues:
            break
        issues.extend(page_issues)
        page += 1
    return issues


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect open Umpire GitLab pipeline-failure candidates."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="~/.local/state/pipeline-repair/umpire-candidates.json",
        help="destination JSON file (default: %(default)s)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="concurrent issue-note requests (default: %(default)s)",
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


def validate_payload(payload):
    if payload.get("schema_version") != 2:
        raise ValueError("schema_version must be 2")
    if not isinstance(payload.get("collected_at"), str):
        raise ValueError("collected_at must be a string")
    if not isinstance(payload.get("warnings"), list) or not all(
        isinstance(warning, str) for warning in payload["warnings"]
    ):
        raise ValueError("warnings must be an array of strings")
    if not isinstance(payload.get("candidates"), list):
        raise ValueError("candidates must be an array")

    required_strings = ("repository_url", "umpire_issue_url")
    for index, candidate in enumerate(payload["candidates"]):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {index} must be an object")
        if set(candidate) != set(required_strings):
            raise ValueError(
                f"candidate {index} must contain only repository_url and umpire_issue_url"
            )
        for field in required_strings:
            if not isinstance(candidate.get(field), str) or not candidate[field]:
                raise ValueError(f"candidate {index}.{field} must be a nonempty string")


def main():
    args = parse_args()
    if not 1 <= args.workers <= 16:
        raise ValueError("--workers must be between 1 and 16")
    if args.request_timeout < 1:
        raise ValueError("--request-timeout must be positive")
    if not 0 <= args.retries <= 5:
        raise ValueError("--retries must be between 0 and 5")
    if shutil.which("glab") is None:
        raise RuntimeError("glab is not installed or is not available on PATH")

    dest = os.path.abspath(os.path.expanduser(args.output))
    dest_dir = os.path.dirname(dest)
    os.makedirs(dest_dir, exist_ok=True)

    warnings = []
    issues = collect_issues(args.request_timeout, args.retries)

    candidates_by_repo = {}
    omitted_no_pipeline = []
    omitted_duplicates = []

    retained_issues = []
    for issue in issues:
        if issue.get("state") != "opened":
            continue
        title = issue.get("title") or ""
        if not title.startswith(TITLE_PREFIX):
            continue
        labels = issue.get("labels") or []
        if REQUIRED_LABEL not in labels:
            continue
        retained_issues.append(issue)

    observations = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                get_best_observation,
                issue,
                args.request_timeout,
                args.retries,
            ): issue
            for issue in retained_issues
        }
        for future in as_completed(futures):
            issue = futures[future]
            try:
                observations[issue["iid"]] = future.result()
            except Exception as exc:
                warnings.append(
                    f"Issue IID {issue.get('iid', 'unknown')}: notes unavailable; "
                    f"using issue description ({exc})"
                )
                observations[issue["iid"]] = (
                    issue.get("description") or "",
                    issue.get("created_at"),
                )

    for issue in retained_issues:
        observation_body, observation_created_at = observations[issue["iid"]]

        reported_pipeline_url = extract_main_pipeline_url(observation_body)
        if not reported_pipeline_url:
            omitted_no_pipeline.append(issue["iid"])
            warnings.append(
                f"Issue IID {issue['iid']}: no main pipeline URL found in observation"
            )
            continue

        repo_url = derive_repo_url(reported_pipeline_url)
        if not repo_url:
            omitted_no_pipeline.append(issue["iid"])
            warnings.append(
                f"Issue IID {issue['iid']}: could not derive repository URL from {reported_pipeline_url}"
            )
            continue

        issue_url = issue.get("web_url")
        if not isinstance(issue_url, str) or not issue_url:
            warnings.append(f"Issue IID {issue['iid']}: missing issue URL")
            continue

        candidate = {
            "repository_url": repo_url,
            "umpire_issue_url": issue_url,
            "_observation_created_at": observation_created_at,
            "_iid": issue["iid"],
        }

        existing = candidates_by_repo.get(repo_url)
        if existing is None:
            candidates_by_repo[repo_url] = candidate
        else:
            prev_ts = existing["_observation_created_at"] or ""
            new_ts = observation_created_at or ""
            if new_ts > prev_ts:
                omitted_duplicates.append(existing["_iid"])
                candidates_by_repo[repo_url] = candidate
            else:
                omitted_duplicates.append(candidate["_iid"])

    if omitted_duplicates:
        warnings.append(
            "Duplicate repositories; omitted issue IIDs: "
            + ", ".join(str(i) for i in sorted(set(omitted_duplicates)))
        )

    candidates = list(candidates_by_repo.values())
    for c in candidates:
        c.pop("_iid", None)
        c.pop("_observation_created_at", None)
    candidates.sort(key=lambda c: c["repository_url"])

    payload = {
        "schema_version": 2,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates,
        "warnings": warnings,
    }

    validate_payload(payload)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=dest_dir,
            prefix=f".{os.path.basename(dest)}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            tmp = output_file.name
            json.dump(payload, output_file, indent=2)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        with open(tmp, "r", encoding="utf-8") as input_file:
            validate_payload(json.load(input_file))
        os.replace(tmp, dest)
    except Exception:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
        raise

    print(f"Output path: {dest}")
    print(f"Open Umpire issues examined: {len(issues)}")
    print(f"Candidates collected: {len(candidates)}")
    print(
        "Issues omitted (no main pipeline URL): "
        + (", ".join(str(i) for i in omitted_no_pipeline) if omitted_no_pipeline else "none")
    )
    if omitted_duplicates:
        print(
            "Duplicates omitted (by repository URL): "
            + ", ".join(str(i) for i in sorted(set(omitted_duplicates)))
        )
    else:
        print("Duplicates omitted (by repository URL): none")
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"- {w}")
    else:
        print("Warnings: none")

    if candidates:
        print("\nCollected repositories:")
        for c in candidates:
            print(f"- {c['repository_url']}")


if __name__ == "__main__":
    main()
