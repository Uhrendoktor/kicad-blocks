"""Tests for inventory routing and generated test-purpose documentation."""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.check_test_inventory import (
    ROOT,
    load_inventory,
    render_documentation,
    validate_inventory,
)


class TestInventoryContract(unittest.TestCase):
    def test_inventory_covers_every_validation_entrypoint_exactly_once(self) -> None:
        entries = load_inventory(ROOT)
        self.assertEqual(validate_inventory(ROOT, entries), [])

    def test_generated_document_matches_inventory(self) -> None:
        entries = load_inventory(ROOT)
        expected = render_documentation(entries)
        actual = (ROOT / "docs" / "test-inventory.md").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_every_pytest_inventory_target_contains_tests(self) -> None:
        entries = load_inventory(ROOT)
        paths = [entry["path"] for entry in entries if entry["mode"] == "pytest"]
        self.assertTrue(paths)
        self.assertTrue(all((ROOT / path).is_file() for path in paths))
