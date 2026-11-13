"""Tests for deterministic test inventory generation."""

from __future__ import annotations

import unittest

from scripts.test_inventory import collect_test_cases_from_source, describe_test


class TestInventoryTests(unittest.TestCase):
    """Verify source discovery and purpose generation for the test inventory."""

    def test_explicit_docstring_is_used_as_the_test_purpose(self) -> None:
        """Prefer the author's behavior-focused purpose over a generated label."""
        self.assertEqual(
            describe_test("test_opaque_name", "Checks the public behavior.\nMore detail."),
            "Checks the public behavior.",
        )

    def test_missing_docstring_falls_back_to_a_readable_test_name(self) -> None:
        """Keep legacy tests inventoried without forcing boilerplate docstrings."""
        self.assertEqual(
            describe_test("test_nested_symbol_pin_rotation"),
            "Checks: nested symbol pin rotation.",
        )

    def test_collector_records_test_class_and_source_line(self) -> None:
        """Retain stable source identifiers for class-based test methods."""
        source = (
            "class ExampleTests:\n"
            "    def test_rejects_invalid_manifest(self):\n"
            "        self.fail('expected')\n"
        )
        cases = collect_test_cases_from_source("tests/example.py", source)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].class_name, "ExampleTests")
        self.assertEqual(cases[0].name, "test_rejects_invalid_manifest")
        self.assertEqual(cases[0].line, 2)
        self.assertEqual(cases[0].purpose, "Checks: rejects invalid manifest.")

    def test_collector_ignores_helpers_that_are_not_tests(self) -> None:
        """Do not mistake test fixtures or helper functions for independent cases."""
        source = (
            "def make_fixture():\n"
            "    return object()\n"
            "def helper():\n"
            "    return None\n"
        )
        self.assertEqual(collect_test_cases_from_source("tests/helper.py", source), [])
