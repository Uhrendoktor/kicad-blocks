"""Tests for AST-based source-quality and test-inventory contracts."""

from __future__ import annotations

import ast
import unittest

from scripts.check_python_quality import analyze_source
from scripts.check_test_inventory import _test_methods


class PythonQualityContractTests(unittest.TestCase):
    """Keep the quality ratchet sensitive to annotation and suppression regressions."""

    def test_counts_missing_annotations_and_explicit_escape_hatches(self) -> None:
        """Count missing signature annotations, Any, and generic object annotations."""
        source = (
            "from typing import Any as Dynamic\n"
            "def example(value: Dynamic, fallback: object):\n"
            "    return value\n"
        )
        metrics = analyze_source(source)
        self.assertEqual(metrics.missing_annotations, 1)
        self.assertEqual(metrics.explicit_any, 1)
        self.assertEqual(metrics.generic_object, 1)

    def test_counts_qualified_typing_any_and_builtins_object(self) -> None:
        """Recognize qualified aliases rather than relying on import spelling."""
        source = (
            "import typing as t\n"
            "import builtins as b\n"
            "def example(value: t.Any) -> b.object:\n"
            "    return value\n"
        )
        metrics = analyze_source(source)
        self.assertEqual(metrics.missing_annotations, 0)
        self.assertEqual(metrics.explicit_any, 1)
        self.assertEqual(metrics.generic_object, 1)

    def test_does_not_count_runtime_object_values_as_annotation_debt(self) -> None:
        """Do not confuse calls to object() with catch-all type annotations."""
        source = (
            "def example() -> str:\n"
            "    value = object()\n"
            "    return str(value)\n"
        )
        self.assertEqual(analyze_source(source).generic_object, 0)

    def test_counts_inline_type_and_lint_suppressions(self) -> None:
        """Count inline suppressions so changed code cannot add policy bypasses."""
        source = (
            "def example(value: str) -> str:  # noqa: F401\n"
            "    return value  # type: ignore[assignment]\n"
        )
        self.assertEqual(analyze_source(source).inline_suppressions, 2)

    def test_documentation_debt_is_measured_only_on_production_sources(self) -> None:
        """Track module/API documentation without imposing prose on tests."""
        missing = "def public_api(value: str) -> str:\n    return value\n"
        present = (
            '"""Module summary."""\n\n'
            "def public_api(value: str) -> str:\n"
            '    """Return the supplied value."""\n'
            "    return value\n"
        )
        self.assertEqual(
            analyze_source(missing, "plugin/kicad-blocks/kicad_blocks/sample.py").documentation_debt,
            2,
        )
        self.assertEqual(
            analyze_source(present, "plugin/kicad-blocks/kicad_blocks/sample.py").documentation_debt,
            0,
        )
        self.assertEqual(analyze_source(missing, "tests/test_sample.py").documentation_debt, 0)


class TestInventoryContractTests(unittest.TestCase):
    """Reject test methods that Python would silently shadow."""

    def test_discovers_test_methods_with_source_lines_and_purpose(self) -> None:
        """Capture the test class, source line, and a useful generated purpose."""
        source = ast.parse(
            "class ExampleTests:\n"
            "    def test_rejects_unknown_values(self) -> None:\n"
            "        assert True\n"
        )
        cases = _test_methods(source, "tests/test_example.py")
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].class_name, "ExampleTests")
        self.assertEqual(cases[0].name, "test_rejects_unknown_values")
        self.assertEqual(cases[0].line, 2)
        self.assertIn("rejects unknown values", cases[0].purpose)

    def test_rejects_duplicate_method_names_in_one_test_class(self) -> None:
        """Fail when a later same-name method would replace an earlier test."""
        source = ast.parse(
            "class ExampleTests:\n"
            "    def test_same(self) -> None:\n"
            "        assert True\n"
            "    def test_same(self) -> None:\n"
            "        assert False\n"
        )
        with self.assertRaisesRegex(ValueError, "would shadow the earlier method"):
            _test_methods(source, "tests/test_example.py")
