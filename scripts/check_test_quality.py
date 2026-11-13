#!/usr/bin/env python3
"""Require each collected Python test to contain a meaningful assertion oracle."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests"


def _test_functions(path: Path, root: Path) -> tuple[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef], ...]:
    """Return module-level and TestCase-style test functions with stable IDs."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path), type_comments=True)
    relative = path.relative_to(root).as_posix()
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    class Collector(ast.NodeVisitor):
        """Collect named tests without descending into nested function bodies."""

        def __init__(self) -> None:
            self.classes: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.classes.append(node.name)
            for child in node.body:
                self.visit(child)
            self.classes.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node.name.startswith("test_"):
                qualname = ".".join((*self.classes, node.name)) if self.classes else node.name
                found.append((f"{relative}::{qualname}", node))

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node.name.startswith("test_"):
                qualname = ".".join((*self.classes, node.name)) if self.classes else node.name
                found.append((f"{relative}::{qualname}", node))

    Collector().visit(tree)
    return tuple(found)


def _assertion_oracle(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Recognize language asserts, unittest assertions, mock assertions and pytest.raises."""
    for node in ast.walk(function):
        if isinstance(node, ast.Assert):
            return True
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else (
            node.func.attr if isinstance(node.func, ast.Attribute) else ""
        )
        if name == "raises" or name.startswith("assert"):
            return True
    return False


def _trivial_assertion(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[int, str] | None:
    """Reject constant-true checks and equality checks that compare an expression to itself."""
    for node in ast.walk(function):
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Constant) and node.test.value is True:
            return node.lineno, "bare assert True never checks behavior"
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name = node.func.attr
        if name in {"assertTrue", "assertIsNotNone"} and node.args:
            value = node.args[0]
            if isinstance(value, ast.Constant) and value.value is True:
                return node.lineno, f"{name}(True) is not a behavioral assertion"
        if name in {"assertEqual", "assertEquals", "assertAlmostEqual"} and len(node.args) >= 2:
            if ast.dump(node.args[0], include_attributes=False) == ast.dump(
                node.args[1], include_attributes=False
            ):
                return node.lineno, f"{name} compares an expression with itself"
    return None


def test_quality_violations(root: Path = TEST_ROOT) -> tuple[str, ...]:
    """Find test methods with no assertion oracle or a provably trivial assertion."""
    findings: list[str] = []
    files = sorted(root.glob("test_*.py"))
    for path in files:
        for identifier, function in _test_functions(path, root.parent):
            if not _assertion_oracle(function):
                findings.append(f"{identifier}:{function.lineno}: test has no assertion oracle")
                continue
            trivial = _trivial_assertion(function)
            if trivial is not None:
                line, message = trivial
                findings.append(f"{identifier}:{line}: {message}")
    return tuple(findings)


def main() -> int:
    """Run the assertion-strength gate over every Python test method."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=TEST_ROOT)
    args = parser.parse_args()
    findings = test_quality_violations(args.root.resolve())
    for finding in findings:
        print(finding)
    if findings:
        print(f"test-quality=failed violations={len(findings)}")
        return 1
    count = sum(len(_test_functions(path, args.root.parent)) for path in args.root.glob("test_*.py"))
    print(f"test-quality=passed methods={count} violations=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
