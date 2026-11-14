#!/usr/bin/env python3
"""Reject no-op tests and exact duplicate test bodies within one fixture scope."""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _collect_cases(
    tree: ast.Module,
) -> list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef, list[ast.stmt]]]:
    """Collect only pytest-discovered functions and methods at module/class scope."""
    cases: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef, list[ast.stmt]]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            body = list(node.body)
            if body and _is_docstring(body[0]):
                body = body[1:]
            cases.append(("", node.name, node, body))
        elif isinstance(node, ast.ClassDef) and _is_pytest_class(node.name):
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith("test_"):
                    body = list(member.body)
                    if body and _is_docstring(body[0]):
                        body = body[1:]
                    cases.append((node.name, f"{node.name}.{member.name}", member, body))
    return cases


def _is_pytest_class(name: str) -> bool:
    """Mirror the repository's configured pytest test-class naming conventions."""
    return name.startswith("Test") or name.endswith(("Test", "Tests", "TestCase"))


def _is_docstring(statement: ast.stmt) -> bool:
    """Return true when a statement is a standalone string-docstring expression."""
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _fingerprint(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    body: list[ast.stmt],
) -> str:
    """Build an AST fingerprint excluding test names and descriptive docstrings."""
    payload = {
        "kind": type(node).__name__,
        "arguments": ast.dump(node.args, include_attributes=False),
        "decorators": [
            ast.dump(decorator, include_attributes=False) for decorator in node.decorator_list
        ],
        "returns": ast.dump(node.returns, include_attributes=False)
        if node.returns is not None
        else None,
        "body": ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False),
    }
    return json.dumps(payload, sort_keys=True)


def scan_tests(root: Path) -> tuple[int, list[str]]:
    """Return test count and high-confidence no-op or duplicate-body findings.

    Duplicate matching is intentionally limited to the same module and class/fixture
    scope so tests running under different fixtures or host environments are not
    conflated.
    """
    test_root = root / "tests"
    count = 0
    errors: list[str] = []
    for path in sorted(test_root.rglob("test_*.py")):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError) as exc:
            errors.append(f"{relative}: cannot parse test source: {exc}")
            continue

        duplicates: dict[tuple[str, str], list[str]] = defaultdict(list)
        for scope, qualified_name, node, body in _collect_cases(tree):
            count += 1
            if not body or all(isinstance(statement, ast.Pass) for statement in body):
                errors.append(
                    f"{relative}:{node.lineno}: {qualified_name} has no executable test body"
                )
            duplicates[(scope, _fingerprint(node, body))].append(qualified_name)

        for names in duplicates.values():
            if len(names) > 1:
                errors.append(
                    f"{relative}: exact duplicate test bodies in the same fixture scope: "
                    + ", ".join(sorted(names))
                )
    return count, errors


def main() -> int:
    """Fail when the suite contains a no-op test or an exact duplicate test body."""
    count, errors = scan_tests(ROOT)
    if errors:
        print("\n".join(errors))
        return 1
    print(f"test-quality=passed ({count} tests; no no-op or exact duplicate bodies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
