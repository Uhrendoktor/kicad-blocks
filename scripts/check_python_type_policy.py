#!/usr/bin/env python3
"""Reject unrestricted Any and generic built-in object annotations repository-wide."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[1]


class TypePolicyViolation(NamedTuple):
    """One source location that violates the concrete-type policy."""

    path: str
    line: int
    message: str


def _dotted_name(node: ast.expr) -> str | None:
    """Return a dotted expression name without evaluating source code."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _type_roots(tree: ast.Module) -> tuple[set[str], set[str], set[str]]:
    """Resolve local aliases for typing modules, Any and builtins.object."""
    typing_modules = {"typing", "typing_extensions"}
    any_names = {"Any"}
    builtins_modules = {"builtins"}
    object_names = {"object"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"typing", "typing_extensions"}:
                    typing_modules.add(alias.asname or alias.name)
                elif alias.name == "builtins":
                    builtins_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in {"typing", "typing_extensions"}:
                for alias in node.names:
                    if alias.name == "Any":
                        any_names.add(alias.asname or alias.name)
                    if alias.name == "*":
                        any_names.add("*")
            elif module == "builtins":
                for alias in node.names:
                    if alias.name == "object":
                        object_names.add(alias.asname or alias.name)
    return typing_modules, any_names, builtins_modules | object_names


def _is_any(node: ast.expr, typing_modules: set[str], any_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in any_names or "*" in any_names and node.id == "Any"
    if isinstance(node, ast.Attribute) and node.attr == "Any":
        root = _dotted_name(node.value)
        return root in typing_modules
    return False


def _is_generic_object(
    node: ast.expr,
    builtin_names: set[str],
    builtin_modules: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in builtin_names
    if isinstance(node, ast.Attribute) and node.attr == "object":
        return _dotted_name(node.value) in builtin_modules
    return False


def _parse_forward_reference(node: ast.expr) -> ast.expr | None:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return None
    try:
        return ast.parse(node.value, mode="eval").body
    except SyntaxError:
        return None


def _contains_forbidden(
    node: ast.expr,
    *,
    typing_modules: set[str],
    any_names: set[str],
    builtin_names: set[str],
    builtin_modules: set[str],
    reject_object: bool,
) -> str | None:
    """Find an unrestricted type within an annotation, including nested forms."""
    forwarded = _parse_forward_reference(node)
    if forwarded is not None:
        return _contains_forbidden(
            forwarded,
            typing_modules=typing_modules,
            any_names=any_names,
            builtin_names=builtin_names,
            builtin_modules=builtin_modules,
            reject_object=reject_object,
        )
    if _is_any(node, typing_modules, any_names):
        return "typing.Any"
    if reject_object and _is_generic_object(node, builtin_names, builtin_modules):
        return "builtins.object"
    if isinstance(node, ast.Subscript):
        constructor = _dotted_name(node.value)
        if constructor is not None and constructor.rsplit(".", 1)[-1] == "Literal":
            return None
        children = [node.value, node.slice]
    else:
        children = [
            child for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr)
        ]
    for child in children:
        violation = _contains_forbidden(
            child,
            typing_modules=typing_modules,
            any_names=any_names,
            builtin_names=builtin_names,
            builtin_modules=builtin_modules,
            reject_object=reject_object,
        )
        if violation:
            return violation
    return None


def _annotation_expressions(tree: ast.Module) -> list[tuple[int, str, ast.expr]]:
    """Collect parameter, return, variable, alias and type-comment annotations."""
    result: list[tuple[int, str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            result.extend(
                (argument.lineno, f"{node.name} parameter", argument.annotation)
                for argument in arguments
                if argument.annotation is not None
            )
            if node.returns is not None:
                result.append((node.lineno, f"{node.name} return", node.returns))
            if node.type_comment:
                try:
                    comment = ast.parse(node.type_comment, mode="func_type")
                except SyntaxError:
                    comment = None
                if comment is not None:
                    result.extend(
                        (node.lineno, f"{node.name} type-comment parameter", value)
                        for value in comment.argtypes
                    )
                    result.append((node.lineno, f"{node.name} type-comment return", comment.returns))
        elif isinstance(node, ast.AnnAssign):
            label = "variable annotation"
            result.append((node.lineno, label, node.annotation))
            annotation_name = _dotted_name(node.annotation)
            if annotation_name is not None and annotation_name.rsplit(".", 1)[-1] == "TypeAlias":
                if node.value is not None:
                    result.append((node.lineno, "type alias", node.value))
        elif isinstance(node, (ast.Assign, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
            comment = getattr(node, "type_comment", None)
            if comment:
                try:
                    expression = ast.parse(comment, mode="eval").body
                except SyntaxError:
                    expression = None
                if expression is not None:
                    result.append((node.lineno, "type comment", expression))
    return result


def source_type_policy_violations(
    source: str,
    display_path: str = "<source>",
) -> tuple[TypePolicyViolation, ...]:
    """Return all Any/object annotation violations in one Python source string."""
    try:
        tree = ast.parse(source, filename=display_path, type_comments=True)
    except SyntaxError as error:
        return (
            TypePolicyViolation(
                display_path,
                error.lineno or 1,
                f"invalid Python syntax: {error.msg}",
            ),
        )
    typing_modules, any_names, builtin_names_and_modules = _type_roots(tree)
    builtin_names = {"object"}
    builtin_modules = {"builtins"}
    for name in builtin_names_and_modules:
        if name == "object":
            builtin_names.add(name)
        elif name not in {"typing", "typing_extensions"}:
            builtin_modules.add(name)

    violations: list[TypePolicyViolation] = []
    seen: set[tuple[int, str]] = set()
    for line, label, expression in _annotation_expressions(tree):
        for reject_object in (False, True):
            kind = _contains_forbidden(
                expression,
                typing_modules=typing_modules,
                any_names=any_names,
                builtin_names=builtin_names,
                builtin_modules=builtin_modules,
                reject_object=reject_object,
            )
            if kind is None:
                continue
            key = (line, kind)
            if key in seen:
                continue
            seen.add(key)
            detail = (
                "unrestricted Any annotations are prohibited"
                if kind == "typing.Any"
                else "generic built-in object annotations are prohibited"
            )
            violations.append(TypePolicyViolation(display_path, line, f"{label}: {detail}"))
            break
    return tuple(violations)


def python_sources(root: Path) -> tuple[Path, ...]:
    """Return tracked-worktree Python sources while excluding generated caches."""
    excluded = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if not any(part in excluded for part in path.relative_to(root).parts)
        )
    )


def repository_violations(root: Path = ROOT) -> tuple[TypePolicyViolation, ...]:
    """Run the type escape-hatch policy across every repository Python source."""
    findings: list[TypePolicyViolation] = []
    for path in python_sources(root):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            findings.append(
                TypePolicyViolation(
                    path.relative_to(root).as_posix(),
                    1,
                    f"could not read source: {error}",
                )
            )
            continue
        findings.extend(
            source_type_policy_violations(source, path.relative_to(root).as_posix())
        )
    return tuple(findings)


def main() -> int:
    """Run the repository-wide zero-escape-hatch gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    findings = repository_violations(args.root.resolve())
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.message}")
    if findings:
        print(f"python-type-policy=failed violations={len(findings)}")
        return 1
    print("python-type-policy=passed violations=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
