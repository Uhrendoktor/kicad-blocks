#!/usr/bin/env python3
"""Enforce syntax-aware Python policy and reviewed type-checking boundaries."""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = ("examples", "plugin", "scripts", "tests")
# The caller-supplied parameter value is stringified and validated before it
# enters the model; all other generic object annotations are prohibited.
ALLOWED_OBJECT_ANNOTATIONS = {
    ("plugin/kicad-blocks/kicad_blocks/model.py", "validate_parameter_value.value"),
}
DYNAMIC_HOST_EXCLUSIONS = {
    "plugin/kicad-blocks/kicad_blocks/plugin.py",
}
SUPPRESSION_PATTERN = re.compile(
    r"#\s*(?:noqa\b|type:\s*ignore\b|pyright:\s*ignore\b|mypy:\s*ignore\b)",
    re.IGNORECASE,
)


def _python_files() -> list[Path]:
    """Return every repository Python source covered by this policy."""
    return sorted(
        path
        for root_name in PYTHON_ROOTS
        for path in (ROOT / root_name).rglob("*.py")
        if path.is_file()
    )


def _explicit_any_lines(tree: ast.AST) -> list[int]:
    """Find Any imports and references, including qualified and aliased imports."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name) and node.id == "Any"
            or isinstance(node, ast.Attribute) and node.attr == "Any"
        ):
            lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom) and any(
            alias.name == "Any" for alias in node.names
        ):
            lines.add(node.lineno)
        elif isinstance(node, ast.Import) and any(
            alias.name.endswith(".Any") for alias in node.names
        ):
            lines.add(node.lineno)
    return sorted(lines)


def _has_object_annotation(annotation: ast.expr) -> bool:
    """Identify generic object types, including nested and qualified annotations."""
    return any(
        (isinstance(node, ast.Name) and node.id == "object")
        or (isinstance(node, ast.Attribute) and node.attr == "object")
        for node in ast.walk(annotation)
    )


def _is_os_system_call(node: ast.Call) -> bool:
    """Identify os.system without flagging unrelated methods named system."""
    function = node.func
    return (
        isinstance(function, ast.Attribute)
        and function.attr == "system"
        and isinstance(function.value, ast.Name)
        and function.value.id == "os"
    )


def _check_file(path: Path) -> list[str]:
    """Return policy violations for one source file."""
    relative = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    errors: list[str] = []

    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError as exc:
        return [f"{relative}:{exc.lineno}: syntax error: {exc.msg}"]

    if (
        relative.startswith(("plugin/", "scripts/", "examples/"))
        and ast.get_docstring(tree) is None
    ):
        errors.append(f"{relative}: missing module docstring")

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and SUPPRESSION_PATTERN.search(token.string):
            errors.append(
                f"{relative}:{token.start[0]}: inline diagnostic suppression is not allowed"
            )

    errors.extend(
        f"{relative}:{line}: explicit Any is prohibited"
        for line in _explicit_any_lines(tree)
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name) and function.id in {"eval", "exec"}:
                errors.append(
                    f"{relative}:{node.lineno}: dynamic {function.id}() execution is not allowed"
                )
            if _is_os_system_call(node):
                errors.append(f"{relative}:{node.lineno}: os.system() is not allowed")

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
            arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            for argument in arguments:
                allowed = (
                    isinstance(argument.annotation, ast.Name)
                    and argument.annotation.id == "object"
                    and (relative, f"{owner}.{argument.arg}") in ALLOWED_OBJECT_ANNOTATIONS
                )
                if (
                    argument.annotation is not None
                    and _has_object_annotation(argument.annotation)
                    and not allowed
                ):
                    errors.append(
                        f"{relative}:{argument.lineno}: generic object annotation needs an explicit reviewed boundary"
                    )
            for argument in (node.args.vararg, node.args.kwarg):
                if (
                    argument is not None
                    and argument.annotation is not None
                    and _has_object_annotation(argument.annotation)
                ):
                    errors.append(
                        f"{relative}:{argument.lineno}: generic object annotation needs an explicit reviewed boundary"
                    )
            if node.returns is not None and _has_object_annotation(node.returns):
                errors.append(
                    f"{relative}:{node.returns.lineno}: generic object return annotation is not allowed"
                )
        elif isinstance(node, ast.AnnAssign) and _has_object_annotation(node.annotation):
            errors.append(
                f"{relative}:{node.annotation.lineno}: generic object variable annotation is not allowed"
            )

    return errors


def _check_pyright_inventory() -> list[str]:
    """Ensure every core module is typed or explicitly excluded as host glue."""
    config_path = ROOT / "pyrightconfig.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"pyrightconfig.json: cannot read reviewed inventory: {exc}"]

    included = [ROOT / entry for entry in config.get("include", [])]
    excluded = set(config.get("exclude", []))
    errors = []
    core = ROOT / "plugin/kicad-blocks/kicad_blocks"
    for path in sorted(core.glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in DYNAMIC_HOST_EXCLUSIONS:
            if relative not in excluded:
                errors.append(
                    f"pyrightconfig.json: dynamic host adapter {relative} must remain explicitly excluded"
                )
            continue
        if not any(path == entry or entry in path.parents for entry in included):
            errors.append(
                f"pyrightconfig.json: production module {relative} is absent from the reviewed include inventory"
            )
    return errors


def _annotation_metrics(source: str, path: str) -> tuple[int, int]:
    """Count missing function annotations and generic object annotations in one file."""
    tree = ast.parse(source, filename=path)
    missing = 0
    generic_object = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        missing += sum(
            argument.annotation is None and argument.arg not in {"self", "cls"}
            for argument in arguments
        )
        missing += sum(
            argument is not None and argument.annotation is None
            for argument in (node.args.vararg, node.args.kwarg)
        )
        missing += node.returns is None

    annotations: list[ast.expr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.arg) and node.annotation is not None
            or isinstance(node, ast.AnnAssign)
        ):
            annotations.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            annotations.append(node.returns)
    for annotation in annotations:
        generic_object += sum(
            (isinstance(item, ast.Name) and item.id == "object")
            or (isinstance(item, ast.Attribute) and item.attr == "object")
            for item in ast.walk(annotation)
        )
    return missing, generic_object


def _git(*arguments: str) -> str:
    """Run a read-only Git command in the repository."""
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _check_annotation_ratchet(base_ref: str) -> list[str]:
    """Block new annotation debt per changed file in the Pyright-reviewed inventory."""
    config = json.loads((ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))
    includes = [ROOT / item for item in config.get("include", [])]
    excludes = set(config.get("exclude", []))
    try:
        merge_base = _git("merge-base", base_ref, "HEAD").strip()
        changed = _git(
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            f"{merge_base}...HEAD",
            "--",
            "*.py",
            "*.pyi",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"annotation ratchet: cannot resolve {base_ref!r}: {exc}"]

    errors: list[str] = []
    for relative in (line for line in changed.splitlines() if line):
        path = ROOT / relative
        if any(
            relative == pattern or (pattern.endswith("/**") and relative.startswith(pattern[:-3]))
            for pattern in excludes
        ):
            continue
        if not any(path == item or item in path.parents for item in includes):
            continue
        if not path.is_file():
            continue
        current = path.read_text(encoding="utf-8")
        previous_result = subprocess.run(
            ["git", "show", f"{merge_base}:{relative}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        previous = previous_result.stdout if previous_result.returncode == 0 else ""
        try:
            current_metrics = _annotation_metrics(current, relative)
            previous_metrics = _annotation_metrics(previous, relative) if previous else (0, 0)
        except SyntaxError as exc:
            errors.append(f"{relative}: cannot measure annotation debt: {exc}")
            continue
        for index, label in enumerate(
            ("missing function annotations", "generic object annotations")
        ):
            if current_metrics[index] > previous_metrics[index]:
                errors.append(
                    f"{relative}: {label} increased from {previous_metrics[index]} "
                    f"to {current_metrics[index]}"
                )
    return errors


def main() -> int:
    """Run repository-wide policy, Pyright inventory and changed-file ratchet checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="merge-base reference for the changed-file annotation debt ratchet",
    )
    args = parser.parse_args()
    errors = [error for path in _python_files() for error in _check_file(path)]
    errors.extend(_check_pyright_inventory())
    errors.extend(_check_annotation_ratchet(args.base))
    if errors:
        print("\n".join(errors))
        return 1
    print("python-policy=passed (global Any/suppression/dynamic-call checks; annotation ratchet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
