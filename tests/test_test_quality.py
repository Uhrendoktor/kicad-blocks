"""Tests for repository-wide Python test-quality contracts."""

from __future__ import annotations

import unittest

from scripts.check_test_quality import (
    annotation_debt,
    bare_assert_lines,
    duplicate_test_bodies,
    suppression_count,
)


class TestQualityPolicyTests(unittest.TestCase):
    """Verify that the static quality gate catches high-confidence test hazards."""

    def test_bare_assert_is_detected_even_when_nested(self) -> None:
        """Find assert statements that Python optimization would remove."""
        source = "def test_contract():\n    if True:\n        assert False\n"
        self.assertEqual(bare_assert_lines("tests/example.py", source), [3])

    def test_identical_test_bodies_are_reported_as_redundant(self) -> None:
        """Detect exact duplicate tests without guessing at semantic similarity."""
        first = "def test_value():\n    self.assertEqual(value, 1)\n"
        second = "def test_other_name():\n    self.assertEqual(value, 1)\n"
        duplicates = duplicate_test_bodies(
            [("tests/first.py", first), ("tests/second.py", second)]
        )
        self.assertEqual(len(duplicates), 1)
        self.assertIn("tests/first.py", duplicates[0])
        self.assertIn("tests/second.py", duplicates[0])

    def test_annotation_debt_counts_any_and_generic_object(self) -> None:
        """Count explicit Any references separately from generic object annotations."""
        source = (
            "from typing import Any\n"
            "value: list[object]\n"
            "def run(item: Any) -> object:\n"
            "    return item\n"
        )
        self.assertEqual(annotation_debt(source), (1, 2))

    def test_inline_suppressions_are_counted(self) -> None:
        """Expose newly added lint and type-check suppressions to the ratchet."""
        source = "value = 1  # noqa: F841\nother = 2  # type: ignore[arg-type]\n"
        self.assertEqual(suppression_count(source), 2)
