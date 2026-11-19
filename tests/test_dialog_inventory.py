"""Regression tests for the native message-dialog call-site inventory."""

from __future__ import annotations

from pathlib import Path

from scripts.dialog_callsite_inventory import (
    COVERAGE_LABELS,
    discover_dialog_calls,
    load_catalog,
    render_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "dialog-inventory.md"


def test_catalog_tracks_every_native_messagebox_and_simpledialog_call_site():
    catalog = load_catalog()
    actual = discover_dialog_calls()
    expected_ids = [item["id"] for item in catalog["call_sites"]]
    actual_ids = [item["id"] for item in actual]
    assert len(actual) == 56
    assert actual_ids == expected_ids


def test_dialog_inventory_distinguishes_tested_partial_and_uncovered_paths():
    catalog = load_catalog()
    rendered = render_inventory(catalog)
    assert {item["coverage"] for item in catalog["call_sites"]} <= set(COVERAGE_LABELS)
    for status, label in COVERAGE_LABELS.items():
        assert f"| {status.title()} |" in rendered
        assert label in rendered
    assert "native OS window was rendered" in rendered


def test_every_tested_dialog_call_site_references_existing_behavior_evidence():
    catalog = load_catalog()
    for item in catalog["call_sites"]:
        if item["coverage"] == "tested":
            assert item["evidence"]
            for path in item["evidence"]:
                assert (ROOT / path).is_file()


def test_committed_dialog_inventory_matches_the_ast_catalog():
    assert OUTPUT.read_text(encoding="utf-8") == render_inventory(load_catalog())
