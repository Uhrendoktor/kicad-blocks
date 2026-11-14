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
# Every Pyright exclusion is exact and carries its migration/host-boundary reason.
# These files remain under repository-wide AST checks, Ruff/Vulture, and the
# changed-file annotation ratchet; they are not invisible to quality gates.
REVIEWED_PYRIGHT_EXCLUSIONS = {
    "plugin/kicad-blocks/kicad_blocks/plugin.py": (
        "KiCad/Qt GUI adapter depends on dynamically registered host objects and native "
        "bindings that are not represented by usable stubs."
    ),
    "plugin/kicad-blocks/kicad_blocks/composition.py": (
        "Nested composition still needs validated TypedDict contracts for dependency "
        "position/pin-map JSON and a typed boundary for dynamically supplied KiCad IPC objects."
    ),
    "plugin/kicad-blocks/kicad_blocks/extraction.py": (
        "The recursive S-expression parser and sheet-path mutation helpers need a typed "
        "node abstraction before standard-mode checking can be enabled."
    ),
    "plugin/kicad-blocks/kicad_blocks/operations.py": (
        "Recursive BOM/validation report assembly needs explicit report TypedDicts and "
        "validated manifest collection schemas before standard-mode checking can be enabled."
    ),
    "plugin/kicad-blocks/kicad_blocks/pcb_layout.py": (
        "The recursive S-expression tree and imported KiCad project JSON need validated "
        "node/project-setting schemas before standard-mode checking can be enabled."
    ),
    "plugin/kicad-blocks/kicad_blocks/pcb_placement.py": (
        "The sidecar JSON and KiCad IPC KIID adapter need fully typed validated records "
        "and a native KIID stub before standard-mode checking can be enabled."
    ),
}
ANNOTATION_RATCHET_ROOTS = ("examples", "plugin", "scripts")
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
            isinstance(node, ast.Name)
            and node.id == "Any"
            or isinstance(node, ast.Attribute)
            and node.attr == "Any"
            or (
                isinstance(node, (ast.ImportFrom, ast.Import))
                and any(alias.name == "Any" or alias.name.endswith(".Any") for alias in node.names)
            )
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
        f"{relative}:{line}: explicit Any is prohibited" for line in _explicit_any_lines(tree)
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
    """Require each core module to be typed or have an exact reviewed migration boundary."""
    config_path = ROOT / "pyrightconfig.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"pyrightconfig.json: cannot read reviewed inventory: {exc}"]

    included = [ROOT / entry for entry in config.get("include", [])]
    excluded = set(config.get("exclude", []))
    errors: list[str] = []
    core = ROOT / "plugin/kicad-blocks/kicad_blocks"
    discovered = {path.relative_to(ROOT).as_posix(): path for path in sorted(core.glob("*.py"))}
    for relative, path in discovered.items():
        if relative in REVIEWED_PYRIGHT_EXCLUSIONS:
            if relative not in excluded:
                errors.append(
                    f"pyrightconfig.json: staged module {relative} must remain explicitly excluded"
                )
            continue
        if relative in excluded:
            errors.append(
                f"pyrightconfig.json: excluded module {relative} has no reviewed migration rationale"
            )
            continue
        if not any(path == entry or entry in path.parents for entry in included):
            errors.append(
                f"pyrightconfig.json: production module {relative} is absent from the reviewed include inventory"
            )

    for relative, rationale in REVIEWED_PYRIGHT_EXCLUSIONS.items():
        if relative not in discovered:
            errors.append(f"python policy: stale Pyright exclusion for missing module {relative}")
        if relative not in excluded:
            errors.append(
                f"pyrightconfig.json: reviewed exclusion {relative} is not in the exclude list"
            )
        if not rationale.strip():
            errors.append(f"python policy: Pyright exclusion {relative} has no rationale")
    for relative in excluded:
        if (
            relative.startswith("plugin/kicad-blocks/kicad_blocks/")
            and relative.endswith(".py")
            and relative not in REVIEWED_PYRIGHT_EXCLUSIONS
        ):
            errors.append(f"python policy: unreviewed exact Pyright exclusion {relative}")
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
        if isinstance(node, ast.arg):
            if node.annotation is not None:
                annotations.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
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
    """Block new annotation debt in every changed production Python file."""
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
        if not any(
            relative == root or relative.startswith(f"{root}/") for root in ANNOTATION_RATCHET_ROOTS
        ):
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
