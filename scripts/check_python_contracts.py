#!/usr/bin/env python3
"""Repository-wide Python syntax/Any policy and changed-file annotation debt ratchet."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_output(root: Path, *arguments: str) -> str:
    """Run Git and return UTF-8 output from the requested repository."""
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def tracked_python_files(root: Path = ROOT) -> list[Path]:
    """Return every tracked Python source and stub file."""
    output = git_output(root, "ls-files", "-z", "--", "*.py", "*.pyi")
    return [root / value for value in output.split("\0") if value]


def explicit_any_lines(tree: ast.AST) -> list[int]:
    """Locate explicit Any imports, references, and qualified typing.Any attributes."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            lines.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "Any" for alias in node.names):
                lines.add(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name.endswith(".Any") for alias in node.names):
                lines.add(node.lineno)
    return sorted(lines)


def _function_annotations(tree: ast.AST) -> list[ast.expr]:
    """Collect argument and return annotations from all functions, including nested ones."""
    annotations: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        for argument in arguments:
            if argument.arg not in {"self", "cls"} and argument.annotation is not None:
                annotations.append(argument.annotation)
        for argument in (node.args.vararg, node.args.kwarg):
            if argument is not None and argument.annotation is not None:
                annotations.append(argument.annotation)
        if node.returns is not None:
            annotations.append(node.returns)
    return annotations


def annotation_metrics(tree: ast.AST) -> tuple[int, int]:
    """Return missing function-annotation count and generic object-annotation count."""
    missing = 0
    generic_object = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        missing += sum(
            argument.annotation is None and argument.arg not in {"self", "cls"}
            for argument in arguments
        )
        missing += sum(
            argument is not None and argument.annotation is None
            for argument in (node.args.vararg, node.args.kwarg)
        )
        missing += node.returns is None
    for annotation in _function_annotations(tree):
        generic_object += sum(
            isinstance(node, ast.Name) and node.id == "object"
            for node in ast.walk(annotation)
        )
    return missing, generic_object


def changed_python_files(root: Path, base: str) -> list[str]:
    """Return changed Python source paths relative to a merge-base reference."""
    output = git_output(
        root,
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{base}...HEAD",
        "--",
        "*.py",
        "*.pyi",
    )
    return sorted(value for value in output.splitlines() if value)


def _base_source(root: Path, base: str, path: str) -> str | None:
    """Read a file from the merge base; None indicates that it is a new file."""
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{base}:{path}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout if result.returncode == 0 else None


def main() -> int:
    """Run repository-wide syntax/Any checks and the changed-file debt ratchet."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Merge-base ref for the type-debt ratchet.",
    )
    args = parser.parse_args()

    try:
        source_files = tracked_python_files()
        any_errors: list[str] = []
        syntax_errors: list[str] = []
        for path in source_files:
            relative = path.relative_to(ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (OSError, SyntaxError) as error:
                syntax_errors.append(f"{relative}: {error}")
                continue
            any_errors.extend(
                f"{relative}:{line}: explicit Any is prohibited"
                for line in explicit_any_lines(tree)
            )

        changed = changed_python_files(ROOT, args.base)
        ratchet_errors: list[str] = []
        for relative in changed:
            current_path = ROOT / relative
            try:
                current_tree = ast.parse(
                    current_path.read_text(encoding="utf-8"),
                    filename=relative,
                )
            except (OSError, SyntaxError) as error:
                ratchet_errors.append(f"{relative}: cannot measure annotation debt: {error}")
                continue
            current_metrics = annotation_metrics(current_tree)
            previous_source = _base_source(ROOT, args.base, relative)
            previous_metrics = (
                (0, 0)
                if previous_source is None
                else annotation_metrics(ast.parse(previous_source, filename=relative))
            )
            labels = ("missing function annotations", "generic object annotations")
            for index, label in enumerate(labels):
                if current_metrics[index] > previous_metrics[index]:
                    ratchet_errors.append(
                        f"{relative}: {label} increased from "
                        f"{previous_metrics[index]} to {current_metrics[index]}"
                    )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"python-contracts=failed: {error}", file=sys.stderr)
        return 1

    for error in syntax_errors + any_errors + ratchet_errors:
        print(f"::error::{error}", file=sys.stderr)
    if syntax_errors or any_errors or ratchet_errors:
        return 1

    print(
        "python-contracts=passed "
        f"tracked_files={len(source_files)} changed_files={len(changed)} explicit_any=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
