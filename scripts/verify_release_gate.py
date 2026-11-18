#!/usr/bin/env python3
"""Require a successful main-branch CI run for the exact release-tag commit."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen
from typing import TypedDict, cast

try:
    from .github_api_types import JSONValue, JSONObject
except ImportError:  # pragma: no cover - direct script execution.
    from github_api_types import JSONValue, JSONObject


class WorkflowRun(TypedDict, total=False):
    """GitHub Actions run fields checked by the release gate."""

    id: int
    head_sha: str
    head_branch: str
    event: str
    path: str
    status: str
    conclusion: str
    html_url: str




def verify_release_gate(
    repo: str, tag: str, version_file: Path, source_sha: str, token: str
) -> WorkflowRun:
    version = version_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid VERSION file value: {version!r}.")
    if tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} does not match release version {version!r}.")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Release source SHA must be a 40-character lowercase commit hash.")

    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    url = f"{api}/repos/{repo}/actions/runs?head_sha={source_sha}&per_page=100"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    last_runs: list[WorkflowRun] = []
    for attempt in range(6):
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            payload_value: JSONValue = cast(
                JSONValue, json.loads(response.read().decode("utf-8"))
            )
        if not isinstance(payload_value, dict):
            raise RuntimeError("GitHub Actions API returned a non-object response.")
        payload = cast(JSONObject, payload_value)
        runs_value = payload.get("workflow_runs", [])
        if not isinstance(runs_value, list):
            raise RuntimeError("GitHub Actions API response has an invalid workflow_runs array.")
        last_runs = []
        for run_value in runs_value:
            if not isinstance(run_value, dict):
                continue
            run_record = cast(JSONObject, run_value)
            string_fields = ("head_sha", "head_branch", "event", "path", "status", "conclusion")
            if any(not isinstance(run_record.get(key), str) for key in string_fields):
                continue
            last_runs.append(cast(WorkflowRun, run_record))
        passing = [
            run
            for run in last_runs
            if run.get("head_sha") == source_sha
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and run.get("path") == ".github/workflows/ci.yml"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
        ]
        if passing:
            return passing[0]
        if attempt < 5:
            time.sleep(10)
    summary = [
        {
            "id": run.get("id"),
            "branch": run.get("head_branch"),
            "event": run.get("event"),
            "path": run.get("path"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "head_sha": run.get("head_sha"),
        }
        for run in last_runs
    ]
    raise RuntimeError(
        "No successful main push CI run exists for the exact release commit "
        f"{source_sha}. Observed runs: {summary!r}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version-file", type=Path, default=Path("VERSION"))
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required.")
    if not token:
        parser.error("GITHUB_TOKEN is required.")
    run = verify_release_gate(args.repo, args.tag, args.version_file, args.source_sha, token)
    print(f"Release gate passed with main CI run {run.get('html_url', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
