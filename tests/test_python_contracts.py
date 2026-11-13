"""Regression tests for repository-wide Python policy helpers."""

from __future__ import annotations

import ast
import unittest

from scripts.check_python_contracts import annotation_metrics, explicit_any_lines


class PythonQualityContractTests(unittest.TestCase):
    def test_explicit_any_import_and_annotation_are_both_reported(self) -> None:
        tree = ast.parse("from typing import Any\nvalue: Any\n")
        self.assertEqual(explicit_any_lines(tree), [1, 2])

    def test_qualified_typing_any_is_reported(self) -> None:
        tree = ast.parse("import typing as t\nvalue: t.Any\n")
        self.assertEqual(explicit_any_lines(tree), [2])

    def test_typed_functions_have_no_annotation_debt(self) -> None:
        tree = ast.parse("def convert(value: int) -> int:\n    return value\n")
        self.assertEqual(annotation_metrics(tree), (0, 0))

    def test_missing_parameter_and_return_annotations_are_counted(self) -> None:
        tree = ast.parse("def convert(value):\n    return value\n")
        self.assertEqual(annotation_metrics(tree), (2, 0))

    def test_generic_object_annotations_are_counted_separately(self) -> None:
        tree = ast.parse(
            "def convert(value: list[object]) -> object:\n    return value[0]\n"
        )
        self.assertEqual(annotation_metrics(tree), (0, 2))
