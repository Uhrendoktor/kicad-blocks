#!/usr/bin/env python3
"""Enforce test assertion safety and an incremental Python annotation-debt ratchet."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import re
import tokenize
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SUPPRESSION_PATTERN = re.compile(
    r"#\s*(?:noqa|type:\s*ignore|pyright:\s*ignore|ty:\s*ignore)\b"
)


def bare_assert_lines(path: str, source: str) -> list[int]:
    """Return line numbers of bare assert statements in a test source file."""
    tree = ast.parse(source, filename=path)
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]


def duplicate_test_bodies(files: Iterable[tuple[str, str]]) -> list[str]:
    """Find exact AST-identical test bodies across the supplied source files."""
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for path, source in files:
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            normalized = "\\n".join(
                (
                    ast.dump(node.args, include_attributes=False),
                    "\\n".join(
                        ast.dump(decorator, include_attributes=False)
                        for decorator in node.decorator_list
                    ),
                    ast.dump(
                        ast.Module(body=node.body, type_ignores=[]),
                        include_attributes=False,
                    ),
                )
            )
            fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            identifier = f"{path}:{node.lineno} ({node.name})"
            if fingerprint in seen:
                duplicates.append(f"{seen[fingerprint]} duplicates {identifier}")
            else:
                seen[fingerprint] = identifier
    return duplicates


def annotation_debt(source: str, path: str = "<source>") -> tuple[int, int]:
    """Count Any references in a source file and built-in object names in annotations."""
    tree = ast.parse(source, filename=path)
    any_references = sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
    )
    annotations: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            annotations.append(node.returns)
    object_annotations = sum(
        1
        for annotation in annotations
        for item in ast.walk(annotation)
        if isinstance(item, ast.Name) and item.id == "object"
    )
    return any_references, object_annotations


def suppression_count(source: str) -> int:
    """Count inline checker/linter suppressions in a Python source file."""
    comments = (
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    )
    return sum(1 for comment in comments if SUPPRESSION_PATTERN.search(comment))


def _git(*args: str, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def check_test_sources(root: Path = ROOT) -> list[str]:
    """Check all test sources for optimized-away assertions and exact duplicate bodies."""
    errors: list[str] = []
    files: list[tuple[str, str]] = []
    for path in sorted((root / "tests").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        files.append((relative, source))
        try:
            lines = bare_assert_lines(relative, source)
        except SyntaxError as exc:
            errors.append(f"{relative}:{exc.lineno}: invalid Python syntax: {exc.msg}")
            continue
        errors.extend(
            f"{relative}:{line}: bare assert is removed by Python optimization; "
            "use a unittest assertion or an explicit failure path"
            for line in lines
        )
    errors.extend(duplicate_test_bodies(files))
    return errors


def check_annotation_ratchet(base_ref: str, root: Path = ROOT) -> list[str]:
    """Block added Any/object annotation debt and inline suppressions in changed Python files."""
    merge_base = _git("merge-base", base_ref, "HEAD", cwd=root).strip()
    changed = _git(
        "diff",
        "--name-only",
        "--diff-filter=ACMRT",
        f"{merge_base}...HEAD",
        "--",
        "*.py",
        "*.pyi",
        cwd=root,
    )
    errors: list[str] = []
    for relative in (line for line in changed.splitlines() if line):
        current_path = root / relative
        if not current_path.is_file():
            continue
        current = current_path.read_text(encoding="utf-8")
        previous_result = subprocess.run(
            ["git", "show", f"{merge_base}:{relative}"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        previous = previous_result.stdout if previous_result.returncode == 0 else ""
        try:
            current_any, current_object = annotation_debt(current, relative)
            previous_any, previous_object = annotation_debt(previous, relative)
        except SyntaxError as exc:
            errors.append(f"{relative}:{exc.lineno}: invalid Python syntax: {exc.msg}")
            continue
        current_suppressions = suppression_count(current)
        previous_suppressions = suppression_count(previous)
        if current_any > previous_any:
            errors.append(
                f"{relative}: explicit Any references increased from {previous_any} "
                f"to {current_any}"
            )
        if current_object > previous_object:
            errors.append(
                f"{relative}: generic object annotations increased from {previous_object} "
                f"to {current_object}"
            )
        if current_suppressions > previous_suppressions:
            errors.append(
                f"{relative}: inline noqa/type-check suppressions increased from "
                f"{previous_suppressions} to {current_suppressions}"
            )
    return errors


def main() -> int:
    """Run repository-wide test checks and the optional changed-file type ratchet."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        help="merge-base reference used to prevent new Any/object annotations or suppressions",
    )
    args = parser.parse_args()
    errors = check_test_sources()
    if args.base:
        try:
            errors.extend(check_annotation_ratchet(args.base))
        except RuntimeError as exc:
            errors.append(f"could not evaluate annotation ratchet: {exc}")

    if errors:
        print("Python quality checks failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Python quality checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
