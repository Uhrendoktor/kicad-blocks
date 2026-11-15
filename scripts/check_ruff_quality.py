"""Apply the full Ruff policy to Python lines changed by the current Git revision."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # noqa: S404
import sys
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
_HUNK = re.compile(r"^@@ -\\d+(?:,\\d+)? \\+(\\d+)(?:,(\\d+))? @@")
_FILE = re.compile(r"^\\+\\+\\+ b/(.+)$")


class _Location(TypedDict, total=False):
    """Ruff diagnostic source range."""

    row: int


class _Finding(TypedDict, total=False):
    """Subset of Ruff's JSON diagnostic schema consumed by this gate."""

    filename: str
    code: str
    message: str
    location: _Location
    end_location: _Location


def _git(arguments: list[str]) -> str:
    """Run a bounded Git query against this checkout."""
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )  # noqa: S603
    except subprocess.TimeoutExpired as error:
        message = f"Git query timed out: {' '.join(arguments)}"
        raise RuntimeError(message) from error
    if result.returncode != 0:
        message = f"Git query failed: {' '.join(arguments)}: {result.stderr.strip()}"
        raise RuntimeError(message)
    return result.stdout


def _base_ref(explicit: str | None) -> str:
    """Resolve the comparison ref for pull-request, push, and local CI runs."""
    if explicit:
        return explicit
    pull_request_base = os.environ.get("GITHUB_BASE_REF")
    if pull_request_base:
        return f"origin/{pull_request_base}"
    push_before = os.environ.get("GITHUB_EVENT_BEFORE")
    if push_before and push_before != "0" * 40:
        return push_before
    return "origin/main"


def added_line_ranges(diff: str) -> dict[str, tuple[tuple[int, int], ...]]:
    """Return zero-context added/modified line ranges by repository-relative path."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current_path: str | None = None
    for line in diff.splitlines():
        file_match = _FILE.match(line)
        if line.startswith("+++ /dev/null"):
            current_path = None
            continue
        if file_match:
            current_path = file_match.group(1)
            ranges.setdefault(current_path, [])
            continue
        hunk_match = _HUNK.match(line)
        if hunk_match is None or current_path is None:
            continue
        first = int(hunk_match.group(1))
        count = int(hunk_match.group(2) or "1")
        if count:
            ranges[current_path].append((first, first + count - 1))
    return {path: tuple(items) for path, items in ranges.items() if items}


def _relative_filename(filename: str) -> str | None:
    """Normalize a Ruff diagnostic filename to a checkout-relative path."""
    candidate = Path(filename)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(ROOT)
        except ValueError:
            return None
    return candidate.as_posix()


def _touches_changed_lines(finding: _Finding, ranges: dict[str, tuple[tuple[int, int], ...]]) -> bool:
    """Return whether a Ruff diagnostic overlaps any added/modified source line."""
    filename = _relative_filename(finding.get("filename", ""))
    if filename is None or filename not in ranges:
        return False
    location = finding.get("location", {})
    end_location = finding.get("end_location", {})
    first = location.get("row", 1)
    last = end_location.get("row", first)
    return any(first <= end and last >= start for start, end in ranges[filename])


def _tracked_python_files() -> list[str]:
    """Return the tracked Python sources and stubs Ruff should inspect."""
    output = _git(["ls-files", "-z", "--", "*.py", "*.pyi"])
    return sorted(path for path in output.split("\\0") if path)


def _ruff_findings(paths: list[str]) -> list[_Finding]:
    """Collect full-policy Ruff diagnostics without failing on legacy baseline debt."""
    if not paths:
        return []
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--exit-zero",
                "--output-format",
                "json",
                *paths,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )  # noqa: S603
    except subprocess.TimeoutExpired as error:
        message = "Ruff check exceeded the 180-second CI limit."
        raise RuntimeError(message) from error
    if result.returncode != 0:
        message = f"Ruff could not complete: {result.stderr.strip()}"
        raise RuntimeError(message)
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        message = "Ruff returned invalid JSON diagnostics."
        raise RuntimeError(message) from error
    return cast(list[_Finding], decoded)


def main() -> int:
    """Run Ruff comprehensively and fail only on diagnostics introduced by this change."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", help="Git ref to compare against; defaults to CI event context.")
    arguments = parser.parse_args()
    try:
        base = _base_ref(arguments.base_ref)
        merge_base = _git(["merge-base", base, "HEAD"]).strip()
        diff = _git(
            [
                "diff",
                "--no-ext-diff",
                "--no-color",
                "--unified=0",
                merge_base,
                "HEAD",
                "--",
                "*.py",
                "*.pyi",
            ]
        )
        ranges = added_line_ranges(diff)
        findings = _ruff_findings(_tracked_python_files())
    except (OSError, RuntimeError, ValueError) as error:
        sys.stderr.write(f"ruff-quality-gate=failed: {error}\\n")
        return 1

    introduced = sorted(
        (item for item in findings if _touches_changed_lines(item, ranges)),
        key=lambda item: (
            item.get("filename", ""),
            item.get("location", {}).get("row", 0),
            item.get("code", ""),
        ),
    )
    if introduced:
        sys.stderr.write(f"ruff-quality-gate=failed introduced_diagnostics={len(introduced)}\\n")
        for item in introduced:
            location = item.get("location", {})
            filename = _relative_filename(item.get("filename", "")) or item.get("filename", "")
            sys.stderr.write(
                f"{filename}:{location.get('row', 1)}: "
                f"{item.get('code', 'RUFF')}: {item.get('message', '')}\\n"
            )
        return 1

    changed_files = len(ranges)
    sys.stdout.write(
        "ruff-quality-gate=passed "
        f"tracked_python_files={len(_tracked_python_files())} "
        f"changed_python_files={changed_files} "
        f"existing_diagnostics={len(findings)}\\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
