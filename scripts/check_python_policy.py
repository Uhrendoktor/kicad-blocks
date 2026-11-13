#!/usr/bin/env python3
"""Enforce syntax-aware Python policy and reviewed type-checking boundaries."""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = ("examples", "plugin", "scripts", "tests")
DYNAMIC_ANY_FILES = {
    "plugin/kicad-blocks/kicad_blocks/composition.py",
    "plugin/kicad-blocks/kicad_blocks/operations.py",
    "plugin/kicad-blocks/kicad_blocks/pcb_layout.py",
    "plugin/kicad-blocks/kicad_blocks/pcb_placement.py",
    "plugin/kicad-blocks/place_imported_layout.py",
}
# This input is intentionally broad: validate_parameter_value stringifies and
# rejects/normalizes caller-supplied values before any value enters the model.
ALLOWED_OBJECT_ANNOTATIONS = {
    ("plugin/kicad-blocks/kicad_blocks/model.py", "validate_parameter_value.value")
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


def _annotation_name(annotation: ast.expr) -> str:
    """Resolve a function-argument annotation's name within its owner."""
    if isinstance(annotation, ast.Name):
        return annotation.id
    return ""


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

    if relative.startswith(("plugin/", "scripts/", "examples/")) and ast.get_docstring(tree) is None:
        errors.append(f"{relative}: missing module docstring")

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and SUPPRESSION_PATTERN.search(token.string):
            errors.append(
                f"{relative}:{token.start[0]}: inline diagnostic suppression is not allowed"
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any" and relative not in DYNAMIC_ANY_FILES:
            errors.append(
                f"{relative}:{node.lineno}: explicit Any is restricted to reviewed dynamic boundaries"
            )

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
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if (
                    argument.annotation is not None
                    and _annotation_name(argument.annotation) == "object"
                    and (relative, f"{owner}.{argument.arg}") not in ALLOWED_OBJECT_ANNOTATIONS
                ):
                    errors.append(
                        f"{relative}:{argument.lineno}: generic object annotation needs an explicit reviewed boundary"
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


def main() -> int:
    """Run repository-wide syntax-aware policy checks."""
    errors = [error for path in _python_files() for error in _check_file(path)]
    errors.extend(_check_pyright_inventory())
    if errors:
        print("\n".join(errors))
        return 1
    print("python-policy=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
