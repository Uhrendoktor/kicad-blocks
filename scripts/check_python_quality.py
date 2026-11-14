#!/usr/bin/env python3
"""Ratchet Python annotation, documentation, and suppression debt on changed files."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPRESSION_RE = re.compile(
    r"#\s*(?:noqa\b|type:\s*ignore\b|pyright:\s*ignore\b|mypy:\s*ignore\b)"
)


@dataclass(frozen=True)
class QualityMetrics:
    """Static debt counts measured in annotations, documentation, and suppressions."""

    missing_annotations: int
    explicit_any: int
    generic_object: int
    inline_suppressions: int
    documentation_debt: int


def _attribute_root(node: ast.AST) -> str | None:
    """Return the root name of a dotted attribute expression."""
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _is_annotated_argument_missing(argument: ast.arg) -> bool:
    """Return whether a non-self argument lacks its annotation."""
    return argument.arg not in {"self", "cls"} and argument.annotation is None


def analyze_source(source: str, path: str = "") -> QualityMetrics:
    """Count annotation escape hatches, untyped signatures, docs debt, and suppressions."""
    tree = ast.parse(source)
    typing_modules: set[str] = set()
    builtins_modules: set[str] = set()
    any_names: set[str] = set()
    object_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"typing", "typing_extensions"}:
                    typing_modules.add(alias.asname or alias.name)
                elif alias.name == "builtins":
                    builtins_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module in {"typing", "typing_extensions"}:
                any_names.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "Any"
                )
            elif node.module == "builtins":
                object_names.update(
                    alias.asname or alias.name
                    for alias in node.names if alias.name == "object"
                )

    annotations: list[ast.expr] = []
    missing_annotations = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = [
                *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
            ]
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            missing_annotations += sum(
                _is_annotated_argument_missing(argument) for argument in arguments
            )
            if node.returns is None:
                missing_annotations += 1
            else:
                annotations.append(node.returns)
            annotations.extend(
                argument.annotation for argument in arguments
                if argument.annotation is not None
            )
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)

    explicit_any = 0
    generic_object = 0
    for annotation in annotations:
        for node in ast.walk(annotation):
            if isinstance(node, ast.Name):
                if node.id == "Any" or node.id in any_names:
                    explicit_any += 1
                if node.id == "object" or node.id in object_names:
                    generic_object += 1
            elif isinstance(node, ast.Attribute):
                root = _attribute_root(node)
                if node.attr == "Any" and root in typing_modules:
                    explicit_any += 1
                if node.attr == "object" and root in builtins_modules:
                    generic_object += 1

    documentation_debt = 0
    if path.startswith(("plugin/kicad-blocks/kicad_blocks/", "scripts/")):
        documentation_debt += int(ast.get_docstring(tree) is None)
        documentation_debt += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.name.startswith("_")
            and ast.get_docstring(node) is None
            for node in tree.body
        )
    return QualityMetrics(
        missing_annotations,
        explicit_any,
        generic_object,
        len(SUPPRESSION_RE.findall(source)),
        documentation_debt,
    )


def _git(*arguments: str, allow_failure: bool = False) -> str:
    """Run one read-only Git command from the repository root."""
    result = subprocess.run(
        ("git", *arguments), cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode and not allow_failure:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(arguments)} failed")
    return result.stdout


def _changed_python_paths(base: str) -> list[tuple[str, str]]:
    """Return (current path, base path) pairs, retaining rename ancestry."""
    output = _git("diff", "--name-status", "--find-renames", f"{base}...HEAD")
    changes: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if not fields:
            continue
        status = fields[0]
        if status.startswith(("R", "C")) and len(fields) >= 3:
            changes.append((fields[2], fields[1]))
        elif status == "D":
            continue
        elif len(fields) >= 2 and fields[1].endswith((".py", ".pyi")):
            changes.append((fields[1], fields[1]))
    return changes


def _source_at(base: str, path: str) -> str:
    """Read a file from the comparison commit; a missing path denotes a new file."""
    return _git("show", f"{base}:{path}", allow_failure=True)


def _validate_new_module_inventory(path: str) -> str | None:
    """Require each new production module to enter or be excluded from Pyright."""
    prefix = "plugin/kicad-blocks/kicad_blocks/"
    if not path.startswith(prefix) or not path.endswith((".py", ".pyi")):
        return None
    import json
    try:
        config = json.loads((ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return f"cannot read pyrightconfig.json: {error}"
    if path in set(config.get("include", [])):
        return None
    if any(fnmatch.fnmatch(path, pattern) for pattern in config.get("exclude", [])):
        return None
    return (
        f"{path}: new production module is missing from pyrightconfig.json include "
        "or a narrowly justified exclude entry"
    )


def validate_changed_files(base: str) -> list[str]:
    """Reject changed-file increases in debt and unreviewed production modules."""
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("The comparison base must be a full 40-character commit SHA.")
    _git("cat-file", "-e", f"{base}^{{commit}}")
    failures: list[str] = []
    for current_path, base_path in _changed_python_paths(base):
        current_file = ROOT / current_path
        if not current_file.is_file():
            continue
        try:
            current_source = current_file.read_text(encoding="utf-8")
            base_source = _source_at(base, base_path)
            previous = (
                analyze_source(base_source, base_path)
                if base_source else QualityMetrics(0, 0, 0, 0, 0)
            )
            current = analyze_source(current_source, current_path)
        except (OSError, SyntaxError) as error:
            failures.append(f"{current_path}: cannot analyze changed source: {error}")
            continue
        for field in (
            "missing_annotations", "explicit_any", "generic_object",
            "inline_suppressions", "documentation_debt",
        ):
            old_count = getattr(previous, field)
            new_count = getattr(current, field)
            if new_count > old_count:
                failures.append(
                    f"{current_path}: {field} increased from {old_count} to {new_count} "
                    f"(base path: {base_path})"
                )
        if not base_source:
            inventory_error = _validate_new_module_inventory(current_path)
            if inventory_error:
                failures.append(inventory_error)
    return failures


def main() -> int:
    """Run the changed-file ratchet against the supplied comparison commit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Full SHA of the target-branch base commit.")
    arguments = parser.parse_args()
    try:
        failures = validate_changed_files(arguments.base)
    except (OSError, RuntimeError, ValueError, SyntaxError) as error:
        print(f"python-quality-ratchet=failed reason={error}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(f"::error::{failure}", file=sys.stderr)
        print(f"python-quality-ratchet=failed findings={len(failures)}")
        return 1
    print("python-quality-ratchet=passed changed_files_checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
