# Copyright (c) 2026 KiCad Blocks contributors.
# ruff: noqa: INP001
"""Apply all Ruff rules and reject diagnostics introduced by either-parent merges."""

from __future__ import annotations

import io
import json
import subprocess  # noqa: S404
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
_DiagnosticKey = tuple[str, str, str]


class _Finding(TypedDict, total=False):
    """Subset of Ruff's JSON diagnostic schema consumed by this gate."""

    filename: str
    code: str
    message: str


def _git(arguments: list[str], *, binary: bool = False) -> str | bytes:
    """Run a bounded Git query against this checkout.

    Raises:
        RuntimeError: If Git fails or exceeds the query time limit.
    """
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=not binary,
            timeout=120,
        )  # noqa: S603
    except subprocess.TimeoutExpired as error:
        message = f"Git query timed out: {' '.join(arguments)}"
        raise RuntimeError(message) from error
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace") if binary else result.stderr
        message = f"Git query failed: {' '.join(arguments)}: {stderr.strip()}"
        raise RuntimeError(message)
    return result.stdout


def _head_parents() -> tuple[str, ...]:
    """Return every parent of HEAD, including both sides of a PR merge commit.

    Raises:
        RuntimeError: If HEAD has no parent commit.
    """
    revision = _git(["rev-list", "--parents", "-n", "1", "HEAD"])
    if not isinstance(revision, str):
        raise RuntimeError("Git returned an unexpected binary revision record.")
    fields = revision.split()
    if len(fields) < 2:
        raise RuntimeError("Ruff baseline comparison requires at least one parent of HEAD.")
    return tuple(fields[1:])


def _python_files(root: Path) -> list[str]:
    """List Python sources and stubs in an extracted Git tree."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".pyi"}
    )


def _tracked_python_files() -> list[str]:
    """Return tracked Python sources and stubs from the active checkout."""
    output = _git(["ls-files", "-z", "--", "*.py", "*.pyi"])
    if not isinstance(output, str):
        raise RuntimeError("Git returned an unexpected binary file list.")
    return sorted(path for path in output.split("\0") if path)


def _ruff_findings(paths: list[str], *, cwd: Path) -> list[_Finding]:
    """Collect full-policy Ruff diagnostics without stopping on existing findings.

    Raises:
        RuntimeError: If Ruff times out, cannot run, or returns invalid JSON.
    """
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
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=240,
        )  # noqa: S603
    except subprocess.TimeoutExpired as error:
        message = "Ruff check exceeded the 240-second CI limit."
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


def _diagnostic_key(finding: _Finding, *, root: Path) -> _DiagnosticKey:
    """Normalize one diagnostic to path, rule code, and message."""
    filename = Path(finding.get("filename", ""))
    try:
        relative = (
            filename.relative_to(root)
            if filename.is_absolute()
            else (root / filename).resolve().relative_to(root)
        )
    except ValueError:
        relative = filename
    return (
        relative.as_posix(),
        finding.get("code", ""),
        finding.get("message", ""),
    )


def _diagnostic_counts(
    findings: list[_Finding],
    *,
    root: Path,
) -> Counter[_DiagnosticKey]:
    """Count diagnostics by stable identity, independent of shifted line numbers."""
    return Counter(_diagnostic_key(item, root=root) for item in findings)


def _parent_findings(revision: str) -> tuple[Counter[_DiagnosticKey], int]:
    """Run Ruff against one parent tree materialized from the local Git object.

    Returns:
        Ruff diagnostics and the number of Python sources checked.

    Raises:
        RuntimeError: If the parent tree cannot be extracted or Ruff cannot run.
    """
    archive = _git(["archive", "--format=tar", revision], binary=True)
    if not isinstance(archive, bytes):
        raise RuntimeError("Git returned an unexpected text archive.")
    with tempfile.TemporaryDirectory(prefix="ruff-parent-baseline-") as temporary:
        root = Path(temporary)
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
                bundle.extractall(path=root, filter="data")
        except (OSError, tarfile.TarError) as error:
            message = f"Unable to extract Ruff baseline tree {revision[:12]}."
            raise RuntimeError(message) from error
        paths = _python_files(root)
        findings = _ruff_findings(paths, cwd=root)
        return _diagnostic_counts(findings, root=root), len(paths)


def main() -> int:
    """Compare full Ruff results against every parent of the current merge.

    Returns:
        Zero when no new diagnostics were introduced; one when the policy or a tool fails.
    """
    try:
        parents = _head_parents()
        baseline: Counter[_DiagnosticKey] = Counter()
        baseline_sources = 0
        for parent in parents:
            counts, source_count = _parent_findings(parent)
            baseline_sources = max(baseline_sources, source_count)
            for key, count in counts.items():
                baseline[key] = max(baseline[key], count)

        current_paths = _tracked_python_files()
        current_findings = _ruff_findings(current_paths, cwd=ROOT)
        current = _diagnostic_counts(current_findings, root=ROOT)
    except (OSError, RuntimeError, ValueError) as error:
        sys.stderr.write(f"ruff-quality-gate=failed: {error}\\n")
        return 1

    remaining = {
        key: count - baseline.get(key, 0)
        for key, count in current.items()
        if count > baseline.get(key, 0)
    }
    introduced: list[_Finding] = []
    for finding in current_findings:
        key = _diagnostic_key(finding, root=ROOT)
        if remaining.get(key, 0) > 0:
            introduced.append(finding)
            remaining[key] -= 1

    if introduced:
        introduced.sort(
            key=lambda item: (
                item.get("filename", ""),
                item.get("code", ""),
                item.get("message", ""),
            )
        )
        sys.stderr.write(f"ruff-quality-gate=failed introduced_diagnostics={len(introduced)}\\n")
        for finding in introduced:
            sys.stderr.write(
                f"{finding.get('filename', '')}: "
                f"{finding.get('code', 'RUFF')}: "
                f"{finding.get('message', '')}\\n"
            )
        return 1

    sys.stdout.write(
        "ruff-quality-gate=passed "
        f"tracked_python_files={len(current_paths)} "
        f"merge_parents={len(parents)} "
        f"baseline_python_files={baseline_sources} "
        f"current_diagnostics={len(current_findings)}\\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
